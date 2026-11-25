"""
RepoMind FastAPI routes.
Endpoints:
- GET /health
- POST /query
- POST /query/stream (Server-Sent Events)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
import time
from typing import AsyncGenerator

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_actor_identity
from app.budget import get_current_budget, request_budget_scope
from app.config import BASE_DIR, DB_PATH, RepositorySnapshot, get_repo_full_name, get_repo_root, repository_scope
from app.database import connect_sqlite, init_db
from app.graph.nodes import reset_token_callback, set_token_callback
from app.graph.workflow import repomind_graph
from app.repo_manager import clone_and_index_repo, get_active_repo_info, prepare_query_repo
from app.schemas import (
    HealthResponse,
    QueryRequest,
    QueryResponse,
    RepoIndexRequest,
    RepoIndexResponse,
    UsageMetrics,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Patterns that must never reach the frontend
_PRIVATE_PATTERNS = [
    re.compile(r"\b(ip|key):[a-f0-9]{8,}\b", re.IGNORECASE),  # hashed IPs / key fingerprints
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),                # raw IPv4 addresses
    re.compile(r"'[a-f0-9]{12,}'"),                            # any hex actor-id in quotes
    re.compile(r"for '[^']{8,}'"),                             # "for '<something>'" identity leaks
]

# Map internal bracket-tags to human-readable labels
_TAG_MAP = {
    "[Input Guardrail | LLM Router]": "Routing query",
    "[Input Guardrail]": "Query validated",
    "[Rate Limiter]": "Rate limit OK",
    "[Orchestrator Plan |": "Planning investigation",
    "[Orchestrator Plan]": "Planning investigation",
    "[Orchestrator Review |": "Reviewing evidence",
    "[Orchestrator Review]": "Reviewing evidence",
    "[Citation Verifier]": "Verifying citations",
    "[Orchestrator Synthesize]": "Synthesizing answer",
    "[Output Guardrail]": "Finalizing response",
    "[No Evidence Exit]": "No evidence found",
    "[General Coding Fast Path]": "Answering question",
    "[repository_intelligence": "Repository Intelligence agent",
    "[debugging": "Debugging agent",
    "[change_impact": "Change Impact agent",
    "[test_intelligence": "Test Intelligence agent",
    "[repository_intelligence | AST Fast Path]": "Repository Intelligence agent (fast path)",
}


def _sanitize_log(log: str) -> str:
    """Convert internal trace log lines into safe, user-friendly UI messages."""
    # Strip private identity / IP data first
    sanitized = log
    for pat in _PRIVATE_PATTERNS:
        sanitized = pat.sub("", sanitized)

    # Remap internal bracket-tags to readable labels
    for tag, label in _TAG_MAP.items():
        if tag in sanitized:
            # Keep the informative part after the tag if any
            after = sanitized.split("]", 1)[-1].strip(" .:") if "]" in sanitized else ""
            # Remove internal attempt numbers, pipe separators, etc.
            after = re.sub(r"\s*\|\s*Attempt\s*\d+/\d*", "", after)
            after = re.sub(r"\s*\|\s*Attempt\s*\d+", "", after)
            sanitized = f"{label}: {after}".strip(": ") if after else label
            break

    # Drop any residual bracket-prefixed internal tags
    sanitized = re.sub(r"^\[[\w\s|]+\]\s*", "", sanitized)

    # Collapse extra whitespace and punctuation
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip(" .:,")
    return sanitized or log  # fall back to original only if result is empty


@router.get("/repo/active")
async def get_active_repository():
    """Retrieve metadata for the operator-configured default repository."""
    return get_active_repo_info()


@router.post("/repo/index", response_model=RepoIndexResponse)
@router.post("/repo/link", response_model=RepoIndexResponse)
async def index_repository(request: RepoIndexRequest):
    """Prepare a public GitHub checkout and return its pinned revision."""
    try:
        result = await anyio.to_thread.run_sync(clone_and_index_repo, request.repo_url)
        return RepoIndexResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as e:
        logger.warning("Failed to prepare repository: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The requested repository could not be indexed.",
        ) from e


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint: verifies database readiness and repository availability."""
    db_ready = False
    try:
        init_db()
        conn = connect_sqlite()
        try:
            conn.execute("SELECT 1").fetchone()
            db_ready = True
        finally:
            conn.close()
    except Exception as e:
        logger.error("Health check database probe failed: %s", e)

    repo_root = get_repo_root()
    repo_name = get_repo_full_name()
    repo_available = bool(repo_name and repo_root and repo_root.exists())
    status_str = "ok" if db_ready else "error"

    return HealthResponse(
        status=status_str,
        repo_name=repo_name,
        repo_available=repo_available,
        database_ready=db_ready,
        version="1.0.0",
    )


