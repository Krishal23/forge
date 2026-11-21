"""Build the evidence pack shown at the merge gate.

Numbers come from stored tool output and the event log, not from what the agents say.
"""

from __future__ import annotations

from collections import Counter

from forge.store import Store

NOTABLE = {
    "policy_violation", "prompt_injection_suspected", "scope_request", "scope_ruling", "scope_expanded",
    "tests_failed", "tests_passed", "security_blocked", "schema_repaired", "agent_output_invalid",
    "provider_failover", "llm_retry", "circuit_open", "duplicate_suppressed", "chaos_injected", "escalated",
    "human_decision", "fix_iteration_started", "approval_recorded", "approval_rejected", "redaction",
    "developer_no_change", "run_resumed", "merged", "deployed",
}

MOCKS_AND_LIMITS = [
    "Deployment is simulated: the merge lands on the run's local main branch and a smoke test runs there.",
    "Approver identity is self-declared in the local dashboard; production would use SSO with RBAC.",
    "The sandbox is process-level (scrubbed env, rlimits, timeout, Python network guard), not a container.",
    "Cost figures are estimates at paid-tier list prices; free tiers bill $0.",
    "Mock mode replays a scripted story per scenario; live mode uses real models.",
]


def _art(store: Store, run_id: str, name: str) -> dict:
    a = store.get_artifact(run_id, name)
    return a["content"] if a else {}


