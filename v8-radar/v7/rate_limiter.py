from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    """Thread-safe per-minute limiter. Designed below seller's 300/min ceiling."""

    def __init__(self, max_calls: int = 220, window_seconds: float = 60.0):
        self.max_calls = int(max_calls)
        self.window_seconds = float(window_seconds)
        self._calls = deque()
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def acquire(self, block: bool = True) -> bool:
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return True
                if not block:
                    return False
                wait_for = max(0.05, self.window_seconds - (now - self._calls[0]) + 0.01)
            time.sleep(min(wait_for, 1.0))

    def count_last_minute(self) -> int:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            return len(self._calls)
