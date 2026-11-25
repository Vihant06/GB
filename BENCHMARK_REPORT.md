# RepoMind Live Browser Benchmark Report (NVIDIA-Only `nvidia/nemotron-3.5-bolt-e4b`)

**Date:** 2026-11-25  
**Model Configuration:** `PRIMARY_MODEL = nvidia/nemotron-3.5-bolt-e4b`, `FAST_MODEL = nvidia/nemotron-3.5-bolt-e4b` (NVIDIA NIM only)  
**Test Environment:** Live browser at `http://localhost:3000` → FastAPI backend at `http://127.0.0.1:8001`  

---

## Executive Summary

| # | Repository | Indexing Time | Prompt Topic | Response Time | Source Cards | Answer Quality |
|---|---|---|---|---|---|---|
| 1 | `pallets/flask` (236 files) | `3.17 s` | WSGI lifecycle, URL matching, error handling & teardowns | **`9.23 s`** | `src/flask/sansio/scaffold.py:L75-L215` | **Abstained** (wrong file retrieved: `scaffold.py` instead of `app.py`/`ctx.py`) |
| 2 | `pallets/flask` (236 files) | `—` | Global state vs `contextvars`, `request`, `session`, `g` | **`25.34 s`** | `src/flask/ctx.py:L3-L3` | **Good** (accurate `contextvars` & `AppContext` citations) |
| 3 | `fastapi/fastapi` (3,279 files) | `~41.5 s` | Request → route → dependency → validation → response | **`~45.9 s`** | `tests/test_request_params/test_default_validation.py`, `tests/test_security_http_bearer.py` | **Partial** (cited tests + Context7 instead of core `fastapi/routing.py` & `dependencies/utils.py`) |
| 4 | `fastapi/fastapi` (3,279 files) | `—` | `yield` dependency lifecycle, nested deps, exceptions & scope | **`~89.9 s`** | `tests/test_dependency_yield_except_httpexception.py`, `tests/test_dependency_after_yield_websockets.py` | **Partial** (accurate test/doc facts, missed `solve_dependencies` / `AsyncExitStack` core code) |

---

## Detailed Prompt-by-Prompt Results

### 1. `pallets/flask` — Prompt 1
- **Time Taken:** `9,233 ms` (**9.23 s**) *(last token at 8.36 s)*
- **Input Prompt:**
  > Trace a request from Flask’s WSGI entry point through URL matching, request context creation, view dispatch, error handling, and response finalization. Explain where exceptions are intercepted and where teardown callbacks run. Cite the relevant functions and line ranges.
- **Generated Output:**
  > The provided verified evidence does not contain information tracing a request from Flask's WSGI entry point through URL matching, request context creation, view dispatch, error handling, or response finalization, nor does it specify where exceptions are intercepted or where teardown callbacks run.
  >
  > Supporting Evidence:
  > - `[src/flask/sansio/scaffold.py:L75-L215]` (repository_intelligence)
  > - `[src/flask/sansio/scaffold.py:L217-L218]` (repository_intelligence)
  > - `[src/flask/sansio/scaffold.py:L220-L221]` (repository_intelligence)
  > - `[src/flask/sansio/scaffold.py:L224-L231]` (repository_intelligence)
  > - `[src/flask/sansio/scaffold.py:L234-L238]` (repository_intelligence)
  > - `[src/flask/sansio/scaffold.py:L241-L246]` (repository_intelligence)
- **Diagnosis:**
  - Honest abstention (no hallucination), **very fast (9.23 s)**, *but* `repository_intelligence` only inspected `src/flask/sansio/scaffold.py` and never opened `src/flask/app.py` (`wsgi_app`, `full_dispatch_request`, `dispatch_request`, `finalize_request`, `do_teardown_request`) or `src/flask/ctx.py`.

---

### 2. `pallets/flask` — Prompt 2
- **Time Taken:** `25,338 ms` (**25.34 s**)
- **Input Prompt:**
  > If an extension stores request-specific state in a global variable instead of Flask’s context-local facilities, what failures could occur under concurrent requests or asynchronous views? Explain how Flask makes request, session, and g available in the correct context, citing the implementation.
