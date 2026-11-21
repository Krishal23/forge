"""Helpers for reading unified diffs."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, new_line_number, text) for every added line in a unified diff."""
    out: list[tuple[str, int, str]] = []
    path: str | None = None
    lineno = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            out.append((path, lineno, line[1:]))
            lineno += 1
        elif line.startswith(" "):
            lineno += 1
    return out
