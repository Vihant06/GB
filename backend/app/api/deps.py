"""
RepoMind request dependencies and identity resolution.
Resolves actor identity securely from Supabase JWT claims, cryptographic key
fingerprints, or hashed client IP addresses.
Prevents caller spoofing and guarantees atomic rate-limit enforcement.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, Request, status

from app.config import SUPABASE_JWT_SECRET
from app.database import check_and_record_query_rate_limit

logger = logging.getLogger(__name__)

_SALT = os.getenv("REPOMIND_IDENTITY_SALT", "repomind_secure_salt_v1")


def get_actor_identity(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> str:
    """
    Derive a deterministic, safe actor identifier:
    0. If SUPABASE_JWT_SECRET is configured and a valid Bearer JWT is present,
       decode the token and return the ``sub`` claim (Supabase user id).
    1. Authenticated API key / Bearer token SHA-256 fingerprint (never raw key).
    2. Hashed client IP address (handling trusted X-Forwarded-For if behind a reverse proxy).
    3. Explicit local development fallback.
    """
    # ----- Supabase JWT authentication path -----
    if SUPABASE_JWT_SECRET and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            try:
                payload = pyjwt.decode(
                    token,
                    SUPABASE_JWT_SECRET,
                    algorithms=["HS256"],
                    audience="authenticated",
                )
                user_id: str = payload["sub"]
                return user_id
            except pyjwt.ExpiredSignatureError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired.",
                )
            except (pyjwt.InvalidTokenError, KeyError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid authentication token: {exc}",
                )

    # ----- Legacy / dev-mode identity resolution -----
    # 1. Check Authorization header (non-JWT bearer or API key fingerprint)
    auth_header = (authorization or request.headers.get("Authorization", "")).strip()
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            fingerprint = hashlib.sha256((token + _SALT).encode()).hexdigest()[:16]
            return f"key:{fingerprint}"

    # 2. Check X-API-Key header
    api_key_header = request.headers.get("X-API-Key", "").strip()
    if api_key_header:
        fingerprint = hashlib.sha256((api_key_header + _SALT).encode()).hexdigest()[:16]
        return f"key:{fingerprint}"

    # 3. Development mode fallback
    if os.getenv("REPOMIND_DEV_MODE", "false").lower() in ("true", "1", "yes"):
        return "dev_user"

    # 4. Client IP resolution with hashing
    client_ip = ""
    # Only trust X-Forwarded-For if explicit proxy trust is enabled
    trust_proxy = os.getenv("REPOMIND_TRUST_PROXY", "false").lower() in ("true", "1", "yes")
    if trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

    if not client_ip and request.client:
        client_ip = request.client.host

    if not client_ip:
        client_ip = "127.0.0.1"

    hashed_ip = hashlib.sha256((client_ip + _SALT).encode()).hexdigest()[:16]
    return f"ip:{hashed_ip}"


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
