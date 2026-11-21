"""Run the target's tests in the sandbox and turn the output into a TestReport."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import defusedxml.ElementTree as ET

from forge.config import TargetConfig
from forge.tools.sandbox import run_sandboxed
from forge.util import new_id

PYTEST_CONFIG_FILES = ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg")


@dataclass
class TestFailure:
    test: str
    message: str


@dataclass
class TestReport:
    ok: bool
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    exit_code: int = 0
    timed_out: bool = False
    duration_ms: float = 0.0
    coverage_pct: float | None = None
    file_coverage: dict[str, float] = field(default_factory=dict)
    failures: list[TestFailure] = field(default_factory=list)
    output_tail: str = ""
    command: list[str] = field(default_factory=list)
    sandbox: str = ""
    injected: bool = False

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d

    def failure_digest(self, limit: int = 6, chars: int = 1200) -> str:
        """Compact failure summary to feed back into the developer's fix prompt."""
        if self.timed_out:
            return "The test run timed out (possible infinite loop or blocking call)."
        parts = [f"- {f.test}: {f.message[:chars]}" for f in self.failures[:limit]]
        if not parts:
            parts.append(self.output_tail[-chars * 2 :])
        return "\n".join(parts)


def _has_pytest_config(root: Path) -> bool:
    if any((root / f).exists() for f in PYTEST_CONFIG_FILES):
        return True
    pyproject = root / "pyproject.toml"
    return pyproject.exists() and "[tool.pytest" in pyproject.read_text()


def _sets_pythonpath(root: Path) -> bool:
    return any("pythonpath" in (root / f).read_text(errors="replace")
               for f in (*PYTEST_CONFIG_FILES, "pyproject.toml") if (root / f).exists())


def build_command(cfg: TargetConfig, cwd: Path, report_dir: Path, test_paths: list[str] | None, coverage: bool):
    cmd = [sys.executable if c in {"python", "python3"} else c for c in cfg.test_command]
    is_pytest = "pytest" in cmd
    env: dict[str, str] = {}
    if is_pytest:
        cmd += ["--junitxml", str(report_dir / "junit.xml")]
        if "-p" not in cmd:
            cmd += ["-p", "no:cacheprovider"]
        # Plugins installed with Forge (pytest-asyncio) warn at start-up; repos that turn warnings
        # into errors would then crash before collecting a single test.
        cmd += ["-W", "ignore::pytest.PytestDeprecationWarning"]
        if any(d.startswith("src/") for d in cfg.source_dirs) and not _sets_pythonpath(cwd):
            cmd += ["-o", "pythonpath=src"]
        if not _has_pytest_config(cwd):
            # Stop pytest from walking up into an unrelated parent project's config.
            ini = report_dir / "pytest.ini"
            ini.write_text("[pytest]\n")
            cmd += ["-c", str(ini), "--rootdir", str(cwd)]
        if coverage and cfg.source_dirs:
            for d in cfg.source_dirs:
                cmd += [f"--cov={d}"]
            cmd += ["--cov-report", f"json:{report_dir / 'coverage.json'}"]
            env["COVERAGE_FILE"] = str(report_dir / ".coverage")
        cmd += test_paths or []
    return cmd, env, is_pytest


def _parse_junit(path: Path, report: TestReport) -> None:
    # The XML contains output from agent-written tests, so use the safe parser.
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        failure = case.find("failure")
        error = case.find("error")
        if failure is not None or error is not None:
            node = failure if failure is not None else error
            msg = (node.get("message") or "") + "\n" + (node.text or "")
            report.failures.append(TestFailure(name, msg.strip()[:4000]))
            if failure is not None:
                report.failed += 1
            else:
                report.errors += 1
        elif case.find("skipped") is not None:
            report.skipped += 1
        else:
            report.passed += 1


def _parse_coverage(path: Path, report: TestReport) -> None:
    data = json.loads(path.read_text())
    report.coverage_pct = round(float(data["totals"]["percent_covered"]), 1)
    report.file_coverage = {
        Path(name).as_posix(): round(float(info["summary"]["percent_covered"]), 1)
        for name, info in data.get("files", {}).items()
    }


def run_tests(
    cwd: Path,
    cfg: TargetConfig,
    report_root: Path,
    test_paths: list[str] | None = None,
    coverage: bool = True,
    timeout_s: float = 180,
    inject_failure: bool = False,
) -> TestReport:
    report_dir = Path(report_root) / new_id("tests")
    report_dir.mkdir(parents=True)
    cmd, env, is_pytest = build_command(cfg, Path(cwd), report_dir, test_paths, coverage)
    res = run_sandboxed(cmd, Path(cwd), timeout_s=timeout_s, env_extra=env)
    report = TestReport(
        ok=False,
        exit_code=res.exit_code,
        timed_out=res.timed_out,
        duration_ms=res.duration_ms,
        output_tail=(res.stdout + "\n" + res.stderr)[-6000:],
        command=cmd,
        sandbox=res.sandbox,
    )
    junit = report_dir / "junit.xml"
    if is_pytest and junit.exists():
        _parse_junit(junit, report)
    elif res.exit_code != 0:
        report.errors = 1
        report.failures.append(TestFailure("test-run", report.output_tail[-3000:]))
    cov = report_dir / "coverage.json"
    if cov.exists():
        _parse_coverage(cov, report)

    report.ok = res.exit_code == 0 and report.failed == 0 and report.errors == 0 and not res.timed_out
    if is_pytest and res.exit_code == 5:  # pytest: no tests collected
        report.ok = False
        report.failures.append(TestFailure("collection", "no tests were collected"))
    if inject_failure:
        report.injected = True
        report.ok = False
        report.failed += 1
        report.failures.insert(0, TestFailure(
            "forge_chaos::injected_failure",
            "Injected test failure (chaos switch fail_tests_next). No real test failed; "
            "this exercises the fix loop.",
        ))
    (report_dir / "report.json").write_text(json.dumps(report.to_dict(), indent=2))
    return report
