"""
Tests for app.config.
"""

from pathlib import Path
from app.config import (
    BASE_DIR,
    REPO_ROOT,
    DB_PATH,
    IGNORED_DIRS,
    EXT_CONFIG,
    RATE_CFG,
    MAX_QUERY_CHARS,
    get_masked_key,
)


def test_config_paths_and_defaults():
    assert isinstance(BASE_DIR, Path)
    assert isinstance(REPO_ROOT, Path)
    assert isinstance(DB_PATH, str)
    assert MAX_QUERY_CHARS == 2000
    assert ".git" in IGNORED_DIRS
    assert "__pycache__" in IGNORED_DIRS


def test_config_external_and_rate_limits():
    assert EXT_CONFIG.max_exa_calls_per_run == 2
    assert EXT_CONFIG.max_context7_calls_per_run == 2
    assert EXT_CONFIG.max_firecrawl_pages_per_run == 5
    assert RATE_CFG.max_queries_per_minute == 6
    assert RATE_CFG.max_queries_per_day == 40
    assert RATE_CFG.daily_exa_usd_cap == 0.25


def test_get_masked_key():
    assert get_masked_key("") == "<not-set>"
    assert get_masked_key("short") == "***"
    assert get_masked_key("nvapi-1234567890abcdef") == "nvap...cdef"
