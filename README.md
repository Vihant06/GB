# RepoMind 🧠

**Multi-Agent Codebase Intelligence & Agentic RAG Platform**

RepoMind analyzes GitHub repositories using specialized LangGraph agents for repository intelligence, debugging, change impact analysis, and test coverage synthesis. Backed by AST parsing, SQLite FTS5 documentation caching, and external live documentation retrieval via Exa, Firecrawl v2, and Context7 v3.

---

## Architecture Overview

```mermaid
flowchart TD
    UserQuery[Developer Query] --> Guardrails[Input Guardrails & SSRF Checks]
    Guardrails --> Router{3-Way LLM Router}
    
    Router -->|Off-Topic| Reject[Polite Reject & Exit]
    Router -->|General Coding| FastPath[Fast Path: General Coding LLM]
    Router -->|Repo Specific| RateLimit[SQLite Atomic Rate Limiting]
    
    RateLimit --> Planner[Orchestrator Planner]
    Planner -->|LangGraph Send Parallel Dispatch| S1[Repository Intelligence Specialist]
    Planner -->|LangGraph Send Parallel Dispatch| S2[Debugging Specialist]
    Planner -->|LangGraph Send Parallel Dispatch| S3[Change Impact Specialist]
    Planner -->|LangGraph Send Parallel Dispatch| S4[Test Intelligence Specialist]
    
    S1 & S2 & S3 & S4 --> Reviewer{Evidence Reviewer}
    Reviewer -->|Insufficient & Attempt < 2| Planner
    Reviewer -->|Sufficient or Attempt = 2| Verifier[Strict Citation Verifier]
    
    Verifier -->|No Verified Citations| NoEvidence[No Evidence Exit]
    Verifier -->|Verified file and line citations| Synthesis[Orchestrator Synthesis]
    
    FastPath & Reject & NoEvidence & Synthesis --> OutputGuardrail[Output Guardrails & Secret Redaction]
    OutputGuardrail --> ClientStream[SSE / JSON Client Response]
```

---

## Project Structure

