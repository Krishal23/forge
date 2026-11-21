"""A private copy of the target repo for each run.

repo/ holds the run branch forge/<run_id>, baseline/ is a worktree of the untouched
main branch, and reports/ keeps tool output out of the diff.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - git is invoked with fixed argv lists, never a shell
from pathlib import Path

from forge.config import FORGE_HOME

COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".venv", "venv", "node_modules", ".coverage",
)
GIT = shutil.which("git") or "/usr/bin/git"
EXCLUDES = "__pycache__/\n*.pyc\n.pytest_cache/\n.coverage\n.ruff_cache/\n"


class WorkspaceError(RuntimeError):
    pass


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,  # ignore the user's hooks, signing and aliases
        "GIT_TERMINAL_PROMPT": "0",
    }
    cmd = [
        GIT, "-c", "user.name=Forge", "-c", "user.email=forge@localhost", "-c", "commit.gpgsign=false",
        "-c", f"core.hooksPath={os.devnull}", "-c", "init.defaultBranch=main", *args,
    ]
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=60, check=False)  # nosec B603
    if check and proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


class Workspace:
    def __init__(self, run_id: str, home: Path = FORGE_HOME):
        self.run_id = run_id
        self.base = Path(home) / "runs" / run_id
        self.repo = self.base / "repo"
        self.baseline = self.base / "baseline"
        self.reports = self.base / "reports"
        self.branch = f"forge/{run_id}"
        self._meta_file = self.base / "workspace.json"

    @classmethod
    def create(cls, run_id: str, source: Path, home: Path = FORGE_HOME) -> Workspace:
        ws = cls(run_id, home)
        if ws.base.exists():
            raise WorkspaceError(f"workspace for {run_id} already exists")
        source = Path(source).resolve()
        if not source.is_dir():
            raise WorkspaceError(f"target {source} is not a directory")
        ws.base.mkdir(parents=True)
        shutil.copytree(source, ws.repo, ignore=COPY_IGNORE)
        ws.reports.mkdir()
        _git(ws.repo, "init", "-q", "-b", "main")
        (ws.repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (ws.repo / ".git" / "info" / "exclude").write_text(EXCLUDES)
        _git(ws.repo, "add", "-A")
        _git(ws.repo, "commit", "-q", "-m", f"baseline: {source.name}")
        base_sha = _git(ws.repo, "rev-parse", "HEAD").strip()
        _git(ws.repo, "checkout", "-q", "-b", ws.branch)
        _git(ws.repo, "worktree", "add", "-q", str(ws.baseline), "main")
        ws._meta_file.write_text(json.dumps({"source": str(source), "base_sha": base_sha, "branch": ws.branch}))
        return ws

    @classmethod
    def open(cls, run_id: str, home: Path = FORGE_HOME) -> Workspace:
        ws = cls(run_id, home)
        if not ws._meta_file.exists():
            raise WorkspaceError(f"no workspace for {run_id}")
        return ws

    @property
    def meta(self) -> dict:
        return json.loads(self._meta_file.read_text())

    @property
    def base_sha(self) -> str:
        return self.meta["base_sha"]

    def _safe(self, path: str) -> Path:
        full = (self.repo / path).resolve()
        if not full.is_relative_to(self.repo.resolve()) or ".git" in Path(path).parts:
            raise WorkspaceError(f"path escapes workspace: {path}")
        return full

    def read(self, path: str) -> str | None:
        full = self._safe(path)
        return full.read_text() if full.is_file() else None

    def write(self, path: str, content: str) -> None:
        full = self._safe(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def read_base(self, path: str) -> str | None:
        """File content at the baseline commit."""
        out = subprocess.run(  # nosec B603
            [GIT, "show", f"{self.base_sha}:{path}"], cwd=self.repo, capture_output=True, text=True,
            timeout=30, check=False,
        )
        return out.stdout if out.returncode == 0 else None

    def head(self) -> str:
        return _git(self.repo, "rev-parse", "HEAD").strip()

    def current_branch(self) -> str:
        return _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def commit(self, message: str) -> str | None:
        """Commit all changes on the run branch. Returns the new sha, or None if nothing changed."""
        if self.current_branch() != self.branch:
            raise WorkspaceError(f"refusing to commit: not on {self.branch}")
        _git(self.repo, "add", "-A")
        if not _git(self.repo, "status", "--porcelain").strip():
            return None
        _git(self.repo, "commit", "-q", "-m", message)
        return self.head()

    def diff(self, context: int = 3) -> str:
        return _git(self.repo, "diff", f"-U{context}", self.base_sha, "HEAD")

    def changed_files(self) -> list[dict]:
        status = _git(self.repo, "diff", "--name-status", self.base_sha, "HEAD").splitlines()
        numstat = _git(self.repo, "diff", "--numstat", self.base_sha, "HEAD").splitlines()
        counts = {}
        for line in numstat:
            added, removed, path = line.split("\t", 2)
            counts[path] = (int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0)
        out = []
        for line in status:
            code, path = line.split("\t", 1)
            a, r = counts.get(path, (0, 0))
            out.append({"path": path, "status": {"A": "added", "M": "modified", "D": "deleted"}.get(code[0], code),
                        "added": a, "removed": r})
        return out

    def merge(self, message: str) -> str:
        """Merge the run branch into main (the baseline worktree). Called only after human approval."""
        _git(self.baseline, "merge", "--no-ff", "-q", "-m", message, self.branch)
        return _git(self.baseline, "rev-parse", "HEAD").strip()
