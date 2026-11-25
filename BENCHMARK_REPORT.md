# RepoMind Live Browser Benchmark Report — Production-First Retrieval & Timing Telemetry

**Date:** 2026-11-25  
**Model:** `nvidia/nemotron-3.5-bolt-e4b` (NVIDIA NIM only)  
**Test Environment:** Live browser at `http://localhost:3000` → FastAPI backend at `http://127.0.0.1:8001`  

---

## Before vs. After Production-First Retrieval (`b87512f`)

| Prompt | Before (Time) | After (TTFT / Total) | Speedup | Specialists Dispatched | Core Production Files Found? |
|---|---|---|---|---|---|
| **Index `pallets/flask`** | `3.17 s` | **`2.68 s`** (warm, 236 files) | — | — | — |
| **Flask Prompt 1** | `9.23 s` | **`4.65 s` TTFT / `6.99 s` total** | **-24%** | 1 (`repository_intelligence`) | Partial (`src/flask/ctx.py` only; missed `src/flask/app.py`) |
| **Flask Prompt 2** | `25.34 s` | **`4.83 s` TTFT / `25.92 s` total** | ~same | 1 (`repository_intelligence`) | Partial (`src/flask/ctx.py` `_AppCtxGlobals`; missed `globals.py`, `RequestContext`) |
| **Index `fastapi/fastapi`** | `~41.5 s` | **`4.23 s`** (warm, 3,279 files) | — | — | — |
| **FastAPI Prompt 1** | `~45.9 s` | **`5.75 s` TTFT / `12.65 s` total** | **3.6x faster** | 1 (`repository_intelligence`) | Partial (`fastapi/applications.py` found; missed `routing.py`, `dependencies/utils.py`) |
| **FastAPI Prompt 2** | `~89.9 s` | **`28.23 s` TTFT / `42.93 s` total** | **2.1x faster** | 4 (all specialists) | Missed core `fastapi/dependencies/utils.py`; cited `tests/` + `docs/de/` |

---

## Scoring Matrix (1–5 Scale)

| Prompt | Right Files Found? | Citations Support Claims? | Covers Requested Behavior? | Overall |
|---|---|---|---|---|
| **Flask Prompt 1** | **1/5** (`ctx.py` only, missed `app.py`) | **5/5** (honest abstention, accurate lines) | **1/5** (abstained due to missing `app.py`) | **2.3/5** |
| **Flask Prompt 2** | **3/5** (`ctx.py` found; missed `globals.py`) | **4/5** (verbatim `_AppCtxGlobals` methods cited) | **3/5** (explained `contextvars` & `g`, missed `LocalProxy` & `RequestContext`) | **3.3/5** |
| **FastAPI Prompt 1** | **3/5** (`applications.py` found; missed `routing.py`, `dependencies/utils.py`) | **4/5** (accurate line ranges on `FastAPI` methods) | **3/5** (covered app setup & middleware, missed `solve_dependencies` & `serialize_response`) | **3.3/5** |
| **FastAPI Prompt 2** | **2/5** (found `tests/test_dependency_*`, missed `dependencies/utils.py`) | **4/5** (accurate test & doc citations, though German `docs/de/` surfaced) | **3/5** (covered `try/except/finally` & `scope='function'`, missed `AsyncExitStack` & nested yield) | **3.0/5** |

---

## Detailed Outputs & Observations

### 1. Flask Prompt 1 (`4.65 s` TTFT / `6.99 s` total)
- **Input:** `Trace a request from Flask’s WSGI entry point through URL matching, request context creation, view dispatch, error handling, and response finalization. Explain where exceptions are intercepted and where teardown callbacks run. Cite the relevant functions and line ranges.`
- **Source Cards:** `src/flask/ctx.py:L53-L103`
- **Output:**
  > The provided verified evidence does not contain information tracing a request from Flask’s WSGI entry point through URL matching, request context creation, view dispatch, error handling, response finalization, or details on where exceptions are intercepted and where teardown callbacks run. The evidence only shows implementations for methods on `_AppCtxGlobals` in `src/flask/ctx.py` `[src/flask/ctx.py:L53-L103]`.

