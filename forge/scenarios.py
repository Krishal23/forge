"""Demo scenarios and sample target repos."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from forge.config import SAMPLES_DIR


@cache
def load_scenarios() -> tuple[dict, ...]:
    path = SAMPLES_DIR / "scenarios.json"
    return tuple(json.loads(path.read_text())) if path.exists() else ()


def get_scenario(scenario_id: str | None) -> dict | None:
    if not scenario_id:
        return None
    return next((s for s in load_scenarios() if s["id"] == scenario_id), None)


def list_targets() -> list[dict]:
    out = []
    for d in sorted(SAMPLES_DIR.iterdir()):
        if d.is_dir() and (d / "forge.toml").exists():
            readme = d / "README.md"
            desc = readme.read_text().splitlines()[2] if readme.exists() and len(readme.read_text().splitlines()) > 2 else ""
            out.append({"name": d.name, "path": str(d), "description": desc})
    return out


def resolve_target(target: str, home: Path | None = None) -> Path:
    """A sample name, a saved repo name (uploaded or added), or a path to a local Python repository."""
    from forge.repos import get_repo_path

    sample = SAMPLES_DIR / target
    if "/" not in target and sample.is_dir():
        return sample.resolve()
    if "/" not in target:
        saved = get_repo_path(target, home) if home else get_repo_path(target)
        if saved is not None:
            if not saved.is_dir():
                raise ValueError(f"saved repo {target!r} points to {saved}, which no longer exists")
            return saved.resolve()
    path = Path(target).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"target {target!r} is not a sample name or an existing directory")
    return path
