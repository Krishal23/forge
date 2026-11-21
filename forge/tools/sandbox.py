"""Run target code with a clean environment, resource limits and a timeout.

Python code inside also cannot open outbound connections (see sandbox_site).
This is a process sandbox, not a container.
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404 - fixed argv lists, never a shell
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from forge.util import monotonic_ms

SITE_DIR = Path(__file__).parent / "sandbox_site"
RLIMIT_EXEC = Path(__file__).parent / "rlimit_exec.py"
MAX_OUTPUT = 20_000
SANDBOX_KIND = "process (scrubbed env, rlimits, timeout, python network guard)"


@dataclass
class SandboxResult:
    cmd: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool
    sandbox: str = SANDBOX_KIND

    def to_dict(self) -> dict:
        return asdict(self)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return text[:half] + f"\n... [{len(text) - MAX_OUTPUT} chars truncated] ...\n" + text[-half:]


def run_sandboxed(
    cmd: list[str],
    cwd: Path,
    timeout_s: float = 180,
    env_extra: dict[str, str] | None = None,
    max_file_mb: int = 64,
    max_open_files: int = 1024,
) -> SandboxResult:
    with tempfile.TemporaryDirectory(prefix="forge-sbx-") as home:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "en_US.UTF-8",
            "HOME": home,
            "TMPDIR": home,
            "PYTHONPATH": str(SITE_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "FORGE_SANDBOX": "1",
            **(env_extra or {}),
        }
        cpu = int(timeout_s) + 5
        full = [sys.executable, str(RLIMIT_EXEC), str(cpu), str(max_file_mb), str(max_open_files), "--", *cmd]
        t0 = monotonic_ms()
        proc = subprocess.Popen(  # nosec B603 - argv built by Forge
            full, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
        return SandboxResult(
            cmd=cmd,
            exit_code=proc.returncode if not timed_out else -9,
            stdout=_truncate(out or ""),
            stderr=_truncate(err or ""),
            duration_ms=round(monotonic_ms() - t0, 1),
            timed_out=timed_out,
        )
