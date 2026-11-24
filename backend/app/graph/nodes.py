"""
RepoMind LangGraph nodes and conditional edge routers.
Orchestrates input guardrails, atomic rate-limit gates, 3-way LLM routing,
parallel specialist execution via LangGraph Send, AST fast paths,
review loops, strict citation verification, and report synthesis.
"""

from __future__ import annotations

import ast
import contextlib
import os
from pathlib import Path
import re
import sqlite3
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Send

from app.agents.base import structured_invoke
from app.agents.specialists import (
    change_impact_agent,
    debugging_agent,
    repo_intel_agent,
    test_intel_agent,
)
from app.budget import get_current_budget
from app.config import (
    DB_PATH,
    MAX_QUERY_CHARS,
    REPO_FULL_NAME,
    fast_llm,
    get_repo_full_name,
    get_repo_root,
    llm,
)
from app.database import (
    check_and_record_query_rate_limit,
    connect_sqlite,
)
from app.graph.state import (
    EvidenceEvaluation,
    InvestigationPlan,
    QueryRouteJudgment,
    RepoMindState,
)
from app.guardrails import (
    _INJECTION_PATTERNS,
    redact_secrets,
    safe_repo_path,
)
from app.tools.web_docs import (
    exa_search_technical,
    firecrawl_ingest_docs,
)


def _is_deep_query(query: str) -> bool:
    ql = query.lower()
    return any(
        k in ql
        for k in (
            "blast radius",
            "compare",
            "firecrawl",
            "exa",
            "context7",
            "github issue",
            "pitfall",
            "migration",
            "all callers",
        )
    )


def classify_query_llm(query: str) -> tuple[str, str]:
    """Fast LLM-as-a-judge call to classify off_topic vs general_coding vs repo_specific."""
    current_repo_name = get_repo_full_name()
    q_lower = query.lower().strip()

    # Direct heuristic classification for repository-related questions
    repo_signals = (
        "this repo",
        "the repo",
        "this repository",
        "the repository",
        "this project",
        "the project",
        "this codebase",
        "the codebase",
        "what does this",
        "what is this",
        "explain this",
        "tell me about this",
        "tell me about",
        "about this",
        "about the",
        "how does this",
        "how to run",
        "architecture",
        "file structure",
        "dependencies",
        "endpoints",
        "routes",
    )
    if any(sig in q_lower for sig in repo_signals):
        return "repo_specific", f"Query asks about the active repository '{current_repo_name}'."

    repo_parts = current_repo_name.lower().split("/")
    if any(part in q_lower for part in repo_parts if len(part) > 2):
        return "repo_specific", f"Query explicitly references '{current_repo_name}'."

    if fast_llm is None and llm is None:
        # Fallback for offline testing without active LLM
        return "repo_specific", "Defaulting to repo_specific in offline mode."

    judge_sys = SystemMessage(
        content=(
            "/no_think\n"
            f"You are a strict router for RepoMind, which indexes the repository '{current_repo_name}'.\n"
            "Classify the user query into exactly one route:\n"
            "1. 'off_topic': Completely unrelated to software engineering, programming, or coding (e.g. food, sports, travel).\n"
            "2. 'general_coding': Pure programming language syntax or theoretical algorithm questions that are completely generic "
            f"and do NOT relate to the project '{current_repo_name}' or its source files.\n"
            f"3. 'repo_specific': Any question about '{current_repo_name}', this repository, this project, its purpose, "
            "architecture, source code, functions, classes, tests, or issues."
        )
    )
    verdict: QueryRouteJudgment = structured_invoke(
        fast_llm or llm,
        QueryRouteJudgment,
        [judge_sys, HumanMessage(content=f"User Query:\n{query}")],
    )
    return verdict.route, verdict.reason


