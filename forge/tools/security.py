"""Security scan of a change: bandit, a secrets scan, and a check for logged card data.

Findings that already exist in the baseline are marked pre_existing and do not block.
Injection-type bandit rules are raised to critical.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - fixed argv lists, never a shell
import sys
from collections import Counter
from pathlib import Path

from forge.config import TargetConfig
from forge.redact import redact
from forge.schemas import SEVERITY_RANK, SecurityFinding, Severity
from forge.tools.diffs import added_lines
from forge.tools.scope_guard import in_dirs
from forge.tools.workspace import Workspace
from forge.util import sha256_hex

CRITICAL_BANDIT = {
    "B608": "CWE-89",  # SQL built from strings
    "B602": "CWE-78",  # subprocess with shell=True
    "B605": "CWE-78",  # os.system / shell
    "B307": "CWE-94",  # eval
    "B102": "CWE-94",  # exec
    "B301": "CWE-502",  # pickle
    "B506": "CWE-502",  # yaml.load
}
_BANDIT_SEVERITY = {"HIGH": Severity.high, "MEDIUM": Severity.medium, "LOW": Severity.low}
DEFAULT_SENSITIVE = ["card_number", "card_no", "pan", "cvv", "cvc", "password", "secret"]
_LOG_CALL = re.compile(r"\b(?:log(?:ger|ging)?\.\w+|print)\s*\(")


def bandit_raw(cwd: Path, paths: list[str]) -> list[dict]:
    paths = [p for p in paths if (Path(cwd) / p).is_file()]
    if not paths:
        return []
    proc = subprocess.run(  # nosec B603 - argv built by Forge
        [sys.executable, "-m", "bandit", "-q", "-f", "json", *paths],
        cwd=cwd, capture_output=True, text=True, timeout=120, check=False,
    )
    try:
        return json.loads(proc.stdout or "{}").get("results", [])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bandit output unreadable: {proc.stderr[:300]}") from exc


def _fingerprint(cwd: Path, r: dict) -> tuple[str, str, str]:
    """Location-independent identity of a bandit finding (survives line shifts)."""
    path = Path(r["filename"]).as_posix()
    try:
        line = (Path(cwd) / path).read_text().splitlines()[r["line_number"] - 1].strip()
    except (OSError, IndexError):
        line = r.get("code", "").strip()
    return (r["test_id"], path, line)


def _bandit_findings(ws: Workspace, files: list[str]) -> list[SecurityFinding]:
    current = bandit_raw(ws.repo, files)
    baseline_counts = Counter(_fingerprint(ws.baseline, r) for r in bandit_raw(ws.baseline, files))
    findings = []
    for r in current:
        fp = _fingerprint(ws.repo, r)
        pre = baseline_counts[fp] > 0
        if pre:
            baseline_counts[fp] -= 1
        test_id = r["test_id"]
        severity = Severity.critical if test_id in CRITICAL_BANDIT else _BANDIT_SEVERITY.get(
            r["issue_severity"], Severity.low)
        cwe = CRITICAL_BANDIT.get(test_id) or (f"CWE-{r['issue_cwe']['id']}" if r.get("issue_cwe") else None)
        findings.append(SecurityFinding(
            id=f"bandit-{test_id}-{sha256_hex('|'.join(fp))[:6]}",
            severity=severity,
            title=f"{test_id} {r['test_name']}: {r['issue_text']}",
            path=Path(r["filename"]).as_posix(),
            line=r["line_number"],
            cwe=cwe,
            source="bandit",
            pre_existing=pre,
            recommendation=r.get("more_info", ""),
        ))
    return findings


def _diff_findings(ws: Workspace, cfg: TargetConfig, sensitive: list[str]) -> list[SecurityFinding]:
    findings = []
    sens = re.compile(r"\b(" + "|".join(re.escape(s) for s in sensitive) + r")\b", re.IGNORECASE)
    for path, lineno, text in added_lines(ws.diff(context=0)):
        _, counts = redact(text)
        for kind in counts:
            findings.append(SecurityFinding(
                id=f"secret-{kind}-{sha256_hex(f'{path}:{lineno}')[:6]}",
                severity=Severity.critical,
                title=f"Possible {kind.replace('_', ' ')} committed in source",
                path=path, line=lineno, cwe="CWE-798" if kind != "card_number" else "CWE-312",
                source="secrets",
                recommendation="Remove the value and load it from the environment or a secrets manager.",
            ))
        if _LOG_CALL.search(text) and sens.search(text) and not in_dirs(path, cfg.test_dirs):
            findings.append(SecurityFinding(
                id=f"policy-sensitive-log-{sha256_hex(f'{path}:{lineno}')[:6]}",
                severity=Severity.high,
                title=f"Sensitive value '{sens.search(text).group(1)}' written to logs/output",
                path=path, line=lineno, cwe="CWE-532",
                source="policy",
                recommendation="Log an identifier or the last 4 digits only; never full card data or credentials.",
            ))
    return findings


def scan_change(ws: Workspace, cfg: TargetConfig, sensitive: list[str] | None = None) -> dict:
    changed = [c["path"] for c in ws.changed_files() if c["status"] != "deleted"]
    source_files = [p for p in changed if p.endswith(".py") and not in_dirs(p, cfg.test_dirs)]
    findings = _bandit_findings(ws, source_files) + _diff_findings(ws, cfg, sensitive or DEFAULT_SENSITIVE)
    findings.sort(key=lambda f: (f.pre_existing, -SEVERITY_RANK[f.severity], f.path, f.line or 0))
    return {
        "tools": ["bandit", "secrets-patterns", "sensitive-logging-policy"],
        "scanned_files": source_files,
        "findings": [f.model_dump(mode="json") for f in findings],
    }


def blocking(findings: list[dict], threshold: Severity = Severity.high) -> list[dict]:
    """New (not pre-existing) findings at or above the threshold block the pipeline."""
    rank = SEVERITY_RANK[threshold]
    return [f for f in findings if not f["pre_existing"] and SEVERITY_RANK[Severity(f["severity"])] >= rank]
