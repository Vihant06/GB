"""
RepoMind external technical documentation tools.
Integrates Context7 REST API v3, Exa Search API, and Firecrawl v2 with SQLite FTS5 caching.
All external content is wrapped in untrusted delimiters to mitigate prompt injection.
Budgets and rate caps are enforced on every invocation.
"""

from __future__ import annotations

import logging
import json
import os
import time
from typing import Literal
from urllib.parse import urlsplit

from langchain_core.tools import tool
import requests

from app.budget import get_current_budget
from app.config import (
    CONTEXT7_API_KEY,
    EXA_API_KEY,
    EXT_CONFIG,
    FIRECRAWL_API_KEY,
)
from app.database import (
    cache_get,
    cache_put,
    index_markdown_chunks,
    query_doc_chunks_fts,
)
from app.guardrails import redact_secrets, validate_external_url, wrap_untrusted

logger = logging.getLogger(__name__)


@tool
def context7_search_docs(
    query: str,
    library: str = "",
    version: str = "",
    language: str = "Python",
) -> str:
    """Retrieve official, version-aware library documentation and code examples via Context7 REST API v3."""
    api_key = (CONTEXT7_API_KEY or os.getenv("CONTEXT7_API_KEY", "")).strip()
    if not api_key:
        return "[SKIPPED] CONTEXT7_API_KEY is not configured. Use read_project_dependencies or local AST/Firecrawl instead."

    lib_clean = library.strip()
    ver_clean = version.strip()
    cite_tag = f"CONTEXT7:{lib_clean or 'auto'}@{ver_clean or 'latest'}"
    cache_key = f"context7|{query.lower().strip()}|{lib_clean.lower()}|{ver_clean.lower()}|{language.lower()}"

    budget = get_current_budget()
    cached = cache_get(cache_key, EXT_CONFIG.search_cache_ttl_sec)
    if cached:
        budget.record_cache_hit("context7")
        return wrap_untrusted(f"{cite_tag} (cached)", cached["content"])

    allowed, reason = budget.try_consume("context7", units=1)
    if not allowed:
        return f"[BUDGET_LIMIT] {reason}"

    params: dict = {"query": query, "type": "json"}
    if lib_clean:
        params["library"] = [lib_clean]
        if ver_clean:
            params["version"] = ver_clean
    if language.strip():
        params["language"] = language.strip()

    headers = {"Authorization": f"Bearer {api_key}"}

    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://context7.com/api/v3/search",
                headers=headers,
                params=params,
                timeout=EXT_CONFIG.http_timeout,
            )
        except requests.RequestException as e:
            if attempt == 2:
                return f"[CONTEXT7_ERROR] Network error: {e}"
            time.sleep(1.5 * (attempt + 1))
            continue

        if resp.status_code == 404:
            return f"[CONTEXT7_EMPTY] No official documentation matched '{query}' ({lib_clean or 'auto'})."
        if resp.status_code in {429, 503}:
            if attempt == 2:
                return f"[CONTEXT7_UNAVAILABLE] Temporary status {resp.status_code}; retry later."
            time.sleep(1.5 * (2**attempt))
            continue
        break

    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "N/A"
        body = resp.text[:220] if resp is not None else ""
        return f"[CONTEXT7_ERROR] HTTP {code}: {body}"

    payload = resp.json()
    blocks = []
    for snip in payload.get("codeSnippets", [])[:3]:
        lib_id = snip.get("libraryId") or lib_clean or "unknown"
        title = snip.get("codeTitle") or "Code Example"
        code_parts = [c.get("code", "")[:500] for c in snip.get("codeList", [])[:2]]
        blocks.append(f"[{cite_tag}] ({lib_id}) {title}\n" + "\n".join(code_parts))

    for info in payload.get("infoSnippets", [])[:3]:
        content = (info.get("content") or "")[:600]
        src = info.get("pageId") or cite_tag
        blocks.append(f"[{cite_tag}] {src}\n{content}")

    if not blocks:
        return f"[CONTEXT7_EMPTY] No snippets returned for '{query}'."

    formatted = "\n\n---\n\n".join(blocks)
    cache_put(
        cache_key,
        "CONTEXT7",
        cite_tag,
        f"{lib_clean or 'auto'}@{ver_clean or 'latest'}",
        formatted,
    )
    return wrap_untrusted(cite_tag, formatted)