def input_guardrail_node(state: RepoMindState):
    """Length, injection, secret checks + 3-way LLM router before any specialist or repo scan."""
    raw_query = (state.get("user_query") or "").strip()
    if not raw_query:
        return {
            "user_query": "",
            "final_report": "I can answer a coding-related question only.",
            "trace_logs": ["[Input Guardrail] Blocked empty query."],
        }

    if len(raw_query) > MAX_QUERY_CHARS:
        return {
            "final_report": f"[GUARDRAIL BLOCKED] Query exceeds maximum length of {MAX_QUERY_CHARS} characters.",
            "trace_logs": [f"[Input Guardrail] Blocked oversized query ({len(raw_query)} chars)."],
        }

    lower_q = raw_query.lower()
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, lower_q):
            return {
                "final_report": "[GUARDRAIL BLOCKED] Query matched a prompt-injection or credential-exfiltration pattern.",
                "trace_logs": ["[Input Guardrail] Blocked prompt-injection attempt."],
            }

    sanitized_query, secret_hits = redact_secrets(raw_query)
    route, reason = classify_query_llm(sanitized_query)

    logs = [f"[Input Guardrail | LLM Router] Route='{route}' ({reason})."]
    if secret_hits:
        logs.append(f"[Input Guardrail] Redacted {secret_hits} sensitive token(s).")

    if route == "off_topic":
        return {
            "user_query": sanitized_query,
            "final_report": "I can answer a coding-related question only.",
            "trace_logs": logs,
        }

    return {
        "user_query": sanitized_query,
        "plan_reasoning": f"__ROUTE__:{route}",
        "trace_logs": logs,
    }


def route_after_input_guardrail(state: RepoMindState) -> Literal["rate_limit_gate", "end"]:
    return "end" if state.get("final_report") else "rate_limit_gate"


def rate_limit_gate_node(state: RepoMindState):
    """Enforce query rate limits right before orchestration."""
    if state.get("final_report"):
        return {}
    actor_id = state.get("actor_id") or "default_user"
    allowed, msg = check_and_record_query_rate_limit(actor_id)
    if not allowed:
        return {
            "final_report": f"[RATE LIMITED] {msg}",
            "trace_logs": [f"[Rate Limiter] {msg}"],
        }
    return {
        "trace_logs": ["[Rate Limiter] Query is within rate limits."]
    }


def route_after_rate_limit(
    state: RepoMindState,
) -> Literal["orchestrator_plan", "general_coding", "end"]:
    if state.get("final_report"):
        return "end"
    if state.get("plan_reasoning") == "__ROUTE__:general_coding":
        return "general_coding"
    return "orchestrator_plan"


def general_coding_node(state: RepoMindState):
    """Fast path for non-repo coding questions: optional Exa + 1-page Firecrawl lookup -> fast_llm."""
    query = state.get("user_query", "").strip()
    web_snippets = []
    used_sources = []

    top_url = None
    if os.getenv("EXA_API_KEY", "").strip():
        try:
            raw_exa = exa_search_technical.invoke(
                {"query": query, "search_mode": "lite", "num_results": 2}
            )
            if raw_exa and not raw_exa.startswith(
                ("[SKIPPED]", "[BUDGET_LIMIT]", "[EXA_ERROR]", "[EXA_EMPTY]")
            ):
                web_snippets.append(f"Exa Results:\n{raw_exa[:1200]}")
                used_sources.append("Exa")
                urls = re.findall(r"https?://[^\s\)\]>\"']+", raw_exa)
                if urls:
                    top_url = urls[0].rstrip(".,;:")
        except Exception:
            pass

    prompt_urls = re.findall(r"https?://[^\s\)\]>\"']+", query)
    target_url = prompt_urls[0].rstrip(".,;:") if prompt_urls else top_url
    if target_url and os.getenv("FIRECRAWL_API_KEY", "").strip():
        try:
            fc_out = firecrawl_ingest_docs.invoke(
                {"url": target_url, "query": query, "max_pages": 1}
            )
            if fc_out and not str(fc_out).startswith(
                ("[SKIPPED]", "[BUDGET_LIMIT]", "[FIRECRAWL_ERROR]", "[FIRECRAWL_EMPTY]")
            ):
                web_snippets.append(f"Firecrawl ({target_url}):\n{str(fc_out)[:1200]}")
                used_sources.append("Firecrawl")
        except Exception:
            pass

    context_block = (
        ("\n\nWeb Reference Context:\n" + "\n\n".join(web_snippets)) if web_snippets else ""
    )

    sys_msg = SystemMessage(
        content=(
            "/no_think\n"
            "You are a concise software engineering assistant. "
            "This query is a general coding question outside the indexed repository. "
            "Give a clear, accurate, concise answer. If web references are provided, cite their URLs."
        )
    )
    model = fast_llm or llm
    if model:
        resp = model.invoke([sys_msg, HumanMessage(content=f"Question: {query}{context_block}")])
        raw_text = getattr(resp, "content", "") or ""
        content = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    else:
        content = f"Mocked answer for query: {query}"

    src_note = ", ".join(used_sources) if used_sources else "direct fast_llm"
    return {
        "final_report": content,
        "trace_logs": [f"[General Coding Fast Path] Answered outside local repo via {src_note}."],
    }


