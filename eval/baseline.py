"""Compare Forge with a single-agent baseline on the hidden acceptance tests.

The baseline makes one model call with the requirement and the full source and applies
whatever edits come back. In mock mode it replays the scripted developer's first attempt,
so the result shows what Forge's checks catch rather than how good a model is.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from forge.agents.base import AgentSpec, run_agent
from forge.agents.mock_script import SCRIPTS
from forge.chaos import Chaos
from forge.config import HIDDEN_TESTS_DIR, TargetConfig
from forge.evaluation import run_hidden_tests
from forge.llm import build_gateway
from forge.orchestrator import Orchestrator
from forge.scenarios import get_scenario, load_scenarios, resolve_target
from forge.schemas import DeveloperOutput, Edit
from forge.store import Store
from forge.tools.edits import apply_plan, plan_edits
from forge.tools.repo_index import RepoIndex
from forge.tools.scope_guard import high_risk_touched, removed_guarded_calls
from forge.tools.security import blocking, scan_change
from forge.tools.testing import run_tests
from forge.tools.workspace import Workspace

BASELINE = AgentSpec(
    name="baseline",
    role="Implement the requirement in the repository.",
    contract=DeveloperOutput,
    max_tokens=16384,
    instructions="Return search/replace edits that implement the requirement. `search` must match the file exactly.",
)


def _source_dump(ws: Workspace, cfg: TargetConfig) -> str:
    parts = []
    for d in cfg.source_dirs + cfg.test_dirs:
        for f in sorted((ws.repo / d).rglob("*.py")):
            rel = f.relative_to(ws.repo).as_posix()
            parts.append(f"<<<FILE {rel}>>>\n{f.read_text()}\n<<<END FILE {rel}>>>")
    return "\n\n".join(parts)


def _measure(ws: Workspace, cfg: TargetConfig, scenario_id: str, scope: list[str] | None) -> dict:
    existing = run_tests(ws.baseline, cfg, ws.reports, coverage=False)
    tests = {p for p in (ws.baseline / cfg.test_dirs[0]).rglob("test_*.py")}
    rel = [t.relative_to(ws.baseline).as_posix() for t in tests]
    regress = run_tests(ws.repo, cfg, ws.reports, test_paths=rel, coverage=False)
    hidden = run_hidden_tests(ws, cfg, scenario_id) or {}
    changed = [c["path"] for c in ws.changed_files()]
    source_changed = [p for p in changed if not p.startswith(tuple(cfg.test_dirs))]
    findings = scan_change(ws, cfg)["findings"]
    index = RepoIndex.build(ws.baseline, cfg.source_dirs, cfg.test_dirs)
    guarded = index.guarded_names(cfg.high_risk_paths)
    removed = sorted({n for p in source_changed if p.endswith(".py")
                      for n in removed_guarded_calls(ws.read_base(p), ws.read(p) or "", guarded)})
    return {
        "hidden_passed": hidden.get("passed", 0), "hidden_total": hidden.get("total", 0),
        "hidden_pass_rate": hidden.get("pass_rate", 0.0),
        "existing_tests_pass": regress.ok, "existing_tests_baseline_ok": existing.ok,
        "blocking_security_findings": len(blocking(findings)),
        "files_changed": source_changed,
        "out_of_scope_files": sorted(set(source_changed) - set(scope)) if scope is not None else None,
        "high_risk_files_changed": high_risk_touched(source_changed, cfg),
        "security_checks_removed": removed,
    }


def run_baseline(scenario: dict, mode: str, home: Path) -> dict:
    store = Store(home / "baseline.db")
    run_id = f"base_{scenario['id'].lower()}"
    store.create_run(run_id, scenario["target"], scenario["requirement"], mode, "IMPLEMENT", scenario["id"])
    ws = Workspace.create(run_id, resolve_target(scenario["target"]), home)
    cfg = TargetConfig.load(ws.repo)
    if mode == "mock":
        files = {p: ws.read(p) for p in cfg_scope(cfg, ws)}
        script = SCRIPTS[scenario["id"]]["developer"]
        meta = {"situation": "scope_decided" if scenario["id"] == "REQ-PAY-001" else "initial",
                "scope": list(files), "files": files}
        out = DeveloperOutput.model_validate(script(meta))
    else:
        gw = build_gateway("live", store=store, chaos=Chaos())
        prompt = f"Requirement:\n{scenario['requirement']}\n\nRepository:\n{_source_dump(ws, cfg)}"
        out = run_agent(gw, store, run_id, "IMPLEMENT", BASELINE, prompt).output
    plan = plan_edits(ws, [Edit(**e.model_dump()) for e in out.edits])
    applied = plan.ok
    if applied:
        apply_plan(ws, plan)
        ws.commit("baseline")
    result = {"applied": applied, "edit_errors": plan.errors, **_measure(ws, cfg, scenario["id"], None)}
    result.update(store.usage_summary(run_id) | {})
    result = {k: result[k] for k in result if k not in {"by_agent", "providers", "latency_ms_total",
                                                         "prompt_tokens", "completion_tokens", "errors"}}
    store.close()
    return result


def cfg_scope(cfg: TargetConfig, ws: Workspace) -> list[str]:
    """All source files: a single agent has no scope."""
    return sorted(p.relative_to(ws.repo).as_posix() for d in cfg.source_dirs for p in (ws.repo / d).rglob("*.py"))


def run_forge(scenario: dict, mode: str, home: Path) -> dict:
    store = Store(home / "forge.db")
    orch = Orchestrator(store, home=home, chaos=Chaos(), mock_latency_ms=0)
    rid = orch.create_run(scenario_id=scenario["id"], mode=mode, requested_by="eval")["id"]
    run = orch.run_sync(rid)
    if run["state"] == "GATE_CRITERIA":
        orch.decide(rid, "approve", "eval-po", answers="Use your stated assumptions.")
        run = orch.wait_idle(rid, timeout=600)
    ws = Workspace.open(rid, home)
    cfg = TargetConfig.load(ws.repo)
    m = _measure(ws, cfg, scenario["id"], run["data"].get("scope", []))
    usage = store.usage_summary(rid)
    events = store.events(rid)
    out = {
        "final_state": run["state"], **m, "calls": usage["calls"], "cache_hits": usage["cache_hits"],
        "cost_usd": usage["cost_usd"], "fix_iterations": run["data"].get("fix_iteration", 0),
        "policy_violations_blocked": sum(e["type"] == "policy_violation" for e in events),
        "security_blocks": sum(e["type"] == "security_blocked" for e in events),
    }
    store.close()
    return out


def main(runs: int = 1, out: str = "eval/results.json", mode: str = "mock") -> int:
    results = []
    for scenario in [s for s in load_scenarios() if (HIDDEN_TESTS_DIR / s["id"]).is_dir()]:
        for i in range(runs):
            with tempfile.TemporaryDirectory(prefix="forge-eval-") as tmp:
                base = run_baseline(scenario, mode, Path(tmp) / "b")
                forge = run_forge(scenario, mode, Path(tmp) / "f")
            results.append({"scenario": scenario["id"], "repeat": i + 1, "mode": mode,
                            "baseline": base, "forge": forge})
            print(f"{scenario['id']} #{i + 1}: baseline hidden {base['hidden_passed']}/{base['hidden_total']}, "
                  f"forge {forge['hidden_passed']}/{forge['hidden_total']}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(results, indent=2))
    md = Path(out).with_suffix(".md")
    md.write_text(render_markdown(results))
    print(f"wrote {out} and {md}")
    return 0


def render_markdown(results: list[dict]) -> str:
    rows = [("| Scenario | System | Hidden tests | Existing tests | Blocking security | High-risk files changed | "
            "Security checks removed | LLM calls | Est. cost |"), "|" + "---|" * 9]
    for r in results:
        for name in ("baseline", "forge"):
            m = r[name]
            rows.append(
                f"| {r['scenario']} | {'Single agent' if name == 'baseline' else 'Forge'} | "
                f"{m['hidden_passed']}/{m['hidden_total']} | {'pass' if m['existing_tests_pass'] else 'FAIL'} | "
                f"{m['blocking_security_findings']} | {', '.join(m['high_risk_files_changed']) or 'none'} | "
                f"{', '.join(m['security_checks_removed']) or 'none'} | {m.get('calls', 0)} | "
                f"${m.get('cost_usd', 0):.4f} |")
    mode = results[0]["mode"] if results else "mock"
    note = ("Mock mode: the baseline is the scripted developer's first attempt with no feedback loop, so this "
            "table shows which problems Forge's controls catch. It is not a model-quality benchmark; run "
            "`forge eval --mode live --runs 3` for that.") if mode == "mock" else f"Live mode, {len(results)} runs."
    return "# Forge vs single-agent baseline\n\n" + note + "\n\n" + "\n".join(rows) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["get_scenario", "main", "shutil"]
