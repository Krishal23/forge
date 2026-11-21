"""Lint check that only counts issues the change introduced (ruff, on changed files)."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv lists, never a shell
import sys
from collections import Counter
from pathlib import Path

from forge.tools.workspace import Workspace

RUFF_SELECT = "E,F,B"
RUFF_IGNORE = "E501"


def ruff_issues(cwd: Path, paths: list[str]) -> list[dict]:
    paths = [p for p in paths if (Path(cwd) / p).is_file()]
    if not paths:
        return []
    proc = subprocess.run(  # nosec B603 - argv built by Forge
        [sys.executable, "-m", "ruff", "check", "--isolated", "--output-format", "json", "--no-cache",
         "--select", RUFF_SELECT, "--ignore", RUFF_IGNORE, *paths],
        cwd=cwd, capture_output=True, text=True, timeout=60, check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ruff failed: {proc.stderr[:400]}")
    issues = []
    for item in json.loads(proc.stdout or "[]"):
        issues.append({
            "path": Path(item["filename"]).resolve().relative_to(Path(cwd).resolve()).as_posix(),
            "line": item["location"]["row"],
            "code": item["code"],
            "message": item["message"],
        })
    return issues


def lint_delta(ws: Workspace, paths: list[str]) -> dict:
    py = [p for p in paths if p.endswith(".py")]
    current = ruff_issues(ws.repo, py)
    baseline = ruff_issues(ws.baseline, py)
    base_keys = Counter((i["path"], i["code"], i["message"]) for i in baseline)
    new = []
    for issue in current:
        key = (issue["path"], issue["code"], issue["message"])
        if base_keys[key] > 0:
            base_keys[key] -= 1
        else:
            new.append(issue)
    return {
        "tool": f"ruff --select {RUFF_SELECT}",
        "files": py,
        "baseline_count": len(baseline),
        "current_count": len(current),
        "new_issues": new,
    }
