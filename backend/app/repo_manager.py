"""
RepoMind Dynamic Repository Manager.
Allows users to provide any GitHub repository URL on the fly,
clones/updates it into data/repositories, indexes its files and AST symbols,
and switches the active analysis context without requiring server restarts.
Handles Git LFS repositories by safely skipping gigabyte model weights.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from git import Repo

from app.config import (
    BASE_DIR,
    GITHUB_TOKEN,
    IGNORED_DIRS,
    get_repo_full_name,
    get_repo_root,
    set_active_repo,
)
from app.database import index_repo_symbols
from app.tools.github_sync import sync_github_issues

# Ensure Git LFS smudge is skipped globally to prevent downloading large model binaries
os.environ["GIT_LFS_SKIP_SMUDGE"] = "1"

logger = logging.getLogger("repomind.repo_manager")


def _remove_readonly(func, path, excinfo):
    """Windows-safe helper for deleting read-only git objects."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _clean_dir(path: Path):
    """Safely and completely delete a directory on Windows/POSIX."""
    if path.exists():
        shutil.rmtree(path, onerror=_remove_readonly)


def parse_github_url(url_or_slug: str) -> Tuple[str, str, str]:
    """
    Parse a GitHub repository URL or slug into (owner, repo_name, canonical_url).
    Examples:
    - https://github.com/psf/requests -> ('psf', 'requests', 'https://github.com/psf/requests')
    - git@github.com:pallets/flask.git -> ('pallets', 'flask', 'https://github.com/pallets/flask')
    - fastapi/fastapi -> ('fastapi', 'fastapi', 'https://github.com/fastapi/fastapi')
    """
    cleaned = url_or_slug.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]

    # Handle SSH format: git@github.com:owner/repo
    if cleaned.startswith("git@github.com:"):
        path_part = cleaned.split("git@github.com:", 1)[1]
    elif "github.com/" in cleaned:
        path_part = cleaned.split("github.com/", 1)[1]
    else:
        path_part = cleaned

    parts = [p for p in path_part.split("/") if p]
    if len(parts) >= 2:
        owner = parts[-2]
        repo = parts[-1]
    elif len(parts) == 1:
        owner = "user"
        repo = parts[0]
    else:
        raise ValueError(f"Invalid GitHub repository identifier: '{url_or_slug}'")

    canonical_url = f"https://github.com/{owner}/{repo}"
    return owner, repo, canonical_url