def _ast_extract_symbol_evidence(sub_query: str, user_query: str) -> list[dict]:
    """Extract exact AST line ranges for explicitly named .py files and Class.method/function symbols."""
    combined = f"{user_query} {sub_query}"
    file_matches = re.findall(r"([A-Za-z0-9_./-]+\.py)\b", combined)
    sym_matches = re.findall(r"\b([A-Z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b", combined)
    if not file_matches and not sym_matches:
        return []

    target_files = []
    for f in dict.fromkeys(file_matches):
        p, err = safe_repo_path(f)
        if not err and p is not None and p.exists() and p.is_file():
            target_files.append((f, p))

    if not target_files:
        return []

    target_methods = {s.split(".")[-1] for s in sym_matches}
    target_classes = {s.split(".")[0] for s in sym_matches}
    evidence = []

    for rel_path, full_path in target_files[:2]:
        source = full_path.read_text(encoding="utf-8", errors="ignore")
        lines = source.splitlines()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (not target_classes or node.name in target_classes):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not target_methods or item.name in target_methods or item.name == "__init__":
                            item_end = getattr(item, "end_lineno", item.lineno)
                            snippet = "\n".join(lines[item.lineno - 1 : item_end])
                            evidence.append({
                                "specialist": "repository_intelligence",
                                "file_path": rel_path,
                                "start_line": item.lineno,
                                "end_line": item_end,
                                "claim": f"`{node.name}.{item.name}` implementation:\n```python\n{snippet[:1500]}\n```",
                            })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in target_methods:
                end_lineno = getattr(node, "end_lineno", node.lineno)
                snippet = "\n".join(lines[node.lineno - 1 : end_lineno])
                evidence.append({
                    "specialist": "repository_intelligence",
                    "file_path": rel_path,
                    "start_line": node.lineno,
                    "end_line": end_lineno,
                    "claim": f"`{node.name}` implementation:\n```python\n{snippet[:1500]}\n```",
                })

    return evidence[:6]


def _run_specialist(agent_graph, specialist_label: str, sub_query: str) -> dict:
    result = agent_graph.invoke({
        "messages": [HumanMessage(content=sub_query)],
        "tool_steps": 0,
        "report": None,
    })
    report = result.get("report")
    if not report:
        return {
            "evidence": [],
            "trace_logs": [f"[{specialist_label}] Returned no structured report."],
        }

    items = [
        {
            "specialist": specialist_label,
            "claim": item.claim,
            "file_path": item.file_path,
            "start_line": item.start_line,
            "end_line": item.end_line,
        }
        for item in report.evidence
    ]
    return {
        "evidence": items,
        "trace_logs": [f"[{specialist_label}] Collected {len(items)} cited items for: '{sub_query}'"],
    }