```
GB/
├── backend/                     # FastAPI, LangGraph multi-agent orchestration, tools & tests
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application factory and lifespan manager
│   │   ├── config.py            # Environment variables, portable paths, and settings
│   │   ├── schemas.py           # Pydantic request/response models and SSE schemas
│   │   ├── database.py          # SQLite connection, WAL mode, FTS5 cache & atomic rate limits
│   │   ├── budget.py            # Request-scoped ContextVar API budget tracking
│   │   ├── guardrails.py        # SSRF, path sandboxing, prompt injection, and redaction
│   │   ├── api/
│   │   │   ├── routes.py        # /health, /query, and /query/stream endpoints
│   │   │   ├── conversations.py # Conversation management & Supabase sync
│   │   │   └── deps.py          # Actor identity resolution and rate limit dependency
│   │   ├── tools/
│   │   │   ├── local_code.py    # AST inspection, line reader, importers, test discoverer
│   │   │   ├── github_sync.py   # Live GitHub Issues and Actions CI synchronization
│   │   │   └── web_docs.py      # Context7 v3, Exa search, Firecrawl v2 scraping, FTS5
│   │   ├── agents/
│   │   │   ├── base.py          # build_specialist(), history compaction, JSON repair
│   │   │   └── specialists.py   # 4 specialist agent configurations and prompts
│   │   └── graph/
│   │       ├── state.py         # RepoMindState, SpecialistState, and report schemas
│   │       ├── nodes.py         # Orchestrator nodes, routers, AST fast paths
│   │       └── workflow.py      # Compiled StateGraph singleton
│   ├── data/
│   │   └── migrations/          # SQL migrations (Supabase auth & chat history)
│   ├── tests/                   # 31 unit & integration tests
│   ├── pyproject.toml
│   └── requirements.txt
│
└── frontend/                    # React 19 + TypeScript + Vite 8 + Tailwind CSS v4 UI
    ├── components/
    │   └── ui/                  # Wayne retro-styled rare-ui components:
    │                            # ai-status, web-search, streaming-response
    ├── src/
    │   ├── App.tsx              # Main application shell & view routing
    │   ├── components/          # ChatSessionPane, HomeSection, SettingsSection
    │   └── types/               # Chat and session data models
    ├── package.json
    └── vite.config.ts           # Dev proxy to FastAPI backend (port 8001)
│   └── run_all.py               # Complete test discovery runner
│
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 4 Specialist Agents

1. **Repository Intelligence Agent:** Explores AST symbol trees, function/class declarations, execution paths, dependencies (`pyproject.toml`, `setup.cfg`), and ingested documentation.
2. **Debugging Agent:** Diagnoses runtime errors, exceptions, stack traces, and cross-references synced GitHub Issues and GitHub Actions CI failures.
3. **Change Impact Agent:** Computes the blast radius of code modifications across upstream importers, call-site references, public APIs, and tests.
4. **Test Intelligence Agent:** Discovers test cases, inspects assertions, and audits production modules for untested methods.

---

## Key Reliability & Security Features

* **Concurrency-Safe Budgeting:** Replaced mutable global state with `contextvars.ContextVar[RunBudgetTracker]` ensuring concurrent requests never leak API call quotas or cache hit metrics.
* **Atomic SQLite Rate Limiting:** Uses `BEGIN IMMEDIATE` transactions with WAL mode and `busy_timeout=5000ms` to serialize sliding-window rate checks across multiple Uvicorn workers.
* **Deterministic Citation Verification:** Strict regex parsing and local filesystem resolution for `[file:Lstart-Lend]` citations. Drops ungrounded claims and activates a no-evidence exit when no citations survive verification.
* **SSRF & Path Sandboxing:** Rejects private/loopback/link-local IPs via DNS resolution checks, blocks path traversal (`..`), Windows drive prefixes (`C:`), and symlink escapes.
* **Secret Redaction:** Masks NVIDIA keys (`nvapi-...`), GitHub tokens (`ghp_...`), and API keys in queries and synthesized reports.

---

## Getting Started

### 1. Prerequisites
- Python 3.11+ (Python 3.13 recommended)
- Node.js 20+ (with npm)
- Git

### 2. Environment Setup

Clone the repository and copy the environment template:
```bash
cp .env.example .env
```

Edit `.env` and set your credentials:
```ini
NVIDIA_API_KEY=nvapi-...
GITHUB_TOKEN=ghp_...
EXA_API_KEY=exa-...
FIRECRAWL_API_KEY=fc-...
CONTEXT7_API_KEY=ctx7_...
```

`FIRECRAWL_API_KEY` enables public web search for questions about framework usage and documentation.
Those questions bypass repository retrieval. If search is unavailable or rate-limited, RepoMind can still answer from general knowledge.

### 3. Backend Installation & Startup

Create and activate a virtual environment, then install backend dependencies:
```bash
uv pip install -r requirements.txt
```

Start the FastAPI backend with Uvicorn:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

The backend is now available at `http://localhost:8001`.
- Swagger Docs: `http://localhost:8001/docs`
- Health check: `http://localhost:8001/health`

### 4. Frontend Startup

In a separate terminal, install dependencies and start the Vite development server:
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` in your browser. The development server proxies API requests to `http://localhost:8001`.

### Separate frontend and backend hosting

- **Frontend:** Build from `frontend/` with `npm ci` and `npm run build`. Publish `dist/` as a static site. Set `VITE_BACKEND_URL` to the backend HTTPS origin at build time. Keep provider keys on the backend.
- **Backend:** Install Git and `backend/requirements.txt`. From `backend/`, run `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001} --workers 1` on a POSIX host. Set the model provider key and `REPOMIND_CORS_ORIGINS` to the exact frontend origin. The host must support HTTP streaming and writable local disk.
- **Cache:** SQLite and shallow public GitHub checkouts use backend-local storage. Set `REPOMIND_DB_PATH` and `REPOMIND_REPOSITORY_DIR` to use a specific writable directory. The default cache allows eight repositories, configurable with `REPOMIND_MAX_CACHED_REPOS`. This is a repository count limit. No database service or persistent volume is required for the cache. On an empty cache, the backend can fetch a pinned commit again if GitHub still serves it.

