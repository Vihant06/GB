"""
Tests for app.agents.base and structured JSON extraction.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from app.agents.base import (
    build_json_template,
    compact_history,
    repair_truncated_json,
    structured_invoke,
)


class SampleModel(BaseModel):
    name: str = Field(description="Name of item")
    count: int = Field(default=1)
    tags: list[str] = Field(default_factory=list)


def test_compact_history():
    msgs = [
        HumanMessage(content="Investigate session pool"),
        AIMessage(content="<think>internal thought</think>Let me call tool"),
        ToolMessage(content="pool size is 10", tool_call_id="1", name="read_file_lines"),
    ]
    compacted = compact_history(msgs)
    assert len(compacted) == 1
    content = compacted[0].content
    assert "Investigate session pool" in content
    assert "[read_file_lines]" in content
    assert "<think>" not in content


def test_build_json_template():
    template = build_json_template(SampleModel)
    assert '"name": "Name of item"' in template
    assert '"count": 1' in template
    assert '"tags": [' in template


def test_repair_truncated_json():
    # Truncated string in key-value
    truncated = '{"name": "test", "tags": ["a", "b'
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    assert repaired.endswith("}")

    # Already valid
    valid = '{"name": "test"}'
    assert repair_truncated_json(valid) == valid


def test_structured_invoke_mocked():
    class DummyResponse:
        content = '{"name": "requests", "count": 5, "tags": ["http", "network"]}'
        additional_kwargs = {}

    class DummyModel:
        def bind(self, **kwargs):
            return self

        def invoke(self, msgs):
            return DummyResponse()

    result = structured_invoke(DummyModel(), SampleModel, [HumanMessage(content="dummy")])
    assert isinstance(result, SampleModel)
    assert result.name == "requests"
    assert result.count == 5
    assert result.tags == ["http", "network"]