def run_repo_intel(state: dict):
    """Use instant AST extraction when query names an exact file + symbol; otherwise run full specialist."""
    sub_query = state.get("sub_query", "")
    user_query = state.get("user_query", "")

    if not _is_deep_query(user_query):
        direct_hits = _ast_extract_symbol_evidence(sub_query, user_query)
        if direct_hits:
            return {
                "evidence": direct_hits,
                "trace_logs": [
                    f"[repository_intelligence | AST Fast Path] Extracted {len(direct_hits)} exact symbol block(s) in <0.1s."
                ],
            }

    return _run_specialist(repo_intel_agent, "repository_intelligence", sub_query)


def run_debugging(state: dict):
    return _run_specialist(debugging_agent, "debugging", state["sub_query"])


def run_change_impact(state: dict):
    return _run_specialist(change_impact_agent, "change_impact", state["sub_query"])


def run_test_intel(state: dict):
    return _run_specialist(test_intel_agent, "test_intelligence", state["sub_query"])


PLANNER_PROMPT = """You are the Orchestrator Planner for RepoMind.
Route the developer's query to 1 to 4 parallel specialist tasks:
- repository_intelligence: local code execution flow, repository layout, README/manifests, Context7 library docs, Firecrawl external doc ingestion, AND extracting actual data content from files.
- debugging: runtime errors, exceptions, GitHub Issues, GitHub Actions CI failures, Exa web search, and Context7 docs.
- change_impact: blast radius of code edits, callers, importers, broken APIs/tests, and migration docs.
- test_intelligence: existing test cases, assertions, test coverage, and untested methods.

Critical planning rules:
1. On Attempt 1, if a query combines independent sources (e.g. Firecrawl URL + Context7 docs + local code), dispatch them as SEPARATE parallel tasks.
2. When the user asks what the repository does, its purpose, architecture, or an overview: dispatch 'repository_intelligence' with a sub_query instructed to list files, read the README, inspect project manifests (package.json, requirements.txt, pyproject.toml), and inspect main entrypoints (e.g. app.js, app.py, main.py).
3. When the user asks about DATA contained in files (e.g. 'list questions', 'show entries', 'what are the top items'), dispatch 'repository_intelligence' with a sub_query that instructs reading the relevant data files and EXTRACTING the actual content values.
4. When dispatching a Firecrawl task, keep concrete technical keywords in the sub_query (e.g. 'Transport Adapters HTTPAdapter Retry mount') so FTS5 retrieves the exact section—never ask vaguely for 'full documentation'.
5. On Attempt 2, ONLY dispatch tasks for the specific gaps listed in Missing Information Feedback; do NOT re-run sources already present in Already Collected Evidence."""


def orchestrator_plan_node(state: RepoMindState):
    attempt = state.get("attempt_count", 0) + 1
    prior_evidence = state.get("evidence", [])
    feedback = state.get("missing_info_feedback", "")
    curr_repo = get_repo_full_name()

    context_msg = f"Developer Query: {state['user_query']}\nActive Repository: {curr_repo}\nAttempt: {attempt}/2"
    if prior_evidence:
        summary_lines = [
            f"- [{e['specialist']}] {e['file_path']}:L{e['start_line']}-L{e['end_line']} -> {e['claim']}"
            for e in prior_evidence
        ]
        context_msg += "\n\nAlready Collected Evidence:\n" + "\n".join(summary_lines)
    if feedback:
        context_msg += f"\n\nMissing Information Feedback from Review:\n{feedback}"

    try:
        plan: InvestigationPlan = structured_invoke(
            fast_llm or llm,
            InvestigationPlan,
            [SystemMessage(content=PLANNER_PROMPT), HumanMessage(content=context_msg)],
        )
        serialized_tasks = [t.model_dump() for t in plan.tasks]
    except Exception as e:
        serialized_tasks = [
            {
                "specialist": "repository_intelligence",
                "sub_query": (
                    f"Inspect files, README, manifest, and entrypoint in '{curr_repo}' relevant to: "
                    f"{state['user_query'][:200]}"
                ),
            }
        ]
        chosen = [t["specialist"] for t in serialized_tasks]
        return {
            "attempt_count": attempt,
            "plan_reasoning": "Fallback routed to repository_intelligence.",
            "tasks": serialized_tasks,
            "trace_logs": [f"[Orchestrator Plan | Attempt {attempt}] Fallback routed to {chosen}."],
        }

    if not serialized_tasks:
        serialized_tasks = [
            {
                "specialist": "repository_intelligence",
                "sub_query": f"Inspect repository context relevant to: {state['user_query'][:200]}",
            }
        ]

    chosen = [t["specialist"] for t in serialized_tasks]
    return {
        "attempt_count": attempt,
        "plan_reasoning": plan.reasoning,
        "tasks": serialized_tasks,
        "trace_logs": [f"[Orchestrator Plan | Attempt {attempt}] Routed to {chosen}: {plan.reasoning}"],
    }


