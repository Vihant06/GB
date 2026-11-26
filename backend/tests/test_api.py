"""
Tests for FastAPI endpoints (/health, /query, /query/stream).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_actor_identity

client = TestClient(app)

def _signed_in():
    return patch.dict(app.dependency_overrides, {get_actor_identity: lambda: "10000000-0000-4000-8000-000000000001"})


def test_workspace_requires_a_verified_account():
    payload = {"query": "Explain app.py", "repo_url": "pallets/flask"}
    with (
        patch("app.api.deps.SUPABASE_URL", "https://example.test"),
        patch("app.api.deps.SUPABASE_PUBLISHABLE_KEY", "test-publishable-key"),
        patch("app.api.deps.requests.get") as get_user,
        patch("app.api.routes.prepare_query_repo") as prepare,
        patch("app.api.routes.clone_and_index_repo") as index,
    ):
        for endpoint in ("/repo/index", "/api/repo/link", "/query", "/api/query/stream"):
            assert client.post(endpoint, json=payload, headers={"X-API-Key": "unverified"}).status_code == 401
        assert client.get("/repo/active").status_code == 401
        get_user.assert_not_called()

        headers = {"Authorization": "Bearer test-access-token"}
        get_user.return_value = MagicMock(status_code=401)
        assert client.post("/query", json=payload, headers=headers).status_code == 401
        original_user = {"id": "20000000-0000-4000-8000-000000000001", "role": "authenticated"}
        get_user.return_value.status_code = 200
        get_user.return_value.json.return_value = {**original_user, "is_anonymous": True}
        assert client.get("/repo/active", headers=headers).status_code == 401
        get_user.return_value.json.return_value = original_user
        assert client.get("/repo/active", headers=headers).status_code == 200
        assert get_user.call_args.args[0] == "https://example.test/auth/v1/user"
        assert get_user.call_args.kwargs["headers"]["Authorization"] == headers["Authorization"]
        prepare.assert_not_called()
        index.assert_not_called()

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["database_ready"] is True
    assert "repo_name" in data


def test_query_validation():
    # Empty query should fail validation (min_length=1)
    with _signed_in():
        response = client.post("/query", json={"query": ""})
    assert response.status_code == 422


def test_query_non_streaming():
    with _signed_in(), patch("app.api.routes.repomind_graph.invoke") as mock_invoke:
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

    with _signed_in(), patch("app.api.routes.repomind_graph.astream", mock_astream):
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


def test_public_fastapi_question_bypasses_linked_checkout():
    from app.graph.state import QueryRouteJudgment

    url = "https://fastapi.tiangolo.com/tutorial/first-steps/"
    with (
        _signed_in(),
        patch.dict("os.environ", {"REPOMIND_DEMO_REPOS": "fastapi/fastapi"}),
        patch("app.api.routes.prepare_query_repo") as prepare,
        patch("app.graph.nodes.check_and_record_query_rate_limit", return_value=(True, "")),
        patch("app.graph.nodes.structured_invoke") as judge,
        patch("app.graph.nodes.fast_llm") as model,
        patch("app.graph.nodes.firecrawl_search_public") as search,
    ):
        judge.return_value = QueryRouteJudgment(
            route="web_research", reason="Public framework usage",
            public_search_query="FastAPI official docs first steps building APIs",
        )
        search.return_value = [{"url": url, "title": "First Steps", "description": "Build an API with @app.get."}]
        model.invoke.return_value.content = "FastAPI builds HTTP APIs. See [web:1]. [web:9]"
        for endpoint, query in (
            ("/query", "Give me the starting snippet code and tell me what FastAPI is used in."),
            ("/query/stream", "I asked you where FastAPI is used in general."),
        ):
            response = client.post(endpoint, json={
                "query": query, "repo_url": "fastapi/fastapi", "repo_revision": "a" * 40,
            })
            assert response.status_code == 200
            assert url in response.text
            assert "FastAPI builds HTTP APIs" in response.text
            assert "[web:9]" not in response.text
        prepare.assert_not_called()
        assert judge.call_count == search.call_count == 2
        assert search.call_args.args == ("FastAPI official docs first steps building APIs",)
        assert "@app.get" in model.invoke.call_args.args[0][-1].content


def test_ready_repository_does_not_wait_for_indexing_lock():
    from app import repo_manager

    revision = "a" * 40
    with TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "team" / "alpha"
        repo.mkdir(parents=True)
        cached = {"repo_name": "team/alpha", "repo_revision": revision}
        with (
            patch.object(repo_manager, "_cache_root", root),
            patch.object(repo_manager, "_ready_indexes", {repo: cached}),
            patch.object(repo_manager, "_checked_revision", return_value=revision) as check,
            patch.object(repo_manager, "_index_lock") as lock,
        ):
            lock.__enter__.side_effect = AssertionError("Warm lookup tried to take the indexing lock")
            assert repo_manager.prepare_query_repo("team/alpha", revision) == cached
            check.assert_called_once_with(repo, revision)
            check.side_effect = ValueError("Dirty checkout")
            try:
                repo_manager.prepare_query_repo("team/alpha", revision)
            except ValueError as exc:
                assert str(exc) == "Dirty checkout"
            else:
                raise AssertionError("Warm lookup must still validate the checkout")


def test_public_repository_quota_across_index_and_query_routes():
    from app.database import reserve_repository_slot

    prepared = {
        "status": "ready", "repo_name": "team/custom", "repo_url": "https://github.com/team/custom",
        "repo_path": ".", "repo_revision": "a" * 40, "total_files_count": 1,
        "docs_files_count": 0, "python_files_count": 1,
    }
    with (
        _signed_in(),
        TemporaryDirectory() as directory,
        patch("app.api.routes.reserve_repository_slot", side_effect=lambda actor, repo:
              reserve_repository_slot(actor, repo, str(Path(directory) / "quota.db"))),
        patch("app.api.routes.clone_and_index_repo", return_value=prepared) as index,
        patch("app.api.routes.prepare_query_repo") as prepare,
    ):
        for number in range(5):
            endpoint = "/repo/index" if number % 2 == 0 else "/api/repo/link"
            assert client.post(endpoint, json={"repo_url": f"team/project-{number}"}).status_code == 200
        assert client.post("/repo/index", json={"repo_url": "https://github.com/Team/Project-0.git"}).status_code == 200
        for endpoint in ("/repo/index", "/api/repo/link", "/query", "/api/query/stream"):
            response = client.post(endpoint, json={"query": "Explain app.py", "repo_url": "team/extra"})
            assert response.status_code == 429
            assert "5 distinct public repositories" in response.json()["detail"]
        assert index.call_count == 6
        prepare.assert_not_called()
        prepare.side_effect = ValueError("Revision unavailable")
        payload = {"repo_url": "team/project-0", "repo_revision": "a" * 40, "query": "Explain app.py"}
        assert client.post("/query/stream", json=payload).status_code == 409
        prepare.assert_called_once_with("team/project-0", "a" * 40)
        with patch.dict(app.dependency_overrides, {get_actor_identity: lambda: "another-account"}):
            assert client.post("/repo/index", json={"repo_url": "team/extra"}).status_code == 200
        from time import time

        next_year = time() + 366 * 24 * 60 * 60
        with patch("app.database.time.time", return_value=next_year):
            assert client.post("/repo/index", json={"repo_url": "team/extra"}).status_code == 200


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
            _signed_in(),
            patch.dict("os.environ", {"REPOMIND_DEMO_REPOS": ",".join(expected)}),
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
