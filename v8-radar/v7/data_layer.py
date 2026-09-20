from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import CONFIG, V72_CONFIG
from .health_v72 import HEALTH_MANAGER
from .tushare_bridge import BRIDGE


class DataUnavailable(RuntimeError):
    pass


@dataclass
class CacheItem:
    value: Any
    stored_at: float
    expires_at: float
    source: str


class UnifiedDataLayer:
    """Shared enrichment layer with TTL cache, circuit breaker and call budgets.

    V6.6 acquisition is deliberately untouched. This layer serves V7/Portfolio
    only, so failures degrade Research fields to UNKNOWN without changing the
    legacy formal radar.
    """
    def __init__(self):
        self._cache: dict[str, CacheItem] = {}
        self._lock = threading.RLock()
        self._stats = {"cache_hits": 0, "cache_misses": 0, "budget_blocks": 0, "calls": 0, "failures": 0}

    def get_cached(self, key: str) -> Any:
        with self._lock:
            item = self._cache.get(key)
            if not item:
                self._stats["cache_misses"] += 1
                return None
            if item.expires_at <= time.monotonic():
                self._cache.pop(key, None)
                self._stats["cache_misses"] += 1
                return None
            self._stats["cache_hits"] += 1
            return item.value

    def cache_meta(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
            if not item or item.expires_at <= time.monotonic():
                return None
            age = max(0.0, time.monotonic() - item.stored_at)
            return {"source": item.source, "cache_age_seconds": round(age, 3), "data_stale": False,
                    "ttl_remaining_seconds": round(max(0.0, item.expires_at-time.monotonic()), 3)}

    def set_cached(self, key: str, value: Any, ttl_seconds: int, source: str) -> Any:
        now = time.monotonic()
        with self._lock:
            self._cache[key] = CacheItem(value=value, stored_at=now, expires_at=now + max(1, ttl_seconds), source=source)
        return value

    def _allowed(self, api_name: str, priority: str) -> None:
        allowed, reason = HEALTH_MANAGER.allow(
            api_name, per_minute=min(CONFIG.api_limit_per_minute, V72_CONFIG.api_default_per_minute),
            per_day=V72_CONFIG.api_default_per_day, priority=priority,
        )
        if not allowed:
            self._stats["budget_blocks"] += 1
            raise DataUnavailable(f"{api_name}:{reason}")

    def relay_call(self, api_name: str, *, cache_key: str, ttl_seconds: int,
                   fallback: Optional[Callable[[], Any]] = None, priority: str = "normal", **kwargs) -> Any:
        cached = self.get_cached(cache_key)
        if cached is not None:
            HEALTH_MANAGER.cache_hit(api_name)
            return cached
        try:
            self._allowed(api_name, priority)
            self._stats["calls"] += 1
            result = BRIDGE.call(api_name, fallback=None, **kwargs)
            HEALTH_MANAGER.success(api_name)
            return self.set_cached(cache_key, result, ttl_seconds, "tushare_relay")
        except Exception:
            self._stats["failures"] += 1
            HEALTH_MANAGER.failure(api_name)
            if fallback is not None:
                return fallback()
            raise

    def relay_pro_bar(self, *, cache_key: str, ttl_seconds: int, fallback=None, priority: str = "normal", **kwargs) -> Any:
        cached = self.get_cached(cache_key)
        if cached is not None:
            HEALTH_MANAGER.cache_hit("pro_bar")
            return cached
        try:
            self._allowed("pro_bar", priority)
            self._stats["calls"] += 1
            result = BRIDGE.pro_bar(fallback=None, **kwargs)
            HEALTH_MANAGER.success("pro_bar")
            return self.set_cached(cache_key, result, ttl_seconds, "tushare_relay")
        except Exception:
            self._stats["failures"] += 1
            HEALTH_MANAGER.failure("pro_bar")
            if fallback is not None:
                return fallback()
            raise

    def health(self) -> dict[str, Any]:
        with self._lock:
            cache_size = len(self._cache); stats = dict(self._stats)
        return {"cache_items": cache_size, "stats": stats, "circuit": HEALTH_MANAGER.snapshot(), "relay": BRIDGE.health()}


DATA_LAYER = UnifiedDataLayer()
