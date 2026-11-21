"""Prompts and contracts for each agent role.

What an agent may write is enforced by the scope guard in the orchestrator, not here.
"""

from __future__ import annotations

import json

from forge.agents.base import AgentSpec
from forge.schemas import (
    AnalysisOutput,
    DesignOutput,
    DeveloperOutput,
    QAOutput,
    QARevisionOutput,
    ReleaseOutput,
    ReviewOutput,
    ScopeArbitrationOutput,
    SecurityReviewOutput,
)


def _j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def block(tag: str, body: str) -> str:
    return f"<<<{tag}>>>\n{(body or '').rstrip()}\n<<<END {tag}>>>"


def clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters omitted)"


def files_block(files: dict[str, str | None]) -> str:
    return "\n\n".join(
        block(f"FILE {path}", content if content is not None else "(file does not exist yet)")
        for path, content in files.items()
    )


def _sections(*pairs: tuple[str, str]) -> str:
    return "\n\n".join(f"## {title}\n{body}" for title, body in pairs if body)


ANALYST = AgentSpec(
    name="analyst",
    role="Turn a product requirement into testable acceptance criteria and a task plan.",
    contract=AnalysisOutput,
    instructions="""
Write acceptance criteria as Given/When/Then with ids AC-1, AC-2, ... Each criterion must be checkable by an
automated test: name functions, inputs and expected results where the requirement does.
Write a task plan with ids T-1, T-2, ...; each task lists the criteria ids it satisfies.
Record assumptions you made. List open questions only for ambiguities that change behaviour; do not ask
about anything the requirement already states. Include a criterion that existing behaviour is preserved
when the requirement says so.""",
)

ARCHITECT = AgentSpec(
    name="architect",
    role="Identify impacted components and propose the smallest design that meets the criteria.",
    contract=DesignOutput,
    instructions="""
Use the repository index and import graph. List every source file that must change as an impacted
component with path, symbols, change_type ("modify" an existing file or "create" a new one) and reason.
The list becomes the developer's file allowlist: files you omit cannot be edited without a scope request.
Prefer the smallest change set. Never include test files (QA owns tests) or configuration files.
List files you deliberately keep out of scope with a reason, especially high-risk files.
Name concrete risks (behaviour changes, concurrency, data migration, security).""",
)

ARBITER = AgentSpec(
    name="arbiter",
    role="As the architect, rule on the developer's requests to expand the change scope.",
    contract=ScopeArbitrationOutput,
    instructions="""
For each requested path return exactly one ruling: "approve" when the file is needed to meet the criteria
and the justification fits the design, otherwise "reject" with a reason and an alternative.
Keep the change small; do not approve files only because they are related.""",
)

DEVELOPER = AgentSpec(
    name="developer",
    role="Implement the planned change with minimal search/replace edits inside the approved scope.",
    contract=DeveloperOutput,
    max_tokens=16384,
    instructions="""
Each edit replaces the text `search` with `replace` in `path`.
- `search` must be copied exactly from the current file content (no line numbers) and match exactly one
  place. Include two or three surrounding lines to make it unique.
- To create a new file, use an empty `search` and put the whole file in `replace`.
- Edit only files in the change scope. If you need another file, return no edits and one scope_request per
  file with a justification; you will be asked again after the architect rules on it.
- Never edit tests; QA owns tests. Never remove authorization, validation or security checks.
- Keep behaviour the criteria do not change. New optional parameters must default to the existing
  behaviour (for example, an omitted timestamp must not turn into "now"). Existing tests encode that
  behaviour and must keep passing. Follow the code style already in the file.
- Use parameterised SQL. Never log card numbers, passwords or keys.
- When fixing a failure, change only what the failure requires.
- If the reported failure cannot be fixed by changing code, return no edits and explain in no_change_reason.""",
)

QA = AgentSpec(
    name="qa",
    role="Write new automated tests that verify every acceptance criterion against the change.",
    contract=QAOutput,
    max_tokens=12288,
    instructions="""
Write new pytest files under the test directory, named test_forge_<topic>.py. Each file lists the criteria
ids it covers; together the files must cover every criterion. Use existing fixtures from conftest.py when
they fit. Tests must be deterministic, offline and fast: use only pytest built-in fixtures (tmp_path,
monkeypatch, capsys) and the standard library; never use plugin fixtures such as mocker. Pass explicit
times or values instead of reading the clock. Do not modify existing files. Test the behaviour
the criteria describe, not implementation details. Prefix test names with the criterion, e.g. test_ac1_...""",
)

QA_REVIEW = AgentSpec(
    name="qa",
    role="Settle a dispute: the developer says a failing QA test contradicts the requirement.",
    contract=QARevisionOutput,
    max_tokens=12288,
    instructions="""
Decide who is right using the requirement text and the acceptance criteria as the only source of truth.
- If your test asserts behaviour the requirement does not ask for, or contradicts it, return verdict
  "test_wrong" with the full corrected content of each QA test file you change. Keep every other test.
- If the code does not meet the requirement, return verdict "code_wrong" and explain precisely which
  requirement sentence the code violates, so the developer can fix it.
Quote the requirement sentence you rely on. Do not weaken a test just to make it pass.""",
)