def fan_out_specialists(state: RepoMindState) -> list[Send]:
    return [
        Send(
            task["specialist"],
            {
                "sub_query": task["sub_query"],
                "user_query": state.get("user_query", ""),
            },
        )
        for task in state["tasks"]
    ]


def orchestrator_review_node(state: RepoMindState):
    evidence = state.get("evidence", [])
    if not evidence:
        return {
            "is_sufficient": False,
            "missing_info_feedback": "No evidence was returned. Broaden file/symbol search terms.",
            "trace_logs": ["[Orchestrator Review] Sufficient=False (0 evidence items collected)."],
        }

    budget = get_current_budget()
    q_lower = state["user_query"].lower()
    missing_tools = []
    if "exa" in q_lower and os.getenv("EXA_API_KEY") and not budget.was_used("exa"):
        missing_tools.append("Exa search was explicitly requested but exa_search_technical was never called.")
    if "firecrawl" in q_lower and os.getenv("FIRECRAWL_API_KEY") and not budget.was_used("firecrawl"):
        missing_tools.append("Firecrawl ingestion was explicitly requested but firecrawl_ingest_docs was never called.")
    if "context7" in q_lower and os.getenv("CONTEXT7_API_KEY") and not budget.was_used("context7"):
        missing_tools.append("Context7 docs were explicitly requested but context7_search_docs was never called.")

    if missing_tools and state.get("attempt_count", 1) < 2:
        msg = " ".join(missing_tools)
        return {
            "is_sufficient": False,
            "missing_info_feedback": msg,
            "trace_logs": [f"[Orchestrator Review | Attempt {state['attempt_count']}] Sufficient=False | Note: {msg}"],
        }

    # Fast-path approval on Attempt 1 for simple queries that already gathered evidence
    if state.get("attempt_count", 1) == 1 and not _is_deep_query(state.get("user_query", "")):
        return {
            "is_sufficient": True,
            "missing_info_feedback": "",
            "trace_logs": [
                f"[Orchestrator Review | Attempt 1] Sufficient=True (fast-path; {len(evidence)} items)."
            ],
        }

    evidence_text = "\n".join(
        f"- [{e['specialist']}] ({e['file_path']}:L{e['start_line']}-L{e['end_line']}) {e['claim'][:300]}"
        for e in evidence
    )
    eval_prompt = (
        f"Developer Query: {state['user_query']}\n\n"
        f"Collected Evidence ({len(evidence)} cited items):\n{evidence_text}\n\n"
        "Evaluation Rules:\n"
        "1. If the evidence includes local source code file/line citations and addresses all requested aspects, mark `is_sufficient: true`.\n"
        "2. Do NOT demand optional API results if a tool reported [SKIPPED], and do NOT demand non-existent tests when a gap is confirmed."
    )
    evaluation: EvidenceEvaluation = structured_invoke(
        fast_llm or llm, EvidenceEvaluation, [HumanMessage(content=eval_prompt)]
    )
    return {
        "is_sufficient": evaluation.is_sufficient,
        "missing_info_feedback": evaluation.missing_info_feedback,
        "trace_logs": [
            f"[Orchestrator Review | Attempt {state['attempt_count']}] "
            f"Sufficient={evaluation.is_sufficient} | Note: {evaluation.missing_info_feedback or 'Complete'}"
        ],
    }


