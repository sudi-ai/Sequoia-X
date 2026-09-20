from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .config import V72_CONFIG
from .data_layer import DATA_LAYER
from .tushare_bridge import BRIDGE
from .runtime_log_v72 import log_event

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT_DIR = Path(__file__).resolve().parent.parent
HEALTH_PATH = ROOT_DIR / "data_v7" / "api_health.json"
CRITICAL_APIS = {"rt_k", "rt_min", "trade_cal", "stk_auction_tick"}
ALL_APIS = ("daily", "trade_cal", "rt_k", "rt_min", "rt_min_daily", "anns_d", "news", "major_news", "report_rc", "stk_auction_o", "stk_auction_tick", "us_tbr", "pro_bar")


def _records(v: Any) -> list[dict[str, Any]]:
    if v is None:
        return []
    if hasattr(v, "to_dict"):
        try:
            return [dict(x) for x in v.to_dict(orient="records")]
        except Exception:
            return []
    if isinstance(v, list):
        return [dict(x) for x in v if isinstance(x, Mapping)]
    if isinstance(v, Mapping):
        return [dict(v)]
    return []


def _row_count(v: Any) -> int:
    rows = _records(v)
    if rows:
        return len(rows)
    try:
        return len(v)
    except Exception:
        return 0 if v is None else 1


def _extract_data_time(v: Any) -> str | None:
    rows = _records(v)
    candidates = []
    for r in rows[:100]:
        for k in ("trade_time", "datetime", "time", "ann_date", "pub_time", "trade_date", "date"):
            x = r.get(k)
            if x not in (None, ""):
                candidates.append(str(x))
                break
    return max(candidates) if candidates else None


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, data: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        log_event("api_health", "保存接口健康状态失败", exc=exc, degraded=True, event="health_state")


def _probe_specs(bridge, now: datetime):
    day = now.strftime("%Y%m%d")
    start_dt = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    end_dt = now.strftime("%Y-%m-%d %H:%M:%S")
    start_day = (now - timedelta(days=5)).strftime("%Y%m%d")
    code = "000001.SZ"
    return {
        "daily": lambda: bridge.call("daily", trade_date=day),
        "trade_cal": lambda: bridge.call("trade_cal", exchange="SSE", start_date=day, end_date=day),
        "rt_k": lambda: bridge.call("rt_k", ts_code=code),
        "rt_min": lambda: bridge.call("rt_min", ts_code=code, freq="1MIN"),
        "rt_min_daily": lambda: bridge.call("rt_min_daily", ts_code=code, freq="1MIN"),
        "anns_d": lambda: bridge.call("anns_d", ts_code=code, start_date=start_day, end_date=day),
        "news": lambda: bridge.call("news", src="sina", start_date=start_dt, end_date=end_dt),
        "major_news": lambda: bridge.call("major_news", src="sina", start_date=start_dt, end_date=end_dt),
        "report_rc": lambda: bridge.call("report_rc", ts_code=code, start_date=start_day, end_date=day),
        "stk_auction_o": lambda: bridge.call("stk_auction_o", ts_code=code, trade_date=day),
        "stk_auction_tick": lambda: bridge.call("stk_auction_tick", ts_code=code, start_date=day, end_date=day),
        "us_tbr": lambda: bridge.call("us_tbr", start_date=start_day, end_date=day),
        "pro_bar": lambda: bridge.pro_bar(ts_code=code, freq="1min", start_date=start_dt, end_date=end_dt),
    }


def _probe_one(name: str, fn, previous: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
    previous = dict(previous or {})
    attempts = int(previous.get("consecutive_failures") or 0)
    was_degraded = previous.get("status") in {"DEGRADED", "HALF_OPEN", "UNAVAILABLE"}
    st = time.perf_counter()
    try:
        value = fn()
        latency = round((time.perf_counter() - st) * 1000, 2)
        row_count = _row_count(value)
        return {
            "api_name": name, "status": "OK", "row_count": row_count, "latency_ms": latency,
            "data_time": _extract_data_time(value), "freshness": "UNKNOWN", "error_message": "",
            "checked_at": now.isoformat(timespec="seconds"), "consecutive_failures": 0,
            "last_success_at": now.isoformat(timespec="seconds"), "last_failure_at": previous.get("last_failure_at"),
            "next_retry_at": None, "recovered": bool(was_degraded),
        }
    except Exception as exc:
        latency = round((time.perf_counter() - st) * 1000, 2)
        attempts += 1
        status = "UNAVAILABLE" if attempts >= V72_CONFIG.health_unavailable_after else "DEGRADED"
        next_retry = (now + timedelta(minutes=V72_CONFIG.health_retry_minutes)).isoformat(timespec="seconds")
        log_event("api_health", f"{name} 健康检查失败", exc=exc, degraded=True, event="health_check")
        return {
            "api_name": name, "status": status, "row_count": 0, "latency_ms": latency,
            "data_time": None, "freshness": "UNKNOWN", "error_message": type(exc).__name__,
            "checked_at": now.isoformat(timespec="seconds"), "consecutive_failures": attempts,
            "last_success_at": previous.get("last_success_at"), "last_failure_at": now.isoformat(timespec="seconds"),
            "next_retry_at": next_retry, "recovered": False,
        }


def run_health_check(*, force: bool = False, now: datetime | None = None, save: bool = True,
                     bridge=None, health_path: Path | None = None) -> dict[str, Any]:
    """Retryable per-interface health check.

    Failures are retried after V72_HEALTH_RETRY_MINUTES. Recovery replaces DEGRADED/
    UNAVAILABLE with OK the same day. It never blocks the V6.6 formal process.
    """
    now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    path = health_path or HEALTH_PATH
    bridge = bridge or BRIDGE
    old = _load_state(path)
    old_day = str(old.get("checked_at") or "")[:10]
    old_map = {str(x.get("api_name")): dict(x) for x in old.get("apis", []) if isinstance(x, Mapping)} if old_day == now.date().isoformat() else {}
    if not getattr(bridge, "enabled", False):
        result = {"checked_at": now.isoformat(timespec="seconds"), "status": "UNAVAILABLE", "apis": [], "critical": {}, "runtime": DATA_LAYER.health()}
        if save:
            _save_state(path, result)
        return result
    specs = _probe_specs(bridge, now)
    out = []
    for name in ALL_APIS:
        prev = old_map.get(name, {})
        if not force and prev:
            if prev.get("status") == "OK":
                out.append(prev)
                continue
            nxt = prev.get("next_retry_at")
            if nxt:
                try:
                    if now < datetime.fromisoformat(str(nxt)).astimezone(CN_TZ):
                        out.append(prev)
                        continue
                except Exception:
                    pass
            # Due retry: mark half-open conceptually, then probe immediately.
            prev = {**prev, "status": "HALF_OPEN"}
        out.append(_probe_one(name, specs[name], prev, now))
    critical = {x["api_name"]: x["status"] for x in out if x["api_name"] in CRITICAL_APIS}
    statuses = {x["status"] for x in out}
    overall = "OK" if statuses == {"OK"} else ("UNAVAILABLE" if statuses == {"UNAVAILABLE"} else "DEGRADED")
    result = {"checked_at": now.isoformat(timespec="seconds"), "status": overall, "apis": out, "critical": critical, "runtime": DATA_LAYER.health()}
    if save:
        _save_state(path, result)
    return result