@tool
def exa_search_technical(
    query: str,
    include_domains: str = "",
    search_mode: Literal["lite", "auto"] = "lite",
    num_results: int = 4,
) -> str:
    """Semantic web search via Exa for stack traces, engineering discussions, and upstream regressions."""
    api_key = (EXA_API_KEY or os.getenv("EXA_API_KEY", "")).strip()
    if not api_key:
        return "[SKIPPED] EXA_API_KEY is not configured."

    domains = [d.strip() for d in include_domains.split(",") if d.strip()]
    n = min(max(1, num_results), EXT_CONFIG.exa_max_results)
    cache_key = f"exa|{search_mode}|{query.lower().strip()}|{','.join(sorted(domains))}|{n}"

    budget = get_current_budget()
    cached = cache_get(cache_key, EXT_CONFIG.search_cache_ttl_sec)
    if cached:
        budget.record_cache_hit("exa")
        return wrap_untrusted("EXA (cached)", cached["content"])

    allowed, reason = budget.try_consume("exa", units=1)
    if not allowed:
        return f"[BUDGET_LIMIT] {reason}"

    body: dict = {
        "query": query,
        "type": search_mode,
        "numResults": n,
        "contents": {"highlights": True},
    }
    if domains:
        body["includeDomains"] = domains

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    resp = None
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.exa.ai/search",
                headers=headers,
                json=body,
                timeout=EXT_CONFIG.http_timeout,
            )
        except requests.RequestException as e:
            if attempt == 2:
                return f"[EXA_ERROR] Network error: {e}"
            time.sleep(1.0 * (attempt + 1))
            continue

        if resp.status_code == 402:
            budget.exa_disabled = True
            return "[EXA_QUOTA_EXCEEDED] HTTP 402 Payment Required; Exa disabled for the rest of this run."
        if resp.status_code in {429, 503}:
            if attempt == 2:
                return f"[EXA_RATE_LIMITED] HTTP {resp.status_code} after retries."
            time.sleep(1.5 * (2**attempt))
            continue
        break

    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "N/A"
        txt = resp.text[:250] if resp is not None else ""
        return f"[EXA_ERROR] HTTP {code}: {txt}"

    data = resp.json()
    cost = (data.get("costDollars") or {}).get("total", 0.0)
    if isinstance(cost, (int, float)):
        budget.total_exa_usd += float(cost)

    results = data.get("results", [])
    if not results:
        return f"[EXA_EMPTY] No Exa results found for '{query}'."

    blocks = []
    for r in results:
        url = r.get("url", "")
        title = r.get("title") or "Untitled"
        highlights = r.get("highlights") or []
        snippet = "\n".join(highlights)[:650] if highlights else (r.get("text") or "")[:650]
        blocks.append(f"[EXA:{url}] {title}\n{snippet}")

    formatted = "\n\n---\n\n".join(blocks)
    cache_put(cache_key, "EXA", results[0].get("url", "EXA:search"), query[:120], formatted)
    return wrap_untrusted(f"EXA mode={search_mode} cost=${cost:.4f}", formatted)


def firecrawl_search_public(query: str) -> list[dict[str, str]]:
    """Find public technical sources with one bounded Firecrawl search request."""
    api_key = (FIRECRAWL_API_KEY or os.getenv("FIRECRAWL_API_KEY", "")).strip()
    if not api_key or not query:
        return []
    budget = get_current_budget()
    cache_key = f"firecrawl-search|{query.casefold()}"
    cached = cache_get(cache_key, EXT_CONFIG.search_cache_ttl_sec)
    if cached:
        try:
            sources = json.loads(cached["content"])
        except (TypeError, ValueError):
            sources = None
        if isinstance(sources, list):
            budget.record_cache_hit("firecrawl")
            return sources

    if not budget.try_firecrawl_slot():
        return []
    allowed, _ = budget.try_consume("firecrawl", 1)
    if not allowed:
        return []
    try:
        response = requests.post(
            "https://api.firecrawl.dev/v2/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "sources": [{"type": "web"}], "limit": 3, "timeout": 6000},
            timeout=8,
            allow_redirects=False,
        )
        if response.status_code != 200:
            logger.info("Firecrawl public search returned HTTP %s", response.status_code)
            return []
        payload = response.json()
        rows = payload.get("data", {}).get("web", []) if isinstance(payload, dict) else []
    except (requests.RequestException, AttributeError, ValueError):
        return []

    sources = []
    for row in (rows if isinstance(rows, list) else [])[:3]:
        if not isinstance(row, dict):
            continue
        source_url, description = row.get("url"), row.get("description")
        if not isinstance(source_url, str) or not isinstance(description, str) or not description.strip():
            continue
        if any(char in source_url for char in '\r\n \t<>()[]'):
            continue
        try:
            parsed = urlsplit(source_url)
        except ValueError:
            continue
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            continue
        title = row.get("title") if isinstance(row.get("title"), str) else parsed.hostname
        sources.append({
            "url": source_url,
            "title": redact_secrets(title[:120])[0],
            "description": redact_secrets(description[:900])[0],
        })
    if sources:
        cache_put(cache_key, "FIRECRAWL_SEARCH", sources[0]["url"], "Public web search", json.dumps(sources))
    return sources


