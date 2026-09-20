# -*- coding: utf-8 -*-
"""统一真实数据层：探测、标准化、缓存、质量检查和 PIT 追加。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

import pandas as pd

from config import PROVIDER_GUARD
from pit_store import PITStore
from provider_guard import ProviderGuard
from tushare_client import get_pro, pro_bar as unified_pro_bar

METADATA_COLUMNS = ("source", "observed_at", "effective_at", "trade_date", "data_quality", "is_pit_safe")
INTERFACES = (
    "rt_k", "adj_factor",
    "trade_cal", "stock_basic", "daily", "daily_basic", "pro_bar", "stk_limit", "limit_list_ths",
    "stk_auction_tick", "stk_auction_o", "stk_auction_c", "moneyflow", "moneyflow_ths",
    "moneyflow_ind_ths", "share_float", "top_list", "block_trade", "margin_detail", "factor_list",
    "factor_value", "rt_min", "rt_min_daily",
)
REQUIRED_FIELDS = {
    "rt_k": ("ts_code", "close", "trade_time"),
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "trade_cal": ("cal_date", "is_open"), "stock_basic": ("ts_code", "name"),
    "daily": ("ts_code", "trade_date", "close"), "daily_basic": ("ts_code", "trade_date"),
    "pro_bar": ("ts_code", "trade_date", "close"), "stk_limit": ("ts_code", "trade_date"),
    "limit_list_ths": ("ts_code", "trade_date"), "stk_auction_tick": ("ts_code",),
    "stk_auction_o": ("ts_code", "trade_date"), "stk_auction_c": ("ts_code", "trade_date"),
    "moneyflow": ("ts_code", "trade_date"), "moneyflow_ths": ("ts_code", "trade_date"),
    "moneyflow_ind_ths": ("trade_date",), "share_float": ("ts_code",),
    "top_list": ("ts_code", "trade_date"), "block_trade": ("ts_code", "trade_date"),
    "margin_detail": ("ts_code", "trade_date"), "factor_list": (),
    "factor_value": ("ts_code", "trade_date"), "rt_min": ("ts_code",), "rt_min_daily": ("ts_code",),
}
TABLE_MAP = {
    "daily": "stock_daily", "pro_bar": "stock_daily", "daily_basic": "daily_basic",
    "stk_auction_tick": "auction_snapshot", "stk_auction_o": "auction_snapshot", "stk_auction_c": "auction_snapshot",
    "moneyflow": "moneyflow", "moneyflow_ths": "moneyflow", "moneyflow_ind_ths": "sector_snapshot",
    "stk_limit": "limitup_pool", "limit_list_ths": "limitup_pool", "share_float": "risk_event",
    "top_list": "risk_event", "block_trade": "risk_event", "margin_detail": "risk_event",
    "factor_value": "chip_snapshot", "rt_min": "market_daily", "rt_min_daily": "market_daily",
}


def normalize_ts_code(value):
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", text):
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) != 6:
        return text
    suffix = "SH" if digits.startswith(("5", "6", "9")) else ("BJ" if digits.startswith(("4", "8")) else "SZ")
    return f"{digits}.{suffix}"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class DataHub:
    def __init__(self, pro=None, store=None, guard=None, pro_bar_func=None):
        self.guard = guard or ProviderGuard(**PROVIDER_GUARD)
        self.store = store or PITStore()
        self.pro_bar_func = pro_bar_func or unified_pro_bar
        self.init_error = ""
        if pro is not None:
            self.pro = pro
        else:
            try:
                self.pro = get_pro()
            except Exception as exc:
                self.pro = None
                self.init_error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _cache_key(name, params):
        raw = json.dumps([name, params], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _empty(name, status="ERROR", error="", trace=None):
        frame = pd.DataFrame(columns=list(METADATA_COLUMNS))
        frame.attrs.update({"endpoint": name, "status": status, "error": error, "provider_trace": trace or []})
        return frame

    def _standardize(self, frame, name, source, trade_date="", pit_safe=False):
        if frame is None:
            return self._empty(name, "EMPTY")
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(frame)
        out = frame.copy()
        observed_at = _now()
        if "ts_code" in out.columns:
            out["ts_code"] = out["ts_code"].map(normalize_ts_code)
        if "trade_date" not in out.columns:
            out["trade_date"] = str(trade_date or "")
        else:
            out["trade_date"] = out["trade_date"].fillna(str(trade_date or "")).astype(str)
        required = REQUIRED_FIELDS.get(name, ())
        missing = [field for field in required if field not in out.columns]
        quality = 0.0 if out.empty else max(0.0, round(1.0 - 0.18 * len(missing), 2))
        out["source"] = source
        out["observed_at"] = observed_at
        out["effective_at"] = out["trade_date"].map(lambda d: f"{d[:4]}-{d[4:6]}-{d[6:8]}T15:00:00+08:00" if len(str(d)) == 8 else observed_at)
        out["data_quality"] = quality
        out["is_pit_safe"] = bool(pit_safe)
        out.attrs.update({"endpoint": name, "status": "OK" if len(out) else "EMPTY", "missing_fields": missing, "recognized_fields": [field for field in required if field in out.columns], "observed_at": observed_at})
        return out

    def fetch_interface(self, name, params=None, use_cache=True, timeout_seconds=8.0, pit_safe=False):
        params = dict(params or {})
        if name not in INTERFACES:
            return self._empty(name, "ERROR", "未知接口")
        if self.pro is None:
            return self._empty(name, "NO_TOKEN", self.init_error or "Tushare 未初始化")
        cache_key = self._cache_key(name, params)
        if use_cache:
            cached = self.store.cache_get(cache_key)
            if cached is not None:
                frame = pd.DataFrame(cached)
                frame.attrs.update({"endpoint": name, "status": "CACHED", "provider_trace": [{"endpoint": name, "status": "CACHED"}]})
                return frame

        def invoke():
            if name == "pro_bar":
                if self.pro_bar_func is unified_pro_bar:
                    return self.pro_bar_func(**params)
                return self.pro_bar_func(api=self.pro, **params)
            return getattr(self.pro, name)(**params)

        sentinel = object()
        raw = self.guard.call(name, invoke, default=sentinel, min_interval=0.05, timeout_seconds=timeout_seconds)
        health = self.guard.snapshot().get(name, {})
        trace = [{"endpoint": name, **health}]
        if raw is sentinel:
            return self._empty(name, health.get("status", "ERROR"), health.get("last_error", ""), trace)
        source = "tushare.pro_bar@dailyfetch" if name == "pro_bar" else f"tushare.{name}@dailyfetch"
        frame = self._standardize(raw, name, source, params.get("trade_date", ""), pit_safe=pit_safe)
        frame.attrs["provider_trace"] = trace
        if not frame.empty:
            self.store.cache_set(cache_key, name, frame.to_dict("records"), ttl_seconds=300)
            table = TABLE_MAP.get(name)
            if table:
                self.store.append_frame(table, frame)
        return frame

    @staticmethod
    def _merge_trace(primary, secondary):
        return list(primary.attrs.get("provider_trace", [])) + list(secondary.attrs.get("provider_trace", []))

    def trade_calendar(self, start_date, end_date=None, **kwargs):
        return self.fetch_interface("trade_cal", {"exchange": "SSE", "start_date": start_date, "end_date": end_date or start_date, "is_open": "1"}, **kwargs)

    def stock_basic(self, limit=50, **kwargs):
        return self.fetch_interface("stock_basic", {"exchange": "", "list_status": "L", "fields": "ts_code,symbol,name,area,industry,list_date", "limit": int(limit)}, **kwargs)

    def daily_bars(self, ts_code, start_date=None, end_date=None, limit=120, **kwargs):
        end_date = end_date or datetime.now().strftime("%Y%m%d")
        start_date = start_date or (datetime.now() - timedelta(days=220)).strftime("%Y%m%d")
        params = {"ts_code": normalize_ts_code(ts_code), "start_date": start_date, "end_date": end_date, "adj": "qfq"}
        first = self.fetch_interface("pro_bar", params, **kwargs)
        if not first.empty:
            return first.sort_values("trade_date").tail(int(limit)).reset_index(drop=True)
        fallback = self.fetch_interface("daily", {k: v for k, v in params.items() if k != "adj"}, **kwargs)
        fallback.attrs["provider_trace"] = self._merge_trace(first, fallback)
        fallback.attrs["fallback_reason"] = first.attrs.get("status")
        return fallback.sort_values("trade_date").tail(int(limit)).reset_index(drop=True) if not fallback.empty else fallback

    def daily_cross_section(self, trade_date, **kwargs):
        return self.fetch_interface("daily", {"trade_date": trade_date}, pit_safe=True, **kwargs)

    def daily_basic(self, ts_code="", trade_date="", start_date="", end_date="", **kwargs):
        params = {k: v for k, v in {"ts_code": normalize_ts_code(ts_code) if ts_code else "", "trade_date": trade_date, "start_date": start_date, "end_date": end_date}.items() if v}
        return self.fetch_interface("daily_basic", params, pit_safe=bool(trade_date), **kwargs)

    def auction_tick(self, trade_date, ts_code="", **kwargs):
        params = {"trade_date": trade_date}
        if ts_code:
            params["ts_code"] = normalize_ts_code(ts_code)
        return self.fetch_interface("stk_auction_tick", params, pit_safe=True, **kwargs)

    def limitup_pool(self, trade_date, **kwargs):
        first = self.fetch_interface("limit_list_ths", {"trade_date": trade_date, "limit_type": "涨停池"}, pit_safe=True, **kwargs)
        if not first.empty:
            return first
        fallback = self.fetch_interface("stk_limit", {"trade_date": trade_date}, pit_safe=True, **kwargs)
        fallback.attrs["provider_trace"] = self._merge_trace(first, fallback)
        fallback.attrs["fallback_reason"] = first.attrs.get("status")
        return fallback

    def moneyflow(self, ts_code, start_date, end_date, **kwargs):
        params = {"ts_code": normalize_ts_code(ts_code), "start_date": start_date, "end_date": end_date}
        first = self.fetch_interface("moneyflow_ths", params, **kwargs)
        if not first.empty:
            return first
        fallback = self.fetch_interface("moneyflow", params, **kwargs)
        fallback.attrs["provider_trace"] = self._merge_trace(first, fallback)
        fallback.attrs["fallback_reason"] = first.attrs.get("status")
        return fallback

    def moneyflow_cross_section(self, trade_date, **kwargs):
        """Return one standardized market-wide money-flow cross section."""
        first = self.fetch_interface("moneyflow_ths", {"trade_date": trade_date}, pit_safe=True, **kwargs)
        if not first.empty:
            return first
        fallback = self.fetch_interface("moneyflow", {"trade_date": trade_date}, pit_safe=True, **kwargs)
        fallback.attrs["provider_trace"] = self._merge_trace(first, fallback)
        fallback.attrs["fallback_reason"] = first.attrs.get("status")
        return fallback

    def chip_related_data(self, ts_code, trade_date, **kwargs):
        return {"daily_basic": self.daily_basic(ts_code=ts_code, trade_date=trade_date, **kwargs), "factor_value": self.fetch_interface("factor_value", {"ts_code": normalize_ts_code(ts_code), "trade_date": trade_date}, pit_safe=True, **kwargs), "share_float": self.share_float(ts_code=ts_code, trade_date=trade_date, **kwargs)}

    def risk_pack(self, trade_date, ts_code="", **kwargs):
        return {"share_float": self.share_float(ts_code=ts_code, trade_date=trade_date, **kwargs), "top_list": self.top_list(trade_date=trade_date, **kwargs), "block_trade": self.fetch_interface("block_trade", {"trade_date": trade_date}, pit_safe=True, **kwargs), "margin_detail": self.margin_detail(trade_date=trade_date, **kwargs)}

    def sector_flow(self, trade_date, **kwargs):
        return self.fetch_interface("moneyflow_ind_ths", {"trade_date": trade_date}, pit_safe=True, **kwargs)

    def top_list(self, trade_date, **kwargs):
        return self.fetch_interface("top_list", {"trade_date": trade_date}, pit_safe=True, **kwargs)

    def share_float(self, ts_code="", trade_date="", **kwargs):
        params = {"start_date": trade_date, "end_date": trade_date} if trade_date else {}
        if ts_code:
            params["ts_code"] = normalize_ts_code(ts_code)
        return self.fetch_interface("share_float", params, pit_safe=bool(trade_date), **kwargs)

    def margin_detail(self, trade_date, **kwargs):
        return self.fetch_interface("margin_detail", {"trade_date": trade_date}, pit_safe=True, **kwargs)

    def health(self):
        return self.guard.snapshot()