def clone_and_index_repo(repo_url_or_slug: str) -> Dict[str, Any]:
    """
    Clone or update a repository, inspect its files, and set it as the active RepoMind target.
    Safely skips binary Git LFS assets to focus on code and documentation.
    """
    owner, repo_name, canonical_url = parse_github_url(repo_url_or_slug)
    full_name = f"{owner}/{repo_name}"

    target_dir = BASE_DIR / "data" / "repositories" / f"{owner}_{repo_name}"
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Setting up repository: %s at %s", full_name, target_dir)

    # 1. Inspect existing directory and repair if needed
    if (target_dir / ".git").exists():
        try:
            repo = Repo(str(target_dir))
            # If working tree has unstaged deletions from prior smudge failure, restore it
            if repo.is_dirty(untracked_files=False):
                logger.info("Resetting working tree for %s...", full_name)
                repo.git.reset("--hard", "HEAD")
        except Exception as e:
            logger.warning("Existing git directory %s is damaged (%s). Re-cloning...", target_dir, e)
            _clean_dir(target_dir)

    # 2. Clone if not present
    if not (target_dir / ".git").exists():
        _clean_dir(target_dir)

        env = os.environ.copy()
        env["GIT_LFS_SKIP_SMUDGE"] = "1"

        clone_candidates = []
        if GITHUB_TOKEN:
            parsed = urllib.parse.urlparse(canonical_url + ".git")
            auth_url = f"https://x-access-token:{GITHUB_TOKEN}@{parsed.netloc}{parsed.path}"
            clone_candidates.append(auth_url)
        clone_candidates.append(canonical_url + ".git")

        success = False
        last_err = None

        for candidate_url in clone_candidates:
            try:
                logger.info("Cloning %s into %s (depth=1, skipping LFS binaries)...", full_name, target_dir)
                Repo.clone_from(
                    candidate_url,
                    str(target_dir),
                    depth=1,
                    multi_options=["-c filter.lfs.smudge=", "-c filter.lfs.required=false"],
                    allow_unsafe_options=True,
                    env=env,
                )
                success = True
                logger.info("Successfully cloned %s.", full_name)
                break
            except Exception as e:
                last_err = e
                _clean_dir(target_dir)
                continue

        if not success:
            err_msg = str(last_err)
            if GITHUB_TOKEN:
                err_msg = err_msg.replace(GITHUB_TOKEN, "[REDACTED_TOKEN]")
            logger.error("Failed to clone repository %s: %s", full_name, err_msg)
            raise RuntimeError(f"Could not clone repository '{full_name}': {err_msg}")

    # 3. Set as active repository in config
    set_active_repo(full_name, target_dir)

    # 4. Analyze repository files and structure
    py_files = []
    code_files = []
    doc_files = []
    total_files = 0

    for p in target_dir.rglob("*"):
        if p.is_file() and not any(part in IGNORED_DIRS for part in p.parts):
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

    # Build fast SQLite FTS5 index of file paths and AST symbols (capped for speed)
    fts_entries: list[tuple[str, str]] = []
    for rel_str in (py_files[:300] + [f for f in code_files if not f.endswith(".py")][:150] + doc_files[:50] + manifests):
        fpath = target_dir / rel_str
        if not fpath.is_file():
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

    try:
        index_repo_symbols(full_name, fts_entries)
    except Exception as e:
        logger.debug("FTS5 repo symbol indexing skipped: %s", e)

    # Optional initial issue sync if token configured
    if GITHUB_TOKEN:
        try:
            sync_github_issues(repo_full_name=full_name, max_issues=10)
        except Exception as e:
            logger.debug("Optional GitHub sync on indexing failed: %s", e)

    effective_code_count = len(py_files) if py_files else len(code_files)

    return {
        "status": "ready",
        "repo_name": full_name,
        "repo_url": canonical_url,
        "repo_path": str(target_dir),
        "python_files_count": effective_code_count,
        "docs_files_count": len(doc_files),
        "total_files_count": total_files,
        "sample_files": (py_files or code_files)[:10],
        "manifests": manifests,
    }


def get_active_repo_info() -> Dict[str, Any]:
    """Retrieve metadata about the currently active repository."""
    full_name = get_repo_full_name()
    root_path = get_repo_root()
    if not full_name or root_path is None or not root_path.exists():
        return {
            "repo_name": "",
            "repo_url": "",
            "repo_path": "",
            "repo_available": False,
            "python_files_count": 0,
            "docs_files_count": 0,
            "total_files_count": 0,
        }

    canonical_url = f"https://github.com/{full_name}"
    exists = root_path.exists()
    py_files_count = 0
    code_files_count = 0
    docs_files_count = 0
    total_files = 0
    if exists:
        for p in root_path.rglob("*"):
            if p.is_file() and not any(part in IGNORED_DIRS for part in p.parts):
                total_files += 1
                if p.suffix == ".py":
                    py_files_count += 1
                    code_files_count += 1
                elif p.suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
                    code_files_count += 1
                elif p.suffix in (".md", ".rst", ".txt"):
                    docs_files_count += 1

    effective_code = py_files_count if py_files_count else code_files_count

    return {
        "repo_name": full_name,
        "repo_url": canonical_url,
        "repo_path": str(root_path),
        "repo_available": exists,
        "python_files_count": effective_code,
        "docs_files_count": docs_files_count,
        "total_files_count": total_files,
    }
