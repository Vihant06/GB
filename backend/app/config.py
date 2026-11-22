"""
RepoMind configuration module.
Centralizes environment variables, portable filesystem paths,
model settings, rate limits, and external service configurations.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import find_dotenv, load_dotenv

# Base project directory (d:\GB)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env without silently overriding process-level environment variables
_env_path = find_dotenv(usecwd=True) or (BASE_DIR / ".env")
if Path(_env_path).exists():
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
_default_repo_path = _default_data_dir / "repositories" / "requests"
_default_db_path = _default_data_dir / "repomind_knowledge.db"

_ACTIVE_REPO = {
    "full_name": os.getenv("REPOMIND_REPO", "psf/requests"),
    "path": Path(os.getenv("REPOMIND_REPO_PATH", str(_default_repo_path))).resolve(),
}


def get_repo_full_name() -> str:
    return _ACTIVE_REPO["full_name"]


def get_repo_root() -> Path:
    return _ACTIVE_REPO["path"]


def set_active_repo(full_name: str, path: Path) -> None:
    _ACTIVE_REPO["full_name"] = full_name
    _ACTIVE_REPO["path"] = path.resolve()
    import sys
    this_mod = sys.modules.get(__name__)
    if this_mod:
        this_mod.REPO_FULL_NAME = full_name
        this_mod.REPO_ROOT = path.resolve()
        this_mod.REPO_PATH = path.resolve()
        this_mod.REPO_URL = f"https://github.com/{full_name}.git"


REPO_FULL_NAME = get_repo_full_name()
REPO_URL = f"https://github.com/{REPO_FULL_NAME}.git"
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
CONTEXT7_API_KEY = os.getenv("CONTEXT7_API_KEY", "")

# Supabase Auth & Chat History
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
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

# LLM Models
PRIMARY_MODEL = os.getenv("REPOMIND_PRIMARY_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
FAST_MODEL = os.getenv("REPOMIND_FAST_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")


def create_chat_nvidia(model_name: str, max_tokens: int = 8192, temperature: float = 0.0):
    """Instantiate a ChatNVIDIA client with safe fallback if dependencies or key are missing."""
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    return ChatNVIDIA(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        max_completion_tokens=max_tokens,
    )


# Lazily or direct instantiated clients
try:
    llm = create_chat_nvidia(PRIMARY_MODEL, max_tokens=8192, temperature=0.0)
    fast_llm = create_chat_nvidia(FAST_MODEL, max_tokens=8192, temperature=0.0)
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
