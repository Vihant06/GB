"""
Tests for FastAPI endpoints (/health, /query, /query/stream).
"""

from unittest.mock import MagicMock, patch
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
        yield {"input_guardrail": {"trace_logs": ["[Input Guardrail] OK"]}}
        yield {"orchestrator_plan": {"plan_reasoning": "Plan ready", "trace_logs": ["[Plan] Ready"]}}
        yield {"orchestrator_synthesize": {"final_report": "Streamed report content."}}

    with patch("app.api.routes.repomind_graph.astream", mock_astream):
        response = client.post(
            "/query/stream",
            json={"query": "Explain session pooling"},
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
