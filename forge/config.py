"""Paths, run budgets and the per-repo settings read from forge.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FORGE_HOME = Path(os.environ.get("FORGE_HOME", PROJECT_ROOT / ".forge"))
SAMPLES_DIR = PROJECT_ROOT / "samples"
HIDDEN_TESTS_DIR = PROJECT_ROOT / "eval" / "hidden"


@dataclass
class Budgets:
    """Hard limits per run. Breaching any of them escalates to a human."""

    max_llm_calls: int = 60
    max_cost_usd: float = 2.00
    max_wall_seconds: int = 1800
    max_fix_iterations: int = 3
    max_schema_repairs: int = 2
    max_arbitration_rounds: int = 2
    max_security_autofix: int = 1


DEFAULT_PROTECTED = ["forge.toml", ".github/**", "**/*.lock", ".env*", "Dockerfile", ".git/**"]


@dataclass
class TargetConfig:
    name: str
    root: Path
    language: str = "python"
    test_command: list[str] = field(default_factory=lambda: ["python", "-m", "pytest", "-q"])
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=lambda: ["tests"])
    protected_paths: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED))
    high_risk_paths: list[str] = field(default_factory=list)
    coverage_threshold: int = 0

    @classmethod
    def load(cls, root: Path) -> TargetConfig:
        root = Path(root).resolve()
        data: dict = {}
        cfg_file = root / "forge.toml"
        if cfg_file.exists():
            data = tomllib.loads(cfg_file.read_text())
        project = data.get("project", {})
        policy = data.get("policy", {})
        source_dirs = project.get("source_dirs") or _detect_source_dirs(root)
        protected = list(dict.fromkeys(DEFAULT_PROTECTED + policy.get("protected_paths", [])))
        return cls(
            name=project.get("name", root.name),
            root=root,
            language=project.get("language", "python"),
            test_command=project.get("test_command", ["python", "-m", "pytest", "-q"]),
            source_dirs=source_dirs,
            test_dirs=project.get("test_dirs") or _detect_test_dirs(root),
            protected_paths=protected,
            high_risk_paths=policy.get("high_risk_paths", []),
            coverage_threshold=int(policy.get("coverage_threshold", 0)),
        )


def _packages(base: Path, prefix: str = "") -> list[str]:
    return sorted(
        prefix + p.name
        for p in base.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and not p.name.startswith(("test", ".", "_"))
    )


def _detect_source_dirs(root: Path) -> list[str]:
    """Top-level packages, or packages under src/ for the src layout. Tests are excluded."""
    found = _packages(root)
    if (root / "src").is_dir():
        found += _packages(root / "src", "src/")
    return found


def _detect_test_dirs(root: Path) -> list[str]:
    found = [d for d in ("tests", "test") if (root / d).is_dir()]
    return found or ["tests"]
