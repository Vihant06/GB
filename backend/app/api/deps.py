"""Authenticate RepoMind requests and authorize repository access."""

from __future__ import annotations

import os
from uuid import UUID

import requests
from fastapi import Depends, Header, HTTPException, status

from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL
from app.database import check_and_record_query_rate_limit

_DEFAULT_DEMO_REPOS = "pallets/flask,fastapi/fastapi,encode/starlette,tiangolo/sqlmodel"


def demo_repositories() -> list[str]:
    return list(dict.fromkeys(
        value.strip().lower()
        for value in os.getenv("REPOMIND_DEMO_REPOS", _DEFAULT_DEMO_REPOS).split(",")
        if value.strip()
    ))


def get_actor_identity(authorization: str | None = Header(default=None)) -> str:
    """Resolve the account through Supabase Auth before allowing workspace operations."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 8192:
        raise HTTPException(status_code=401, detail="Sign in to use RepoMind.")
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(status_code=503, detail="RepoMind authentication is not configured on the backend.")

    try:
        response = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/auth/v1/user",
            headers={"apikey": SUPABASE_PUBLISHABLE_KEY, "Authorization": f"Bearer {token}"},
            timeout=5,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Could not reach the sign-in service.") from exc

    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Your session has expired or is invalid. Sign in again.")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="The sign-in service could not verify your session.")
    try:
        user = response.json()
        if user.get("role") != "authenticated" or user.get("is_anonymous"):
            raise ValueError("A permanent account is required")
        return str(UUID(user["id"]))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="A signed-in account is required.") from exc


def enforce_rate_limit(actor_id: str = Depends(get_actor_identity)) -> str:
    """
    FastAPI dependency that enforces per-minute and per-day rate limits in SQLite atomically.
    Raises HTTP 429 Too Many Requests if quota is exceeded.
    """
    allowed, reason = check_and_record_query_rate_limit(actor_id=actor_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason,
        )
    return actor_id