Linking a repo returns its commit SHA. The client sends that SHA with each query and uses it in source links. The single-worker backend keeps one checkout per repo. If that checkout holds a different SHA, the query fails with a conflict instead of changing revisions.

**Portfolio access:** Signed-in users can index up to five distinct public GitHub repositories in a rolling 365-day window. A first indexing attempt reserves a slot, and retries or queries for the same repository reuse it. The quota survives restarts when the SQLite database is on persistent storage. Set `REPOMIND_DEMO_REPOS` to customize the suggestions in the picker.

### Email and password sign-in and saved chats

1. In your Supabase project, apply `backend/data/migrations/001_chat_history_auth.sql` and then `002_saved_chat_snapshots.sql`. If migration 001 is already applied, run only migration 002.
2. In **Supabase → Authentication → Sign In / Providers**, enable Email and allow new signups if visitors may create accounts.
3. For the current demo, turn **Confirm email** off in the Email settings. RepoMind checks that setting before creating a new account, so it won't start an email-confirmation flow. Previously unconfirmed test accounts may still need to be activated in Supabase.
4. Set `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` on the frontend and restart or rebuild. Use the project's publishable key or legacy anon key.
5. On a separate backend host, set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` to the same project and public key. A local backend can read the public values from the sibling `frontend/.env.local` when the backend settings are absent. Restart the backend after changing its configuration.
6. Sign up with a username, email, and password. Sign in with your email and password. RepoMind uses the username for the greeting. Send a message, then open **History** in the side rail after a reload or sign in with the same account on another device. If the sidebar says the saved-chat tables are missing, run the migrations in step 1 in the same Supabase project as the frontend URL, then select **Retry history**.

See the [Supabase password authentication guide](https://supabase.com/docs/guides/auth/passwords) for Email setup.

Saved messages include chat text, the repository revision, and verified citations. Attachment contents stay out of saved history. Supabase row-level security restricts history to its owner.

---

## API Endpoints

### `GET /health`
Returns system readiness, database status, and repository availability:
```json
{
  "status": "ok",
  "repo_name": "psf/requests",
  "repo_available": true,
  "database_ready": true,
  "version": "1.0.0"
}
```

### `POST /query`
Non-streaming JSON query endpoint:
```json
{
  "query": "Explain how Session.send handles connection adapters in requests.",
  "response_style": "detailed"
}
```

### `POST /query/stream`
Server-Sent Events (SSE) streaming endpoint returning real-time LangGraph execution events:
- `event: status` — Current execution phase.
- `event: progress` — Node and specialist progress updates.
- `event: token` — Incremental report text chunks.
- `event: complete` — Final Markdown engineering report, usage metrics, and trace logs.
- `event: error` — Structured error message.

---

## Running the Automated Test Suite

Run the complete test suite with the built-in runner:
```bash
python tests/run_all.py
```

The suite executes 31 tests covering:
- Configuration and portable path resolution (`test_config.py`)
- SQLite WAL mode, FTS5 search, and atomic rate limiting (`test_database.py`)
- Request-scoped ContextVar budget isolation under concurrency (`test_budget.py`)
- Security guardrails, SSRF, path sandboxing, and citations (`test_guardrails.py`)
- AST code inspection and mocked external tools (`test_tools.py`)
- Base specialist agent builder and JSON recovery (`test_agents.py`)
- Compiled StateGraph assembly and routing (`test_graph.py`)
- FastAPI `/health`, `/query`, and `/query/stream` SSE endpoints (`test_api.py`)
