# -*- coding: utf-8 -*-
"""付费数据源保护：限流、超时、熔断和可解释状态。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic, sleep


@dataclass
class ProviderHealth:
    name: str
    calls: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    last_call_time: str = ""
    last_error: str = ""
    last_latency_ms: float = 0.0
    last_rows: int = 0
    last_fields: int = 0
    status: str = "NOT_TESTED"
    permission: str = "UNKNOWN"
    circuit_open_until: float = 0.0


def classify_provider_error(exc):
    message = f"{type(exc).__name__}: {exc}"
    lower = message.lower()
    if isinstance(exc, TimeoutError) or "timeout" in lower or "timed out" in lower or "超时" in message:
        return "TIMEOUT", "UNKNOWN", message
    permission_tokens = ("permission", "no privilege", "not authorized", "权限", "没有访问", "无权", "积分不足")
    if any(token in lower or token in message for token in permission_tokens):
        return "NO_PERMISSION", "DENIED", message
    return "ERROR", "UNKNOWN", message


class ProviderGuard:
    def __init__(self, fail_threshold=3, cooldown_seconds=45):
        self.fail_threshold = int(fail_threshold)
        self.cooldown_seconds = int(cooldown_seconds)
        self._health = {}
        self._last_call = {}
        self._lock = Lock()

    def _h(self, name):
        with self._lock:
            if name not in self._health:
                self._health[name] = ProviderHealth(name)
            return self._health[name]

    @staticmethod
    def _shape(result):
        if result is None:
            return 0, 0
        try:
            rows = len(result)
        except Exception:
            rows = 1
        columns = getattr(result, "columns", None)
        try:
            fields = len(columns) if columns is not None else 0
        except Exception:
            fields = 0
        return int(rows), int(fields)

    @staticmethod
    def _run_with_timeout(fn, timeout_seconds):
        if timeout_seconds is None or float(timeout_seconds) <= 0:
            return fn()
        queue = Queue(maxsize=1)

        def worker():
            try:
                queue.put((True, fn()))
            except BaseException as exc:
                queue.put((False, exc))

        Thread(target=worker, daemon=True).start()
        try:
            ok, value = queue.get(timeout=float(timeout_seconds))
        except Empty as exc:
            raise TimeoutError(f"接口调用超过 {timeout_seconds} 秒") from exc
        if ok:
            return value
        raise value

    def call(self, name, fn, default=None, min_interval=0.0, timeout_seconds=None):
        h = self._h(name)
        now = monotonic()
        if now < h.circuit_open_until:
            h.status = "CIRCUIT_OPEN"
            return default
        if min_interval > 0:
            wait = float(min_interval) - (now - self._last_call.get(name, 0.0))
            if wait > 0:
                sleep(wait)
        t0 = monotonic()
        h.last_call_time = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            result = self._run_with_timeout(fn, timeout_seconds)
            rows, fields = self._shape(result)
            h.calls += 1
            h.consecutive_errors = 0
            h.last_error = ""
            h.last_latency_ms = round((monotonic() - t0) * 1000, 1)
            h.last_rows = rows
            h.last_fields = fields
            h.status = "OK" if rows > 0 else "EMPTY"
            h.permission = "GRANTED"
            self._last_call[name] = monotonic()
            return result
        except BaseException as exc:
            status, permission, message = classify_provider_error(exc)
            h.calls += 1
            h.errors += 1
            h.consecutive_errors += 1
            h.last_error = message
            h.last_latency_ms = round((monotonic() - t0) * 1000, 1)
            h.last_rows = 0
            h.last_fields = 0
            h.status = status
            h.permission = permission
            self._last_call[name] = monotonic()
            if h.consecutive_errors >= self.fail_threshold:
                h.circuit_open_until = monotonic() + self.cooldown_seconds
            return default

    def snapshot(self):
        now = monotonic()
        out = {}
        for name, h in self._health.items():
            item = asdict(h)
            item["circuit_open"] = now < h.circuit_open_until
            item["error_rate"] = round(h.errors / max(h.calls, 1), 4)
            item.pop("circuit_open_until", None)
            out[name] = item
        return out
