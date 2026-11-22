"""
Tests for app.tools (local_code, github_sync, web_docs).
"""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.tools.local_code import (
    read_project_dependencies,
    list_repo_files,
    inspect_python_symbols,
    read_file_lines,
    find_importers,
    find_symbol_references,
    find_test_cases,
    find_untested_symbols,
    search_code_pattern,
)
from app.tools.github_sync import (
    search_github_issues_and_ci,
    sync_github_issues,
)
from app.tools.web_docs import (
    context7_search_docs,
    exa_search_technical,
    firecrawl_ingest_docs,
)
from app.budget import request_budget_scope, get_current_budget


def test_local_code_inspection():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src_file = root / "mymodule.py"
        src_file.write_text(
            "import os\nfrom sys import path\n\ndef my_func(a, b):\n    return a + b\n\nclass MyClass:\n    def method(self):\n        pass\n",
            encoding="utf-8",
        )

        with patch("app.tools.local_code.REPO_ROOT", root), patch("app.guardrails.REPO_ROOT", root):
            # Test inspect_python_symbols
            syms = inspect_python_symbols.invoke({"file_path": "mymodule.py"})
            assert "def my_func(a, b)" in syms
            assert "class MyClass:" in syms
            assert "def method(self)" in syms

            # Test read_file_lines
            lines = read_file_lines.invoke({"file_path": "mymodule.py", "start_line": 4, "end_line": 5})
            assert "def my_func(a, b):" in lines

            # Test search_code_pattern
            matches = search_code_pattern.invoke({"pattern": "my_func"})
            assert "mymodule.py:L4" in matches


def test_local_code_importers_and_references():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "mod_a.py").write_text("def target_fn():\n    pass\n", encoding="utf-8")
        (root / "mod_b.py").write_text("from mod_a import target_fn\ntarget_fn()\n", encoding="utf-8")

        with patch("app.tools.local_code.REPO_ROOT", root):
            importers = find_importers.invoke({"module_or_symbol": "target_fn"})
            assert "mod_b.py:L1" in importers

            refs = find_symbol_references.invoke({"symbol_name": "target_fn"})
            assert "mod_b.py" in refs


def test_github_sync_mocked():
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"number": 42, "title": "Connection pool timeout", "state": "open", "body": "Details here", "html_url": "https://github.com/test/issues/42"}
        ]
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            with patch("app.tools.github_sync.connect_sqlite", lambda: __import__("app.database", fromlist=["connect_sqlite"]).connect_sqlite(db_path)):
                count = sync_github_issues(repo_full_name="test/repo", db_path=db_path)
                assert count == 1


def test_web_docs_budget_and_mock():
    with request_budget_scope():
        with patch.dict("os.environ", {"CONTEXT7_API_KEY": "test-key"}):
            with patch("requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "codeSnippets": [{"libraryId": "requests", "codeTitle": "GET example", "codeList": [{"code": "requests.get()"}]}]
                }
                mock_get.return_value = mock_resp

                import time
                res = context7_search_docs.invoke({"query": f"unique requests query {time.time()}"})
                assert "requests.get()" in res
                budget = get_current_budget()
                assert budget.context7_calls == 1
