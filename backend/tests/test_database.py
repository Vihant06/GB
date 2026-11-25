"""
Tests for app.database.
"""

import tempfile
import time
from pathlib import Path

from app.database import (
    connect_sqlite,
    init_db,
    cache_get,
    cache_put,
    index_markdown_chunks,
    index_repo_symbols,
    query_doc_chunks_fts,
    query_repo_symbols_fts,
    check_and_record_query_rate_limit,
    record_external_usage_and_check_cap,
    upsert_external_records,
)


def test_sqlite_wal_and_busy_timeout():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        init_db(db_path)
        conn = connect_sqlite(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert mode.lower() == "wal"
            timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            assert timeout >= 5000
        finally:
            conn.close()


def test_language_aware_doc_ranking():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        entries = [
            ("docs/de/dependencies-with-yield.md", "dependencies yield cleanup"),
            ("docs/en/dependencies-with-yield.md", "dependencies yield cleanup"),
            ("fastapi/dependencies/utils.py", "def solve_dependencies yield cleanup"),
            ("tests/test_yield_dep.py", "def test_yield_cleanup"),
        ]
        index_repo_symbols("test/repo", entries, db_path=db_path)

        # English query: production > English doc > test, and German doc pushed after
        en_hits = [fp for fp, _ in query_repo_symbols_fts("test/repo", "dependencies yield cleanup", limit=3, db_path=db_path)]
        assert en_hits[0] == "fastapi/dependencies/utils.py"
        assert "docs/en/dependencies-with-yield.md" in en_hits
        assert "docs/de/dependencies-with-yield.md" not in en_hits

        # Explicit German request keeps docs/de searchable
        de_hits = [fp for fp, _ in query_repo_symbols_fts("test/repo", "German docs/de dependencies yield", limit=4, db_path=db_path)]
        assert "docs/de/dependencies-with-yield.md" in de_hits


def test_cache_put_get_and_ttl():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        cache_put("test|key1", "test_src", "https://example.com/doc", "Doc 1", "Sample content", db_path=db_path)
        
        hit = cache_get("test|key1", ttl_sec=60, db_path=db_path)
        assert hit is not None
        assert hit["title"] == "Doc 1"
        assert hit["content"] == "Sample content"

        # Test expired TTL
        miss = cache_get("test|key1", ttl_sec=-1, db_path=db_path)
        assert miss is None


def test_fts5_indexing_and_query():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        md = "# Section One\nThis explains authentication handling.\n\n# Section Two\nThis describes rate limits."
        num_chunks = index_markdown_chunks("https://example.com/api", md, db_path=db_path)
        assert num_chunks == 2

        results = query_doc_chunks_fts("authentication", db_path=db_path)
        assert len(results) > 0
        assert results[0][1] == "Section One"


def test_atomic_rate_limits():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        actor = "test_user_1"

        # Should allow first 6 queries
        for _ in range(6):
            allowed, msg = check_and_record_query_rate_limit(actor, db_path=db_path)
            assert allowed, f"Should be allowed but got: {msg}"

        # 7th query within 60s should be rejected (max 6/min)
        allowed, msg = check_and_record_query_rate_limit(actor, db_path=db_path)
        assert not allowed
        assert "Rate limit exceeded" in msg


def test_daily_external_spend_caps():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        
        # Test Exa spend cap ($0.25)
        allowed, msg = record_external_usage_and_check_cap("exa", units=1, cost_usd=0.20, db_path=db_path)
        assert allowed
        allowed, msg = record_external_usage_and_check_cap("exa", units=1, cost_usd=0.10, db_path=db_path)
        assert not allowed
        assert "Daily Exa spend cap" in msg


def test_upsert_external_records():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        records = [
            {"id": "GH-1", "source": "github_issues", "status": "open", "title": "Bug", "details": "Desc"}
        ]
        upsert_external_records(records, db_path=db_path)
        conn = connect_sqlite(db_path)
        try:
            row = conn.execute("SELECT title, status FROM external_issues WHERE external_id = 'GH-1'").fetchone()
            assert row[0] == "Bug"
            assert row[1] == "open"
        finally:
            conn.close()


def test_language_aware_doc_ranking():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        entries = [
            ("docs/de/dependencies-with-yield.md", "dependencies yield cleanup"),
            ("docs/en/dependencies-with-yield.md", "dependencies yield cleanup"),
            ("fastapi/dependencies/utils.py", "def solve_dependencies yield cleanup"),
            ("tests/test_yield_dep.py", "def test_yield_cleanup"),
        ]
        index_repo_symbols("test/repo", entries, db_path=db_path)

        # English query: production > English doc > test, and German doc pushed after
        en_hits = [fp for fp, _ in query_repo_symbols_fts("test/repo", "dependencies yield cleanup", limit=3, db_path=db_path)]
        assert en_hits[0] == "fastapi/dependencies/utils.py"
        assert "docs/en/dependencies-with-yield.md" in en_hits
        assert "docs/de/dependencies-with-yield.md" not in en_hits

        # Explicit German request keeps docs/de searchable
        de_hits = [fp for fp, _ in query_repo_symbols_fts("test/repo", "German docs/de dependencies yield", limit=4, db_path=db_path)]
        assert "docs/de/dependencies-with-yield.md" in de_hits