def route_after_review(state: RepoMindState) -> Literal["orchestrator_plan", "verify_citations"]:
    if state.get("is_sufficient") or state.get("attempt_count", 1) >= 2:
        return "verify_citations"
    return "orchestrator_plan"


def verify_citations_node(state: RepoMindState):
    """Verify local REPO_ROOT file/line citations AND external GitHub/CI/Context7/Exa/Firecrawl citations."""
    verified = []
    dropped = 0
    seen = set()

    valid_ext_ids = set()
    cached_urls = set()
    if Path(DB_PATH).exists():
        try:
            with contextlib.closing(connect_sqlite()) as conn:
                valid_ext_ids = {
                    row[0].upper()
                    for row in conn.execute("SELECT external_id FROM external_issues").fetchall()
                }
                cached_urls = {
                    row[0].upper()
                    for row in conn.execute("SELECT url FROM external_docs_cache").fetchall()
                }
        except sqlite3.OperationalError:
            pass

    ext_prefixes = (
        "GITHUB_ISSUE:",
        "GH-",
        "CI_LOG:",
        "GHA-",
        "CONTEXT7:",
        "EXA:",
        "FIRECRAWL:",
        "DOC_CACHE:",
        "EXTERNAL:",
        "HTTP://",
        "HTTPS://",
    )

    for item in state.get("evidence", []):
        fp = (item.get("file_path") or "").strip()
        claim = (item.get("claim") or "").strip()
        key = (fp, item.get("start_line"), item.get("end_line"), claim)
        if not fp or key in seen:
            continue
        seen.add(key)

        fp_upper = fp.upper()
        matched_ext = next((ext_id for ext_id in valid_ext_ids if ext_id in fp_upper), None)
        matched_cache = any(u in fp_upper for u in cached_urls if len(u) > 6)
        if matched_ext or matched_cache or fp_upper.startswith(ext_prefixes):
            item_copy = dict(item)
            if fp_upper.startswith(
                (
                    "HTTP://",
                    "HTTPS://",
                    "EXTERNAL:",
                    "EXA:",
                    "FIRECRAWL:",
                    "CONTEXT7:",
                    "DOC_CACHE:",
                    "GITHUB_ISSUE:",
                    "CI_LOG:",
                )
            ):
                item_copy["file_path"] = fp
            else:
                item_copy["file_path"] = f"EXTERNAL:{matched_ext or fp}"
            item_copy["start_line"] = 1
            item_copy["end_line"] = 1
            verified.append(item_copy)
            continue

        target, err = safe_repo_path(fp)
        if err or target is None or not target.exists() or not target.is_file():
            dropped += 1
            continue

        total_lines = len(target.read_text(encoding="utf-8", errors="ignore").splitlines())
        start = max(1, int(item.get("start_line", 1)))
        end = min(total_lines, int(item.get("end_line", start)))
        if 1 <= start <= end <= total_lines:
            item_copy = dict(item)
            item_copy["start_line"] = start
            item_copy["end_line"] = end
            verified.append(item_copy)
        else:
            dropped += 1

    return {
        "verified_evidence": verified,
        "trace_logs": [
            f"[Citation Verifier] Kept {len(verified)} valid citations (dropped {dropped} invalid)."
        ],
    }


def route_after_verify_citations(
    state: RepoMindState,
) -> Literal["orchestrator_synthesize", "no_evidence_exit"]:
    return "orchestrator_synthesize" if state.get("verified_evidence") else "no_evidence_exit"


def no_evidence_exit_node(state: RepoMindState):
    curr_repo = get_repo_full_name()
    return {
        "final_report": (
            f"No verified code, test, issue, or documentation evidence was found in `{curr_repo}` "
            f"for your query: *{state['user_query']}*\n\n"
            "**Suggestions:**\n"
            "- Try referencing a specific module, file, symbol, exception, test, or issue in the repository.\n"
            "- Make sure the repository is cloned and accessible."
        ),
        "trace_logs": ["[No Evidence Exit] Skipped synthesis after 0 verified citations."],
    }


