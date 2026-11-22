"""
RepoMind LangGraph orchestrator workflow graph.
Assembles the complete state graph with parallel Send dispatch, 2-attempt review loops,
citation verification, no-evidence exits, and secret-redaction output guardrails.
Compiled once per process as a thread-safe singleton.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    fan_out_specialists,
    general_coding_node,
    input_guardrail_node,
    no_evidence_exit_node,
    orchestrator_plan_node,
    orchestrator_review_node,
    orchestrator_synthesize_node,
    output_guardrail_node,
    rate_limit_gate_node,
    route_after_input_guardrail,
    route_after_rate_limit,
    route_after_review,
    route_after_verify_citations,
    run_change_impact,
    run_debugging,
    run_repo_intel,
    run_test_intel,
    verify_citations_node,
)
from app.graph.state import RepoMindState


def build_repomind_graph():
    """Build and compile the RepoMind orchestrator StateGraph."""
    workflow = StateGraph(RepoMindState)

    workflow.add_node("input_guardrail", input_guardrail_node)
    workflow.add_node("rate_limit_gate", rate_limit_gate_node)
    workflow.add_node("general_coding", general_coding_node)
    workflow.add_node("orchestrator_plan", orchestrator_plan_node)
    workflow.add_node("repository_intelligence", run_repo_intel)
    workflow.add_node("debugging", run_debugging)
    workflow.add_node("change_impact", run_change_impact)
    workflow.add_node("test_intelligence", run_test_intel)
    workflow.add_node("orchestrator_review", orchestrator_review_node)
    workflow.add_node("verify_citations", verify_citations_node)
    workflow.add_node("no_evidence_exit", no_evidence_exit_node)
    workflow.add_node("orchestrator_synthesize", orchestrator_synthesize_node)
    workflow.add_node("output_guardrail", output_guardrail_node)

    # Edge wiring
    workflow.add_edge(START, "input_guardrail")
    workflow.add_conditional_edges(
        "input_guardrail",
        route_after_input_guardrail,
        {"rate_limit_gate": "rate_limit_gate", "end": END},
    )
    workflow.add_conditional_edges(
        "rate_limit_gate",
        route_after_rate_limit,
        {
            "orchestrator_plan": "orchestrator_plan",
            "general_coding": "general_coding",
            "end": END,
        },
    )
    workflow.add_edge("general_coding", "output_guardrail")
    workflow.add_conditional_edges(
        "orchestrator_plan",
        fan_out_specialists,
        ["repository_intelligence", "debugging", "change_impact", "test_intelligence"],
    )
    for specialist_node in ["repository_intelligence", "debugging", "change_impact", "test_intelligence"]:
        workflow.add_edge(specialist_node, "orchestrator_review")

    workflow.add_conditional_edges(
        "orchestrator_review",
        route_after_review,
        {"orchestrator_plan": "orchestrator_plan", "verify_citations": "verify_citations"},
    )
    workflow.add_conditional_edges(
        "verify_citations",
        route_after_verify_citations,
        {"orchestrator_synthesize": "orchestrator_synthesize", "no_evidence_exit": "no_evidence_exit"},
    )
    workflow.add_edge("no_evidence_exit", "output_guardrail")
    workflow.add_edge("orchestrator_synthesize", "output_guardrail")
    workflow.add_edge("output_guardrail", END)

    return workflow.compile()


# Compiled singleton graph
repomind_graph = build_repomind_graph()