- **Generated Output:**
  > Failures under concurrent requests or asynchronous views when using a global variable for request-specific state include data corruption, race conditions, and requests incorrectly accessing state belonging to other concurrent requests. Flask makes request, session, and g available in the correct context by using contextvars `[src/flask/ctx.py:L3-L3]`, which allows context to be managed via `AppContext.push()` `[src/flask/ctx.py:L416-L444]` and accessed through methods like `AppContext.request()` `[src/flask/ctx.py:L371-L379]` and `AppContext.session()` `[src/flask/ctx.py:L396-L403]`. The context is managed using `__enter__` and `__exit__` methods on `AppContext` `[src/flask/ctx.py:L506-L516]`.
- **Diagnosis:**
  - Direct, accurate, well-cited (`src/flask/ctx.py`), **25.34 s**. Could be enriched by also citing `src/flask/globals.py` (`LocalProxy`, `_cv_app`, `_cv_request`).

---

### 3. `fastapi/fastapi` — Prompt 1
- **Time Taken:** **~45.9 s** (`~45,900 ms`)
- **Input Prompt:**
  > Trace how FastAPI turns an incoming HTTP request into a validated endpoint call and serialized response. Explain how route construction, dependency resolution, request validation, and response validation interact, including how validation errors are handled. Cite the relevant code.
- **Generated Output:**
  > FastAPI handles the transformation of an incoming HTTP request into a validated endpoint call and a serialized response through mechanisms involving route construction, dependency resolution, and request/response validation. Validation errors are handled by specific exception handlers, such as `request_validation_exception_handler` `[CONTEXT7:FastAPI@latest]`.
  >
  > Supporting evidence includes:
  > - Testing how missing values use specified defaults without validation `[tests/test_request_params/test_default_validation.py:L19-L40]`.
  > - Testing how missing values use None default correctly `[tests/test_request_params/test_default_validation.py:L45-L66]`.
  > - Testing that explicit values matching defaults are still validated `[tests/test_request_params/test_default_validation.py:L71-L98]`.
  > - Testing that explicit None input is validated `[tests/test_request_params/test_default_validation.py:L103-L120]`.
  > - Testing model validation with defaults `[tests/test_request_params/test_default_validation.py:L141-L157]`.
  > - Testing parameter validation with defaults `[tests/test_request_params/test_default_validation.py:L162-L180]`.
  > - Testing default factory execution `[tests/test_request_params/test_default_validation.py:L185-L206]`.
  > - Covering inferred parameters validation `[tests/test_request_params/test_default_validation.py:L250-L268]`.
  > - Testing rejection of invalid default values, resulting in a 422 status code for validation errors `[tests/test_request_params/test_default_validation.py:L310-L333]`.
  > - Covering file parameter default validation `[tests/test_request_params/test_default_validation.py:L357-L373]`.
  >
  > Custom dependency implementation example:
  > ```python
  >     async def __call__(
  >         self, request: HTTPConnection
  >     ) -> HTTPAuthorizationCredentials | None:
  >         return await super().__call__(request=request)
  > ```
  > `[tests/test_security_http_bearer.py:L42-L45]`
- **Diagnosis:**
  - Instead of reading `fastapi/routing.py` (`APIRoute`, `get_request_handler`, `serialize_response`), `fastapi/dependencies/utils.py` (`solve_dependencies`), and `fastapi/exception_handlers.py`, the FTS5 index & planner pulled test files (`tests/...`) and Context7 docs.

---

### 4. `fastapi/fastapi` — Prompt 2
- **Time Taken:** **~89.9 s** (`~89,900 ms`)
- **Input Prompt:**
  > Analyze the lifecycle of a dependency that uses yield, including a nested dependency and an endpoint that raises an exception. Explain when cleanup executes, how exceptions propagate through the dependency stack, and how dependency scope affects cleanup timing where supported. Cite the implementation and relevant tests.
