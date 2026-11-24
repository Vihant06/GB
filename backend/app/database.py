"""
RepoMind SQLite persistence layer.
Handles SQLite connection pooling/context, schema initialization, WAL mode,
FTS5 documentation search, and concurrency-safe atomic rate limiting.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Literal

from app.config import DB_PATH, RATE_CFG

_SCHEMA_LOCK = threading.Lock()
_INITIALIZED_DBS: set[str] = set()


def _utc_day_str(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def connect_sqlite(db_path: str | None = None, timeout: float = 5.0) -> sqlite3.Connection:
    """
    Open a SQLite connection configured for concurrent WAL operation.
    Enforces busy_timeout and WAL journal mode.
    """
    target_db = db_path or DB_PATH
    Path(target_db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target_db, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)};")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str | None = None, force: bool = False) -> None:
    """
    Idempotent schema initialization. Runs only once per database file unless force=True.
    """
    target_db = str(Path(db_path or DB_PATH).resolve())
    with _SCHEMA_LOCK:
        if not force and target_db in _INITIALIZED_DBS:
            return

        with contextlib.closing(connect_sqlite(target_db)) as conn:
            with conn:
                # Schema version table for lightweight migration tracking
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at REAL NOT NULL
                    )
                """)

                # External issues and GitHub Actions run logs
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS external_issues (
                        external_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        title TEXT NOT NULL,
                        details TEXT NOT NULL
                    )
                """)

                # Documentation cache
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS external_docs_cache (
                        cache_key TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        fetched_at INTEGER NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_docs_cache_source 
                    ON external_docs_cache(source, fetched_at)
                """)

                # FTS5 Full Text Search index for documentation chunks
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS external_doc_chunks_fts USING fts5(
                        url,
                        section_heading,
                        chunk_text
                    )
                """)

                # FTS5 Full Text Search index for local repository files & AST symbols
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS repo_symbols_fts USING fts5(
                        repo_name,
                        file_path,
                        symbols_text
                    )
                """)

                # Rate limiting and API cost ledger
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rate_limit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        actor_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        service TEXT NOT NULL,
                        units INTEGER NOT NULL,
                        cost_usd REAL NOT NULL,
                        created_at REAL NOT NULL,
                        day_utc TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rl_actor_time 
                    ON rate_limit_events(actor_id, created_at)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rl_day_service 
                    ON rate_limit_events(day_utc, service)
                """)

                # Purge obsolete synthetic mock records from initial testing
                conn.execute(
                    "DELETE FROM external_issues WHERE source = 'JIRA' OR external_id IN ('REQ-412', 'GHA-RUN-908')"
                )

                conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, ?)",
                    (time.time(),),
                )

        _INITIALIZED_DBS.add(target_db)


def check_and_record_query_rate_limit(
    actor_id: str = "default_user",
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Enforce per-minute and per-day query sliding windows in SQLite atomically.
    Uses IMMEDIATE transactions to serialize check-and-record across multiple processes/workers.
    """
    init_db(db_path)
    now = time.time()
    today = _utc_day_str(now)

    with contextlib.closing(connect_sqlite(db_path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            min_count = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_events WHERE actor_id = ? AND event_type = 'query' AND created_at >= ?",
                (actor_id, now - 60.0),
            ).fetchone()[0]

            day_count = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_events WHERE actor_id = ? AND event_type = 'query' AND day_utc = ?",
                (actor_id, today),
            ).fetchone()[0]

            if min_count >= RATE_CFG.max_queries_per_minute:
                conn.execute("ROLLBACK")
                return False, f"Rate limit exceeded: max {RATE_CFG.max_queries_per_minute} queries/minute for '{actor_id}'."
            if day_count >= RATE_CFG.max_queries_per_day:
                conn.execute("ROLLBACK")
                return False, f"Daily quota exceeded: max {RATE_CFG.max_queries_per_day} queries/day for '{actor_id}'."

            conn.execute(
                "INSERT INTO rate_limit_events (actor_id, event_type, service, units, cost_usd, created_at, day_utc) "
                "VALUES (?, 'query', 'orchestrator', 1, 0.0, ?, ?)",
                (actor_id, now, today),
            )
            conn.execute("COMMIT")
            return True, ""
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise e


