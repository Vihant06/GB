"""
RepoMind base specialist builder and structured JSON extraction utilities.
Ensures history compaction, robust JSON recovery, and compiled LangGraph specialist subgraphs.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, Type, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from typing_extensions import TypedDict

from app.config import fast_llm, llm

T = TypeVar("T", bound=BaseModel)


class SpecialistState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_steps: int
    report: BaseModel | None


def compact_history(messages: list, max_total_chars: int = 9000) -> list:
    """Flatten tool calls and observations into one clean HumanMessage (prevents orphaned ToolMessage errors)."""
    task_text = ""
    findings = []

    for m in messages:
        content = m.content if isinstance(m.content, str) else str(m.content)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        if not content:
            continue

        if isinstance(m, HumanMessage) and not task_text:
            task_text = content
        elif isinstance(m, ToolMessage):
            tool_name = getattr(m, "name", "tool")
            findings.append(f"[{tool_name}]\n{content[:2200]}")
        elif isinstance(m, AIMessage):
            findings.append(f"[Analysis]\n{content[:1500]}")

    digest = (
        f"Investigation Task:\n{task_text}\n\n"
        f"Collected Evidence & Tool Outputs:\n" + "\n\n---\n\n".join(findings)
    )
    if len(digest) > max_total_chars:
        digest = digest[:max_total_chars] + "\n...[truncated]..."

    return [HumanMessage(content=digest)]


def build_json_template(schema_cls: Type[BaseModel]) -> str:
    """Convert a Pydantic model into a flat JSON example without verbose $defs."""
    root = schema_cls.model_json_schema()
    defs = root.get("$defs", {})

    def _example_for_prop(prop: dict):
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            return _example_for_obj(defs.get(ref_name, {}))
        if "anyOf" in prop:
            non_null = [x for x in prop["anyOf"] if x.get("type") != "null"]
            return _example_for_prop(non_null[0]) if non_null else None
        if "default" in prop:
            return prop["default"]
        if "enum" in prop:
            return " | ".join(str(v) for v in prop["enum"])
        ptype = prop.get("type")
        if ptype == "string":
            return prop.get("description", "string")
        if ptype == "integer":
            return 1
        if ptype == "number":
            return 0.0
        if ptype == "boolean":
            return True
        if ptype == "array":
            return [_example_for_prop(prop.get("items", {}))]
        if ptype == "object":
            return _example_for_obj(prop)
        return "string"

    def _example_for_obj(obj_schema: dict) -> dict:
        return {
            field_name: _example_for_prop(prop)
            for field_name, prop in obj_schema.get("properties", {}).items()
        }

    return json.dumps(_example_for_obj(root), indent=2)


def repair_truncated_json(text: str) -> str | None:
    """Close unclosed quotes, arrays, and braces if JSON output was cut off mid-stream."""
    start = text.find("{")
    if start == -1:
        return None
    s = text[start:].strip()

    # Normalize trailing commas before closing braces
    s = re.sub(r",\s*([}\]])", r"\1", s)

    # Insert missing comma between adjacent objects: "} {" -> "}, {"
    s = re.sub(r"\}\s*\{", "}, {", s)

    if s.endswith("}"):
        return s

    last_cut = max(s.rfind(","), s.rfind("}"), s.rfind("]"))
    if last_cut > 0 and not s.endswith(("}", "]")):
        # Only truncate to last separator if ending is clearly incomplete
        candidate_tail = s[last_cut:]
        if "{" in candidate_tail or '"' in candidate_tail:
            s = s[:last_cut]

    in_str, esc, stack = False, False, []
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()

    if in_str:
        s += '"'
    s = re.sub(r",\s*$", "", s)
    while stack:
        s += stack.pop()
    return re.sub(r",\s*([}\]])", r"\1", s)


def _safe_parse_model(schema_cls: Type[T], raw_json: str) -> T | None:
    """Attempt multiple parsing strategies to parse raw JSON into schema_cls."""
    # 1. Direct Pydantic JSON validation
    try:
        return schema_cls.model_validate_json(raw_json)
    except Exception:
        pass

    # 2. Python json.loads with strict=False (allows unescaped control characters)
    try:
        parsed_dict = json.loads(raw_json, strict=False)
        return schema_cls.model_validate(parsed_dict)
    except Exception:
        pass

    # 3. Clean trailing commas & missing commas, then parse
    cleaned = re.sub(r",\s*([}\]])", r"\1", raw_json)
    cleaned = re.sub(r"\}\s*\{", "}, {", cleaned)
    try:
        parsed_dict = json.loads(cleaned, strict=False)
        return schema_cls.model_validate(parsed_dict)
    except Exception:
        pass

    # 4. Repair truncated structure
    repaired = repair_truncated_json(raw_json)
    if repaired:
        try:
            return schema_cls.model_validate_json(repaired)
        except Exception:
            pass
        try:
            parsed_dict = json.loads(repaired, strict=False)
            return schema_cls.model_validate(parsed_dict)
        except Exception:
            pass

    return None


def _fallback_extract_report(schema_cls: Type[T], text: str) -> T | None:
    """Robust regex fallback for report schemas when JSON syntax is corrupted."""
    try:
        # Extract summary if present
        summary_m = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
        summary = summary_m.group(1) if summary_m else "Investigated repository files and collected evidence."

        # Extract specialist if present
        spec_m = re.search(r'"specialist"\s*:\s*"([^"]+)"', text)
        specialist = spec_m.group(1) if spec_m else "repository_intelligence"

        # Extract evidence items by regex
        evidence_items = []
        item_pattern = re.compile(
            r'\{[^{}]*?"claim"\s*:\s*"(?P<claim>[^"]+)"[^{}]*?"file_path"\s*:\s*"(?P<fp>[^"]+)"[^{}]*?"start_line"\s*:\s*(?P<start>\d+)[^{}]*?"end_line"\s*:\s*(?P<end>\d+)[^{}]*?\}',
            re.DOTALL,
        )
        for m in item_pattern.finditer(text):
            evidence_items.append({
                "claim": m.group("claim"),
                "file_path": m.group("fp"),
                "start_line": int(m.group("start")),
                "end_line": int(m.group("end")),
            })

        fallback_dict = {
            "specialist": specialist,
            "summary": summary,
            "evidence": evidence_items,
        }
        return schema_cls.model_validate(fallback_dict)
    except Exception:
        return None


def structured_invoke(model: Any, schema_cls: Type[T], messages: list) -> T:
    """Extract validated Pydantic JSON via compact templates + JSON mode."""
    template = build_json_template(schema_cls)
    combined_text = "\n\n".join(
        m.content if isinstance(m.content, str) else str(m.content) for m in messages
    )
    sys_msg = SystemMessage(
        content=(
            "/no_think\n"
            "You are a strict JSON extractor. Output ONLY raw valid JSON. "
            "Keep summaries concise and include AT MOST 5 items per list/array."
        )
    )
    prompt_msg = HumanMessage(
        content=(
            f"{combined_text}\n\n"
            "Respond ONLY with a single valid JSON object matching this exact structure "
            "(AT MOST 5 items in any list, no markdown fences, no commentary):\n"
            f"{template}"
        )
    )

    runners = []
    if hasattr(model, "bind"):
        try:
            runners.append(model.bind(response_format={"type": "json_object"}))
        except Exception:
            pass
    runners.append(model)
    if llm and llm is not model:
        runners.append(llm)

    last_err = None
    last_cleaned = ""
    for runner in runners:
        try:
            resp = runner.invoke([sys_msg, prompt_msg])
        except Exception as e:
            last_err = e
            continue

        raw = (
            getattr(resp, "content", "")
            or getattr(resp, "additional_kwargs", {}).get("reasoning_content", "")
            or ""
        )
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
        last_cleaned = cleaned

        candidate = None
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            candidate = match.group(0)
        elif "{" in cleaned:
            candidate = repair_truncated_json(cleaned)

        if not candidate:
            last_err = ValueError(f"Empty or non-JSON response: {cleaned[:250]}")
            continue

        result = _safe_parse_model(schema_cls, candidate)
        if result is not None:
            return result

        last_err = ValueError(f"Could not parse valid {schema_cls.__name__} from model output.")

    # Final fallback attempt for report schemas
    if last_cleaned:
        fallback = _fallback_extract_report(schema_cls, last_cleaned)
        if fallback is not None:
            return fallback

    if last_err is not None:
        raise last_err
    raise ValueError("Failed to extract structured response from model.")


def build_specialist(
    tools: list,
    system_prompt: str,
    report_cls: Type[BaseModel],
    finalize_instruction: str,
    max_steps: int = 6,
):
    """Assemble and compile an isolated specialist LangGraph ReAct agent."""
    model = fast_llm or llm
    bound_llm = model.bind_tools(tools) if hasattr(model, "bind_tools") else model

    def agent_node(state: SpecialistState):
        msgs = [SystemMessage(content=system_prompt)] + state["messages"]
        response = bound_llm.invoke(msgs)
        return {"messages": [response], "tool_steps": state.get("tool_steps", 0) + 1}

    def route_agent(state: SpecialistState) -> Literal["tools", "finalize"]:
        last_msg = state["messages"][-1]
        if getattr(last_msg, "tool_calls", None) and state.get("tool_steps", 0) < max_steps:
            return "tools"
        return "finalize"

    def finalize_node(state: SpecialistState):
        report = structured_invoke(
            fast_llm or llm,
            report_cls,
            compact_history(state["messages"]) + [HumanMessage(content=finalize_instruction)],
        )
        return {"report": report}

    g = StateGraph(SpecialistState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_agent, {"tools": "tools", "finalize": "finalize"})
    g.add_edge("tools", "agent")
    g.add_edge("finalize", END)
    return g.compile()
