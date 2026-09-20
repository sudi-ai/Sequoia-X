# -*- coding: utf-8 -*-
"""真实数据层离线故障注入测试，不消耗付费接口额度。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pandas as pd

from live_data_verifier import check_mirror
from live_runtime import resolve_mode
from paid_data_hub import DataHub, METADATA_COLUMNS
from pit_store import PITStore
from provider_guard import ProviderGuard
from tushare_client import load_token, token_status


class FakePro:
    def daily(self, **_):
        return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260902", "open": 10, "high": 11, "low": 9.8, "close": 10.6, "vol": 1000}])

    def daily_basic(self, **_):
        return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260902", "turnover_rate": 2.1}])

    def trade_cal(self, **_):
        return pd.DataFrame([{"cal_date": "20260902", "is_open": 1}])


def fake_pro_bar(api=None, **_):
    assert api is not None
    return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260902", "open": 10, "high": 11, "low": 9.8, "close": 10.6, "vol": 1000}])


def run():
    results = {}
    with tempfile.TemporaryDirectory(prefix="radar_live_test_") as folder:
        root = Path(folder)
        missing = root / "missing.json"
        results["token_unconfigured"] = token_status(missing, {}).get("configured") is False
        secret = root / "secrets.local.json"
        secret.write_text(json.dumps({"tushare_token": "secret-first"}), encoding="utf-8")
        token, source, _ = load_token(secret, {"TUSHARE_TOKEN": "environment-second"})
        results["token_priority"] = token == "secret-first" and source == "secrets.local.json"

        store = PITStore(root / "pit.db")
        hub = DataHub(pro=FakePro(), store=store, pro_bar_func=fake_pro_bar)
        frame = hub.daily_bars("000001", start_date="20260901", end_date="20260902", use_cache=False)
        results["standardization"] = not frame.empty and all(column in frame.columns for column in METADATA_COLUMNS) and frame.iloc[0]["ts_code"] == "000001.SZ"
        results["pit_append"] = store.counts()["stock_daily"] == 1
        cached = hub.fetch_interface("pro_bar", {"ts_code": "000001.SZ", "start_date": "20260901", "end_date": "20260902", "adj": "qfq"}, use_cache=True)
        results["cache"] = cached.attrs.get("status") == "CACHED"

        class BadTokenPro:
            def trade_cal(self, **_):
                raise RuntimeError("token invalid")

        bad = DataHub(pro=BadTokenPro(), store=PITStore(root / "bad.db"))
        wrong = bad.fetch_interface("trade_cal", {}, use_cache=False)
        results["token_error"] = wrong.attrs.get("status") == "ERROR" and "token invalid" in wrong.attrs.get("error", "")

        class NoPermissionPro:
            def top_list(self, **_):
                raise RuntimeError("抱歉，您没有访问该接口的权限")

        denied = DataHub(pro=NoPermissionPro(), store=PITStore(root / "denied.db"))
        no_permission = denied.fetch_interface("top_list", {}, use_cache=False)
        results["no_permission"] = no_permission.attrs.get("status") == "NO_PERMISSION"

        guard = ProviderGuard(fail_threshold=1, cooldown_seconds=1)
        marker = object()
        timeout_value = guard.call("slow", lambda: time.sleep(0.2), default=marker, timeout_seconds=0.02)
        results["single_interface_timeout"] = timeout_value is marker and guard.snapshot()["slow"]["status"] == "TIMEOUT"
        results["mirror_failure"] = check_mirror("http://127.0.0.1:1/", timeout=0.05)["status"] == "ERROR"

        changed = hub._standardize(pd.DataFrame([{"symbol_changed": "000001", "last": 10}]), "daily", "test")
        results["api_field_change"] = changed.iloc[0]["data_quality"] < 1 and bool(changed.attrs.get("missing_fields"))

        signal = {"snapshot_id": "immutable-1", "trade_date": "20260902", "decision_time": "2026-09-02T10:35:00+08:00", "ts_code": "000001.SZ", "name": "测试", "price": 10.6, "score": 88, "pool": "A", "market_phase": "启动", "sector": "银行", "auction_quality": 80, "main_flow_score": 82, "chip_lock_score": 79, "trend_score": 85, "buy_timing_score": 81, "rr": 2.0, "event_risk": 4, "distribution_risk": 5, "data_quality": 0.95, "source": "test", "observed_at": "2026-09-02T10:35:00+08:00", "effective_at": "2026-09-02T10:35:00+08:00", "is_pit_safe": True, "extra_feature": 7}
        store.append_signal_snapshot(signal)
        duplicate_blocked = False
        try:
            store.append_signal_snapshot(signal)
        except sqlite3.IntegrityError:
            duplicate_blocked = True
        results["pit_immutable"] = duplicate_blocked
        results["sample_gate"] = resolve_mode("DEMO", {"live_ready": True, "pit_ready": True}) == "DEMO" and resolve_mode("LIVE", {"live_ready": True, "pit_ready": False}) == "LIVE" and resolve_mode("PIT", {"live_ready": False, "pit_ready": True}) == "PIT" and resolve_mode("LIVE", {"live_ready": False, "pit_ready": False}) == "DEMO"

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        raise AssertionError("离线真实数据接入测试失败: " + ", ".join(failed))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("ALL LIVE DATA INTEGRATION TESTS PASSED")
    return results


if __name__ == "__main__":
    run()
