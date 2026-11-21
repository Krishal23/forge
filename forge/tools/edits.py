"""Apply search/replace edits from the developer agent.

All edits are checked in memory first. Nothing is written unless every edit matches
exactly one place and every changed Python file still parses.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field

from forge.schemas import Edit
from forge.tools.workspace import Workspace

MAX_EDITS = 30
MAX_FILE_BYTES = 200_000


@dataclass
class EditPlan:
    files: dict[str, str] = field(default_factory=dict)  # path -> new content
    originals: dict[str, str | None] = field(default_factory=dict)  # path -> content before (None = new file)
    applied: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.files)

    def actions(self) -> list[tuple[str, str]]:
        return [(p, "create" if self.originals[p] is None else "modify") for p in self.files]


def _replace_tolerant(content: str, search: str, replace: str) -> tuple[str | None, int]:
    """Exact match first; then a match that ignores trailing whitespace per line.

    Returns (new_content, match_count). new_content is None unless exactly one match.
    """
    count = content.count(search)
    if count == 1:
        return content.replace(search, replace, 1), 1
    if count > 1:
        return None, count
    lines = content.splitlines()
    target = [s.rstrip() for s in search.strip("\n").splitlines()]
    if not target:
        return None, 0
    n = len(target)
    starts = [i for i in range(len(lines) - n + 1) if [s.rstrip() for s in lines[i : i + n]] == target]
    if len(starts) != 1:
        return None, len(starts)
    i = starts[0]
    new_lines = [*lines[:i], *replace.strip("\n").splitlines(), *lines[i + n :]]
    trailing = "\n" if content.endswith("\n") else ""
    return "\n".join(new_lines) + trailing, 1


def _closest_hint(content: str, search: str) -> str:
    first = next((s.strip() for s in search.splitlines() if s.strip()), "")
    candidates = [s.strip() for s in content.splitlines() if s.strip()]
    close = difflib.get_close_matches(first, candidates, n=1, cutoff=0.5)
    return f" Closest existing line: {close[0]!r}." if close else ""


def plan_edits(ws: Workspace, edits: list[Edit]) -> EditPlan:
    plan = EditPlan()
    if len(edits) > MAX_EDITS:
        plan.errors.append(f"too many edits ({len(edits)} > {MAX_EDITS}); split the change")
        return plan
    for i, edit in enumerate(edits, 1):
        label = f"edit {i} ({edit.path})"
        if edit.path not in plan.files:
            plan.originals[edit.path] = ws.read(edit.path)
            if plan.originals[edit.path] is not None:
                plan.files[edit.path] = plan.originals[edit.path]  # type: ignore[assignment]
        current = plan.files.get(edit.path)

        if edit.search == "":
            if current is not None:
                plan.errors.append(f"{label}: file already exists; use a non-empty search anchor to modify it")
                continue
            plan.files[edit.path] = edit.replace if edit.replace.endswith("\n") else edit.replace + "\n"
            plan.applied.append({"path": edit.path, "action": "create"})
            continue

        if current is None:
            plan.errors.append(f"{label}: file does not exist; use an empty search to create it")
            continue
        new, matches = _replace_tolerant(current, edit.search, edit.replace)
        if new is None:
            if matches == 0:
                plan.errors.append(f"{label}: search text not found.{_closest_hint(current, edit.search)}")
            else:
                plan.errors.append(f"{label}: search text matches {matches} places; include more context")
            continue
        plan.files[edit.path] = new
        plan.applied.append({"path": edit.path, "action": "modify"})

    # Drop files that ended up unchanged.
    for path in [p for p, c in plan.files.items() if c == plan.originals.get(p)]:
        del plan.files[path]
        del plan.originals[path]

    for path, content in plan.files.items():
        if len(content.encode()) > MAX_FILE_BYTES:
            plan.errors.append(f"{path}: resulting file exceeds {MAX_FILE_BYTES} bytes")
        if path.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as exc:
                plan.errors.append(f"{path}: edit produces invalid Python (line {exc.lineno}: {exc.msg})")
    if not plan.files and not plan.errors:
        plan.errors.append("edits produced no changes")
    return plan


def apply_plan(ws: Workspace, plan: EditPlan) -> None:
    if not plan.ok:
        raise ValueError("refusing to apply a plan with errors")
    for path, content in plan.files.items():
        ws.write(path, content)
