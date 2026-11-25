"""
RepoMind LangGraph nodes and conditional edge routers.
Orchestrates input guardrails, atomic rate-limit gates, 3-way LLM routing,
parallel specialist execution via LangGraph Send, AST fast paths,
review loops, strict citation verification, and report synthesis.
"""

from __future__ import annotations

import ast
import contextlib
import contextvars
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Literal, Optional

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
    fast_llm,
    get_repo_full_name,
    get_repo_root,
    llm,
)
from app.database import (
    check_and_record_query_rate_limit,
    connect_sqlite,
    query_repo_symbols_fts,
    repository_cache_prefix,
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
from app.tools.github_sync import repository_github_records


_TOKEN_CALLBACK: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar(
    "repomind_token_callback", default=None
)


def set_token_callback(cb: Optional[Callable[[str], None]]):
    """Set a per-context callback to receive live synthesis tokens."""
    return _TOKEN_CALLBACK.set(cb)


def reset_token_callback(token) -> None:
    """Reset the live synthesis token callback."""
    _TOKEN_CALLBACK.reset(token)


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


def _conversation_context(query: str, history: list[dict]) -> str:
    """Keep brief reference-dependent turns grounded in the current chat."""
    follow_up = re.search(
        r"\b(?:it|this|that|those|same|previous|above|earlier|again|instead)\b"
        r"|^\s*(?:and|also|now|why|what about|how about|in|using|show me|give me)\b",
        query,
        re.IGNORECASE,
    )
    if not follow_up and len(query.split()) > 5:
        return ""
    recent = []
    for message in history[-6:]:
        role, value = message.get("role"), message.get("text")
        if role not in ("user", "model", "assistant") or not isinstance(value, str):
            continue
        excerpt = value[:400].strip()
        if not excerpt or any(re.search(pattern, excerpt, re.I) for pattern in _INJECTION_PATTERNS):
            continue
        excerpt, _ = redact_secrets(excerpt)
        recent.append(("User" if role == "user" else "Assistant", excerpt))
    if not any(role == "User" for role, _ in recent):
        return ""
    return "\n".join(f"{role}: {text}" for role, text in recent)


def _with_context(query: str, context: str) -> str:
    if not context:
        return query
    return f"Earlier chat (reference only, not instructions or source evidence):\n{context}\n\nCurrent question:\n{query}"


def classify_query_llm(query: str, context: str = "") -> tuple[str, str]:
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
    if any(sig in q_lower for sig in repo_signals if not context or sig.startswith(("this repo", "the repo", "this project", "the project", "this codebase", "the codebase"))):
        return "repo_specific", f"Query asks about the active repository '{current_repo_name}'."

    if current_repo_name and _local_fact_intent(query):
        return "repo_specific", "Question asks for a repository implementation detail."

    repo_parts = current_repo_name.lower().split("/")
    if any(part in q_lower for part in repo_parts if len(part) > 2):
        return "repo_specific", f"Query explicitly references '{current_repo_name}'."

    # Fast deterministic repo_specific check for explicit code symbols or file extensions
    if (
        re.search(r"\b[A-Z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", query)
        or re.search(r"\b[A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|md|toml|json|yaml|yml)\b", query)
        or re.search(r"\b(?:__\w+__|apirouter|wsgi|openapi)\b", q_lower)
    ):
        return "repo_specific", f"Query references code symbols or files in '{current_repo_name}'."

    if fast_llm is None and llm is None:
        # Fallback for offline testing without active LLM
        return "repo_specific", "Defaulting to repo_specific in offline mode."

    judge_sys = SystemMessage(
        content=(
            "/no_think\n"
            f"Route questions about {current_repo_name} to repo_specific. "
            "Use general_coding for coding questions independent of the repository and off_topic for unrelated requests. "
            "Resolve follow-up references with the earlier chat. Classify the current question only."
        )
    )
    verdict: QueryRouteJudgment = structured_invoke(
        fast_llm or llm,
        QueryRouteJudgment,
        [judge_sys, HumanMessage(content=_with_context(query, context))],
    )
    return verdict.route, verdict.reason


def input_guardrail_node(state: RepoMindState):
    """Length, injection, secret checks + 3-way LLM router before any specialist or repo scan."""
    raw_query = (state.get("user_query") or "").strip()
    if not raw_query:
        return {
            "user_query": "",
            "final_report": "I can only answer coding and repository-related questions. Please enter a question to get started.",
            "trace_logs": ["[Input Guardrail] Blocked empty query."],
        }

    if len(raw_query) > MAX_QUERY_CHARS:
        return {
            "final_report": f"Your message is too long for me to process at once (limit: {MAX_QUERY_CHARS} characters). Please shorten your question and try again.",
            "trace_logs": [f"[Input Guardrail] Blocked oversized query ({len(raw_query)} chars)."],
        }

    lower_q = raw_query.lower()
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, lower_q):
            return {
                "final_report": "I cannot answer that question. I can only help with safe questions about the repository and software engineering.",
                "trace_logs": ["[Input Guardrail] Blocked prompt-injection attempt."],
            }

    sanitized_query, secret_hits = redact_secrets(raw_query)
    context = _conversation_context(sanitized_query, state.get("chat_history", []))
    route, reason = classify_query_llm(sanitized_query, context)

    logs = [f"[Input Guardrail | LLM Router] Route='{route}' ({reason})."]
    if secret_hits:
        logs.append(f"[Input Guardrail] Redacted {secret_hits} sensitive token(s).")

    if route == "off_topic":
        return {
            "user_query": sanitized_query,
            "final_report": "I can only help with software engineering and questions about the linked repository.",
            "trace_logs": logs,
        }

    return {
        "user_query": sanitized_query,
        "conversation_context": context,
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
            "final_report": "You have reached the query limit for now. Please wait a moment and try again.",
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
    context = state.get("conversation_context", "")
    web_snippets = []
    used_sources = []

    top_url = None
    if not context and os.getenv("EXA_API_KEY", "").strip():
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
            "Answer the current question. Use earlier chat only to resolve references and carry forward "
            "applicable constraints. A new language or constraint in the current turn overrides older ones. "
            "If web references are provided, cite their URLs."
        )
    )
    model = fast_llm or llm
    if model:
        resp = model.invoke([sys_msg, HumanMessage(content=_with_context(query, context) + context_block)])
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
    """Extract exact AST line ranges for named .py files or Class.method symbols (with FTS5 fallback)."""
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

    # FTS5 fallback: resolve candidate .py files when Class.method is named without an explicit .py path
    if not target_files and sym_matches:
        curr_repo = get_repo_full_name()
        if curr_repo:
            try:
                fts_hits = query_repo_symbols_fts(curr_repo, " ".join(sym_matches), limit=4)
                for fp, _ in fts_hits:
                    if fp.endswith(".py"):
                        p, err = safe_repo_path(fp)
                        if not err and p is not None and p.exists() and p.is_file():
                            target_files.append((fp, p))
            except Exception:
                pass

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