def orchestrator_synthesize_node(state: RepoMindState):
    verified = state.get("verified_evidence", [])[:18]
    ext_prefixes = (
        "EXTERNAL:",
        "EXA:",
        "FIRECRAWL:",
        "CONTEXT7:",
        "DOC_CACHE:",
        "GITHUB_ISSUE:",
        "CI_LOG:",
        "http://",
        "https://",
    )

    evidence_lines = []
    for e in verified:
        fp = e["file_path"]
        cite = f"[{fp}]" if fp.startswith(ext_prefixes) else f"[{fp}:L{e['start_line']}-L{e['end_line']}]"
        evidence_lines.append(f"- {cite} ({e['specialist']}) {e['claim']}")

    synth_model = (llm or fast_llm) if _is_deep_query(state.get("user_query", "")) else (fast_llm or llm)
    sys_msg = SystemMessage(
        content=(
            "/no_think\n"
            "You are the Lead Engineering Responder for RepoMind.\n"
            "Your PRIMARY job is to DIRECTLY ANSWER the developer's question using the verified evidence.\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Read the Developer Query carefully. Answer EXACTLY what they asked.\n"
            "2. If they ask for a list (e.g. 'top 5 questions', 'list all endpoints'), give them that list directly.\n"
            "3. If they ask about data or content in files (e.g. questions, configs, entries), extract and present that DATA—do NOT describe the code structure that processes it.\n"
            "4. If they ask about architecture or how code works, then explain the architecture.\n"
            "5. NEVER produce a meta-description of the codebase when the user wants actual content/data from files.\n"
            "6. Keep your answer focused and concise. Use bullet points and headers only when they help clarity.\n"
            "7. Do NOT start with 'Engineering Report' headers unless the user explicitly asked for a report."
        )
    )
    synth_prompt = f"""Rules:
1. Every local code claim MUST include an inline citation formatted as [file_path:Lstart-Lend].
2. External findings MUST cite their exact tag or URL from the verified evidence.
3. NEVER invent causal links between unrelated items.
4. NEVER attribute class fields/methods to local classes unless confirmed in local source code evidence.
5. ONLY include sections that have verified evidence—completely OMIT empty sections.
6. Provide thorough, complete answers without cutting off or ending mid-sentence.
7. ANSWER the question directly first, then provide supporting evidence. Do NOT just describe how the code is structured.

Developer Query:
{state['user_query']}

Verified Evidence:
{chr(10).join(evidence_lines)}"""

    if synth_model and hasattr(synth_model, "stream"):
        chunks = [
            chunk.content
            for chunk in synth_model.stream([sys_msg, HumanMessage(content=synth_prompt)])
            if chunk.content
        ]
        report = re.sub(r"<think>.*?</think>", "", "".join(chunks), flags=re.DOTALL).strip()
    else:
        report = f"# Synthesized Report for {state['user_query']}\n\nEvidence verified:\n" + "\n".join(evidence_lines)

    return {
        "final_report": report,
        "trace_logs": [f"[Orchestrator Synthesize] Done via {getattr(synth_model, 'model', 'model')}."],
    }


def output_guardrail_node(state: RepoMindState):
    """Post-synthesis guardrail: redact leaked secrets and strip :L1-L1 placeholder citations."""
    report = state.get("final_report") or ""
    if not report:
        return {"trace_logs": ["[Output Guardrail] Empty report; skipped."]}

    cleaned, secret_hits = redact_secrets(report)
    cleaned, placeholder_hits = re.subn(r"\s*\[[^\]]+:L1-L1\]", "", cleaned)

    logs = []
    if secret_hits:
        logs.append(f"[Output Guardrail] Redacted {secret_hits} secret(s) from final report.")
    if placeholder_hits:
        logs.append(f"[Output Guardrail] Stripped {placeholder_hits} :L1-L1 placeholder citation(s).")
    if not logs:
        logs.append("[Output Guardrail] Passed secret and citation-hygiene checks.")

    return {
        "final_report": cleaned.strip(),
        "trace_logs": logs,
    }
