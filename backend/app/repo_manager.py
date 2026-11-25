"""
Prepare pinned public GitHub checkouts and local SQLite symbol indexes.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Dict, Tuple

from git import Repo

from app.config import (
    BASE_DIR,
    IGNORED_DIRS,
    get_repo_full_name,
)
from app.database import _classify_repo_file, _extract_doc_lang, index_repo_symbols

logger = logging.getLogger("repomind.repo_manager")
_ready_indexes: dict[Path, Dict[str, Any]] = {}
_index_lock = Lock()
_cache_root = Path(os.getenv(
    "REPOMIND_REPOSITORY_DIR", str(BASE_DIR / "data" / "repositories" / "managed")
)).resolve()


def parse_github_url(url_or_slug: str) -> Tuple[str, str, str]:
    """Accept a GitHub repository URL or owner/repository slug."""
    value = url_or_slug.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.query or parsed.fragment:
            raise ValueError("Expected a GitHub repository URL.")
        value = parsed.path.removeprefix("/")
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})", value.removesuffix(".git"))
    if not match or match[2] in (".", "..") or match[2].endswith("."):
        raise ValueError("Expected a GitHub owner/repository slug.")
    owner, name = match.groups()
    return owner, name, f"https://github.com/{owner}/{name}"


def prepare_query_repo(repo_url_or_slug: str, revision: str | None = None) -> Dict[str, Any]:
    """Resolve a clean checkout and matching SQLite index without switching global state."""
    if revision is not None and not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Expected a full Git commit SHA.")
    owner, name, canonical_url = parse_github_url(repo_url_or_slug)
    full_name = f"{owner}/{name}"
    # One cached checkout per repo. Rebuild the requested commit after an ephemeral disk reset.
    target_dir = _cache_root / owner.lower() / name.lower()

    with _index_lock:
        if not target_dir.exists():
            max_repos = max(1, int(os.getenv("REPOMIND_MAX_CACHED_REPOS", "8")))
            cached_repos = sum(1 for directory in _cache_root.glob("*/*/.git") if directory.is_dir())
            if cached_repos >= max_repos:
                raise ValueError("The repository cache is full. Ask the operator to free space.")
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="clone-", dir=target_dir.parent) as staging:
                checkout = Path(staging) / "checkout"
                with Repo.init(checkout, mkdir=True) as repo:
                    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
                    subprocess.run(
                        ["git", "-c", "credential.helper=", "fetch", "--depth=1", "--no-tags",
                         canonical_url + ".git", revision or "HEAD"],
                        cwd=checkout, env=env, check=True, capture_output=True, timeout=45,
                    )
                    with repo.git.custom_environment(GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0"):
                        repo.git.checkout("--detach", "FETCH_HEAD")
                    if revision and repo.head.commit.hexsha != revision:
                        raise ValueError("GitHub did not return the requested revision.")
                checkout.rename(target_dir)

        with Repo(target_dir, search_parent_directories=False) as repo:
            actual = repo.head.commit.hexsha
            if revision and actual != revision:
                raise ValueError("The requested revision is not available in this checkout.")
            if repo.is_dirty(untracked_files=True):
                raise ValueError("The cached checkout has local changes. Use a clean checkout.")

        cached = _ready_indexes.get(target_dir)
        if cached and cached["repo_revision"] == actual:
            return dict(cached)
        result = _index_checkout(full_name, canonical_url, target_dir, actual)
        _ready_indexes[target_dir] = result
        return dict(result)


def clone_and_index_repo(repo_url_or_slug: str) -> Dict[str, Any]:
    return prepare_query_repo(repo_url_or_slug)


def _index_checkout(full_name: str, canonical_url: str, target_dir: Path, revision: str) -> Dict[str, Any]:
    py_files = []
    code_files = []
    doc_files = []
    total_files = 0

    for p in target_dir.rglob("*"):
        if not p.is_symlink() and p.is_file() and not any(part in IGNORED_DIRS for part in p.parts):
            total_files += 1
            rel_str = str(p.relative_to(target_dir)).replace("\\", "/")
            if p.suffix == ".py":
                py_files.append(rel_str)
                code_files.append(rel_str)
            elif p.suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
                code_files.append(rel_str)
            elif p.suffix in (".md", ".rst", ".txt"):
                doc_files.append(rel_str)

    # Check for package dependency manifests (Python and Node.js)
    manifests = [
        m for m in ("pyproject.toml", "requirements.txt", "package.json", "setup.py", "setup.cfg")
        if (target_dir / m).exists()
    ]

    # Build fast SQLite FTS5 index prioritizing ALL production source files first, then tests & English docs
    prod_py = [f for f in py_files if _classify_repo_file(f) == "prod"]
    test_py = [f for f in py_files if _classify_repo_file(f) == "test"]
    doc_py = [f for f in py_files if _classify_repo_file(f) == "doc"]
    non_py_prod = [f for f in code_files if not f.endswith(".py") and _classify_repo_file(f) == "prod"]
    en_docs = [f for f in doc_files if (_extract_doc_lang(f) in (None, "en"))]
    trans_docs = [f for f in doc_files if (_extract_doc_lang(f) not in (None, "en"))]

    ordered_files = list(dict.fromkeys(
        prod_py[:800] + non_py_prod[:200] + test_py[:300] + manifests + en_docs[:100] + trans_docs[:50] + doc_py[:50]
    ))

    fts_entries: list[tuple[str, str]] = []
    for rel_str in ordered_files:
        fpath = target_dir / rel_str
        if fpath.is_symlink() or not fpath.is_file() or fpath.stat().st_size > 512_000:
            continue
        if rel_str.endswith(".py"):
            try:
                src = fpath.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(src)
                syms = []
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        syms.append(f"def {node.name} (L{node.lineno})")
                    elif isinstance(node, ast.ClassDef):
                        methods = [
                            m.name for m in node.body
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ][:20]
                        syms.append(f"class {node.name} (L{node.lineno}): {' '.join(methods)}")
                sym_text = f"{rel_str} | " + "; ".join(syms) if syms else rel_str
                fts_entries.append((rel_str, sym_text[:1500]))
            except Exception:
                fts_entries.append((rel_str, rel_str))
        else:
            try:
                preview = " ".join(fpath.read_text(encoding="utf-8", errors="ignore").splitlines()[:15])
                fts_entries.append((rel_str, f"{rel_str} | {preview[:400]}"))
            except Exception:
                fts_entries.append((rel_str, rel_str))

    index_repo_symbols(f"{full_name.lower()}@{revision}", fts_entries)

    effective_code_count = len(py_files) if py_files else len(code_files)

    result = {
        "status": "ready",
        "repo_name": full_name,
        "repo_url": canonical_url,
        "repo_path": str(target_dir),
        "repo_revision": revision,
        "python_files_count": effective_code_count,
        "docs_files_count": len(doc_files),
        "total_files_count": total_files,
        "sample_files": (py_files or code_files)[:10],
        "manifests": manifests,
    }
    return result


def get_active_repo_info() -> Dict[str, Any]:
    """Return metadata only for an operator-configured default, not another chat's repo."""
    configured = get_repo_full_name()
    if configured:
        owner, name, url = parse_github_url(configured)
        root = _cache_root / owner.lower() / name.lower()
        cached = _ready_indexes.get(root)
        if cached and (root / ".git").is_dir():
            return {**{key: value for key, value in cached.items() if key != "repo_path"}, "repo_available": True}
        return {"repo_name": configured, "repo_url": url, "repo_available": False}
    return {"repo_name": "", "repo_url": "", "repo_available": False}
