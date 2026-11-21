"""Per-provider circuit breaker (closed, open, half-open)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_s: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self.state = "closed"
        self.failures = 0
        self._opened_at = 0.0

    def allow(self) -> bool:
        with self._lock:
            if self.state == "open" and self._clock() - self._opened_at >= self.cooldown_s:
                self.state = "half_open"
            return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self.state = "closed"
            self.failures = 0

    def record_failure(self) -> bool:
        """Count a failure. Returns True if this failure opened the breaker."""
        with self._lock:
            self.failures += 1
            if self.state == "half_open" or (self.state == "closed" and self.failures >= self.failure_threshold):
                self.state = "open"
                self._opened_at = self._clock()
                return True
            return False

    def trip(self, seconds: float) -> None:
        """Open immediately for `seconds` (e.g. a provider reports its daily quota is used up)."""
        with self._lock:
            self.state = "open"
            self.failures = max(self.failures, self.failure_threshold)
            self._opened_at = self._clock() - self.cooldown_s + seconds

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self.state, "failures": self.failures}