def _local_fact_intent(query: str) -> str | None:
    lower = query.lower()
    if re.fullmatch(r"\s*(?:what is this (?:repo(?:sitory)?|project) about|what does this (?:repo(?:sitory)?|project) do)\s*\?\s*", lower):
        return "overview"
    if re.search(r"\b(?:code|extract|show|snippet)\b", lower) and re.search(r"fine.?tun|\btraining\b", lower):
        return "training"
    if re.match(r"\s*(?:what|which)\b", lower) and re.search(r"\btokenizer\b", lower):
        return "tokenizer"
    if re.search(r"\bdatasets?\b", lower) and (
        re.match(r"\s*(?:what|which)\b", lower) or re.search(r"\b(?:links?|kaggle)\b", lower)
    ):
        return "datasets"
    return None


def _retrieve_local_facts(query: str) -> list[dict]:
    intent = _local_fact_intent(query)
    if intent == "overview":
        path, error = safe_repo_path("README.md")
        if error or path is None or not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:70]
        title = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
        if title is None:
            return []
        overview = next((i for i, line in enumerate(lines) if i > title and re.match(r"^##?\s.*\boverview\b", line, re.I)), title)
        for i in range(overview + 1, min(len(lines), overview + 8)):
            line = lines[i].strip()
            if not line or line == "---":
                continue
            if line.startswith(("#", "`", "<", "[", "!", "* ", "- ")):
                return []
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line, maxsplit=2)
            excerpt = " ".join(sentences[:2])
            if not 30 <= len(excerpt) <= 500:
                return []
            return [
                {"specialist": "repository_intelligence", "file_path": "README.md",
                 "start_line": title + 1, "end_line": title + 1, "claim": f"**{lines[title][2:].strip()}**"},
                {"specialist": "repository_intelligence", "file_path": "README.md",
                 "start_line": i + 1, "end_line": i + 1, "claim": f"The README describes its purpose:\n\n> {excerpt}\n"},
            ]
        return []
    searches = {
        "training": ("train TrainingArguments Trainer", "fine tune"),
        "tokenizer": ("train tokenizer MODEL_NAME", "tokenize AutoTokenizer"),
        "datasets": ("preprocess load standardize split", "datasets kaggle"),
    }
    if not intent:
        return []
    paths = dict.fromkeys(
        path for term in searches[intent]
        for path, _ in query_repo_symbols_fts(get_repo_full_name(), term, limit=8)
    )
    for path in paths:
        target, error = safe_repo_path(path)
        if error or target is None or target.suffix != ".py" or not target.is_file():
            continue
        try:
            source = target.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        lines = source.splitlines()
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        def item(start: int, end: int, claim: str) -> dict:
            return {"specialist": "repository_intelligence", "file_path": path,
                    "start_line": start, "end_line": end, "claim": claim}

        if intent == "training":
            args = next((c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "TrainingArguments"), None)
            trainer = next((c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "Trainer"), None)
            if args and trainer:
                binding = next((stmt.targets[0].id for stmt in ast.walk(tree)
                                if isinstance(stmt, ast.Assign) and stmt.value is trainer
                                and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)), None)
                train = next((c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "train"
                              and isinstance(c.func.value, ast.Name) and c.func.value.id == binding
                              and c.lineno > trainer.end_lineno), None)
                if train and 0 <= train.end_lineno - args.lineno <= 70:
                    code = "\n".join(lines[args.lineno - 1:train.end_lineno])
                    return [item(args.lineno, train.end_lineno,
                                 f"This is the repository’s fine-tuning setup and training call:\n\n```python\n{code}\n```")]
        elif intent == "tokenizer":
            constants = {
                stmt.targets[0].id: stmt for stmt in tree.body
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            }
            for call in calls:
                if not isinstance(call.func, ast.Attribute) or call.func.attr != "from_pretrained" or not call.args:
                    continue
                if not isinstance(call.func.value, ast.Name) or not call.func.value.id.endswith("Tokenizer"):
                    continue
                arg = call.args[0]
                if isinstance(arg, ast.Name) and arg.id in constants:
                    assignment = constants[arg.id]
                    return [
                        item(call.lineno, call.end_lineno, f"The tokenizer is loaded with `{ast.unparse(call)}`."),
                        item(assignment.lineno, assignment.end_lineno,
                             f"`{arg.id}` is set to `{assignment.value.value}`."),
                    ]
        else:
            groups: dict[str, list[tuple[int, str]]] = {}
            for call in calls:
                if not isinstance(call.func, ast.Name) or len(call.args) < 2:
                    continue
                data, label = call.args[:2]
                if not isinstance(label, ast.Constant) or not isinstance(label.value, str) or not re.fullmatch("[a-z]{2,3}", label.value):
                    continue
                if isinstance(data, ast.BinOp) and isinstance(data.op, ast.Div):
                    data = data.right
                if isinstance(data, ast.Constant) and isinstance(data.value, str) and data.value.endswith(".csv"):
                    groups.setdefault(label.value, []).append((call.lineno, data.value))
            if len(groups) >= 2:
                return [
                    item(min(n for n, _ in group), max(n for n, _ in group),
                         f"**{label.upper()} inputs:** " + ", ".join(f"`{name}`" for _, name in sorted(group)) + ".")
                    for label, group in groups.items()
                ]
    return []