def record_external_usage_and_check_cap(
    service: Literal["exa", "firecrawl", "context7"],
    units: int = 1,
    cost_usd: float = 0.0,
    actor_id: str = "default_user",
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Persist external API usage and enforce daily spend/credit caps atomically across workers.
    """
    init_db(db_path)
    now = time.time()
    today = _utc_day_str(now)

    with contextlib.closing(connect_sqlite(db_path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if service == "exa":
                spent_today = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) FROM rate_limit_events WHERE service = 'exa' AND day_utc = ?",
                    (today,),
                ).fetchone()[0]
                if float(spent_today) + float(cost_usd) > RATE_CFG.daily_exa_usd_cap:
                    conn.execute("ROLLBACK")
                    return False, f"Daily Exa spend cap (${RATE_CFG.daily_exa_usd_cap:.2f}) reached."

            elif service == "firecrawl":
                pages_today = conn.execute(
                    "SELECT COALESCE(SUM(units), 0) FROM rate_limit_events WHERE service = 'firecrawl' AND day_utc = ?",
                    (today,),
                ).fetchone()[0]
                if int(pages_today) + int(units) > RATE_CFG.daily_firecrawl_pages_cap:
                    conn.execute("ROLLBACK")
                    return False, f"Daily Firecrawl cap ({RATE_CFG.daily_firecrawl_pages_cap} pages) reached."

            elif service == "context7":
                calls_today = conn.execute(
                    "SELECT COALESCE(SUM(units), 0) FROM rate_limit_events WHERE service = 'context7' AND day_utc = ?",
                    (today,),
                ).fetchone()[0]
                if int(calls_today) + int(units) > RATE_CFG.daily_context7_calls_cap:
                    conn.execute("ROLLBACK")
                    return False, f"Daily Context7 cap ({RATE_CFG.daily_context7_calls_cap} calls) reached."

            conn.execute(
                "INSERT INTO rate_limit_events (actor_id, event_type, service, units, cost_usd, created_at, day_utc) "
                "VALUES (?, 'api_call', ?, ?, ?, ?, ?)",
                (actor_id, service, int(units), float(cost_usd), now, today),
            )
            conn.execute("COMMIT")
            return True, ""
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise e


def cache_get(cache_key: str, ttl_sec: int, db_path: str | None = None) -> dict | None:
    """Retrieve an item from external_docs_cache if present and not expired."""
    init_db(db_path)
    with contextlib.closing(connect_sqlite(db_path)) as conn:
        row = conn.execute(
            "SELECT source, url, title, content, fetched_at FROM external_docs_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()

    if not row:
        return None

    fetched_at = int(row[4])
    if int(time.time()) - fetched_at > ttl_sec:
        return None

    return {
        "source": row[0],
        "url": row[1],
        "title": row[2],
        "content": row[3],
        "fetched_at": fetched_at,
    }


def cache_put(
    cache_key: str,
    source: str,
    url: str,
    title: str,
    content: str,
    db_path: str | None = None,
) -> None:
    """Store or update a cached document entry."""
    init_db(db_path)
    with contextlib.closing(connect_sqlite(db_path)) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO external_docs_cache (cache_key, source, url, title, content, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    source=excluded.source,
                    url=excluded.url,
                    title=excluded.title,
                    content=excluded.content,
                    fetched_at=excluded.fetched_at
                """,
                (cache_key, source, url, title, content, int(time.time())),
            )


def _chunk_markdown_by_headings(markdown: str, max_chunk_chars: int = 1200) -> list[tuple[str, str]]:
    """Split Markdown into (section_heading, chunk_text) pairs for SQLite FTS5 indexing."""
    sections = []
    heading = "Overview"
    buf = []

    def _flush():
        text = "\n".join(buf).strip()
        if not text:
            return
        for i in range(0, len(text), max_chunk_chars):
            sections.append((heading, text[i : i + max_chunk_chars]))

    for line in markdown.splitlines():
        m = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if m:
            _flush()
            heading = m.group(2).strip()
            buf = [line]
        else:
            buf.append(line)
    _flush()
    return sections or [("Overview", markdown[:max_chunk_chars])]


def index_markdown_chunks(url: str, markdown: str, db_path: str | None = None) -> int:
    """Replace existing FTS5 chunks for a URL and insert freshly split heading chunks."""
    init_db(db_path)
    chunks = _chunk_markdown_by_headings(markdown)
    with contextlib.closing(connect_sqlite(db_path)) as conn:
        with conn:
            conn.execute("DELETE FROM external_doc_chunks_fts WHERE url = ?", (url,))
            conn.executemany(
                "INSERT INTO external_doc_chunks_fts (url, section_heading, chunk_text) VALUES (?, ?, ?)",
                [(url, h, c) for h, c in chunks],
            )
    return len(chunks)


