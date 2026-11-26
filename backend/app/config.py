"""
RepoMind configuration module.
Centralizes environment variables, portable filesystem paths,
model settings, rate limits, and external service configurations.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from dataclasses import dataclass
from dotenv import dotenv_values, find_dotenv, load_dotenv

# Base project directory (d:\GB)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from backend/ and project root without overriding process-level environment variables
for _candidate_env in (BASE_DIR / ".env", BASE_DIR.parent / ".env"):
    if _candidate_env.exists():
        load_dotenv(_candidate_env, override=False)
_env_path = find_dotenv(usecwd=True)
if _env_path and Path(_env_path).exists():
    load_dotenv(_env_path, override=False)


@dataclass
class ExternalConfig:
    http_timeout: int = 20
    firecrawl_timeout: int = 30
    firecrawl_max_pages: int = 5
    # Respects free-tier 10 RPM /scrape limit
    firecrawl_min_interval_sec: float = 6.5
    exa_max_results: int = 4
    docs_cache_ttl_sec: int = 7 * 24 * 3600   # 7 days for crawled documentation
    search_cache_ttl_sec: int = 24 * 3600     # 24 hours for Exa / Context7 lookups
    max_exa_calls_per_run: int = 2
    max_context7_calls_per_run: int = 2
    max_firecrawl_pages_per_run: int = 5
    max_response_chars: int = 2600


@dataclass
class RateLimitConfig:
    max_queries_per_minute: int = 6
    max_queries_per_day: int = 40
    daily_exa_usd_cap: float = 0.25
    daily_firecrawl_pages_cap: int = 25
    daily_context7_calls_cap: int = 40


# Dynamic Repository and Path Configuration
_default_data_dir = BASE_DIR / "data"
_default_repositories_dir = _default_data_dir / "repositories"
_default_repo_path = _default_repositories_dir / "active_repo"
_default_db_path = _default_data_dir / "repomind_knowledge.db"

_repo_name_env = os.getenv("REPOMIND_REPO", "").strip()
_repo_path_env = os.getenv("REPOMIND_REPO_PATH", "").strip()

if _repo_name_env:
    _initial_path = Path(_repo_path_env if _repo_path_env else str(_default_repositories_dir / _repo_name_env.replace('/', '_'))).resolve()
else:
    _initial_path = Path(_repo_path_env).resolve() if _repo_path_env else _default_repo_path

_ACTIVE_REPO = {
    "full_name": _repo_name_env,
    "path": _initial_path,
}

@dataclass(frozen=True)
class RepositorySnapshot:
    name: str
    root: Path
    revision: str | None = None

    @property
    def index_key(self) -> str:
        return f"{self.name.lower()}@{self.revision}" if self.revision else self.name


_REPOSITORY_SNAPSHOT: ContextVar[RepositorySnapshot | None] = ContextVar(
    "repository_snapshot", default=None
)


@contextmanager
def repository_scope(snapshot: RepositorySnapshot):
    token = _REPOSITORY_SNAPSHOT.set(snapshot)
    try:
        yield snapshot
    finally:
        _REPOSITORY_SNAPSHOT.reset(token)


def get_repo_snapshot() -> RepositorySnapshot | None:
    return _REPOSITORY_SNAPSHOT.get()


def get_repo_full_name() -> str:
    snapshot = get_repo_snapshot()
    if snapshot is not None:
        return snapshot.name
    return _ACTIVE_REPO.get("full_name", "")


def get_repo_root() -> Path:
    snapshot = get_repo_snapshot()
    if snapshot is not None:
        return snapshot.root
    return _ACTIVE_REPO.get("path") or _default_repo_path


def set_active_repo(full_name: str, path: Path) -> None:
    _ACTIVE_REPO["full_name"] = full_name
    _ACTIVE_REPO["path"] = path.resolve()
    import sys
    this_mod = sys.modules.get(__name__)
    if this_mod:
        this_mod.REPO_FULL_NAME = full_name
        this_mod.REPO_ROOT = path.resolve()
        this_mod.REPO_PATH = path.resolve()
        this_mod.REPO_URL = f"https://github.com/{full_name}.git" if full_name else ""


REPO_FULL_NAME = get_repo_full_name()
REPO_URL = f"https://github.com/{REPO_FULL_NAME}.git" if REPO_FULL_NAME else ""
REPO_PATH = get_repo_root()
REPO_ROOT = REPO_PATH
DB_PATH = str(Path(os.getenv("REPOMIND_DB_PATH", str(_default_db_path))).resolve())


def __getattr__(name: str):
    if name == "REPO_FULL_NAME":
        return get_repo_full_name()
    if name in ("REPO_ROOT", "REPO_PATH"):
        return get_repo_root()
    if name == "REPO_URL":
        return f"https://github.com/{get_repo_full_name()}.git"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

MAX_QUERY_CHARS = 2000

# API Keys
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
CONTEXT7_API_KEY = os.getenv("CONTEXT7_API_KEY", "")

# Supabase Auth & Chat History
# A local split checkout can reuse the public values from the frontend settings.
# Separate deployments set both backend variables explicitly.
_frontend_auth = {
    **dotenv_values(BASE_DIR.parent / "frontend" / ".env"),
    **dotenv_values(BASE_DIR.parent / "frontend" / ".env.local"),
}
SUPABASE_URL = os.getenv("SUPABASE_URL") or _frontend_auth.get("VITE_SUPABASE_URL") or ""
SUPABASE_PUBLISHABLE_KEY = (
    os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    or _frontend_auth.get("VITE_SUPABASE_PUBLISHABLE_KEY") or ""
)
SUPABASE_ANON_KEY = SUPABASE_PUBLISHABLE_KEY
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")

# Server & CORS Configuration
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "REPOMIND_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

# Config instances
EXT_CONFIG = ExternalConfig()
RATE_CFG = RateLimitConfig()

# LLM Models (NVIDIA NIM & Groq mixed defaults)
PRIMARY_MODEL = os.getenv("REPOMIND_PRIMARY_MODEL", "nvidia/nemotron-3.5-bolt-e4b")
FAST_MODEL = os.getenv("REPOMIND_FAST_MODEL", "nvidia/nemotron-3.5-bolt-e4b")
GROQ_FAST_MODEL = os.getenv("REPOMIND_GROQ_FAST_MODEL", "qwen/qwen3.8-27b")
GROQ_MODEL = os.getenv("REPOMIND_GROQ_MODEL", "openai/gpt-oss-120b")


def create_chat_nvidia(model_name: str, max_tokens: int = 8192, temperature: float = 0.0):
    """Instantiate a ChatNVIDIA client with safe fallback if dependencies or key are missing."""
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    return ChatNVIDIA(
        model=model_name,
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )


def create_chat_groq(model_name: str, max_tokens: int = 4096, temperature: float = 0.0, max_retries: int = 0):
    """Instantiate a ChatGroq client with zero-sleep retries so fallbacks trigger immediately on 429."""
    from langchain_groq import ChatGroq
    return ChatGroq(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )


# True NVIDIA-only fast mode: use nvidia/nemotron-3.5-bolt-e4b (0.67s chat, 0.51s tool-calling)
# unless REPOMIND_PROVIDER=groq is explicitly requested.
try:
    if os.getenv("REPOMIND_PROVIDER", "nvidia").lower() == "groq" and GROQ_API_KEY:
        nim_fast = create_chat_nvidia(FAST_MODEL, max_tokens=2048, temperature=0.0)
        groq_primary = create_chat_groq(GROQ_MODEL, max_tokens=4096, temperature=0.0, max_retries=0)
        fast_llm = nim_fast
        llm = groq_primary.with_fallbacks([nim_fast])
    else:
        llm = create_chat_nvidia(PRIMARY_MODEL, max_tokens=4096, temperature=0.0)
        fast_llm = create_chat_nvidia(FAST_MODEL, max_tokens=2048, temperature=0.0)
except Exception:
    # Allows offline testing/mocking without failing on import
    llm = None  # type: ignore
    fast_llm = None  # type: ignore


def get_masked_key(key: str) -> str:
    """Return a masked representation of an API key for safe logging."""
    if not key:
        return "<not-set>"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"
