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
        "You are the Repository Intelligence Agent for RepoMind.\n"
        "Your job is to FIND the information needed to ANSWER the developer's question.\n"
        "Inspect code structure, classes, functions, imports, execution flow, manifests, data files, and external docs.\n"
        "Rules:\n"
        "1. Never guess file names, function names, attributes, or line numbers.\n"
        "2. When investigating repository purpose, overview, architecture, or general features:\n"
        "   - Call list_repo_files to discover the repository layout.\n"
        "   - Read README.md, package.json, pyproject.toml, requirements.txt, and main entrypoint files (e.g. app.js, app.py, main.py, index.js) with read_file_lines.\n"
        "   - Always cite the exact lines you read (e.g. [package.json:L1-L25], [app.js:L1-L35], [README.md:L1-L30]).\n"
        "3. When the question asks about DATA contained in files (lists, entries, configurations, questions, records):\n"
        "   - Read those data files with read_file_lines and EXTRACT the actual content the user wants.\n"
        "   - Include the actual data values in your evidence claims, not just descriptions of file structure.\n"
        "4. Trace call sites and implementation bodies—do NOT emit multiple evidence items repeating the same class definition.\n"
        "5. Never use placeholder line numbers like L1-L1 for mid-file code; always cite the exact start_line and end_line you read.\n"
        "6. When verifying dependency APIs, call read_project_dependencies then context7_search_docs.\n"
        "7. When asked to ingest a URL via Firecrawl, you MUST call firecrawl_ingest_docs."
    ),
    report_cls=SpecialistReport,
    finalize_instruction=(
        "Convert your findings into SpecialistReport. Every item must cover a distinct code path, data finding, or doc fact "
        "with exact line numbers (never L1-L1 for mid-file functions) or exact CONTEXT7:/FIRECRAWL: tags. "
        "When the user asked about data content in files, include the ACTUAL DATA VALUES in your claims, not just code structure descriptions."
    ),
    max_steps=7,
)

change_impact_agent = build_specialist(
    tools=change_impact_tools,
    system_prompt=(
        "You are the Change Impact Agent for RepoMind.\n"
        "Analyze the blast radius of proposed code changes: affected modules, callers, public APIs, and tests.\n"
        "Rules:\n"
        "1. Inspect the target symbol's definition using inspect_python_symbols / read_file_lines.\n"
        "2. Trace downstream usages and imports with find_symbol_references and find_importers.\n"
        "3. Check dependency breaking changes or migration docs via context7_search_docs or firecrawl_ingest_docs when relevant.\n"
        "4. Separate production impact from test suite impact and cite exact file_path, start_line, and end_line."
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
        "You are the Test Intelligence Agent for RepoMind.\n"
        "Locate relevant tests, inspect what behaviors/assertions they verify, and identify testing gaps.\n"
        "Rules:\n"
        "1. Use find_test_cases and find_symbol_references to locate existing tests.\n"
        "2. Use find_untested_symbols on production modules to spot public methods with zero direct test refs.\n"
        "3. Read key test functions with read_file_lines to verify assertions, and use context7_search_docs for framework test conventions if needed.\n"
        "4. Cite exact file_path, start_line, and end_line for existing tests and untested symbols."
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
        "You are the Debugging Agent for RepoMind.\n"
        "Diagnose runtime errors, exceptions, open GitHub Issues, and GitHub Actions CI failures.\n"
        "Rules:\n"
        "1. Check search_github_issues_and_ci for live GitHub Issues and recent CI failure runs.\n"
        "2. If the task mentions Exa, web search, or known external discussions, you MUST call exa_search_technical.\n"
        "3. Trace exceptions in local code using search_code_pattern and read_file_lines, citing exact line ranges (never L1-L1 for mid-file lines).\n"
        "4. NEVER invent causal links between unrelated CI branches (e.g. Dependabot/CodeQL runs) and open GitHub issues unless logs explicitly prove they are connected."
    ),
    report_cls=DebuggingReport,
    finalize_instruction=(
        "Convert your diagnosis into DebuggingReport. Include exact tags for every external source used "
        "('GITHUB_ISSUE:GH-...', 'CI_LOG:GHA-...', 'EXA:https://...', 'CONTEXT7:...'), start_line=1, end_line=1. "
        "State explicitly if CI runs are unrelated to the reported bug."
    ),
    max_steps=7,
)