### 2. Flask Prompt 2 (`4.83 s` TTFT / `25.92 s` total)
- **Input:** `If an extension stores request-specific state in a global variable instead of Flask’s context-local facilities, what failures could occur under concurrent requests or asynchronous views? Explain how Flask makes request, session, and g available in the correct context, citing the implementation.`
- **Source Cards:** `src/flask/ctx.py:L53-L57`
- **Output:** Explained concurrent/async state corruption and `_AppCtxGlobals` dictionary-backed attribute access (`__getattr__`, `__setattr__`, `__delattr__`, `get`, `pop`, `setdefault` at `src/flask/ctx.py:L53-L103`).

### 3. FastAPI Prompt 1 (`5.75 s` TTFT / `12.65 s` total)
- **Input:** `Trace how FastAPI turns an incoming HTTP request into a validated endpoint call and serialized response. Explain how route construction, dependency resolution, request validation, and response validation interact, including how validation errors are handled. Cite the relevant code.`
- **Source Cards:** `fastapi/applications.py:L1237-L1318`
- **Output:** Explained `FastAPI.add_api_route`, `build_middleware_stack`, `ExceptionMiddleware`, `__call__`, and `setup` with citations across `fastapi/applications.py:L70-L1318` (replacing earlier test-only citations with core production code).

### 4. FastAPI Prompt 2 (`28.23 s` TTFT / `42.93 s` total)
- **Input:** `Analyze the lifecycle of a dependency that uses yield, including a nested dependency and an endpoint that raises an exception. Explain when cleanup executes, how exceptions propagate through the dependency stack, and how dependency scope affects cleanup timing where supported. Cite the implementation and relevant tests.`
- **Source Cards:** `tests/test_dependency_after_yield_websockets.py:L23-L29`, `tests/test_dependency_yield_except_httpexception.py:L18-L25`, `docs/de/docs/tutorial/dependencies/dependencies-in-path-operation-decorators.md:L51-L51`, `docs/de/docs/tutorial/dependencies/dependencies-with-yield.md:L124-L124`, `docs/de/docs/tutorial/handling-errors.md:L34-L34`
- **Output:** Covered `finally` cleanup, `HTTPException` re-raise propagation, and `scope='function'` timing using verified test cases and docs, without reaching `fastapi/dependencies/utils.py`.

---

## Diagnosis & Recommended Next Steps

1. **Multi-File Breadth in `repo_intel_agent`:** On Flask Prompt 1 and FastAPI Prompt 1, `repo_intel_agent` inspected only **1 file** (`src/flask/ctx.py` or `fastapi/applications.py`), emitted 6 methods from that single file, and stopped. Instructing `repo_intel_agent` to inspect **at least 2–3 distinct candidate files** from the FTS5 list on multi-stage lifecycle questions will immediately bring `src/flask/app.py`, `fastapi/routing.py`, and `fastapi/dependencies/utils.py` into evidence.
2. **Exclude Translated Docs (`docs/de/`, `docs/es/`, etc.) from FTS5 Indexing:** FastAPI Prompt 2 surfaced German docs (`docs/de/...`). Filtering `docs/<lang>/` in `_classify_repo_file()` or preferring `docs/en/` keeps documentation clean and English-only.
3. **Prevent 4-Way Parallel Fan-Out on FastAPI Prompt 2:** FastAPI Prompt 2 took `42.93 s` (vs `12.65 s` for Prompt 1) because the planner dispatched all 4 specialists (`repository_intelligence`, `test_intelligence`, `debugging`, `change_impact`). Restricting it to `repository_intelligence` + `test_intelligence` will cut its latency roughly in half.
4. **Model Upgrade After Evidence Is Fixed:** Now that latency is `7–26s` for single-specialist runs on `bolt-e4b`, once multi-file retrieval pulls `app.py` / `routing.py` / `dependencies/utils.py`, testing a stronger synthesizer (e.g. `qwen3.8-27b` or `gpt-oss-120b` with NIM fallback) can be evaluated cleanly.