def _format_sse(event_type: str, data: dict) -> str:
    """Format a standard Server-Sent Event frame."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _resolve_query_snapshot(request: QueryRequest) -> RepositorySnapshot:
    target = request.repo_url or get_repo_full_name()
    if not target:
        if request.repo_revision:
            raise HTTPException(status_code=400, detail="A repository is required for a pinned revision.")
        return RepositorySnapshot("", BASE_DIR / "data" / ".no-repository")
    try:
        result = await anyio.to_thread.run_sync(prepare_query_repo, target, request.repo_revision)
    except ValueError as exc:
        raise HTTPException(status_code=409 if request.repo_revision else 400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Could not prepare requested repository: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The requested repository could not be prepared.") from exc
    return RepositorySnapshot(result["repo_name"], Path(result["repo_path"]), result["repo_revision"])


def _run_graph_sync(initial_state: dict, budget_tracker, snapshot: RepositorySnapshot) -> dict:
    """Run LangGraph synchronously inside an isolated thread with propagated budget context."""
    with request_budget_scope(budget_tracker), repository_scope(snapshot):
        return repomind_graph.invoke(initial_state)


@router.post("/query", response_model=QueryResponse)
async def execute_query(
    request: QueryRequest,
    req: Request,
    actor_id: str = Depends(get_actor_identity),
):
    """Execute a non-streaming query against RepoMind."""
    snapshot = await _resolve_query_snapshot(request)

    with request_budget_scope() as budget:
        initial_state = {
            "user_query": request.query,
            "chat_history": request.history[-6:],
            "actor_id": actor_id,
            "attempt_count": 0,
            "plan_reasoning": "",
            "tasks": [],
            "evidence": [],
            "verified_evidence": [],
            "is_sufficient": False,
            "missing_info_feedback": "",
            "trace_logs": [],
            "final_report": "",
        }

        try:
            result = await anyio.to_thread.run_sync(_run_graph_sync, initial_state, budget, snapshot)
        except Exception as e:
            logger.exception("Error executing RepoMind query: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An internal error occurred during query execution.",
            )

        report = result.get("final_report", "")
        raw_logs_str = " ".join(result.get("trace_logs", []))
        status_val = "completed"
        if "[Rate Limiter]" in raw_logs_str and "limit" in report.lower():
            status_val = "rate_limited"
        elif "[Input Guardrail] Blocked" in raw_logs_str or "I cannot answer that question" in report:
            status_val = "blocked"

        usage = UsageMetrics(
            exa_calls=budget.exa_calls,
            context7_calls=budget.context7_calls,
            firecrawl_pages=budget.firecrawl_pages,
            cache_hits=budget.cache_hits,
            total_exa_usd=budget.total_exa_usd,
        )

        return QueryResponse(
            query=request.query,
            repo_revision=snapshot.revision,
            report=report,
            trace_logs=[_sanitize_log(l) for l in result.get("trace_logs", [])],
            usage=usage,
            status=status_val,
        )


@router.post("/query/stream")
async def stream_query(
    request: QueryRequest,
    req: Request,
    actor_id: str = Depends(get_actor_identity),
):
    """
    Stream query execution traces, node progress, tokens, and final engineering report via SSE.
    """
    snapshot = await _resolve_query_snapshot(request)

    async def event_generator() -> AsyncGenerator[str, None]:
        with request_budget_scope() as budget:
            initial_state = {
                "user_query": request.query,
                "chat_history": request.history[-6:],
                "actor_id": actor_id,
                "attempt_count": 0,
                "plan_reasoning": "",
                "tasks": [],
                "evidence": [],
                "verified_evidence": [],
                "is_sufficient": False,
                "missing_info_feedback": "",
                "trace_logs": [],
                "final_report": "",
            }

            # 1. Initial Status Event
            current_repo_name = snapshot.name
            status_msg = f"Analyzing query for repository {current_repo_name}..." if current_repo_name else "Analyzing query..."
            yield _format_sse(
                "status",
                {"stage": "init", "message": status_msg, "text": "", "repo_revision": snapshot.revision},
            )

            final_report = ""
            collected_logs = []
            verified_citations = []
            streamed_live_tokens = False
            sse_queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()
            req_start = time.perf_counter()
            last_step_t = req_start
            ttft_ms: int | None = None

            def _on_live_token(tok: str) -> None:
                loop.call_soon_threadsafe(sse_queue.put_nowait, ("token", tok))

            cb_token = set_token_callback(_on_live_token)

            async def _produce_graph_events() -> None:
                try:
                    with repository_scope(snapshot):
                        async for event in repomind_graph.astream(initial_state, stream_mode="updates"):
                            await sse_queue.put(("event", event))
                    await sse_queue.put(("done", None))
                except Exception as exc:
                    await sse_queue.put(("error", exc))

            producer_task = asyncio.create_task(_produce_graph_events())

            try:
                while True:
                    if await req.is_disconnected():
                        producer_task.cancel()
                        logger.info("Client disconnected from /query/stream for actor '%s'", actor_id)
                        return

                    kind, payload_item = await sse_queue.get()
                    if kind == "done":
                        break
                    if kind == "error":
                        raise payload_item
                    if kind == "token":
                        if not streamed_live_tokens:
                            ttft_ms = int((time.perf_counter() - req_start) * 1000)
                        streamed_live_tokens = True
                        yield _format_sse("token", {"token": payload_item, "text": payload_item})
                        continue

                    now_t = time.perf_counter()
                    step_ms = int((now_t - last_step_t) * 1000)
                    last_step_t = now_t
                    event = payload_item
                    for node_name, node_output in event.items():
                        logger.info("[SSE Timing] node=%s step_ms=%d total_ms=%d", node_name, step_ms, int((now_t - req_start) * 1000))
                        logs = node_output.get("trace_logs", [])
                        if logs:
                            collected_logs.extend(logs)

                        if node_output.get("final_report"):
                            final_report = node_output["final_report"]

                        # Yield incremental stage progress
                        if node_name == "orchestrator_plan":
                            yield _format_sse(
                                "progress",
                                {
                                    "node": "planner",
                                    "stage": "planning",
                                    "message": "Understanding your question",
                                },
                            )
                        elif node_name in (
                            "repository_intelligence",
                            "debugging",
                            "change_impact",
                            "test_intelligence",
                        ):
                            yield _format_sse(
                                "progress",
                                {
                                    "node": node_name,
                                    "stage": "specialist",
                                    "message": "Looking through the repository",
                                },
                            )
                        elif node_name == "verify_citations":
                            verified = node_output.get("verified_evidence", [])
                            verified_citations = list(dict.fromkeys(
                                f"{e['file_path']}:L{e['start_line']}-L{e['end_line']}" for e in verified
                            ))
                            yield _format_sse(
                                "progress",
                                {
                                    "node": "citation_verifier",
                                    "stage": "verification",
                                    "message": "Checking the sources",
                                },
                            )

                # 2. Fallback token stream if no live synthesis tokens were emitted (e.g. guardrail or mock)
                if final_report and not streamed_live_tokens:
                    words = final_report.split(" ")
                    for i, word in enumerate(words):
                        if await req.is_disconnected():
                            return
                        chunk = word + (" " if i < len(words) - 1 else "")
                        yield _format_sse("token", {"token": chunk, "text": chunk})
                        if i % 6 == 0:
                            await asyncio.sleep(0.01)

                # 3. Final Complete Event
                usage_dict = {
                    "exa_calls": budget.exa_calls,
                    "context7_calls": budget.context7_calls,
                    "firecrawl_pages": budget.firecrawl_pages,
                    "cache_hits": budget.cache_hits,
                    "total_exa_usd": budget.total_exa_usd,
                }

                total_ms = int((time.perf_counter() - req_start) * 1000)
                logger.info("[SSE Timing] complete total_ms=%d ttft_ms=%s", total_ms, ttft_ms)
                yield _format_sse(
                    "complete",
                    {
                        "report": final_report,
                        "text": "",
                        "trace_logs": [_sanitize_log(l) for l in collected_logs],
                        "verified_citations": verified_citations,
                        "repo_revision": snapshot.revision,
                        "usage": usage_dict,
                        "timing_ms": {"total_ms": total_ms, "ttft_ms": ttft_ms},
                        "done": True,
                    },
                )

            except Exception as e:
                logger.exception("Error in /query/stream: %s", e)
                yield _format_sse(
                    "error",
                    {"error": "RepoMind couldn’t complete this answer. Please try again."},
                )
            finally:
                reset_token_callback(cb_token)
                if not producer_task.done():
                    producer_task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
