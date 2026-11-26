"""
RepoMind LangGraph State models and Pydantic schemas.
Defines EvidenceItem, Specialist Reports, InvestigationPlan, and RepoMindState.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict


class EvidenceItem(BaseModel):
    claim: str = Field(description="Technical finding answering part of the query.")
    file_path: str = Field(
        description=(
            "Relative repository path (e.g. 'src/requests/sessions.py') OR external tag "
            "('GITHUB_ISSUE:GH-...', 'CI_LOG:GHA-...', 'CONTEXT7:lib@ver', 'EXA:https://...', 'FIRECRAWL:https://...')."
        )
    )
    start_line: int = Field(description="Starting line number supporting this claim (1 for external sources).")
    end_line: int = Field(description="Ending line number supporting this claim (1 for external sources).")


class SpecialistReport(BaseModel):
    specialist: Literal["repository_intelligence"] = "repository_intelligence"
    summary: str = Field(description="Concise architectural or execution-flow explanation.")
    evidence: list[EvidenceItem] = Field(
        description="Grounded findings with exact file/line or external citations."
    )


class ChangeImpactReport(BaseModel):
    specialist: Literal["change_impact"] = "change_impact"
    summary: str = Field(description="Concise blast-radius summary covering affected modules, APIs, and tests.")
    evidence: list[EvidenceItem] = Field(
        description="Grounded impact findings with exact file/line or external citations."
    )


class TestIntelligenceReport(BaseModel):
    specialist: Literal["test_intelligence"] = "test_intelligence"
    summary: str = Field(description="Concise summary of relevant tests, coverage findings, and testing gaps.")
    evidence: list[EvidenceItem] = Field(
        description="Grounded test and coverage findings with exact file/line or external citations."
    )


class DebuggingReport(BaseModel):
    specialist: Literal["debugging"] = "debugging"
    summary: str = Field(
        description="Concise root-cause diagnosis referencing code, GitHub Issues, CI runs, or external docs."
    )
    evidence: list[EvidenceItem] = Field(
        description="Grounded debugging findings with exact file/line or external citations."
    )


SpecialistName = Literal[
    "repository_intelligence",
    "debugging",
    "change_impact",
    "test_intelligence",
]


class QueryRouteJudgment(BaseModel):
    route: Literal["off_topic", "general_coding", "web_research", "repo_specific"] = Field(
        description=(
            "'off_topic' for non-coding topics (recipes, travel, movies, etc.); "
            "'general_coding' for standalone coding tasks that need no lookup; "
            "'web_research' for public technical explanations, official examples, docs, or current information; "
            "'repo_specific' when the answer depends on the linked checkout."
        )
    )
    reason: str = Field(description="One-line explanation for the chosen route.")
    public_search_query: str = Field(default="", description="Short public keywords for web_research only.")


class SpecialistTask(BaseModel):
    specialist: SpecialistName = Field(description="Which specialist agent to invoke.")
    sub_query: str = Field(description="Focused, concrete question for this specialist to investigate.")


class InvestigationPlan(BaseModel):
    reasoning: str = Field(description="Brief rationale for selecting these specialists.")
    tasks: list[SpecialistTask] = Field(
        min_length=1,
        max_length=4,
        description="1 to 4 parallel specialist tasks needed to answer the developer query.",
    )


class EvidenceEvaluation(BaseModel):
    is_sufficient: bool = Field(description="True if collected evidence is enough to accurately answer the query.")
    missing_info_feedback: str = Field(
        description="If is_sufficient is False, specify exactly what code, callers, logs, or tests are still missing."
    )


class RepoMindState(TypedDict):
    user_query: str
    chat_history: NotRequired[list[dict]]
    conversation_context: NotRequired[str]
    public_search_query: NotRequired[str]
    input_decision: NotRequired[dict]
    actor_id: str
    attempt_count: int
    plan_reasoning: str
    tasks: list[dict]
    evidence: Annotated[list[dict], operator.add]
    verified_evidence: list[dict]
    is_sufficient: bool
    missing_info_feedback: str
    trace_logs: Annotated[list[str], operator.add]
    final_report: str