def query_doc_chunks_fts(
    query: str,
    urls: list[str] | None = None,
    limit: int = 4,
    db_path: str | None = None,
) -> list[tuple[str, str, str]]:
    """Safe BM25/FTS5 query over cached Firecrawl documentation chunks."""
    init_db(db_path)
    tokens = [
        t for t in re.findall(r"[a-zA-Z0-9_]{2,}", query)
        if t.upper() not in {"AND", "OR", "NOT", "NEAR"}
    ]
    rows = []

    with contextlib.closing(connect_sqlite(db_path)) as conn:
        if tokens:
            fts_expr = " OR ".join(f'"{t}"' for t in tokens[:10])
            try:
                if urls:
                    placeholders = ",".join("?" for _ in urls)
                    rows = conn.execute(
                        f"SELECT url, section_heading, chunk_text FROM external_doc_chunks_fts "
                        f"WHERE external_doc_chunks_fts MATCH ? AND url IN ({placeholders}) "
                        f"ORDER BY bm25(external_doc_chunks_fts) LIMIT ?",
                        [fts_expr, *urls, limit],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT url, section_heading, chunk_text FROM external_doc_chunks_fts "
                        "WHERE external_doc_chunks_fts MATCH ? ORDER BY bm25(external_doc_chunks_fts) LIMIT ?",
                        [fts_expr, limit],
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []

        if not rows and urls:
            placeholders = ",".join("?" for _ in urls)
            rows = conn.execute(
                f"SELECT url, section_heading, chunk_text FROM external_doc_chunks_fts "
                f"WHERE url IN ({placeholders}) LIMIT ?",
                [*urls, limit],
            ).fetchall()

    return rows


def upsert_external_records(records: list[dict], db_path: str | None = None) -> None:
    """Upsert GitHub issues or run failure summaries into SQLite."""
    if not records:
        return
    init_db(db_path)
    with contextlib.closing(connect_sqlite(db_path)) as conn:
        with conn:
            conn.executemany(
                """
                INSERT INTO external_issues (external_id, source, status, title, details)
                VALUES (:id, :source, :status, :title, :details)
                ON CONFLICT(external_id) DO UPDATE SET
                    status=excluded.status,
                    title=excluded.title,
                    details=excluded.details
                """,
                records,
            )


def index_repo_symbols(
    repo_name: str,
    entries: list[tuple[str, str]],
    db_path: str | None = None,
) -> int:
    """Replace FTS5 symbol index for a repository with (file_path, symbols_text) entries."""
    if not repo_name:
        return 0
    init_db(db_path)
    with contextlib.closing(connect_sqlite(db_path)) as conn:
        with conn:
            conn.execute("DELETE FROM repo_symbols_fts WHERE repo_name = ?", (repo_name,))
            if entries:
                conn.executemany(
                    "INSERT INTO repo_symbols_fts (repo_name, file_path, symbols_text) VALUES (?, ?, ?)",
                    [(repo_name, fp, sym) for fp, sym in entries],
                )
    return len(entries)


def query_repo_symbols_fts(
    repo_name: str,
    query: str,
    limit: int = 6,
    db_path: str | None = None,
) -> list[tuple[str, str]]:
    """Fast BM25/FTS5 lookup of candidate repository files and symbols for a query."""
    if not repo_name or not query:
        return []
    init_db(db_path)
    tokens = [
        t for t in re.findall(r"[a-zA-Z0-9_]{2,}", query)
        if t.upper() not in {"AND", "OR", "NOT", "NEAR"}
    ]
    if not tokens:
        return []
    fts_expr = " OR ".join(f'"{t}"' for t in tokens[:10])
    with contextlib.closing(connect_sqlite(db_path)) as conn:
        try:
            return conn.execute(
                "SELECT file_path, symbols_text FROM repo_symbols_fts "
                "WHERE repo_symbols_fts MATCH ? AND repo_name = ? "
                "ORDER BY bm25(repo_symbols_fts) LIMIT ?",
                [fts_expr, repo_name, limit],
            ).fetchall()
        except sqlite3.OperationalError:
            return []