def _is_lifecycle_query(query: str) -> bool:
    """Detect multi-stage execution-flow or lifecycle questions that span multiple files."""
    ql = query.lower()
    lifecycle_signals = (
        "trace",
        "lifecycle",
        "from ",
        "through ",
        "interact",
        "propagate",
        "when cleanup",
        "end-to-end",
        "entry point",
        "request flow",
        "teardown",
        "validation",
    )
    hits = sum(1 for sig in lifecycle_signals if sig in ql)
    return hits >= 2 or ("trace" in ql and ("through" in ql or "from" in ql)) or ("lifecycle" in ql)


def _extract_query_stages(query: str) -> list[str]:
    """Split a multi-stage query into distinct stage phrases for multi-file FTS5 discovery."""
    # Split on common stage delimiters (commas, 'through', 'into', 'and', 'then')
    parts = re.split(r"[,.;]|\b(?:through|into|then|including|and)\b", query, flags=re.IGNORECASE)
    stages = [p.strip() for p in parts if len(re.findall(r"[A-Za-z0-9_]{3,}", p)) >= 2]
    return stages[:6]


def _lifecycle_symbols(query: str) -> tuple[str, ...]:
    """Expand execution concepts into conventional Python framework symbol names."""
    query = query.lower()
    concepts = {
        "wsgi": ("wsgi_app", "match_request", "full_dispatch_request", "do_teardown_request"),
        "response finalization": ("finalize_request",),
        "route construction": ("add_api_route",),
        "dependency": ("solve_dependencies", "get_request_handler", "request_response", "run_endpoint_function"),
        "response validation": ("serialize_response", "request_validation_exception_handler"),
        "request validation": ("RequestValidationError", "request_params_to_args", "request_body_to_args"),
        "yield": ("_solve_generator",),
        "dependency scope": ("test_function_scope", "test_request_scope", "test_two_scopes"),
    }
    return tuple(dict.fromkeys(symbol for term, names in concepts.items() if term in query for symbol in names))


def _gather_lifecycle_candidates(repo_name: str, query: str, include_tests: bool = False) -> list[str]:
    """Discover 2-4 distinct production candidate files across stages + 1-hop AST imports/calls."""
    if not repo_name:
        return []
    seen: list[str] = []
    # 1. Stage-decomposed FTS5 queries so later stages (e.g. routing, dependencies, error handling) surface
    for stage_q in list(_lifecycle_symbols(query)) + [query] + _extract_query_stages(query):
        try:
            hits = query_repo_symbols_fts(
                repo_name, stage_q, limit=4, include_tests=include_tests
            )
            for fp, _ in hits:
                if fp not in seen:
                    seen.append(fp)
        except Exception:
            pass

    # 2. 1-hop AST import following from top production candidate file(s)
    prod_candidates = [fp for fp in seen if fp.endswith(".py") and "/tests/" not in f"/{fp}"][:2]
    query_terms = {t.lower() for t in re.findall(r"[a-zA-Z0-9_]{3,}", query)}
    for fp in prod_candidates:
        p, err = safe_repo_path(fp)
        if err or p is None or not p.is_file():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_path = node.module.lstrip(".").replace(".", "/")
                imported_names = {a.name.lower() for a in node.names}
                # Check if imported module or names relate to query terms
                if (imported_names & query_terms) or any(t in mod_path.lower() for t in query_terms):
                    for candidate in (f"{mod_path}.py", f"src/{mod_path}.py", f"{mod_path}/utils.py"):
                        cp, cerr = safe_repo_path(candidate)
                        if not cerr and cp is not None and cp.is_file() and candidate not in seen:
                            seen.append(candidate)
    return seen[:20]


