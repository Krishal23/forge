"""Hash test files so a change that weakens or deletes a test is caught."""

from __future__ import annotations

from pathlib import Path

from forge.tools.scope_guard import Violation
from forge.util import sha256_hex


def snapshot_tests(root: Path, test_dirs: list[str]) -> dict[str, str]:
    root = Path(root)
    locked = {}
    for d in test_dirs:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            locked[f.relative_to(root).as_posix()] = sha256_hex(f.read_bytes())
    return locked


def lock_files(root: Path, paths: list[str]) -> dict[str, str]:
    return {p: sha256_hex((Path(root) / p).read_bytes()) for p in paths if (Path(root) / p).is_file()}


def verify(root: Path, locked: dict[str, str]) -> list[Violation]:
    out = []
    for path, digest in sorted(locked.items()):
        f = Path(root) / path
        if not f.is_file():
            out.append(Violation("locked_test", path, "locked test file was deleted"))
        elif sha256_hex(f.read_bytes()) != digest:
            out.append(Violation("locked_test", path, "locked test file was modified"))
    return out
