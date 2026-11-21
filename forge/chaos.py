"""Fault injection switches used by the dashboard and the tests.

Every injected fault is logged as `chaos_injected`, so it is never mistaken for a real one.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

CORRUPTED_OUTPUT = 'Sure! Here is the result you asked for:\n{"summary": "truncated output", "acceptance_criteria": ['


@dataclass
class ChaosState:
    providers_down: set[str] = field(default_factory=set)
    corrupt_next: int = 0
    fail_tests_next: int = 0
    plant_vuln_next: int = 0
    repeat_next: int = 0


class Chaos:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._s = ChaosState()

    def snapshot(self) -> dict:
        with self._lock:
            d = asdict(self._s)
            d["providers_down"] = sorted(self._s.providers_down)
            return d

    def set_provider_down(self, name: str, down: bool = True) -> None:
        with self._lock:
            (self._s.providers_down.add if down else self._s.providers_down.discard)(name)

    def is_down(self, name: str) -> bool:
        with self._lock:
            return name in self._s.providers_down

    def arm(self, switch: str, count: int = 1) -> None:
        if switch not in {"corrupt_next", "fail_tests_next", "plant_vuln_next", "repeat_next"}:
            raise ValueError(f"unknown chaos switch {switch!r}")
        with self._lock:
            setattr(self._s, switch, max(0, int(count)))

    def take(self, switch: str) -> bool:
        """Consume one armed fault. Returns True if the fault should fire now."""
        with self._lock:
            n = getattr(self._s, switch)
            if n > 0:
                setattr(self._s, switch, n - 1)
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._s = ChaosState()


CHAOS = Chaos()
