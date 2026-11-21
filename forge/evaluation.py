"""Run the hidden acceptance tests for a scenario against a copy of the final code."""

from __future__ import annotations

import shutil

from forge.config import HIDDEN_TESTS_DIR, TargetConfig
from forge.tools.testing import run_tests
from forge.tools.workspace import Workspace
from forge.util import new_id


def run_hidden_tests(ws: Workspace, cfg: TargetConfig, scenario_id: str | None) -> dict | None:
    if not scenario_id:
        return None
    src = HIDDEN_TESTS_DIR / scenario_id
    files = sorted(src.glob("test_*.py")) if src.is_dir() else []
    if not files:
        return None
    scratch = ws.reports / new_id("acceptance")
    repo = scratch / "repo"
    shutil.copytree(ws.repo, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    test_dir = repo / (cfg.test_dirs[0] if cfg.test_dirs else "tests")
    test_dir.mkdir(parents=True, exist_ok=True)
    rel = []
    for f in files:
        shutil.copy(f, test_dir / f.name)
        rel.append((test_dir / f.name).relative_to(repo).as_posix())
    report = run_tests(repo, cfg, scratch, test_paths=rel, coverage=False)
    return {
        "scenario": scenario_id,
        "ok": report.ok,
        "passed": report.passed,
        "failed": report.failed + report.errors,
        "total": report.total,
        "pass_rate": round(report.passed / report.total, 3) if report.total else 0.0,
        "failures": [{"test": f.test, "message": f.message[:500]} for f in report.failures[:10]],
    }