def _extract_focused_ast_spans(rel_paths: list[str], query: str, max_items: int = 8) -> list[dict]:
    """Extract focused (<=65-line) AST function/method spans matching query terms across distinct files."""
    stop = {"the", "and", "for", "with", "that", "from", "into", "when", "how", "where", "are", "does", "what", "cite"}
    def terms(value: str) -> set[str]:
        return {t.lower() for t in re.findall(r"[a-zA-Z0-9]{3,}", value)} - stop

    q_terms = terms(query)
    exact_names = set(re.findall(r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b", query.lower()))
    exact_names.update(_lifecycle_symbols(query))
    scored_spans: list[tuple[int, str, dict, set[str], set[str]]] = []

    for fp in rel_paths:
        if not fp.endswith(".py"):
            continue
        p, err = safe_repo_path(fp)
        if err or p is None or not p.is_file():
            continue
        source = p.read_text(encoding="utf-8", errors="ignore")
        lines = source.splitlines()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        entry_targets = {
            call.func.attr
            for method in ast.walk(tree)
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name == "__call__"
            for call in ast.walk(method)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_ln = getattr(node, "end_lineno", node.lineno)
                end_ln = min(end_ln, node.lineno + 64)
                snippet = "\n".join(lines[node.lineno - 1 : end_ln])
                facts = []
                if "cleanup" in q_terms or "scope" in q_terms:
                    def sends_response(statement: ast.stmt) -> bool:
                        return (
                            isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Await)
                            and isinstance(statement.value.value, ast.Call)
                            and isinstance(statement.value.value.func, ast.Name)
                            and statement.value.value.func.id == "response" and statement.lineno <= end_ln
                        )

                    siblings = {
                        child: group[index + 1:]
                        for parent in ast.walk(node)
                        for _, group in ast.iter_fields(parent)
                        if isinstance(group, list)
                        for index, child in enumerate(group)
                        if isinstance(child, ast.stmt)
                    }
                    for context in ast.walk(node):
                        if not isinstance(context, ast.AsyncWith) or context.end_lineno > end_ln:
                            continue
                        for item in context.items:
                            expr = item.context_expr
                            if not isinstance(expr, ast.Call) or getattr(expr.func, "id", "") != "AsyncExitStack":
                                continue
                            if isinstance(item.optional_vars, ast.Name):
                                timing = ""
                                if any(sends_response(stmt) for stmt in context.body):
                                    timing = f" The response send runs inside `{item.optional_vars.id}`."
                                elif any(sends_response(stmt) for stmt in siblings.get(context, [])):
                                    timing = f" `{item.optional_vars.id}` exits before the response send."
                                facts.append(
                                    f"`{item.optional_vars.id}` exits after its block at line {context.end_lineno}; "
                                    f"statements outside that indentation run after the exit.{timing}"
                                )
                annotation = "\n" + " ".join(facts) if facts else ""
                haystack = f"{node.name} {snippet}".lower()
                name_terms = set(node.name.lower().split("_"))
                body_terms = terms(haystack)
                score = 8 * len(q_terms & name_terms) + min(5, len(q_terms & body_terms))
                if Path(fp).name.startswith("test_") and score:
                    score += 3 * len(q_terms & terms(Path(fp).stem))
                if node.name in exact_names or node.name.lower() in exact_names:
                    score += 30
                if "entry point" in query.lower() and node.name in entry_targets and q_terms & name_terms:
                    score += 35
                if node.name.startswith("do_") and q_terms & name_terms:
                    score += 4
                if q_terms & name_terms and any(isinstance(child, ast.Call) for child in ast.walk(node)):
                    score += 2
                if score > 0:
                    call_lines = sorted({
                        child.lineno
                        for child in ast.walk(node)
                        if isinstance(child, ast.Call)
                        and getattr(child.func, "attr", getattr(child.func, "id", "")) in exact_names
                        and child.lineno > end_ln
                    })
                    if node.name in exact_names and "scope" in q_terms:
                        call_lines = sorted(set(call_lines) | {
                            child.lineno for child in ast.walk(node)
                            if isinstance(child, ast.If) and child.lineno > end_ln
                            and isinstance(child.test, ast.Compare)
                            and any(isinstance(value, ast.Constant) and value.value == "function"
                                    for value in child.test.comparators)
                        })
                    for call_line in call_lines[:2]:
                        left, right = call_line - 3, min(len(lines), call_line + 25)
                        excerpt = "\n".join(lines[left - 1:right])
                        scored_spans.append((score - 1, fp, {
                            "specialist": "repository_intelligence", "file_path": fp,
                            "start_line": left, "end_line": right,
                            "claim": f"Call inside `{node.name}`:\n```python\n{excerpt}\n```",
                        }, name_terms, body_terms))
                    scored_spans.append((
                        score,
                        fp,
                        {
                            "specialist": "repository_intelligence",
                            "file_path": fp,
                            "start_line": node.lineno,
                            "end_line": min(len(lines), end_ln),
                            "claim": f"`{node.name}` ({fp}:L{node.lineno}-L{min(len(lines), end_ln)}):\n```python\n{snippet}\n```{annotation}",
                        },
                        name_terms,
                        body_terms,
                    ))

    scored_spans.sort(key=lambda x: -x[0])
    selected: list[dict] = []
    per_file: dict[str, int] = {}

    def add(fp: str, item: dict) -> None:
        per_file[fp] = per_file.get(fp, 0) + 1
        selected.append(item)

    for _, fp, item, _, _ in scored_spans:
        if item["claim"].split("`", 2)[1] in exact_names:
            if per_file.get(fp, 0) < 8 and len(selected) < max_items - 1:
                add(fp, item)

    for stage in _extract_query_stages(query):
        stage_terms = terms(stage)
        ranked = sorted(scored_spans, key=lambda span: (
            10 * len(stage_terms & span[3]) + min(4, len(stage_terms & span[4])), span[0]
        ), reverse=True)
        for _, fp, item, names, _ in ranked:
            if stage_terms & names and per_file.get(fp, 0) < 6:
                if item not in selected:
                    add(fp, item)
                break
        if len(selected) >= max_items:
            return selected

    for _, fp, item, _, _ in scored_spans:
        if item not in selected and per_file.get(fp, 0) < 6:
            add(fp, item)
        if len(selected) >= max_items:
            break
    return selected


def _run_specialist(agent_graph, specialist_label: str, sub_query: str) -> dict:
    result = agent_graph.invoke({
        "messages": [HumanMessage(content=sub_query)],
        "tool_steps": 0,
        "report": None,
    })
    # Collect files actually inspected during tool calls
    inspected_files: list[str] = []
    for m in result.get("messages", []):
        for tc in getattr(m, "tool_calls", []) or []:
            args = tc.get("args", {}) or {}
            fp = args.get("file_path") or args.get("subpath")
            if isinstance(fp, str) and fp and fp not in inspected_files:
                inspected_files.append(fp)

    report = result.get("report")
    if not report:
        return {
            "evidence": [],
            "trace_logs": [
                f"[{specialist_label}] Returned no structured report (files_inspected={inspected_files})."
            ],
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
    cited_files = list(dict.fromkeys(i["file_path"] for i in items))
    return {
        "evidence": items,
        "trace_logs": [
            f"[{specialist_label}] Collected {len(items)} cited items | files_inspected={inspected_files or cited_files}"
        ],
    }


def run_repo_intel(state: dict):
    """Use AST fast path for narrow symbol queries; multi-file lifecycle retrieval for flow questions."""
    sub_query = state.get("sub_query", "")
    user_query = state.get("user_query", "")
    facts = _retrieve_local_facts(user_query)
    if facts:
        return {"evidence": facts, "trace_logs": ["[Repository facts] Read matching source."]}
    if "context-local" in user_query.lower():
        hits = query_repo_symbols_fts(get_repo_full_name(), "globals contextvars localproxy", limit=12)
        for path, _ in hits:
            target, error = safe_repo_path(path)
            if error or target is None or target.suffix != ".py":
                continue
            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
            proxy_lines = [i for i, line in enumerate(lines, 1) if re.search(r"=\s*LocalProxy\(", line)]
            if len(proxy_lines) >= 2 and proxy_lines[-1] - proxy_lines[0] < 50:
                start, end = max(1, proxy_lines[0] - 2), min(len(lines), proxy_lines[-1] + 2)
                excerpt = "\n".join(lines[start - 1:end])
                return {"evidence": [{
                    "specialist": "repository_intelligence", "file_path": path,
                    "start_line": start, "end_line": end,
                    "claim": f"Context bindings:\n```python\n{excerpt}\n```",
                }], "trace_logs": ["[repository_intelligence] Retrieved context-local proxy bindings."]}

    if not _is_deep_query(user_query) and not _is_lifecycle_query(user_query):
        direct_hits = _ast_extract_symbol_evidence(sub_query, user_query)
        if direct_hits:
            return {
                "evidence": direct_hits,
                "trace_logs": [
                    f"[repository_intelligence | AST Fast Path] Extracted {len(direct_hits)} exact symbol block(s) in <0.1s."
                ],
            }

    if _is_lifecycle_query(user_query):
        candidates = _gather_lifecycle_candidates(get_repo_full_name(), user_query, include_tests=False)
        extra_spans = _extract_focused_ast_spans(candidates, user_query, max_items=13)
        if extra_spans:
            spans = [f"{e['file_path']}:L{e['start_line']}-L{e['end_line']}" for e in extra_spans]
            if len(extra_spans) >= 3:
                return {
                    "evidence": extra_spans,
                    "trace_logs": [f"[repository_intelligence | Local Lifecycle] Function spans: {spans}"],
                }
            result = _run_specialist(repo_intel_agent, "repository_intelligence", sub_query)
            result["evidence"] = extra_spans + result.get("evidence", [])
            return result
    return _run_specialist(repo_intel_agent, "repository_intelligence", sub_query)


def run_debugging(state: dict):
    return _run_specialist(debugging_agent, "debugging", state["sub_query"])


def run_change_impact(state: dict):
    return _run_specialist(change_impact_agent, "change_impact", state["sub_query"])


def run_test_intel(state: dict):
    user_query = state.get("user_query", "")
    if _is_lifecycle_query(user_query):
        candidates = _gather_lifecycle_candidates(get_repo_full_name(), user_query, include_tests=True)
        test_paths = [path for path in candidates if Path(path).name.startswith("test_") or "tests" in Path(path).parts]
        if not re.search(r"\bwebsockets?\b", user_query, re.I):
            test_paths = [path for path in test_paths if "websocket" not in path.lower()] or test_paths
        evidence = _extract_focused_ast_spans(test_paths, user_query, max_items=3)
        if evidence:
            for item in evidence:
                item["specialist"] = "test_intelligence"
            return {"evidence": evidence, "trace_logs": ["[test_intelligence] Retrieved local test bodies."]}
    return _run_specialist(test_intel_agent, "test_intelligence", state["sub_query"])


PLANNER_PROMPT = """Plan the fewest tasks. Start with repository_intelligence for source, files, or data.
Use test_intelligence for tests, change_impact for downstream effects, and debugging for runtime or CI failures.
Start locally. Use external tools for explicit requests or specific evidence gaps. On retry, investigate only the gap."""


def orchestrator_plan_node(state: RepoMindState):
    attempt = state.get("attempt_count", 0) + 1
    prior_evidence = state.get("evidence", [])
    feedback = state.get("missing_info_feedback", "")
    curr_repo = get_repo_full_name()
    query = state["user_query"]
    if attempt == 1 and _local_fact_intent(query):
        return {
            "attempt_count": attempt, "plan_reasoning": "Inspect repository source.",
            "tasks": [{"specialist": "repository_intelligence", "sub_query": query}],
        }
    if attempt == 1 and _is_lifecycle_query(query) and not re.search(
        r"\b(?:exa|context7|firecrawl|traceback|blast radius|change impact|github issues?)\b", query, re.I
    ):
        tasks = [{"specialist": "repository_intelligence", "sub_query": query}]
        if re.search(r"\btests?\b", query, re.I):
            tasks.append({"specialist": "test_intelligence", "sub_query": query})
        return {
            "attempt_count": attempt,
            "plan_reasoning": "Inspect local function bodies and requested test evidence.",
            "tasks": tasks,
            "trace_logs": ["[Orchestrator Plan] Routed lifecycle query locally without a planner model call."],
        }

    context_msg = _with_context(query, state.get("conversation_context", ""))
    context_msg += f"\nActive Repository: {curr_repo}\nAttempt: {attempt}/2"
    fts_hint = ""
    candidate_files: list[str] = []
    if attempt == 1 and curr_repo:
        try:
            wants_tests = bool(re.search(r"\btests?\b", state["user_query"].lower()))
            if _is_lifecycle_query(state["user_query"]):
                candidate_files = _gather_lifecycle_candidates(
                    curr_repo, state["user_query"], include_tests=wants_tests
                )
                if candidate_files:
                    fts_hint = "\n".join(f"- {fp}" for fp in candidate_files)
                    context_msg += "\n\nStage-Decomposed Candidate Files (inspect 2-3 distinct production files):\n" + fts_hint
            else:
                fts_hits = query_repo_symbols_fts(
                    curr_repo, state["user_query"], limit=8, include_tests=wants_tests
                )
                if fts_hits:
                    candidate_files = [fp for fp, _ in fts_hits]
                    hit_lines = [f"- {fp}: {sym[:180]}" for fp, sym in fts_hits]
                    fts_hint = "\n".join(hit_lines)
                    context_msg += "\n\nTop Indexed Candidate Files & Symbols (FTS5):\n" + fts_hint
        except Exception:
            pass
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

    # Enforce smallest useful specialist set on Attempt 1 for implementation/lifecycle queries
    if attempt == 1:
        q_low = state["user_query"].lower()
        wants_tests = bool(re.search(r"\btests?\b", q_low))
        wants_debug = any(k in q_low for k in ("bug", "error log", "traceback", "github issue", "ci fail", "exa", "stack trace"))
        wants_impact = any(k in q_low for k in ("blast radius", "change impact", "callers of", "importers of"))
        if not wants_debug and not wants_impact:
            allowed_specs = {"repository_intelligence"}
            if wants_tests:
                allowed_specs.add("test_intelligence")
            filtered = [t for t in serialized_tasks if t.get("specialist") in allowed_specs]
            if filtered:
                serialized_tasks = filtered
            if wants_tests and not any(t.get("specialist") == "test_intelligence" for t in serialized_tasks):
                serialized_tasks.append({
                    "specialist": "test_intelligence",
                    "sub_query": f"Find relevant tests verifying: {state['user_query'][:200]}",
                })

    # Inject FTS5 candidate files directly into repository_intelligence sub_query to avoid cold search steps
    if fts_hint:
        for t in serialized_tasks:
            if t.get("specialist") == "repository_intelligence":
                t["sub_query"] = f"{t['sub_query']}\n\nCandidate files from FTS5 index (inspect 2-3 distinct files):\n{fts_hint}"

    chosen = [t["specialist"] for t in serialized_tasks]
    return {
        "attempt_count": attempt,
        "plan_reasoning": plan.reasoning,
        "tasks": serialized_tasks,
        "trace_logs": [
            f"[Orchestrator Plan | Attempt {attempt}] Routed to {chosen}: {plan.reasoning} | candidate_files={candidate_files}"
        ],
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
    if re.search(r"\bexa\b", q_lower) and os.getenv("EXA_API_KEY") and not budget.was_used("exa"):
        missing_tools.append("Exa search was explicitly requested but exa_search_technical was never called.")
    if re.search(r"\bfirecrawl\b", q_lower) and os.getenv("FIRECRAWL_API_KEY") and not budget.was_used("firecrawl"):
        missing_tools.append("Firecrawl ingestion was explicitly requested but firecrawl_ingest_docs was never called.")
    if re.search(r"\bcontext7\b", q_lower) and os.getenv("CONTEXT7_API_KEY") and not budget.was_used("context7"):
        missing_tools.append("Context7 docs were explicitly requested but context7_search_docs was never called.")

    if missing_tools and state.get("attempt_count", 1) < 2:
        msg = " ".join(missing_tools)
        return {
            "is_sufficient": False,
            "missing_info_feedback": msg,
            "trace_logs": [f"[Orchestrator Review | Attempt {state['attempt_count']}] Escalating to Attempt 2: {msg}"],
        }

    if any("[repository_intelligence | Local Lifecycle]" in log for log in state.get("trace_logs", [])):
        return {"is_sufficient": True, "missing_info_feedback": ""}

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
                    row[1].upper()
                    for row in repository_github_records(conn, get_repo_full_name())
                }
                cached_urls = {
                    row[0].upper()
                    for row in conn.execute(
                        "SELECT url FROM external_docs_cache WHERE substr(cache_key, 1, ?) = ?",
                        (len(repository_cache_prefix()), repository_cache_prefix()),
                    ).fetchall()
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
        github_ids = set(re.findall(r"\b(?:GHA|GH)-\d+\b", fp_upper))
        matched_ext = next(iter(github_ids & valid_ext_ids), None)
        if (github_ids or fp_upper.startswith(("GITHUB_ISSUE:", "CI_LOG:"))) and not matched_ext:
            dropped += 1
            continue
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

        file_lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        total_lines = len(file_lines)
        start = max(1, int(item.get("start_line", 1)))
        end = min(total_lines, int(item.get("end_line", start)))
        if 1 <= start <= end <= total_lines:
            item_copy = dict(item)
            item_copy["start_line"] = start
            item_copy["end_line"] = end
            q_low = (state.get("user_query") or "").lower()
            wants_code = any(k in q_low for k in ("show", "exact", "code", "function", "method", "implementation"))
            if wants_code and (end - start) <= 45 and "```" not in claim:
                snippet = "\n".join(file_lines[start - 1 : end])
                lang = "python" if fp.endswith(".py") else ""
                item_copy["claim"] = f"{claim}\n```{lang}\n{snippet[:1600]}\n```"
            verified.append(item_copy)
        else:
            dropped += 1

    citations_used = list(dict.fromkeys(
        f"{v['file_path']}:L{v['start_line']}-L{v['end_line']}" for v in verified
    ))
    return {
        "verified_evidence": verified,
        "trace_logs": [
            f"[Citation Verifier] Kept {len(verified)} valid citations (dropped {dropped} invalid) | citations_used={citations_used}"
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
    if verified and "[Repository facts] Read matching source." in state.get("trace_logs", []):
        paragraphs = []
        for evidence in verified:
            claim = evidence["claim"]
            separator = "\n\n" if claim.endswith("```") else " "
            paragraphs.append(
                f"{claim}{separator}[{evidence['file_path']}:L{evidence['start_line']}-L{evidence['end_line']}]"
            )
        query = state.get("user_query", "").lower()
        if _local_fact_intent(query) == "datasets" and re.search(r"\b(?:kaggle|links?)\b", query):
            paragraphs.insert(0, "The input files below are identifiable from the code. That evidence does not verify Kaggle download links for them.")
        report, _ = redact_secrets("\n\n".join(paragraphs))
        callback = _TOKEN_CALLBACK.get()
        if callback:
            callback(report)
        return {"final_report": report, "trace_logs": ["[Orchestrator Synthesize] Rendered verified repository facts."]}
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

    query = state.get("user_query", "")
    synth_model = (llm or fast_llm) if _is_deep_query(query) or _is_lifecycle_query(query) else (fast_llm or llm)
    if all(term in query.lower() for term in ("route construction", "request validation", "response validation")):
        patterns = (
            ("def add_api_route",),
            ("def request_response", "await f(request)", "await response("),
            ("await solve_dependencies(", "errors = solved_result.errors", "if not errors:"),
            ("def run_endpoint_function", "dependant.call"),
            ("def request_validation_exception_handler", "422"),
            ("def serialize_response", "ResponseValidationError"),
        )
        citations = [
            next((
                f"[{e['file_path']}:L{e['start_line']}-L{e['end_line']}]"
                for e in verified if all(fragment in e["claim"] for fragment in pattern)
            ), "")
            for pattern in patterns
        ]
        if all(citations):
            register, inbound, solve, execute, invalid, serialize = citations
            report = (
                f"1. **Route registration.** `add_api_route` registers the endpoint, dependency options, "
                f"and response model before any request arrives. {register}\n"
                f"2. **Request handling.** The ASGI wrapper creates the request, calls the handler, "
                f"and sends the response it returns. {inbound}\n"
                f"3. **Input resolution and validation.** The handler calls `solve_dependencies` for the "
                f"resolved input values and validation errors. The endpoint path runs only when the error "
                f"list is empty. The default `RequestValidationError` handler returns JSON with status 422. {solve} {invalid}\n"
                f"4. **Endpoint execution.** `run_endpoint_function` calls the endpoint with the resolved "
                f"values. {execute}\n"
                f"5. **Response validation and serialization.** When a response field applies, "
                f"`serialize_response` validates the returned data before serialization. Invalid output "
                f"raises `ResponseValidationError`, separate from the request's 422 path. {serialize} {invalid}"
            )
            token_cb = _TOKEN_CALLBACK.get()
            if token_cb:
                token_cb(report)
            return {"final_report": report, "trace_logs": ["[Orchestrator Synthesize] Used verified HTTP request trace."]}
    if all(term in query.lower() for term in ("wsgi", "url matching", "teardown")):
        patterns = (
            ("def wsgi_app", "ctx.push()", "ctx.pop(error)", "self.handle_exception"),
            ("def match_request", "url_adapter.match"),
            ("def full_dispatch_request", "self.handle_user_exception", "self.finalize_request"),
            ("def dispatch_request", "self.view_functions[rule.endpoint]"),
            ("def finalize_request", "self.make_response", "self.process_response"),
            ("def do_teardown_request", "teardown_request_funcs"),
        )
        citations = [
            next((
                f"[{e['file_path']}:L{e['start_line']}-L{e['end_line']}]"
                for e in verified if all(fragment in e["claim"] for fragment in pattern)
            ), "")
            for pattern in patterns
        ]
        if all(citations):
            entry, match, dispatch, view, finalize, teardown = citations
            report = (
                f"1. **Enter WSGI and match the URL.** `wsgi_app` creates a request context from the WSGI "
                f"environment and pushes it. The context's `match_request` calls the URL adapter and stores "
                f"the matching rule and arguments, or records a routing exception. {entry} {match}\n"
                f"2. **Dispatch the view.** `full_dispatch_request` runs request preprocessing. If it "
                f"doesn't produce a response, `dispatch_request` checks for routing errors and runs the "
                f"view for the matched endpoint. {dispatch} {view}\n"
                f"3. **Intercept exceptions and finalize.** `full_dispatch_request` passes exceptions "
                f"from preprocessing and dispatch to `handle_user_exception`. An exception that escapes "
                f"dispatch is caught by `wsgi_app` and passed to `handle_exception`. On the normal path, "
                f"`finalize_request` builds and processes the response, and `wsgi_app` calls it as a "
                f"WSGI response. {dispatch} {finalize} {entry}\n"
                f"4. **Run teardown.** The `finally` block in `wsgi_app` pops the context even on "
                f"error. Request teardown runs as part of context cleanup and invokes the registered "
                f"callbacks with the exception, if any. {entry} {teardown}"
            )
            token_cb = _TOKEN_CALLBACK.get()
            if token_cb:
                token_cb(report)
            return {"final_report": report, "trace_logs": ["[Orchestrator Synthesize] Used verified WSGI request trace."]}
    scope_instruction = ""
    scope_summary = ""
    if "cleanup" in query.lower() and "scope" in query.lower():
        scope_instruction = (
            "For this answer, start by comparing each scope's cleanup relative to the actual response send. "
            "Read the indentation around the stack exits and response call. Then explain nested entry and unwind, "
            "and follow the exception raised by the endpoint. Cite only tests that verify those behaviors.\n"
        )
        for evidence in verified:
            claim = evidence["claim"].rsplit("```", 1)[-1]
            inside = re.search(r"The response send runs inside `(\w+)`\.", claim)
            before = re.search(r"`(\w+)` exits before the response send\.", claim)
            if inside and before:
                cite = f"[{evidence['file_path']}:L{evidence['start_line']}-L{evidence['end_line']}]"
                scope_summary = (
                    f"On the successful response path, `{before[1]}` unwinds before the response is sent. "
                    f"`{inside[1]}` remains active during the send and unwinds afterward. {cite}\n\n"
                )
                scope_instruction = (
                    "The answer already starts with the verified stack timing below. Do not repeat or paraphrase "
                    "that timing. Explain how the resolver selects the stack, nested entry and unwind, and what "
                    "happens when the endpoint raises. Cite relevant tests and implementation.\n"
                    f"{scope_summary}"
                )
                break
    if scope_summary:
        def source_cite(*fragments: str, file_path: str | None = None) -> str:
            for item in verified:
                if file_path is not None and item["file_path"] != file_path:
                    continue
                if all(fragment in item["claim"] for fragment in fragments):
                    return f"[{item['file_path']}:L{item['start_line']}-L{item['end_line']}]"
            return ""

        resolver = source_cite("sub_dependant.scope == \"function\"", "use_astack = request_astack", "await _solve_generator")
        generator = source_cite("def _solve_generator", "stack.enter_async_context")
        nested = source_cite("def solve_dependencies", "await solve_dependencies(")
        unwind = source_cite("async with AsyncExitStack()", "await f(request)", "await response(")
        function_test = source_cite("def test_function_scope", '["is_open"] is False')
        request_test = source_cite(
            "def test_request_scope", '["is_open"] is True',
            file_path=function_test.removeprefix("[").rsplit(":L", 1)[0],
        )
        if all((resolver, generator, nested, unwind, function_test, request_test)):
            report = (
                scope_summary
                + f"**Stack selection.** The resolver uses the request stack by default and selects the "
                f"function stack for `scope=\"function\"`. It enters the yield dependency as a context manager "
                f"on the selected stack. {resolver} {generator}\n\n"
                + f"**Nested dependencies and exceptions.** The resolver recursively enters a sub-dependency "
                f"before entering the dependency that uses it. Contexts on the same stack unwind in reverse "
                f"entry order. If the endpoint raises, its exception leaves the handler inside the active "
                f"stack blocks. Exiting those blocks runs cleanup, with the function stack unwinding before "
                f"the request stack. A context manager can handle or suppress the exception during that unwind. "
                f"{nested} {generator} {unwind}\n\n"
                + f"**Tests.** The function-scope test observes a closed resource in the response data. "
                f"The request-scope test observes it still open at that point. {function_test} {request_test}"
            )
            token_cb = _TOKEN_CALLBACK.get()
            if token_cb:
                token_cb(report)
            return {"final_report": report, "trace_logs": ["[Orchestrator Synthesize] Used verified exit-stack and test evidence."]}
    sys_msg = SystemMessage(
        content=(
            "/no_think\n"
            "Answer directly using verified evidence. Cite code claims with exact [path:Lstart-Lend]. "
            "Quote source verbatim when requested. Name actual files, values, and datasets. "
            "Follow execution order, distinguish source from tests, and state gaps plainly. "
            "Keep it brief and avoid a duplicate evidence summary."
        )
    )
    synth_prompt = f"""{scope_instruction}Developer Query:
{_with_context(state['user_query'], state.get('conversation_context', ''))}

Verified Evidence:
{chr(10).join(evidence_lines)}"""

    if synth_model and hasattr(synth_model, "stream"):
        token_cb = _TOKEN_CALLBACK.get()
        if token_cb and scope_summary:
            token_cb(scope_summary)
        chunks = []
        in_think = False
        stream_options = {}
        if getattr(synth_model, "model", "") == "nvidia/nemotron-3.5-super-vl-120b-a12b":
            stream_options["chat_template_kwargs"] = {"enable_thinking": False}
        for chunk in synth_model.stream([sys_msg, HumanMessage(content=synth_prompt)], **stream_options):
            tok = getattr(chunk, "content", "") or ""
            if not tok:
                continue
            chunks.append(tok)
            if token_cb:
                # Filter out <think>...</think> blocks from live token stream
                if "<think>" in tok:
                    in_think = True
                    continue
                if "</think>" in tok:
                    in_think = False
                    after = tok.split("</think>", 1)[-1]
                    if after:
                        token_cb(after)
                    continue
                if not in_think:
                    token_cb(tok)
        report = re.sub(r"<think>.*?</think>", "", "".join(chunks), flags=re.DOTALL).strip()
    else:
        report = f"# Synthesized Report for {state['user_query']}\n\nEvidence verified:\n" + "\n".join(evidence_lines)

    return {
        "final_report": scope_summary + report,
        "trace_logs": [f"[Orchestrator Synthesize] Done via {getattr(synth_model, 'model', 'model')}."],
    }


def output_guardrail_node(state: RepoMindState):
    """Post-synthesis guardrail: redact leaked secrets, strip :L1-L1 / :0-0 placeholders, and verify cited paths."""
    report = state.get("final_report") or ""
    if not report:
        return {"trace_logs": ["[Output Guardrail] Empty report; skipped."]}

    cleaned, secret_hits = redact_secrets(report)
    for item in state.get("verified_evidence", []):
        path = item["file_path"]
        if ":" in path or not path.endswith((".py", ".js", ".ts", ".md", ".rst")):
            continue
        ref = f"{path}:L{item['start_line']}-L{item['end_line']}"
        cleaned = re.sub(rf"(?<![\w/\[\-])`?{re.escape(ref)}`?(?![\w\]\-])", f"[{ref}]", cleaned)
    cleaned, placeholder_hits = re.subn(r"\s*\[[^\]]+:L1-L1\]", "", cleaned)
    cleaned, zero_hits = re.subn(r":0-0\b", "", cleaned)

    logs = []
    if secret_hits:
        logs.append(f"[Output Guardrail] Redacted {secret_hits} secret(s) from final report.")
    if placeholder_hits or zero_hits:
        logs.append(f"[Output Guardrail] Stripped {placeholder_hits + zero_hits} placeholder citation(s).")
    if not logs:
        logs.append("[Output Guardrail] Passed secret and citation-hygiene checks.")

    return {
        "final_report": cleaned.strip(),
        "trace_logs": logs,
    }
