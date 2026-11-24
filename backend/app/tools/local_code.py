"""
RepoMind local repository tools.
Provides AST and multi-language symbol parsing, line reading, import graph exploration,
call-site reference tracing, test discovery, and pattern searching.
All file access is strictly sandboxed against path traversal.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tomllib
from typing import Generator

from langchain_core.tools import tool

from app.config import IGNORED_DIRS, get_repo_root
from app.guardrails import safe_repo_path

# Sentinel for testing patch compatibility: None means dynamically resolve via get_repo_root()
REPO_ROOT: Path | None = None


def _current_root() -> Path:
    """Return patched REPO_ROOT if present, otherwise active repo root."""
    if REPO_ROOT is not None:
        return REPO_ROOT
    return get_repo_root()

SUPPORTED_CODE_EXTS = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".md",
    ".txt",
    ".rst",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".sh",
    ".toml",
    ".sql",
    ".env.example",
}


def iter_py_files(repo_root: Path | None = None) -> Generator[Path, None, None]:
    """Iterate all Python source files in the repo, skipping ignored directories."""
    root_resolved = (repo_root or _current_root()).resolve()
    if not root_resolved.exists():
        return
    for p in sorted(root_resolved.rglob("*.py")):
        if not any(part in IGNORED_DIRS for part in p.parts):
            yield p


def iter_code_files(repo_root: Path | None = None) -> Generator[Path, None, None]:
    """Iterate all source code, documentation, and configuration files in the repo."""
    root_resolved = (repo_root or _current_root()).resolve()
    if not root_resolved.exists():
        return
    for p in sorted(root_resolved.rglob("*")):
        if p.is_file() and not any(part in IGNORED_DIRS for part in p.parts):
            if p.suffix.lower() in SUPPORTED_CODE_EXTS or p.name in (
                "Dockerfile",
                "Makefile",
                "LICENSE",
            ):
                yield p


@tool
def read_project_dependencies() -> str:
    """Read library names and version constraints from package.json, pyproject.toml, setup.py, or requirements.txt."""
    root = _current_root().resolve()
    if not root.exists():
        return f"Repository root does not exist: {root}"

    findings = []

    # Node.js package.json
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            raw_pkg = pkg_json.read_text(encoding="utf-8", errors="ignore")
            pkg_lines = len(raw_pkg.splitlines()) or 1
            data = json.loads(raw_pkg)
            lines = [f"[package.json:L1-L{pkg_lines} | {data.get('name', 'project')} v{data.get('version', '')}]"]
            if data.get("description"):
                lines.append(f"Description: {data.get('description')}")
            if data.get("main"):
                lines.append(f"Main Entrypoint: {data.get('main')}")
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            if deps:
                lines.append("dependencies:")
                lines.extend(f"- {k}: {v}" for k, v in list(deps.items())[:30])
            if dev_deps:
                lines.append("devDependencies:")
                lines.extend(f"- {k}: {v}" for k, v in list(dev_deps.items())[:20])
            findings.append("\n".join(lines))
        except Exception as e:
            findings.append(f"[package.json parse warning] {e}")

    # Python pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            raw_pyp = pyproject.read_text(encoding="utf-8", errors="ignore")
            pyp_lines = len(raw_pyp.splitlines()) or 1
            data = tomllib.loads(raw_pyp)
            proj = data.get("project", {})
            deps = proj.get("dependencies", [])
            opt_deps = proj.get("optional-dependencies", {})
            if deps:
                findings.append(f"[pyproject.toml:L1-L{pyp_lines} dependencies]\n" + "\n".join(f"- {d}" for d in deps))
            for group, items in opt_deps.items():
                findings.append(
                    f"[pyproject.toml:L1-L{pyp_lines} optional:{group}]\n" + "\n".join(f"- {d}" for d in items[:20])
                )
        except Exception as e:
            findings.append(f"[pyproject.toml parse warning] {e}")

    # Python requirements.txt & setup
    for fname in ["setup.cfg", "setup.py", "requirements.txt", "requirements-dev.txt"]:
        fpath = root / fname
        if fpath.exists():
            text = fpath.read_text(encoding="utf-8", errors="ignore")
            total_f_lines = len(text.splitlines()) or 1
            matches = re.findall(
                r"""['"]([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?\s*(?:==|>=|<=|~=|!=|>|<)[^'"]+)['"]""",
                text,
            )
            if fname.endswith(".txt"):
                matches = [
                    ln.strip()
                    for ln in text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
            if matches:
                findings.append(
                    f"[{fname}:L1-L{total_f_lines}]\n" + "\n".join(f"- {m}" for m in dict.fromkeys(matches[:30]))
                )

    return "\n\n".join(findings) or "No dependency manifest files found in repository root."


@tool
def list_repo_files(subpath: str = "") -> str:
    """List source code, documentation, and configuration files inside REPO_ROOT (sandboxed against path traversal)."""
    root_resolved = _current_root().resolve()
    base, err = safe_repo_path(subpath, root_resolved)
    if err or base is None:
        return err
    if not base.exists():
        return f"Directory '{subpath}' does not exist."
    if base.is_file():
        return str(base.relative_to(root_resolved)).replace("\\", "/")

    results = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not any(part in IGNORED_DIRS for part in p.parts):
            if p.suffix.lower() in SUPPORTED_CODE_EXTS or p.name in (
                "Dockerfile",
                "Makefile",
                "LICENSE",
            ):
                results.append(str(p.relative_to(root_resolved)).replace("\\", "/"))
    return "\n".join(results[:150]) or "No matching files found."


@tool
def inspect_python_symbols(file_path: str) -> str:
    """Parse a source file inside REPO_ROOT and return symbols (classes, functions, routes, imports) and exact line ranges."""
    root_resolved = _current_root().resolve()
    target, err = safe_repo_path(file_path, root_resolved)
    if err or target is None:
        return err
    if not target.exists() or not target.is_file():
        return f"File '{file_path}' not found."

    source = target.read_text(encoding="utf-8", errors="ignore")
    lines = source.splitlines()

    # Python files: Use AST
    if file_path.endswith(".py"):
        try:
            tree = ast.parse(source, filename=file_path)
            out_lines = [f"=== AST Summary for {file_path} ==="]
            for node in tree.body:
                if isinstance(node, ast.Import):
                    out_lines.append(f"[L{node.lineno}] import {', '.join(a.name for a in node.names)}")
                elif isinstance(node, ast.ImportFrom):
                    out_lines.append(
                        f"[L{node.lineno}] from {node.module or ''} import {', '.join(a.name for a in node.names)}"
                    )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in node.args.args]
                    end_line = getattr(node, "end_lineno", node.lineno)
                    out_lines.append(f"[L{node.lineno}-L{end_line}] def {node.name}({', '.join(args)})")
                elif isinstance(node, ast.ClassDef):
                    end_line = getattr(node, "end_lineno", node.lineno)
                    out_lines.append(f"[L{node.lineno}-L{end_line}] class {node.name}:")
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            args = [a.arg for a in item.args.args]
                            item_end = getattr(item, "end_lineno", item.lineno)
                            out_lines.append(
                                f"    [L{item.lineno}-L{item_end}] def {item.name}({', '.join(args)})"
                            )
            return "\n".join(out_lines)
        except SyntaxError as e:
            return f"SyntaxError parsing {file_path}: {e}"

    # JavaScript / TypeScript / Node files: Regex symbol inspection
    if file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        out_lines = [f"=== Code Symbol Summary for {file_path} ==="]
        for idx, line in enumerate(lines, start=1):
            sline = line.strip()
            # Imports
            if re.match(r"^(?:import\s+.+?from\s+['\"][^'\"]+['\"]|const\s+.+?=\s*require\(['\"][^'\"]+['\"]\))", sline):
                out_lines.append(f"[L{idx}] {sline[:80]}")
            # Express / API routes
            elif re.search(r"\b(?:router|app)\.(get|post|put|delete|patch|use)\s*\(", sline):
                out_lines.append(f"[L{idx}] {sline[:90]}")
            # Functions
            elif re.search(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)", sline):
                m = re.search(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)", sline)
                out_lines.append(f"[L{idx}] function {m.group(1)}()")
            # Arrow / Const functions
            elif re.search(r"(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", sline):
                m = re.search(r"(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", sline)
                out_lines.append(f"[L{idx}] const {m.group(1)} = (...) =>")
            # Classes
            elif re.search(r"(?:export\s+)?class\s+([A-Za-z0-9_$]+)", sline):
                m = re.search(r"(?:export\s+)?class\s+([A-Za-z0-9_$]+)", sline)
                out_lines.append(f"[L{idx}] class {m.group(1)}")
        return "\n".join(out_lines) if len(out_lines) > 1 else f"=== {file_path} (No high-level exports found) ==="

    # Documentation / Config files: Return outline
    out_lines = [f"=== Document Summary for {file_path} ==="]
    for idx, line in enumerate(lines[:60], start=1):
        if line.startswith(("#", "-", "*", '"', "'")):
            out_lines.append(f"[L{idx}] {line.strip()[:80]}")
    return "\n".join(out_lines[:40])


@tool
def read_file_lines(file_path: str, start_line: int, end_line: int) -> str:
    """Read specific 1-indexed inclusive line numbers from a file inside REPO_ROOT."""
    root_resolved = _current_root().resolve()
    target, err = safe_repo_path(file_path, root_resolved)
    if err or target is None:
        return err
    if not target.exists() or not target.is_file():
        return f"File '{file_path}' not found."

    all_lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not all_lines:
        return f"=== {file_path} (Empty file) ==="

    start = max(1, start_line)
    end = min(len(all_lines), end_line)
    if start > len(all_lines):
        return f"Start line {start} exceeds file length ({len(all_lines)} lines)."
    if end < start:
        end = start

    if end - start > 120:
        end = start + 120

    numbered = [f"{i}: {all_lines[i - 1]}" for i in range(start, end + 1)]
    return f"=== {file_path} (L{start}-L{end}) ===\n" + "\n".join(numbered)


@tool
def find_importers(module_or_symbol: str) -> str:
    """Find all files in the repository that import or require a given module or symbol name, with exact line numbers."""
    root_resolved = _current_root().resolve()
    hits = []
    for p in iter_code_files(root_resolved):
        rel = str(p.relative_to(root_resolved)).replace("\\", "/")
        tag = "[TEST]" if any(t in rel.lower() for t in ("test", "spec")) else "[PROD]"
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        if p.suffix == ".py":
            try:
                tree = ast.parse("\n".join(lines), filename=rel)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if module_or_symbol in alias.name:
                                hits.append(f"{tag} {rel}:L{node.lineno} -> import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        imported_names = [a.name for a in node.names]
                        if module_or_symbol in mod or module_or_symbol in imported_names:
                            hits.append(
                                f"{tag} {rel}:L{node.lineno} -> from {mod} import {', '.join(imported_names)}"
                            )
            except SyntaxError:
                pass
        else:
            # JS/TS or generic imports
            for idx, line in enumerate(lines, start=1):
                if module_or_symbol in line and any(k in line for k in ("import", "require(", "from")):
                    hits.append(f"{tag} {rel}:L{idx} -> {line.strip()[:90]}")

    return "\n".join(hits[:100]) or f"No imports matching '{module_or_symbol}' found."


@tool
def find_symbol_references(symbol_name: str) -> str:
    """Find all call sites, attribute accesses, and references to a function, method, or class across the repo."""
    root_resolved = _current_root().resolve()
    hits = []
    regex = re.compile(rf"\b{re.escape(symbol_name)}\b")

    for p in iter_code_files(root_resolved):
        rel = str(p.relative_to(root_resolved)).replace("\\", "/")
        tag = "[TEST]" if any(t in rel.lower() for t in ("test", "spec")) else "[PROD]"
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        seen_lines = set()
        for idx, line in enumerate(lines, start=1):
            if regex.search(line) and idx not in seen_lines:
                seen_lines.add(idx)
                hits.append(f"{tag} {rel}:L{idx} -> {line.strip()[:100]}")
                if len(hits) >= 120:
                    return "\n".join(hits)

    return "\n".join(hits[:120]) or f"No references to '{symbol_name}' found."


@tool
def find_test_cases(keyword: str) -> str:
    """Search test files, classes, and test functions matching a keyword across the repository."""
    root_resolved = _current_root().resolve()
    kw = keyword.lower()
    hits = []

    for p in iter_code_files(root_resolved):
        rel = str(p.relative_to(root_resolved)).replace("\\", "/")
        if not any(t in rel.lower() for t in ("test", "spec", "__tests__")):
            continue

        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        if p.suffix == ".py":
            try:
                tree = ast.parse("\n".join(lines), filename=rel)
                file_matches = kw in rel.lower()
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                        doc = ast.get_docstring(node) or ""
                        if file_matches or kw in node.name.lower() or kw in doc.lower():
                            end_lineno = getattr(node, "end_lineno", node.lineno)
                            hits.append(f"[{rel}:L{node.lineno}-L{end_lineno}] def {node.name}()")
                    elif isinstance(node, ast.ClassDef):
                        cls_match = file_matches or kw in node.name.lower()
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                                doc = ast.get_docstring(item) or ""
                                if cls_match or kw in item.name.lower() or kw in doc.lower():
                                    item_end = getattr(item, "end_lineno", item.lineno)
                                    hits.append(
                                        f"[{rel}:L{item.lineno}-L{item_end}] {node.name}.{item.name}()"
                                    )
            except SyntaxError:
                pass
        else:
            # JS/TS tests (it, test, describe)
            for idx, line in enumerate(lines, start=1):
                if any(k in line for k in ("it(", "test(", "describe(", "it.only(", "test.only(")):
                    if kw in line.lower() or kw in rel.lower():
                        hits.append(f"[{rel}:L{idx}] {line.strip()[:90]}")

    return "\n".join(hits[:120]) or f"No test functions matching '{keyword}' found."


@tool
def find_untested_symbols(source_file_path: str) -> str:
    """Compare public functions/methods in a production file against test references in the repository."""
    root_resolved = _current_root().resolve()
    target, err = safe_repo_path(source_file_path, root_resolved)
    if err or target is None:
        return err
    if not target.exists() or not target.is_file():
        return f"Source file '{source_file_path}' not found."

    source = target.read_text(encoding="utf-8", errors="ignore")
    lines = source.splitlines()

    public_symbols = []
    if source_file_path.endswith(".py"):
        try:
            src_tree = ast.parse(source, filename=source_file_path)
            for node in src_tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    public_symbols.append((node.name, node.name, node.lineno, end_lineno))
                elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                            item_end = getattr(item, "end_lineno", item.lineno)
                            public_symbols.append((f"{node.name}.{item.name}", item.name, item.lineno, item_end))
        except SyntaxError as e:
            return f"SyntaxError parsing {source_file_path}: {e}"
    else:
        # JS/TS public functions
        for idx, line in enumerate(lines, start=1):
            m = re.search(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)", line)
            if m:
                public_symbols.append((m.group(1), m.group(1), idx, idx + 5))
            m2 = re.search(r"(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", line)
            if m2:
                public_symbols.append((m2.group(1), m2.group(1), idx, idx + 5))

    tested_names = set()
    for p in iter_code_files(root_resolved):
        rel = str(p.relative_to(root_resolved)).replace("\\", "/")
        if any(t in rel.lower() for t in ("test", "spec")):
            try:
                t_text = p.read_text(encoding="utf-8", errors="ignore")
                for _, short_name, _, _ in public_symbols:
                    if short_name in t_text:
                        tested_names.add(short_name)
            except Exception:
                continue

    lines_out = [f"=== Test Reference Audit for {source_file_path} ==="]
    for qualified, short_name, start, end in public_symbols:
        status = "REFERENCED_IN_TESTS" if short_name in tested_names else "POTENTIAL_GAP (0 direct test refs)"
        lines_out.append(f"[{status}] {qualified} ({source_file_path}:L{start}-L{end})")

    return "\n".join(lines_out) or f"No public symbols detected in '{source_file_path}'."


@tool
def search_code_pattern(pattern: str) -> str:
    """Regex/text search across all source code and config files in the repository with exact file paths and line numbers."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(pattern), re.IGNORECASE)

    root_resolved = _current_root().resolve()
    hits = []
    for p in iter_code_files(root_resolved):
        rel = str(p.relative_to(root_resolved)).replace("\\", "/")
        try:
            flines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for idx, line in enumerate(flines, start=1):
            if regex.search(line):
                hits.append(f"{rel}:L{idx} -> {line.strip()[:100]}")
                if len(hits) >= 80:
                    return "\n".join(hits)
    return "\n".join(hits) or f"No matches found for '{pattern}'."