def _md_table(headers: list[str], rows: list[list]) -> str:
    def cell(v) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def build_evidence(store: Store, run_id: str) -> dict:
    run = store.get_run(run_id)
    data = run["data"]
    analysis = _art(store, run_id, "analysis")
    design = _art(store, run_id, "design")
    diff = _art(store, run_id, "diff")
    tests = _art(store, run_id, "test_report")
    qa = _art(store, run_id, "qa_tests")
    quality = _art(store, run_id, "quality")
    security = _art(store, run_id, "security")
    review = _art(store, run_id, "review")
    release = _art(store, run_id, "release")
    acceptance = _art(store, run_id, "acceptance_eval")
    baseline = _art(store, run_id, "baseline_tests")
    deployment = _art(store, run_id, "deployment")
    events = store.events(run_id)
    counts = Counter(e["type"] for e in events)
    usage = store.usage_summary(run_id)
    approvals = store.approvals(run_id)
    chain_ok, bad = store.verify_chain(run_id)
    diff_art = store.get_artifact(run_id, "diff")

    criteria = analysis.get("acceptance_criteria", [])
    coverage = {c["criterion"]: c for c in review.get("coverage", [])}
    qa_by_ac: dict[str, list[str]] = {}
    for t in qa.get("tests", []):
        for c in t.get("criteria", []):
            qa_by_ac.setdefault(c, []).append(t["path"])
    matrix = [
        {"id": c["id"], "then": c["then"], "qa_tests": qa_by_ac.get(c["id"], []),
         "review_status": coverage.get(c["id"], {}).get("status", "unknown")}
        for c in criteria
    ]
    findings = security.get("findings", [])
    new_findings = [f for f in findings if not f.get("pre_existing")]
    rubric = review.get("rubric", [])
    avg = round(sum(r["score"] for r in rubric) / len(rubric), 2) if rubric else None
    history = [
        {"seq": e["seq"], "ts": e["ts"], "type": e["type"], "stage": e["stage"], "agent": e["agent"],
         "data": e["data"]}
        for e in events if e["type"] in NOTABLE
    ]
    summary = {
        "run_id": run_id,
        "title": run["title"],
        "target": run["target"],
        "mode": run["mode"],
        "state": run["state"],
        "branch": data.get("branch"),
        "base_sha": data.get("base_sha"),
        "head": diff.get("head"),
        "diff_hash": diff_art["hash"] if diff_art else None,
        "files_changed": diff.get("files", []),
        "tests": {k: tests.get(k) for k in ("ok", "passed", "failed", "errors", "total", "coverage_pct",
                                            "injected", "sandbox")},
        "baseline_coverage_pct": baseline.get("coverage_pct"),
        "test_iterations": len(data.get("test_history", [])),
        "fix_iterations": data.get("fix_iteration", 0),
        "lint_new_issues": len(quality.get("lint", {}).get("new_issues", [])),
        "security": {
            "new": len(new_findings),
            "pre_existing": len(findings) - len(new_findings),
            "blocking": len(security.get("blocking", [])),
            "accepted_risks": data.get("accepted_risks", []),
        },
        "review": {"recommendation": review.get("recommendation"), "average_score": avg},
        "acceptance_eval": acceptance or None,
        "policy_violations": counts["policy_violation"],
        "schema_repairs": counts["schema_repaired"],
        "provider_failovers": counts["provider_failover"],
        "duplicates_suppressed": counts["duplicate_suppressed"],
        "chaos_injected": counts["chaos_injected"],
        "usage": {k: usage[k] for k in ("calls", "errors", "cache_hits", "prompt_tokens", "completion_tokens",
                                         "cost_usd", "latency_ms_total", "providers")},
        "audit": {"chain_ok": chain_ok, "first_bad_seq": bad, "events": len(events),
                  "head_hash": events[-1]["hash"] if events else None},
        "approvals": [{k: a[k] for k in ("gate", "decision", "approver", "comment", "artifact_hash", "ts")}
                      for a in approvals],
        "known_risks": review.get("known_risks", []) + design.get("risks", []),
        "risk_level": release.get("risk_level"),
    }

    md = [f"# Evidence pack: {run['title'] or run_id}", ""]
    md += [f"- Run `{run_id}` on target `{run['target']}` (mode: {run['mode']}, state: {run['state']})",
           (f"- Branch `{data.get('branch')}` from base `{(data.get('base_sha') or '')[:10]}`, "
           f"head `{(diff.get('head') or '')[:10]}`"),
           f"- Diff hash `{(summary['diff_hash'] or '')[:16]}` (approvals are bound to this hash)",
           (f"- Audit log: {len(events)} hash-chained events, "
           f"{'verified intact' if chain_ok else f'TAMPERED at seq {bad}'}"), ""]
    md += ["## Requirement", "", run["requirement"], ""]
    if analysis:
        md += ["## Acceptance criteria and test evidence", "",
               _md_table(["AC", "Then", "QA tests", "Reviewer"],
                         [[m["id"], m["then"], ", ".join(m["qa_tests"]) or "none", m["review_status"]]
                          for m in matrix]), ""]
        if analysis.get("assumptions"):
            md += ["Assumptions:", *[f"- {a}" for a in analysis["assumptions"]], ""]
        if data.get("clarifications"):
            md += ["Product owner answers:", "", data["clarifications"], ""]
    if design:
        md += ["## Design and scope", "", design.get("approach", ""), "",
               "Approved scope: " + ", ".join(f"`{p}`" for p in data.get("scope", [])), ""]
        scope_changes = [h for h in data.get("scope_history", []) if h.get("action") != "set"]
        if scope_changes:
            md += ["Scope changes:", *[f"- {h['action']} `{h['path']}` by {h['by']}: {h.get('reason', '')}"
                                       for h in scope_changes], ""]
        if design.get("out_of_scope"):
            md += ["Kept out of scope:", *[f"- `{o['path']}`: {o['reason']}" for o in design["out_of_scope"]], ""]
    md += ["## Change", "", _md_table(["File", "Status", "+", "-"],
                                      [[f["path"], f["status"], f["added"], f["removed"]]
                                       for f in diff.get("files", [])]), ""]
    md += ["## Tests", "",
           (f"Final run: **{tests.get('passed', 0)} passed, {tests.get('failed', 0)} failed, "
           f"{tests.get('errors', 0)} errors**, line coverage {tests.get('coverage_pct')}% "
           f"(baseline {baseline.get('coverage_pct')}%). Sandbox: {tests.get('sandbox', 'n/a')}."), ""]
    if data.get("test_history"):
        md += [_md_table(["#", "Result", "Passed", "Failed", "Coverage", "Note"],
                         [[i + 1, "pass" if h["ok"] else "FAIL", h["passed"], h["failed"] + h.get("errors", 0),
                           h.get("coverage_pct"), "injected failure" if h.get("injected") else
                           "; ".join(h.get("failing", [])[:2])]
                          for i, h in enumerate(data["test_history"])]), ""]
    if acceptance:
        md += ["## Hidden acceptance tests (never shown to agents)", "",
               f"{acceptance['passed']}/{acceptance['total']} passed (pass rate {acceptance['pass_rate']:.0%}).", ""]
    if quality:
        lint = quality.get("lint", {})
        md += ["## Quality checks", "",
               f"- Lint ({lint.get('tool')}): {len(lint.get('new_issues', []))} new issues in changed files",
               (f"- Coverage threshold {quality.get('coverage_threshold')}%: "
               f"{'met' if quality.get('coverage_ok') else 'NOT met'}"),
               *[f"- Warning: {w}" for w in quality.get("warnings", [])], ""]
    md += ["## Security", ""]
    if findings:
        md += [_md_table(["Severity", "Source", "Where", "Finding", "Status"],
                         [[f["severity"], f["source"], f"{f['path']}:{f.get('line') or ''}", f["title"],
                           "pre-existing" if f.get("pre_existing") else
                           ("accepted risk" if f["id"] in data.get("accepted_risks", []) else "new")]
                          for f in findings]), ""]
    else:
        md += ["No findings in changed files.", ""]
    if review:
        md += ["## Review", "", review.get("summary", ""), "",
               _md_table(["Criterion", "Score", "Justification"],
                         [[r["criterion"], r["score"], r["justification"]] for r in rubric]), "",
               f"Average {avg}/5, recommendation: **{review.get('recommendation')}**", ""]
    risks = summary["known_risks"]
    if risks:
        md += ["## Known risks", "", *[f"- {r}" for r in dict.fromkeys(risks)], ""]
    if release:
        md += ["## Release", "", release.get("release_notes", ""), "",
               f"Risk level: **{release.get('risk_level')}**", "", "Rollback plan: " + release.get("rollback_plan", ""),
               "", "Post-deploy checks:", *[f"- {c}" for c in release.get("deployment_checks", [])], ""]
    md += ["## Workflow history", "",
           (f"Fix iterations: {summary['fix_iterations']}, policy violations blocked: {summary['policy_violations']}, "
           f"schema repairs: {summary['schema_repairs']}, provider failovers: {summary['provider_failovers']}, "
           f"duplicate actions suppressed: {summary['duplicates_suppressed']}, injected faults: "
           f"{summary['chaos_injected']}."), ""]
    shown = [h for h in history if h["type"] not in {"tests_passed", "llm_retry"}][:40]
    if shown:
        md += [_md_table(["Seq", "Stage", "Event", "Detail"],
                         [[h["seq"], h["stage"] or "", h["type"], _detail(h)] for h in shown]), ""]
    md += ["## Model usage (estimated cost)", "",
           (f"{usage['calls']} calls ({usage['errors']} errors, {usage['cache_hits']} cache hits), "
           f"{usage['prompt_tokens'] + usage['completion_tokens']} tokens, est. ${usage['cost_usd']:.4f}, "
           f"providers: {', '.join(usage['providers']) or 'none'}."), "",
           _md_table(["Agent", "Calls", "Errors", "Tokens", "p50 ms", "p95 ms", "Est. $"],
                     [[a, u["calls"], u["errors"], u["prompt_tokens"] + u["completion_tokens"], u["p50_ms"],
                       u["p95_ms"], f"{u['cost_usd']:.4f}"] for a, u in usage["by_agent"].items()]), ""]
    if approvals:
        md += ["## Human decisions", "", _md_table(
            ["Gate", "Decision", "By", "Bound to hash", "Comment"],
            [[a["gate"], a["decision"], a["approver"], (a["artifact_hash"] or "")[:16], a["comment"] or ""]
             for a in approvals]), ""]
    if deployment:
        md += ["## Deployment (simulated)", "",
               (f"Environment: {deployment.get('environment')}, merged commit `{deployment.get('sha', '')[:10]}`, "
               f"smoke tests {'passed' if deployment.get('smoke_ok') else 'FAILED'}."), ""]
    md += ["## Mocked dependencies and limitations", "", *[f"- {m}" for m in MOCKS_AND_LIMITS], ""]
    return {"summary": summary, "criteria_matrix": matrix, "history": history, "markdown": "\n".join(md)}


def _detail(h: dict) -> str:
    d = h["data"]
    for key in ("detail", "reason", "path", "fault", "action", "to", "summary", "situation"):
        if d.get(key):
            extra = f" ({d['rule']})" if d.get("rule") else ""
            return f"{d[key]}{extra}"[:140]
    if "failed" in d:
        return f"{d.get('passed')} passed, {d.get('failed')} failed"
    return ""
