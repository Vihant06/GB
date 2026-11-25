"""
RepoMind Security & Guardrails module.
Provides:
- SSRF validation (HTTPS-only, public IP and DNS enforcement)
- Strict repository path sandboxing (traversal, absolute path, and symlink escape defenses)
- Prompt injection detection
- Secret redaction for credentials, API tokens, and passwords
- Deterministic citation parsing and verification ([file:Lstart-Lend])
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
import re
import socket
from typing import Tuple
from urllib.parse import urlparse

from app.config import EXT_CONFIG, get_repo_root

# Sentinel for testing patch compatibility: None means dynamically resolve via get_repo_root()
REPO_ROOT: Path | None = None

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"reveal\s+(your\s+)?(system\s+prompt|api[_\s]?key|secret|token|\.env)",
    r"\b(?:give|show|share|print|tell)\s+(?:me\s+)?(?:your\s+|the\s+)?(?:api[ _-]?keys?\s+you\s+us(?:e|ed)|(?:hidden\s+|internal\s+|your\s+)?system\s+prompt(?:\s+of\s+your\b|\s+you\s+use\b|\s*$))",
    r"(show|print|leak|expose|dump)\s+(me\s+)?(all\s+)?(env\s*vars?|environment\s+variables|secrets?|api[_\s]?keys?|credentials|os\.environ|process\.env|\.env)",
    r"\bdrop\s+table\b",
    r"<\s*script\b",
]

_SECRET_PATTERNS = [
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_NVIDIA_KEY]"),
    (
        re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (re.compile(r"\b(?:sk-|fc-|exa-)[A-Za-z0-9_-]{12,}\b"), "[REDACTED_API_KEY]"),
    (
        re.compile(
            r"(?i)\b(?:api_key|secret_key|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-.]{12,}['\"]?"
        ),
        "[REDACTED_CREDENTIAL]",
    ),
]


def check_prompt_injection(text: str) -> tuple[bool, str]:
    """Check text for common prompt injection patterns."""
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True, f"Blocked prompt injection pattern: {pattern}"
    return False, ""


def redact_secrets(text: str) -> tuple[str, int]:
    """Redact sensitive keys, tokens, and passwords from input or output text."""
    redacted = text
    total_hits = 0
    for pattern, repl in _SECRET_PATTERNS:
        redacted, n = pattern.subn(repl, redacted)
        total_hits += n
    return redacted, total_hits


def validate_external_url(url: str) -> tuple[bool, str]:
    """
    SSRF guardrail: enforce HTTPS-only public hosts and block private/loopback/link-local IPs.
    Resolves DNS and checks every IP returned.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return False, "Empty URL."

    try:
        parsed = urlparse(cleaned)
    except Exception:
        return False, "Malformed URL."

    if parsed.scheme.lower() != "https":
        return False, "Only https:// URLs are permitted."

    if parsed.username or parsed.password:
        return False, "URLs with embedded user credentials are not allowed."

    if parsed.port and parsed.port not in (443, 8443):
        return False, f"Port {parsed.port} is not permitted for documentation retrieval."

    host = (parsed.hostname or "").lower().strip(".")
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False, f"Blocked local hostname: {host}"

    # Literal IP check
    try:
        ip_obj = ipaddress.ip_address(host)
        if not ip_obj.is_global or ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_reserved:
            return False, f"Blocked non-public IP address: {host}"
        return True, ""
    except ValueError:
        pass

    # DNS resolution check
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        if not infos:
            return False, f"Could not resolve host '{host}'."
        for _, _, _, _, sockaddr in infos:
            ip_obj = ipaddress.ip_address(sockaddr[0])
            if not ip_obj.is_global or ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_reserved:
                return False, f"Hostname '{host}' resolves to non-public IP {sockaddr[0]}."
    except socket.gaierror as e:
        return False, f"DNS resolution failed for '{host}': {e}"
    except Exception as e:
        return False, f"Address validation error for '{host}': {e}"

    return True, ""


def safe_repo_path(rel_path: str, repo_root: Path | None = None) -> tuple[Path | None, str]:
    """
    Resolve rel_path strictly inside repo_root.
    Rejects:
    - Absolute paths (e.g. /etc/passwd or C:\\windows)
    - Windows drive paths (e.g. C:file)
    - Path traversal ('..')
    - Symlink escapes outside repo_root
    """
    raw = (rel_path or "").strip()
    root = (repo_root or (REPO_ROOT if REPO_ROOT is not None else get_repo_root())).resolve()

    # Reject absolute or Windows drive paths when repository-relative paths are expected
    if Path(raw).is_absolute() or re.match(r"^[A-Za-z]:", raw):
        return None, f"[PATH_TRAVERSAL_BLOCKED] Absolute path '{raw}' is not allowed."

    cleaned = raw.lstrip("/\\")
    if not cleaned:
        return root, ""

    # Reject explicit traversal tokens before resolving
    parts = Path(cleaned).parts
    if ".." in parts:
        return None, f"[PATH_TRAVERSAL_BLOCKED] Traversal '..' in '{rel_path}' is blocked."

    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"[PATH_TRAVERSAL_BLOCKED] '{rel_path}' escapes repository root."

    return candidate, ""


def wrap_untrusted(tag: str, content: str, max_chars: int | None = None) -> str:
    """Wrap untrusted external tool content with clear delimiters."""
    limit = max_chars or EXT_CONFIG.max_response_chars
    return (
        f"--- EXTERNAL UNTRUSTED CONTENT [{tag}] ---\n"
        f"{content[:limit]}\n"
        f"--- END EXTERNAL CONTENT [{tag}] ---"
    )


_CITATION_PATTERN = re.compile(
    r"\[(?P<path>[a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+):L(?P<start>\d+)(?:-L(?P<end>\d+))?\]"
)


def verify_citation_deterministic(
    citation_str: str,
    repo_root: Path | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Deterministically verify a [file:Lstart-Lend] citation against local files in the repository.
    Returns: (is_valid, error_reason, content_lines)
    """
    m = _CITATION_PATTERN.match(citation_str.strip())
    if not m:
        return False, f"Malformed citation format: '{citation_str}'. Expected '[file:Lstart-Lend]'", []

    rel_path = m.group("path")
    start_line = int(m.group("start"))
    end_line = int(m.group("end")) if m.group("end") else start_line

    if start_line <= 0:
        return False, f"Start line must be positive: {start_line}", []
    if end_line < start_line:
        return False, f"End line ({end_line}) cannot be less than start line ({start_line})", []

    target_path, err = safe_repo_path(rel_path, repo_root)
    if err or target_path is None:
        return False, err or "Path resolution error", []

    if not target_path.exists() or not target_path.is_file():
        return False, f"Cited file does not exist: {rel_path}", []

    try:
        lines = target_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        return False, f"Could not read cited file '{rel_path}': {e}", []

    total_lines = len(lines)
    if start_line > total_lines:
        return False, f"Start line {start_line} exceeds file length ({total_lines} lines)", []

    actual_end = min(end_line, total_lines)
    extracted = lines[start_line - 1 : actual_end]

    return True, "", extracted
