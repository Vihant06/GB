"""
RepoMind Specialist Agents definition.
Configures tools, system prompts, and report schemas for:
- Repository Intelligence
- Change Impact
- Test Intelligence
- Debugging
"""

from __future__ import annotations

from app.agents.base import build_specialist
from app.graph.state import (
    ChangeImpactReport,
    DebuggingReport,
    SpecialistReport,
    TestIntelligenceReport,
)
from app.tools.github_sync import search_github_issues_and_ci
from app.tools.local_code import (
    find_importers,
    find_symbol_references,
    find_test_cases,
    find_untested_symbols,
    inspect_python_symbols,
    list_repo_files,
    read_file_lines,
    read_project_dependencies,
    search_code_pattern,
)
from app.tools.web_docs import (
    context7_search_docs,
    exa_search_technical,
    firecrawl_ingest_docs,
)

repo_intel_tools = [
    list_repo_files,
    search_code_pattern,
    inspect_python_symbols,
    read_file_lines,
    read_project_dependencies,
    context7_search_docs,
    firecrawl_ingest_docs,
]

change_impact_tools = [
    find_importers,
    find_symbol_references,
    inspect_python_symbols,
    read_file_lines,
    read_project_dependencies,
    context7_search_docs,
    firecrawl_ingest_docs,
]

test_intel_tools = [
    find_test_cases,
    find_untested_symbols,
    find_symbol_references,
    read_file_lines,
    read_project_dependencies,
    context7_search_docs,
]

debugging_tools = [
    search_code_pattern,
    read_file_lines,
    read_project_dependencies,
    search_github_issues_and_ci,
    exa_search_technical,
    context7_search_docs,
]

repo_intel_agent = build_specialist(
    tools=repo_intel_tools,
    system_prompt=(
        "Find the exact local source for the question. Search for named symbols or data, then read the supporting lines. "
        "For overviews, use the README and entry point. For flows, follow callers into implementations. "
        "For code extraction, read the full requested block. For datasets, identify real files or source IDs. "
        "Use external docs only for an explicit request or unresolved library behavior."
    ),
    report_cls=SpecialistReport,
    finalize_instruction=(
        "Convert your findings into SpecialistReport. Every item must cover a distinct code path, data finding, or doc fact "
        "with EXACT verbatim file_path and exact line numbers (never L1-L1 for mid-file functions) or exact CONTEXT7:/FIRECRAWL: tags. "
        "If the user asked to show code/functions or list all items, include the full verbatim code snippet or complete list in the claim."
    ),
    max_steps=5,
)

change_impact_agent = build_specialist(
    tools=change_impact_tools,
    system_prompt=(
        "Read the target definition and trace callers, imports, public APIs, and tests. "
        "Separate verified impact from hypotheses. Use migration docs only when relevant."
    ),
    report_cls=ChangeImpactReport,
    finalize_instruction=(
        "Convert your change impact analysis into ChangeImpactReport. "
        "Include exact file_path, start_line, and end_line for every affected caller, API, doc, and test."
    ),
    max_steps=6,
)

test_intel_agent = build_specialist(
    tools=test_intel_tools,
    system_prompt=(
        "Locate tests and read their assertions. Explain what they demonstrate. "
        "Report coverage gaps only when requested and distinguish missing references from measured coverage."
    ),
    report_cls=TestIntelligenceReport,
    finalize_instruction=(
        "Convert your test and coverage analysis into TestIntelligenceReport. "
        "Cite specific test function line ranges (e.g. tests/test_requests.py:L1685-L1755) and untested symbols."
    ),
    max_steps=6,
)

debugging_agent = build_specialist(
    tools=debugging_tools,
    system_prompt=(
        "Trace the reported failure in local source. Check GitHub issues or CI when relevant. "
        "Use Exa for explicit web searches. Tie each cause to observed evidence."
    ),
    report_cls=DebuggingReport,
    finalize_instruction=(
        "Convert your diagnosis into DebuggingReport. Include exact tags for every external source used "
        "('GITHUB_ISSUE:GH-...', 'CI_LOG:GHA-...', 'EXA:https://...', 'CONTEXT7:...'), start_line=1, end_line=1. "
        "State explicitly if CI runs are unrelated to the reported bug."
    ),
    max_steps=7,
)