@tool
def firecrawl_ingest_docs(
    url: str,
    query: str,
    crawl_subpages: bool = False,
    max_pages: int = 3,
) -> str:
    """Ingest external documentation via Firecrawl v2 (/v2/map + /v2/scrape) into SQLite FTS5."""
    api_key = (FIRECRAWL_API_KEY or os.getenv("FIRECRAWL_API_KEY", "")).strip()
    if not api_key:
        return "[SKIPPED] FIRECRAWL_API_KEY is not configured."

    ok, err = validate_external_url(url)
    if not ok:
        return f"[SECURITY_BLOCKED] {err}"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    cap = min(max(1, max_pages), EXT_CONFIG.firecrawl_max_pages)
    target_urls = [url]
    budget = get_current_budget()

    if crawl_subpages and cap > 1:
        allowed, _ = budget.try_consume("firecrawl", 1)
        if allowed:
            budget.pace_firecrawl()
            try:
                map_resp = requests.post(
                    "https://api.firecrawl.dev/v2/map",
                    headers=headers,
                    json={"url": url, "search": query, "limit": cap * 3},
                    timeout=EXT_CONFIG.firecrawl_timeout,
                )
                if map_resp.status_code == 200:
                    raw_links = map_resp.json().get("links", [])
                    discovered = []
                    for entry in raw_links:
                        candidate = entry.get("url") if isinstance(entry, dict) else entry
                        if isinstance(candidate, str):
                            valid_u, _ = validate_external_url(candidate)
                            if valid_u and candidate not in discovered:
                                discovered.append(candidate)
                    if discovered:
                        target_urls = discovered[:cap]
            except requests.RequestException:
                pass

    ingested_urls = []
    notes = []

    for page_url in target_urls[:cap]:
        cached = cache_get(f"firecrawl|{page_url}", EXT_CONFIG.docs_cache_ttl_sec)
        if cached:
            budget.record_cache_hit("firecrawl")
            ingested_urls.append(page_url)
            notes.append(f"cache_hit:{page_url}")
            continue

        allowed, reason = budget.try_consume("firecrawl", 1)
        if not allowed:
            notes.append(reason)
            break

        budget.pace_firecrawl()
        try:
            scrape_resp = requests.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers=headers,
                json={"url": page_url, "formats": ["markdown"], "onlyMainContent": True},
                timeout=EXT_CONFIG.firecrawl_timeout,
            )
        except requests.RequestException as e:
            notes.append(f"network_error:{page_url} ({e})")
            continue

        if scrape_resp.status_code in {402, 429}:
            notes.append(f"HTTP {scrape_resp.status_code} on {page_url}")
            break
        if scrape_resp.status_code != 200:
            notes.append(f"HTTP {scrape_resp.status_code} on {page_url}")
            continue

        data = scrape_resp.json().get("data") or {}
        md = (data.get("markdown") or "").strip()
        title = (data.get("metadata") or {}).get("title") or page_url
        if md:
            cache_put(f"firecrawl|{page_url}", "FIRECRAWL", page_url, title, md)
            index_markdown_chunks(page_url, md)
            ingested_urls.append(page_url)
            notes.append(f"scraped:{page_url}")

    if not ingested_urls:
        return f"[FIRECRAWL_EMPTY] Could not ingest {url}. Notes: {'; '.join(notes)}"

    chunks = query_doc_chunks_fts(query, urls=ingested_urls, limit=4)
    blocks = [
        f"[FIRECRAWL:{u}] ## {heading}\n{chunk_text[:700]}"
        for u, heading, chunk_text in chunks
    ]
    return wrap_untrusted(f"FIRECRAWL pages={len(ingested_urls)}", "\n\n---\n\n".join(blocks))
