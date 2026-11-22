"""
RepoMind GitHub synchronization tools.
Syncs live open GitHub Issues and GitHub Actions workflow run failures into SQLite.
Provides local fast BM25/keyword retrieval over synced records.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any

from langchain_core.tools import tool
import requests

from app.config import EXT_CONFIG, GITHUB_TOKEN, REPO_FULL_NAME, get_repo_full_name
from app.database import connect_sqlite, upsert_external_records

logger = logging.getLogger(__name__)


def get_gh_headers() -> dict[str, str]:
    """Prepare authentication and JSON headers for GitHub REST API v3."""
    headers = {"Accept": "application/vnd.github+json"}
    token = (GITHUB_TOKEN or os.getenv("GITHUB_TOKEN", "")).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def sync_github_issues(
    repo_full_name: str | None = None,
    max_issues: int = 15,
    db_path: str | None = None,
) -> int:
    """Sync live open GitHub Issues (excluding pull requests) into SQLite."""
    target_repo = repo_full_name or get_repo_full_name()
    url = f"https://api.github.com/repos/{target_repo}/issues"
    try:
        resp = requests.get(
            url,
            headers=get_gh_headers(),
            params={"state": "open", "per_page": max_issues},
            timeout=EXT_CONFIG.http_timeout,
        )
    except requests.RequestException as e:
        logger.warning("Failed to connect to GitHub Issues API for '%s': %s", target_repo, e)
        return 0

    if resp.status_code != 200:
        logger.warning(
            "GitHub Issues API returned %d for '%s': %s",
            resp.status_code,
            target_repo,
            resp.text[:200],
        )
        return 0

    records = []
    for issue in resp.json():
        if "pull_request" in issue:
            continue
        body = (issue.get("body") or "No description provided.")[:2500]
        records.append({
            "id": f"GH-{issue['number']}",
            "source": "GITHUB_ISSUE",
            "status": issue.get("state", "open").upper(),
            "title": issue.get("title", ""),
            "details": f"URL: {issue.get('html_url')}\n{body}",
        })

    upsert_external_records(records, db_path=db_path)
    logger.info("Synced %d open GitHub issues from '%s'.", len(records), target_repo)
    return len(records)


def sync_github_ci_failures(
    repo_full_name: str | None = None,
    max_runs: int = 5,
    db_path: str | None = None,
) -> int:
    """Sync recent failed GitHub Actions workflow runs into SQLite."""
    target_repo = repo_full_name or get_repo_full_name()
    url = f"https://api.github.com/repos/{target_repo}/actions/runs"
    try:
        resp = requests.get(
            url,
            headers=get_gh_headers(),
            params={"status": "failure", "per_page": max_runs},
            timeout=EXT_CONFIG.http_timeout,
        )
    except requests.RequestException as e:
        logger.warning("Failed to connect to GitHub Actions API for '%s': %s", target_repo, e)
        return 0

    if resp.status_code != 200:
        logger.warning(
            "GitHub Actions API returned %d for '%s': %s",
            resp.status_code,
            target_repo,
            resp.text[:200],
        )
        return 0

    records = []
    for run in resp.json().get("workflow_runs", []):
        run_id = f"GHA-{run['id']}"
        failed_steps = []
        try:
            jobs_resp = requests.get(
                run["jobs_url"],
                headers=get_gh_headers(),
                timeout=EXT_CONFIG.http_timeout,
            )
            if jobs_resp.status_code == 200:
                for job in jobs_resp.json().get("jobs", []):
                    if job.get("conclusion") == "failure":
                        bad = [
                            s["name"]
                            for s in job.get("steps", [])
                            if s.get("conclusion") == "failure"
                        ]
                        failed_steps.append(f"Job '{job['name']}' failed at steps: {bad}")
        except Exception as e:
            logger.debug("Failed fetching job details for run %s: %s", run_id, e)

        records.append({
            "id": run_id,
            "source": "CI_LOG",
            "status": (run.get("conclusion") or "FAILED").upper(),
            "title": f"{run.get('name')} (branch: {run.get('head_branch')}, sha: {run.get('head_sha', '')[:7]})",
            "details": "\n".join(failed_steps) or f"Workflow URL: {run.get('html_url')}",
        })

    upsert_external_records(records, db_path=db_path)
    logger.info("Synced %d failed GitHub Actions runs from '%s'.", len(records), target_repo)
    return len(records)


@tool
def search_github_issues_and_ci(query: str) -> str:
    """Search synced GitHub Issues and GitHub Actions CI failure records stored in SQLite."""
    with contextlib.closing(connect_sqlite()) as conn:
        rows = conn.execute(
            "SELECT source, external_id, status, title, details FROM external_issues"
        ).fetchall()

    if not rows:
        return "No GitHub Issues or CI failures synced in SQLite."

    tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9_-]{3,}", query)]
    scored = []
    for row in rows:
        blob = " ".join(row).lower()
        score = sum(1 for t in tokens if t in blob)
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [r for s, r in scored if s > 0][:8] or [r for _, r in scored[:6]]

    blocks = [
        f"[{source}:{ext_id} | {status}] {title}\n{details[:700]}"
        for source, ext_id, status, title, details in selected
    ]
    return "\n\n---\n\n".join(blocks)
