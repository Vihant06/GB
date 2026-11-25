"""
Tests for FastAPI endpoints (/health, /query, /query/stream).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["database_ready"] is True
    assert "repo_name" in data


def test_query_validation():
    # Empty query should fail validation (min_length=1)
    response = client.post("/query", json={"query": ""})
    assert response.status_code == 422


def test_query_non_streaming():
    with patch("app.api.routes.repomind_graph.invoke") as mock_invoke:
        mock_invoke.return_value = {
            "final_report": "Report explaining session adapter.",
            "trace_logs": ["[Input Guardrail] Done", "[Synthesis] Complete"],
        }

        response = client.post(
            "/query",
            json={"query": "Explain requests.Session adapter"},
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"] == "Report explaining session adapter."
        assert len(data["trace_logs"]) == 2
        assert "usage" in data
        assert data["status"] == "completed"


def test_query_streaming_sse():
    async def mock_astream(*args, **kwargs):
        assert args[0]["chat_history"] == [{"role": "user", "text": "Explain session pooling."}]
        yield {"input_guardrail": {"trace_logs": ["[Input Guardrail] OK"]}}
        yield {"orchestrator_plan": {"plan_reasoning": "Plan ready", "trace_logs": ["[Plan] Ready"]}}
        yield {"orchestrator_synthesize": {"final_report": "Streamed report content."}}

    with patch("app.api.routes.repomind_graph.astream", mock_astream):
        response = client.post(
            "/query/stream",
            json={"query": "Show it in Java.", "history": [{"role": "user", "text": "Explain session pooling."}]},
            headers={"X-API-Key": "test-key-123"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = response.text
        assert "event: status" in body
        assert "event: progress" in body
        progress_events = [event for event in body.split("\n\n") if event.startswith("event: progress")]
        assert progress_events and all("Plan ready" not in event and "[Plan]" not in event for event in progress_events)
        assert any("Understanding your question" in event for event in progress_events)
        assert "event: token" in body
        assert "event: complete" in body


def test_overlapping_queries_keep_repository_revision_and_evidence():
    from app.config import RepositorySnapshot, get_repo_snapshot, repository_scope
    from app.database import cache_get, cache_put, index_repo_symbols, query_repo_symbols_fts
    from app.guardrails import safe_repo_path

    barrier = Barrier(2, timeout=10)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        expected = {"team/alpha": ("a" * 40, "Alpha"), "team/beta": ("b" * 40, "Beta")}
        db = str(root / "index.db")
        for name, (revision, value) in expected.items():
            checkout = root / value
            checkout.mkdir()
            (checkout / "app.py").write_text(value, encoding="utf-8")
            (checkout / ".git").mkdir()
            index_repo_symbols(f"{name}@{revision}", [(f"{value}.py", "shared_symbol")], db)
            with repository_scope(RepositorySnapshot(name, checkout, revision)):
                cache_put("shared", "EXA", "https://example.com", "Shared", value, db)

        def prepare(name, revision):
            sha, value = expected[name]
            if revision != sha:
                raise ValueError("The requested revision is not available.")
            return {"repo_name": name, "repo_revision": sha, "repo_path": str(root / value)}

        def report():
            snapshot = get_repo_snapshot()
            assert snapshot is not None
            sha, value = expected[snapshot.name]
            assert snapshot.revision == sha
            path, error = safe_repo_path("app.py")
            assert not error and path.read_text(encoding="utf-8") == value
            assert safe_repo_path(".git/config")[0] is None
            assert query_repo_symbols_fts(snapshot.name, "shared_symbol", db_path=db) == [(f"{value}.py", "shared_symbol")]
            other_name = next(name for name in expected if name != snapshot.name)
            assert query_repo_symbols_fts(other_name, "shared_symbol", db_path=db) == []
            assert cache_get("shared", 60, db)["content"] == value
            return {"final_report": f"{value} at {sha}"}

        def sync_graph(_):
            barrier.wait()
            return report()

        async def stream_graph(*args, **kwargs):
            await asyncio.to_thread(barrier.wait)
            yield {"orchestrator_synthesize": report()}

        def query(name, endpoint):
            revision, _ = expected[name]
            with TestClient(app) as local_client:
                return local_client.post(endpoint, json={
                    "query": "Explain app.py", "repo_url": name, "repo_revision": revision,
                })

        with (
            patch("app.api.routes.prepare_query_repo", side_effect=prepare),
            patch("app.api.routes.repomind_graph.invoke", side_effect=sync_graph),
            patch("app.api.routes.repomind_graph.astream", side_effect=stream_graph),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                alpha = pool.submit(query, "team/alpha", "/query")
                beta = pool.submit(query, "team/beta", "/query/stream")
                first, second = alpha.result(timeout=15), beta.result(timeout=15)
            assert first.status_code == second.status_code == 200
            assert first.json()["report"] == f"Alpha at {'a' * 40}"
            assert first.json()["repo_revision"] == "a" * 40
            assert f"Beta at {'b' * 40}" in second.text and "Alpha" not in second.text
            for endpoint in ("/query", "/query/stream"):
                mismatch = client.post(endpoint, json={
                    "query": "Explain app.py", "repo_url": "team/alpha", "repo_revision": "b" * 40,
                })
                assert mismatch.status_code == 409