SECURITY = AgentSpec(
    name="security",
    role="Review the change for vulnerabilities that static scanners miss.",
    contract=SecurityReviewOutput,
    instructions="""
Scanner findings are listed in the input. You cannot remove or downgrade them. Report only additional
findings, with source "llm", using paths from the diff: authorization gaps, race conditions, missing input
validation, injection, unsafe handling or logging of card data and credentials.
Use critical or high only for issues that are exploitable as written. Give a concrete recommendation.""",
)

REVIEWER = AgentSpec(
    name="reviewer",
    role="Score the change against a fixed rubric and summarise evidence and known risks for the approver.",
    contract=ReviewOutput,
    instructions="""
Score each rubric criterion exactly once from 1 to 5 with a justification grounded in the evidence:
requirements_coverage, correctness, test_quality, security, scope_discipline, code_quality.
A 5 needs direct evidence. For each acceptance criterion give its coverage status and the evidence
(test name or code location). List known risks honestly, including limitations of the tests.
Recommend request_changes only for problems that must be fixed before merge.""",
)

RELEASE = AgentSpec(
    name="release",
    role="Assemble release notes, a rollback plan and deployment checks for the human approver.",
    contract=ReleaseOutput,
    instructions="""
Write release notes a reviewer can read in one minute, a concrete rollback plan for this change, a risk
level, and checks to run after deployment. You cannot merge or deploy: a human approves both.""",
)

ALL_SPECS = [ANALYST, ARCHITECT, ARBITER, DEVELOPER, QA, QA_REVIEW, SECURITY, REVIEWER, RELEASE]


def analyst_prompt(requirement: str, repo_summary: str, clarifications: str | None, feedback: str | None) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Answers from the product owner", clarifications or ""),
        ("Reviewer feedback on your previous analysis", feedback or ""),
        ("Repository overview", block("REPO INDEX", clip(repo_summary, 3500))),
    )


def architect_prompt(requirement, analysis, repo_summary, ranked, files, impact, cfg, clarifications) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Acceptance criteria and tasks", _j(analysis)),
        ("Product owner answers", clarifications or ""),
        ("Repository index (modules, signatures, imports)", block("REPO INDEX", clip(repo_summary, 7000))),
        ("Files ranked by relevance to the requirement", _j(ranked)),
        ("Import graph for the ranked files", _j(impact)),
        ("Policy", _j({"source_dirs": cfg.source_dirs, "test_dirs": cfg.test_dirs,
                       "protected_paths": cfg.protected_paths, "high_risk_paths": cfg.high_risk_paths})),
        ("Contents of the most relevant files", files_block(files)),
    )


def developer_prompt(requirement, analysis, design, scope, files, feedback, test_names) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Acceptance criteria and tasks", _j(analysis)),
        ("Design", _j(design)),
        ("Change scope (you may edit only these files)", _j(scope)),
        ("Existing tests (read-only; they must keep passing)", ", ".join(test_names)),
        ("Feedback you must address", feedback or ""),
        ("Current content of files in scope", files_block(files)),
    )


def arbiter_prompt(requirement, design, requests, files) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Your design", _j(design)),
        ("Scope expansion requests from the developer", _j(requests)),
        ("Requested files", files_block(files)),
    )


def qa_prompt(analysis, design, diff, files, conftest, existing_tests, test_dir) -> str:
    return _sections(
        ("Acceptance criteria", _j(analysis.get("acceptance_criteria", []))),
        ("Design", _j({k: design.get(k) for k in ("summary", "approach")})),
        ("Write new files under", test_dir + "/"),
        ("Existing test files (do not modify)", ", ".join(existing_tests)),
        ("Shared fixtures", files_block({f"{test_dir}/conftest.py": conftest}) if conftest else ""),
        ("Change under test", block("DIFF", clip(diff, 12000))),
        ("Changed source files after the change", files_block(files)),
    )


def qa_dispute_prompt(requirement, analysis, failures, developer_reason, qa_files, source_files) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Acceptance criteria", _j(analysis.get("acceptance_criteria", []))),
        ("Failing tests", block("TEST OUTPUT", clip(failures, 6000))),
        ("Developer's position (the developer made no code change)", block("DEVELOPER", developer_reason)),
        ("Your test files", files_block(qa_files)),
        ("Current implementation", files_block(source_files)),
    )


def security_prompt(requirement, diff, scanner_findings, changed) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Changed files", _j(changed)),
        ("Scanner findings (bandit, secrets, logging policy)", _j(scanner_findings)),
        ("Diff", block("DIFF", clip(diff, 14000))),
    )


def reviewer_prompt(requirement, analysis, design, diff, tests, qa_tests, quality, security, history) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Acceptance criteria", _j(analysis.get("acceptance_criteria", []))),
        ("Design", _j({k: design.get(k) for k in ("summary", "approach", "risks")})),
        ("Final test run", _j(tests)),
        ("QA tests and the criteria they cover", _j(qa_tests)),
        ("Quality checks", _j(quality)),
        ("Security findings", _j(security)),
        ("Workflow history (fix iterations, policy violations, scope changes)", _j(history)),
        ("Diff", block("DIFF", clip(diff, 14000))),
    )


def release_prompt(requirement, review, tests, security, changed) -> str:
    return _sections(
        ("Requirement", block("REQUIREMENT", requirement)),
        ("Changed files", _j(changed)),
        ("Review", _j(review)),
        ("Tests", _j(tests)),
        ("Security", _j(security)),
    )
