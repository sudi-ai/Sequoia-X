from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .config import CONFIG
from .rate_limiter import SlidingWindowRateLimiter


MODULE_DIR = Path(__file__).resolve().parent
ROOT_DIR = MODULE_DIR.parent
LOG_PATH = ROOT_DIR / "logs" / "tushare_bridge.log"
DEFAULT_HTTP_URL = "https://tt.dailyfetch.top/"


@dataclass
class BridgeHealth:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    fallback_calls: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None
    total_latency_ms: float = 0.0

    @property
    def average_latency_ms(self) -> float:
        done = self.successful_calls + self.failed_calls
        return round(self.total_latency_ms / done, 2) if done else 0.0


class TushareBridge:
    """Single initialization point for the seller's Tushare-compatible relay.

    Important: token is read only from TUSHARE_TOKEN. It is never logged.
    The private SDK http-url override is intentionally isolated here so a future
    switch back to official Tushare requires changing one module only.
    """

    def __init__(self):
        self.http_url = os.getenv("TUSHARE_HTTP_URL", DEFAULT_HTTP_URL).strip() or DEFAULT_HTTP_URL
        self.token = os.getenv("TUSHARE_TOKEN", "").strip()
        self.enabled = bool(CONFIG.tushare_enabled and CONFIG.paid_provider_enabled and self.token)
        self._pro = None
        self._ts = None
        self._lock = threading.RLock()
        self._health = BridgeHealth()
        self._limiter = SlidingWindowRateLimiter(CONFIG.api_limit_per_minute)

    def _log(self, level: str, api_name: str, latency_ms: float, message: str = "") -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            safe_message = str(message).replace(self.token, "***") if self.token else str(message)
            row = {
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "level": level,
                "api": api_name,
                "latency_ms": round(float(latency_ms), 2),
                "calls_last_minute": self._limiter.count_last_minute(),
                "message": safe_message[:800],
            }
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _initialize(self):
        if not self.enabled:
            raise RuntimeError("Tushare relay disabled or TUSHARE_TOKEN is not configured")
        with self._lock:
            if self._pro is not None:
                return self._pro
            try:
                import tushare as ts
            except Exception as exc:
                raise RuntimeError("Missing dependency: tushare. Install requirements_v7.txt") from exc
            pro = ts.pro_api(self.token)
            # Seller-required relay override. Keep this private-SDK dependency isolated here.
            pro._DataApi__http_url = self.http_url
            self._ts = ts
            self._pro = pro
            return pro

    @property
    def pro(self):
        return self._initialize()

    def _invoke_with_retry(self, api_name: str, func: Callable[[], Any], *, fallback: Optional[Callable[[], Any]] = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(CONFIG.max_retries + 1):
            start = time.perf_counter()
            self._health.total_calls += 1
            try:
                if not self._limiter.acquire(block=False):
                    raise RuntimeError("V7 internal rate limit reached")
                result = func()
                latency = (time.perf_counter() - start) * 1000.0
                self._health.successful_calls += 1
                self._health.last_success_at = datetime.now().astimezone().isoformat(timespec="seconds")
                self._health.total_latency_ms += latency
                self._log("INFO", api_name, latency, f"rows={_row_count(result)} attempt={attempt+1}")
                return result
            except Exception as exc:
                last_exc = exc
                latency = (time.perf_counter() - start) * 1000.0
                self._health.failed_calls += 1
                self._health.last_failure_at = datetime.now().astimezone().isoformat(timespec="seconds")
                self._health.last_error = repr(exc)
                self._health.total_latency_ms += latency
                self._log("ERROR", api_name, latency, f"attempt={attempt+1} {repr(exc)}")
                if attempt < CONFIG.max_retries:
                    time.sleep(CONFIG.retry_backoff_seconds * (2 ** attempt))
                    continue
        if fallback is not None:
            self._health.fallback_calls += 1
            return fallback()
        assert last_exc is not None
        raise last_exc

    def call(self, api_name: str, *, fallback: Optional[Callable[[], Any]] = None, **kwargs) -> Any:
        def invoke():
            pro = self._initialize()
            api = getattr(pro, api_name)
            return api(**kwargs)
        return self._invoke_with_retry(api_name, invoke, fallback=fallback)

    def pro_bar(self, *, fallback: Optional[Callable[[], Any]] = None, **kwargs) -> Any:
        def invoke():
            self._initialize()
            return self._ts.pro_bar(api=self._pro, **kwargs)
        return self._invoke_with_retry("pro_bar", invoke, fallback=fallback)

    def health(self) -> dict[str, Any]:
        data = asdict(self._health)
        data["average_latency_ms"] = self._health.average_latency_ms
        data["calls_last_minute"] = self._limiter.count_last_minute()
        data["enabled"] = self.enabled
        data["relay_url"] = self.http_url
        data["token_configured"] = bool(self.token)
        return data


def _row_count(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(len(value))
    except Exception:
        return 1


BRIDGE = TushareBridge()
