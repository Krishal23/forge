"""Policy checks run on every proposed change before it is written.

- protected paths can never change
- the developer only edits files in scope, and never tests
- QA only adds new test files
- locked tests cannot change
- a change cannot remove a call to a check defined in a high-risk file
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import asdict, dataclass
from functools import cache

from forge.config import TargetConfig


@dataclass
class Violation:
    rule: str
    path: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@cache
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_regex(p).match(path) for p in patterns)


def in_dirs(path: str, dirs: list[str]) -> bool:
    return any(path == d or path.startswith(d.rstrip("/") + "/") for d in dirs)


def check_changes(
    actions: list[tuple[str, str]],
    role: str,
    cfg: TargetConfig,
    scope: set[str] | None = None,
    locked: set[str] | None = None,
) -> list[Violation]:
    """actions: (path, 'create'|'modify'). role: 'developer' or 'qa'."""
    scope = scope or set()
    locked = locked or set()
    out: list[Violation] = []
    for path, action in actions:
        if matches_any(path, cfg.protected_paths):
            out.append(Violation("protected_path", path, "protected by policy; agents may never change it"))
            continue
        if path in locked:
            out.append(Violation("locked_test", path, "test file is hash-locked; tests may not be weakened"))
            continue
        is_test = in_dirs(path, cfg.test_dirs)
        if role == "developer":
            if is_test:
                out.append(Violation("tests_owned_by_qa", path, "developer may not edit tests; QA owns tests"))
            elif path not in scope:
                out.append(Violation("out_of_scope", path, "not in the approved change scope; file a scope request"))
        elif role == "qa":
            if not is_test:
                out.append(Violation("qa_tests_only", path, "QA may only write files under test directories"))
            elif action == "modify":
                out.append(Violation("qa_no_modify", path, "QA may only add new test files"))
        else:
            out.append(Violation("unknown_role", path, f"role {role!r} may not write files"))
    return out


def _call_names(source: str) -> Counter:
    names: Counter = Counter()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names[func.attr] += 1
            elif isinstance(func, ast.Name):
                names[func.id] += 1
    return names


def removed_guarded_calls(before: str | None, after: str, guarded: set[str]) -> list[str]:
    """Guarded function names called fewer times after the change than before."""
    if before is None or not guarded:
        return []
    b, a = _call_names(before), _call_names(after)
    return sorted(n for n in guarded if a[n] < b[n])


def check_guarded_calls(originals: dict[str, str | None], files: dict[str, str], guarded: set[str]) -> list[Violation]:
    out = []
    for path, after in files.items():
        for name in removed_guarded_calls(originals.get(path), after, guarded):
            out.append(Violation(
                "guarded_call_removed", path,
                f"removes a call to {name}(), a check defined in a high-risk module; needs human review",
            ))
    return out


def high_risk_touched(paths: list[str], cfg: TargetConfig) -> list[str]:
    return sorted(p for p in paths if matches_any(p, cfg.high_risk_paths))
