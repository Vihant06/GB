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
from typing import AsyncGenerator

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_actor_identity
from app.budget import get_current_budget, request_budget_scope
from app.config import DB_PATH, get_repo_full_name, get_repo_root
from app.database import connect_sqlite, init_db
from app.graph.workflow import repomind_graph
from app.repo_manager import clone_and_index_repo, get_active_repo_info
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


@router.get("/repo/active")
async def get_active_repository():
    """Retrieve metadata about the currently loaded repository."""
    return get_active_repo_info()


@router.post("/repo/index", response_model=RepoIndexResponse)
@router.post("/repo/link", response_model=RepoIndexResponse)
async def index_repository(request: RepoIndexRequest):
    """
    Clone, analyze, and index a new GitHub repository dynamically.
    Switches the active analysis context without restarting the server.
    """
    try:
        result = await anyio.to_thread.run_sync(clone_and_index_repo, request.repo_url)
        return RepoIndexResponse(**result)
    except Exception as e:
        logger.exception("Failed to clone/index repository %s: %s", request.repo_url, e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to clone and index repository '{request.repo_url}': {str(e)}",
        )


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


def _run_graph_sync(initial_state: dict, budget_tracker) -> dict:
    """Run LangGraph synchronously inside an isolated thread with propagated budget context."""
    with request_budget_scope(budget_tracker):
        return repomind_graph.invoke(initial_state)


@router.post("/query", response_model=QueryResponse)
async def execute_query(
    request: QueryRequest,
    req: Request,
    actor_id: str = Depends(get_actor_identity),
):
    """Execute a non-streaming query against RepoMind."""
    if request.repo_url:
        try:
            await anyio.to_thread.run_sync(clone_and_index_repo, request.repo_url)
        except Exception as e:
            logger.warning("Could not dynamically prepare repository %s: %s", request.repo_url, e)

    with request_budget_scope() as budget:
        initial_state = {
            "user_query": request.query,
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
            result = await anyio.to_thread.run_sync(_run_graph_sync, initial_state, budget)
        except Exception as e:
            logger.exception("Error executing RepoMind query: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An internal error occurred during query execution.",
            )

        report = result.get("final_report", "")
        status_val = "completed"
        if "[RATE LIMITED]" in report:
            status_val = "rate_limited"
        elif "[GUARDRAIL BLOCKED]" in report:
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
            report=report,
            trace_logs=result.get("trace_logs", []),
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
    if request.repo_url:
        try:
            await anyio.to_thread.run_sync(clone_and_index_repo, request.repo_url)
        except Exception as e:
            logger.warning("Could not dynamically prepare repository %s: %s", request.repo_url, e)

    async def event_generator() -> AsyncGenerator[str, None]:
        with request_budget_scope() as budget:
            initial_state = {
                "user_query": request.query,
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
            current_repo_name = get_repo_full_name()
            status_msg = f"Analyzing query for repository {current_repo_name}..." if current_repo_name else "Analyzing query..."
            yield _format_sse(
                "status",
                {"stage": "init", "message": status_msg, "text": ""},
            )

            final_report = ""
            collected_logs = []

            try:
                # Stream node updates from LangGraph
                async for event in repomind_graph.astream(initial_state, stream_mode="updates"):
                    # Check for client disconnect
                    if await req.is_disconnected():
                        logger.info("Client disconnected from /query/stream for actor '%s'", actor_id)
                        return

                    for node_name, node_output in event.items():
                        logs = node_output.get("trace_logs", [])
                        if logs:
                            collected_logs.extend(logs)
                            for log in logs:
                                yield _format_sse(
                                    "progress",
                                    {"node": node_name, "message": log, "stage": node_name},
                                )

                        if node_output.get("final_report"):
                            final_report = node_output["final_report"]

                        # Yield incremental stage progress
                        if node_name == "orchestrator_plan":
                            reasoning = node_output.get("plan_reasoning", "")
                            yield _format_sse(
                                "progress",
                                {
                                    "node": "planner",
                                    "stage": "planning",
                                    "message": f"Investigation Plan: {reasoning}",
                                },
                            )
                        elif node_name in (
                            "repository_intelligence",
                            "debugging",
                            "change_impact",
                            "test_intelligence",
                        ):
                            evidence_count = len(node_output.get("evidence", []))
                            yield _format_sse(
                                "progress",
                                {
                                    "node": node_name,
                                    "stage": "specialist",
                                    "message": f"Specialist '{node_name}' gathered {evidence_count} evidence item(s).",
                                },
                            )
                        elif node_name == "verify_citations":
                            verified_count = len(node_output.get("verified_evidence", []))
                            yield _format_sse(
                                "progress",
                                {
                                    "node": "citation_verifier",
                                    "stage": "verification",
                                    "message": f"Verified {verified_count} grounded citation(s).",
                                },
                            )

                # 2. Stream Report Tokens
                if final_report:
                    # Stream tokens in small chunks for responsive UX
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

                yield _format_sse(
                    "complete",
                    {
                        "report": final_report,
                        "text": "",
                        "trace_logs": collected_logs,
                        "usage": usage_dict,
                        "done": True,
                    },
                )

            except Exception as e:
                logger.exception("Error in /query/stream: %s", e)
                yield _format_sse(
                    "error",
                    {"error": f"Investigation failed: {str(e)}"},
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