- **Generated Output:**
  > Cleanup executes in the `finally` block after the `yield` statement completes, whether the execution was successful or an exception was raised `[tests/test_dependency_yield_except_httpexception.py:L18-L25]`. Exceptions propagate through the dependency stack, and the `finally` block is guaranteed to execute even when an `HTTPException` is raised during dependency execution `[tests/test_dependency_yield_except_httpexception.py:L18-L25]` `[EXA:https://github.com/tiangolo/fastapi/blob/master/tests/test_dependency_yield_except_httpexception.py]`. Dependency scope affects cleanup timing; for scope `'function'`, cleanup runs after the path operation function but before the response `[CONTEXT7:FastAPI@latest]`.
  >
  > Supporting Evidence:
  > - Cleanup (`finally`) executes after `yield` in `contextmanager`, setting `session.open=False` `[tests/test_dependency_after_yield_websockets.py:L24-L29]`.
  > - Dependency cleanup (`finally`) executes even when `HTTPException` is raised during dependency execution `[tests/test_dependency_yield_except_httpexception.py:L18-L25]`.
  > - Exception propagation is correctly tested, showing both `'except'` and `'finally'` blocks are hit `[tests/test_dependency_yield_except_httpexception.py:L53-L60]`.
  > - Dependency with yield can catch exceptions thrown during path operation `[EXA:https://github.com/tiangolo/fastapi/blob/master/tests/test_dependency_yield_except_httpexception.py]`.
  > - Finally block execution is guaranteed regardless of exception `[EXA:https://github.com/tiangolo/fastapi/blob/master/tests/test_dependency_yield_except_httpexception.py]`.
  > - Documentation confirms catching exceptions within yield dependencies is possible `[EXA:https://github.com/tiangolo/fastapi/blob/master/docs/en/docs/tutorial/dependencies/dependencies-with-yield.md]`.
  > - `fastapi.Depends` supports `scope='function'` for execution timing `[CONTEXT7:FastAPI@latest]`.
  > - Scope `'function'` runs cleanup after path operation function but before response `[CONTEXT7:FastAPI@latest]`.
- **Diagnosis:**
  - Good test & Context7/Exa coverage, **89.9 s** (ran both `test_intelligence` and `debugging` + Exa + Context7), *but* missed the core implementation (`AsyncExitStack` / `solve_dependencies` / `contextmanager_in_threadpool` in `fastapi/dependencies/utils.py` and `fastapi/routing.py`) and didn't detail nested yield dependency ordering.

---

## Root Causes & Next-Step Betterment Plan

1. **Prioritize Production Source Files Over `tests/` & `docs/` in FTS5 Indexing (`repo_manager.py` + `database.py`):**
   - Currently `repo_manager.py` indexes `py_files[:300]` in alphabetical/rglob order, so in large repos like `fastapi/fastapi` (1,233 `.py` files, mostly `tests/` and `docs_src/`), core source files (`fastapi/*.py`, `src/flask/*.py`) are crowded out by test files.
   - **Fix:** Sort/prioritize non-test, non-docs production `.py` files first when building `repo_symbols_fts`, and add a production-vs-test boost in `query_repo_symbols_fts()`.

2. **Pair `bolt-e4b` Retrieval with a Stronger Synthesizer (`config.py`):**
   - `nvidia/nemotron-3.5-bolt-e4b` cut latency dramatically (`9.2s–45.9s` vs `120s–189s`), *but* a 4B model struggles to synthesize deep multi-file architectural traces (like Flask Prompt 1 and FastAPI Prompt 1/2).
   - **Fix:** Use `bolt-e4b` for fast tool-calling (`fast_llm`) and a larger/stronger model for `orchestrator_synthesize_node` (`llm`), *or* pre-fetch full AST method bodies into synthesis context.

3. **Avoid Unnecessary External Search (`debugging` / Exa / Context7) on Pure Architecture Queries (`nodes.py`):**
   - FastAPI Prompt 2 took `89.9 s` because the planner dispatched `debugging` (running Exa + Context7) in addition to `test_intelligence`, while skipping `repository_intelligence` on core `fastapi/dependencies/utils.py`.
   - **Fix:** Tighten `PLANNER_PROMPT` so architectural/lifecycle questions dispatch `repository_intelligence` (on core source) + `test_intelligence` (only when tests are explicitly requested), keeping external APIs off unless errors or external docs are asked for.
