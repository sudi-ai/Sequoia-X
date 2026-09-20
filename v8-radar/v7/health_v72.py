from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass
class ApiCircuit:
    state: str = "CLOSED"  # CLOSED / DEGRADED / HALF_OPEN
    consecutive_failures: int = 0
    last_failure_at: str | None = None
    last_success_at: str | None = None
    degraded_until_mono: float = 0.0
    calls_today: int = 0
    calls_minute: int = 0
    cache_hits: int = 0
    failures: int = 0


class ApiHealthManager:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 120):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(10, cooldown_seconds)
        self._states: dict[str, ApiCircuit] = {}
        self._minute_events: dict[str, list[float]] = {}
        self._day = datetime.now().date().isoformat()
        self._lock = threading.RLock()

    def _state(self, api: str) -> ApiCircuit:
        return self._states.setdefault(api, ApiCircuit())

    def _roll_day(self) -> None:
        day = datetime.now().date().isoformat()
        if day != self._day:
            for s in self._states.values():
                s.calls_today = 0
            self._day = day

    def allow(self, api: str, *, per_minute: int, per_day: int, priority: str = "normal") -> tuple[bool, str]:
        now = time.monotonic()
        with self._lock:
            self._roll_day()
            s = self._state(api)
            events = [x for x in self._minute_events.setdefault(api, []) if now - x < 60]
            self._minute_events[api] = events
            s.calls_minute = len(events)
            if s.state == "DEGRADED":
                if now >= s.degraded_until_mono:
                    s.state = "HALF_OPEN"
                else:
                    return False, "circuit_degraded"
            if s.state == "HALF_OPEN" and events:
                return False, "half_open_probe_pending"
            if len(events) >= per_minute:
                return False, "minute_budget_reached"
            # actual holdings/hard-risk calls can exceed the soft daily budget by 10% only
            ceiling = int(per_day * (1.10 if priority in {"holding", "hard_risk"} else 1.0))
            if s.calls_today >= max(1, ceiling):
                return False, "daily_budget_reached"
            events.append(now)
            s.calls_minute = len(events)
            s.calls_today += 1
            return True, "allowed"

    def success(self, api: str) -> None:
        with self._lock:
            s = self._state(api)
            s.consecutive_failures = 0
            s.last_success_at = datetime.now().astimezone().isoformat(timespec="seconds")
            s.state = "CLOSED"
            s.degraded_until_mono = 0.0

    def failure(self, api: str) -> None:
        with self._lock:
            s = self._state(api)
            s.failures += 1
            s.consecutive_failures += 1
            s.last_failure_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if s.consecutive_failures >= self.failure_threshold:
                s.state = "DEGRADED"
                s.degraded_until_mono = time.monotonic() + self.cooldown_seconds

    def cache_hit(self, api: str) -> None:
        with self._lock:
            self._state(api).cache_hits += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {k: asdict(v) for k, v in self._states.items()}


HEALTH_MANAGER = ApiHealthManager()
