# RepoMind Benchmark Report — Execution-Path Retrieval, Language Ranking & Routing

**Date:** 2026-11-25  
**Model (Fixed):** `nvidia/nemotron-3.5-bolt-e4b` (NVIDIA NIM only)  
**Environment:** Live browser (`http://localhost:3000`, fresh "New Chat" conversation per prompt) → FastAPI (`127.0.0.1:8001`)  
**Cache Conditions:** Warm local clones (`D:\GB\backend\data\repositories\pallets_flask` and `fastapi_fastapi`); 0 external Exa/Context7/Firecrawl calls made.  

---

## 1. Changes Implemented (`2a9c4a9`)

1. **Stage-Decomposed Lifecycle Retrieval & 1-Hop AST Following** ([nodes.py](file:///d:/GB/backend/app/graph/nodes.py)):
   - Added `_is_lifecycle_query()`, `_extract_query_stages()`, `_gather_lifecycle_candidates()` (stage FTS5 + 1-hop AST `ImportFrom` following), and `_extract_focused_ast_spans()` (<=65-line focused spans across 2–3 distinct files).
   - Preserved the `<0.1s` AST fast-path for narrow queries (Flask Prompt 2 dropped to `5.46 s` backend total).
   - Added retrieval telemetry (`candidate_files`, `files_inspected`, `citations_used`) to `trace_logs`.
2. **Language-Aware Documentation Ranking** ([database.py](file:///d:/GB/backend/app/database.py), [repo_manager.py](file:///d:/GB/backend/app/repo_manager.py)):
   - Added `_extract_doc_lang()` and `_detect_requested_doc_langs()` so English queries rank production source > English docs > tests > translated `docs/<lang>/` copies, while keeping translated docs searchable for matching-language or explicit requests.
3. **Smallest Useful Specialist Set** ([nodes.py](file:///d:/GB/backend/app/graph/nodes.py), [specialists.py](file:///d:/GB/backend/app/agents/specialists.py)):
   - Enforced `repository_intelligence` only on Attempt 1 for lifecycle/implementation questions, adding `test_intelligence` only when tests are explicitly requested (cutting FastAPI Prompt 2 from 4 specialists to 2).
4. **Regression Tests** ([test_database.py](file:///d:/GB/backend/tests/test_database.py), [test_graph.py](file:///d:/GB/backend/tests/test_graph.py)):
   - Added `test_language_aware_doc_ranking`, `test_lifecycle_retrieval_across_files`, and `test_specialist_routing_smallest_set`.
   - **Backend Suite (`tests/run_all.py`):** **34 passed, 0 failed** in `4.17s`.

---

## 2. Repository Indexing Times (Measured Separately, Warm Cache)

| Repository | Total Files | Python Files | Docs Files | Indexing Time |
|---|---|---|---|---|
| `pallets/flask` | 236 | 83 | 95 | **3,006 ms (3.01 s)** |
| `fastapi/fastapi` | 3,279 | 1,233 | 1,734 | **7,182 ms (7.18 s)** |

*(Note: Every `POST /query/stream` currently re-invokes `clone_and_index_repo` because `repo_url` is sent with each query, adding ~1.3–3.0 s of pre-graph overhead per query.)*

---

## 3. Before-and-After Measurements

| Prompt | Baseline Total | After Total (Browser / Backend) | Backend TTFT | Specialists (Before → After) | External Calls |
|---|---|---|---|---|---|
| **Flask Prompt 1** | `6.99 s` *(abstained)* | **`33.31 s` / `30.92 s`** | `26.27 s` | `1` → `1` | `0` |
| **Flask Prompt 2** | `25.92 s` | **`8.99 s` / `5.46 s`** (**2.9x faster**) | `2.71 s` | `1` → `1` (AST fast path) | `0` |
| **FastAPI Prompt 1** | `12.65 s` | **`34.79 s` / `30.33 s`** | `24.89 s` | `1` → `1` | `0` |
| **FastAPI Prompt 2** | `42.93 s` | **`44.83 s` / `39.06 s`** | `32.84 s` | `4` → **`2`** | `0` (down from Exa+Context7) |

### Per-Node Backend Timings (`[SSE Timing]` from `task-2521.log`)

| Node | Flask 1 | Flask 2 | FastAPI 1 | FastAPI 2 |
|---|---|---|---|---|
| `input_guardrail` | 5 ms | 3 ms | 3 ms | 1,098 ms |
| `rate_limit_gate` | 7 ms | 13 ms | 11 ms | 10 ms |
| `orchestrator_plan` | 1,881 ms | 2,024 ms | 1,968 ms | 3,166 ms |
| `repository_intelligence` | 23,817 ms | **35 ms** *(AST fast path)* | 22,241 ms | 19,199 ms |
| `test_intelligence` | — | — | — | 8,558 ms |
| `orchestrator_review` | 2 ms | 3 ms | 3 ms | 4 ms |
| `verify_citations` | 23 ms | 17 ms | 54 ms | 34 ms |
| `orchestrator_synthesize` | 5,174 ms | 3,354 ms | 6,049 ms | 6,985 ms |
| `output_guardrail` | 2 ms | 1 ms | 1 ms | 2 ms |

---

## 4. Quality Evaluation & Scoring (1–5 Scale)

| Prompt | Found Implementation Files? | Citations Support Claims? | Covered Requested Behavior? | Citations Used |
|---|---|---|---|---|
| **Flask Prompt 1** | **3/5** (`src/flask/app.py`, `src/flask/ctx.py`, `src/flask/views.py` — resolved prior abstention) | **4/5** (accurate lines `app.py:L32-L91`, `views.py:L78-L83`, `ctx.py:L154-L206`) | **2/5** (cited imports/context removal & `views.py`, missed `wsgi_app` / `full_dispatch_request` / `finalize_request` bodies) | 9 |
| **Flask Prompt 2** | **3/5** (`src/flask/ctx.py` `_AppCtxGlobals`; missed `globals.py` `LocalProxy`) | **4/5** (`src/flask/ctx.py:L53-L103`) | **3/5** (explained concurrent/async corruption & `_AppCtxGlobals.__dict__`, missed `LocalProxy` / `RequestContext`) | 6 |
| **FastAPI Prompt 1** | **4/5** (`fastapi/applications.py` **and** `fastapi/routing.py` — major gain) | **4/5** (`routing.py:L66-L78`, `applications.py:L8-L12`) | **2/5** (cited import statements in `routing.py` rather than `get_request_handler` / `solve_dependencies` / `serialize_response` bodies) | 9 |
| **FastAPI Prompt 2** | **2/5** (found 5 English `tests/test_dependency_*.py` files; **0 German `docs/de/` files**; missed `fastapi/dependencies/utils.py`) | **5/5** (verified `test_dependency_yield_scope.py:L152-L212` matches tested revision: `scope="function"` closes before streaming response body (`is_open is False`), `scope="request"` stays open (`is_open is True`)) | **3/5** (accurate cleanup, `HTTPException` propagation, and `scope` timing from tests; missed `AsyncExitStack` in `utils.py`) | 11 |

---

## 5. Full Verbatim Prompt Outputs

### Flask Prompt 1 (`33.31 s` browser / `30.92 s` backend)
- **Prompt:** `Trace a request from Flask’s WSGI entry point through URL matching, request context creation, view dispatch, error handling, and response finalization. Explain where exceptions are intercepted and where teardown callbacks run. Cite the relevant functions and line ranges.`
- **Output:**
```text
Exceptions are intercepted within the context removal function, and teardown callbacks run during this process [src/flask/app.py:L84-L91].
The request flow involves:
Request start signaled [src/flask/app.py:L49-L49].
URL matching and view dispatch occur via dispatch_request [src/flask/views.py:L78-L83].
Request context is created using AppContext [src/flask/app.py:L32-L32].
Request handling utilizes the Request wrapper [src/flask/app.py:L52-L52].
Response finalization uses the Response wrapper [src/flask/app.py:L53-L53].
Request finished signaled [src/flask/app.py:L48-L48].
Supporting Evidence:
[src/flask/app.py:L32-L32]
[src/flask/app.py:L52-L52]
[src/flask/app.py:L53-L53]
[src/flask/app.py:L43-L43]
[src/flask/app.py:L49-L49]
[src/flask/app.py:L48-L48]
[src/flask/app.py:L84-L91]
[src/flask/ctx.py:L154-L206]
[src/flask/views.py:L78-L83]
```

### Flask Prompt 2 (`8.99 s` browser / `5.46 s` backend)
- **Prompt:** `If an extension stores request-specific state in a global variable instead of Flask’s context-local facilities, what failures could occur under concurrent requests or asynchronous views? Explain how Flask makes request, session, and g available in the correct context, citing the implementation.`
- **Output:**
```text
Failures under concurrent requests or asynchronous views when using a global variable for request-specific state include data corruption, race conditions, and requests incorrectly accessing state belonging to other concurrent requests [src/flask/ctx.py:L53-L57]. Flask makes request, session, and g available in the correct context through mechanisms that manage context isolation, as evidenced by the implementation of _AppCtxGlobals which manages attributes via self.__dict__ [src/flask/ctx.py:L59-L60].
Supporting Evidence:
[src/flask/ctx.py:L53-L57]
[src/flask/ctx.py:L59-L60]
[src/flask/ctx.py:L62-L66]
[src/flask/ctx.py:L68-L77]
[src/flask/ctx.py:L79-L91]
[src/flask/ctx.py:L93-L103]
```

### FastAPI Prompt 1 (`34.79 s` browser / `30.33 s` backend)
- **Prompt:** `Trace how FastAPI turns an incoming HTTP request into a validated endpoint call and serialized response. Explain how route construction, dependency resolution, request validation, and response validation interact, including how validation errors are handled. Cite the relevant code.`
- **Output:**
```text
FastAPI turns an incoming HTTP request into a validated endpoint call and serialized response by importing routing components [fastapi/applications.py:L8-L8], utilizing dependency solving via get_dependant and solve_dependencies [fastapi/routing.py:L66-L66], performing request validation, and using jsonable_encoder for response serialization [fastapi/routing.py:L77-L77]. Validation errors are handled by RequestValidationError [fastapi/routing.py:L78-L78], and exception handlers like request_validation_exception_handler are imported for the error flow [fastapi/applications.py:L12-L12].
Supporting Evidence:
[fastapi/routing.py:L66-L66] (repository_intelligence) Routing logic imports necessary components like dependencies and validation utilities.
from fastapi.dependencies.utils import (
[fastapi/routing.py:L66-L66] (repository_intelligence) Dependency solving is handled by get_dependant and solve_dependencies.
from fastapi.dependencies.utils import (
[fastapi/routing.py:L78-L78] (repository_intelligence) Request validation errors are handled via RequestValidationError.
from fastapi.exceptions import (
[fastapi/routing.py:L77-L77] (repository_intelligence) Response serialization utility is available via jsonable_encoder.
from fastapi.encoders import jsonable_encoder
[fastapi/applications.py:L8-L8] (repository_intelligence) The application setup imports routing components from fastapi.routing.
from fastapi import routing
[fastapi/applications.py:L12-L12] (repository_intelligence) Exception handlers like request_validation_exception_handler are imported for error flow.
from fastapi.exception_handlers import (
```

### FastAPI Prompt 2 (`44.83 s` browser / `39.06 s` backend)
- **Prompt:** `Analyze the lifecycle of a dependency that uses yield, including a nested dependency and an endpoint that raises an exception. Explain when cleanup executes, how exceptions propagate through the dependency stack, and how dependency scope affects cleanup timing where supported. Cite the implementation and relevant tests.`
- **Output:**
```text
Cleanup executes in a finally block, which runs whether an exception occurs or not, as shown by test_dependency_gets_exception [tests/test_dependency_yield_except_httpexception.py:L53-L61] where state["finally"] is True after an exception, and test_dependency_no_exception [tests/test_dependency_yield_except_httpexception.py:L64-L72] where state["finally"] is True after success.
Exceptions propagate through the dependency stack such that if an exception is raised after the yield point, it is captured or shown in server logs, as demonstrated by test_broken_raise [tests/test_dependency_after_yield_raise.py:L46-L48] which expects a ValueError, and test_broken_session_stream_raise [tests/test_dependency_after_yield_streaming.py:L88-L130] which expects (ValueError, Exception).
Dependency scope affects cleanup timing where supported, with testing present for function, request, and nested scopes [tests/test_dependency_yield_scope.py:L152-L212], and specific testing for WebSocket contexts showing scope usage [tests/test_dependency_yield_scope_websockets.py:L76-L78].
Supporting Evidence:
test_dependency_gets_exception [tests/test_dependency_yield_except_httpexception.py:L53-L61]
test_dependency_no_exception [tests/test_dependency_yield_except_httpexception.py:L64-L72]
test_broken_raise [tests/test_dependency_after_yield_raise.py:L46-L48]
test_broken_session_stream_raise [tests/test_dependency_after_yield_streaming.py:L88-L130]
test_dependency_yield_scope [tests/test_dependency_yield_scope.py:L152-L212]
test_dependency_yield_scope_websockets [tests/test_dependency_yield_scope_websockets.py:L76-L78]
```

---

## 6. Remaining Misses & Next Steps

1. **Top-of-File Import Citations vs. Function Bodies:** On Flask 1 and FastAPI 1, `repo_intel_agent` (`bolt-e4b`) stopped after reading lines 1–90 (top-of-file imports) in `app.py` and `routing.py` instead of reading the downstream function definitions (`wsgi_app`, `full_dispatch_request`, `get_request_handler`, `solve_dependencies`). Having `run_repo_intel` detect when all citations in a file are `< L100` (import headers) and automatically pull the matching AST function spans in that file will fix this cleanly.
2. **FastAPI Prompt 2 Core Source Coverage:** `repository_intelligence` returned 4 items, yet `test_intelligence`'s 6 test items dominated synthesis and `fastapi/dependencies/utils.py` (`solve_dependencies`, `AsyncExitStack`) was still missed.
3. **Skip Redundant `clone_and_index_repo` on Active Repo Queries:** Avoiding re-indexing on `/query/stream` when `repo_url` already matches the active repo will save ~1.3–3.0 s on every query.

## 7. Concurrent portfolio smoke test — November 26, 2026

One overlapping pair of requests from independent browser tabs. The production frontend build was served on a separate local origin from FastAPI. The repositories were indexed before timing.

| Repository and prompt | Browser completion | Evidence |
|---|---:|---|
| Flask WSGI lifecycle | 2.56 s | WSGI entry, URL matching, dispatch, error handling, and teardown at commit `49943b5a94413a530761350f04e896f76e8d620e`. |
| Regional sentiment tokenizer | 1.22 s | `AutoTokenizer.from_pretrained(MODEL_NAME)` and `xlm-roberta-base` at commit `60493ad012e07bfefb6ceba2efd24c07c0867667`. |

Each browser card pointed to its own repository and pinned SHA. An anonymous attempt to index a repo outside the configured demo allowlist was blocked. API tests also cover invalid and valid owner tokens and denial through direct query routes. These are local smoke-test timings, excluding indexing and host cold starts.
