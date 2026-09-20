# -*- coding: utf-8 -*-
"""真实付费数据接入审计。只探测、标准化、缓存和落 PIT，不做交易。"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from paid_data_hub import DataHub, INTERFACES, METADATA_COLUMNS, normalize_ts_code
from tushare_client import HTTP_URL, token_status

ROOT = Path(__file__).resolve().parent
AUDIT_JSON = ROOT / "LIVE_DATA_AUDIT.json"
AUDIT_TXT = ROOT / "LIVE_DATA_AUDIT.txt"
INTEGRATION_JSON = ROOT / "LIVE_DATA_INTEGRATION_AUDIT.json"
INTEGRATION_TXT = ROOT / "LIVE_DATA_INTEGRATION_AUDIT.txt"
FIXED_CODES = ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "300750.SZ"]


def check_mirror(url=HTTP_URL, timeout=5.0):
    started = datetime.now()
    try:
        response = requests.get(url, timeout=float(timeout), allow_redirects=True)
        latency = round((datetime.now() - started).total_seconds() * 1000, 1)
        normal = response.status_code < 500
        return {"url": url, "status": "OK" if normal else "ERROR", "http_status": response.status_code, "latency_ms": latency, "error": "" if normal else f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"url": url, "status": "ERROR", "http_status": None, "latency_ms": round((datetime.now() - started).total_seconds() * 1000, 1), "error": f"{type(exc).__name__}: {exc}"}


def _frame_status(name, frames, hub):
    frames = [frame for frame in frames if frame is not None]
    rows = sum(len(frame) for frame in frames)
    fields = sorted({str(column) for frame in frames for column in frame.columns})
    states = [str(frame.attrs.get("status", "EMPTY")) for frame in frames]
    errors = [str(frame.attrs.get("error", "")) for frame in frames if frame.attrs.get("error")]
    missing = sorted({str(field) for frame in frames for field in frame.attrs.get("missing_fields", [])})
    health = hub.health().get(name, {})
    if rows:
        state = "OK"
        permission = "GRANTED"
    elif "NO_PERMISSION" in states:
        state, permission = "NO_PERMISSION", "DENIED"
    elif "TIMEOUT" in states:
        state, permission = "TIMEOUT", "UNKNOWN"
    elif "NO_TOKEN" in states:
        state, permission = "NO_TOKEN", "UNKNOWN"
    elif "ERROR" in states:
        state, permission = "ERROR", "UNKNOWN"
    else:
        state, permission = "EMPTY", health.get("permission", "GRANTED" if frames else "UNKNOWN")
    observed = [str(frame.attrs.get("observed_at", "")) for frame in frames if frame.attrs.get("observed_at")]
    timestamps_ok = True
    codes_ok = True
    for frame in frames:
        if not frame.empty:
            timestamps_ok = timestamps_ok and all(column in frame.columns for column in METADATA_COLUMNS)
            if "observed_at" in frame:
                timestamps_ok = timestamps_ok and bool(pd.to_datetime(frame["observed_at"], errors="coerce").notna().all())
            if "ts_code" in frame:
                values = [str(value) for value in frame["ts_code"].dropna() if str(value)]
                codes_ok = codes_ok and all(normalize_ts_code(value) == value for value in values)
    return {
        "permission": permission,
        "last_call_time": health.get("last_call_time", max(observed) if observed else ""),
        "latency_ms": health.get("last_latency_ms", 0),
        "rows": rows,
        "fields": len(fields),
        "field_names": fields,
        "missing_expected_fields": missing,
        "status": state,
        "last_error": health.get("last_error", "") or " | ".join(errors),
        "timestamps_ok": timestamps_ok,
        "codes_normalized": codes_ok,
    }


def _probe(hub, name, params, pit_safe=False):
    return hub.fetch_interface(name, params, use_cache=False, timeout_seconds=8.0, pit_safe=pit_safe)


def run(output_json=AUDIT_JSON, output_txt=AUDIT_TXT):
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    token = token_status()
    mirror = check_mirror()
    audit = {
        "version": "V8.6.6 LiveData",
        "generated_at": generated_at,
        "mode": "VERIFY_ONLY",
        "token": {"configured": token["configured"], "source": token["source"], "error": token["error"]},
        "mirror": mirror,
        "interfaces": {},
        "sampled_stocks": [],
        "quality": {},
        "safety": {"trading_enabled": False, "auto_parameter_update": False, "auto_champion_promotion": False, "auto_a_plus": False},
    }
    hub = DataHub()
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
    if hub.pro is None:
        for name in INTERFACES:
            frame = hub.fetch_interface(name, use_cache=False)
            audit["interfaces"][name] = _frame_status(name, [frame], hub)
        audit["overall_status"] = "NOT_READY"
        audit["quality"] = {"no_crash": True, "metadata_complete": False, "timestamps_ok": False, "codes_normalized": False, "silent_fallback": False}
        return _write_audit(audit, output_json, output_txt)

    frames = {}
    calendar = _probe(hub, "trade_cal", {"exchange": "SSE", "start_date": start, "end_date": today, "is_open": "1"})
    frames["trade_cal"] = [calendar]
    trade_date = today
    if not calendar.empty and "cal_date" in calendar:
        trade_date = max(str(value) for value in calendar["cal_date"].dropna())
    stock_basic = _probe(hub, "stock_basic", {"exchange": "", "list_status": "L", "fields": "ts_code,symbol,name,area,industry,list_date", "limit": 80})
    frames["stock_basic"] = [stock_basic]
    available = [normalize_ts_code(value) for value in stock_basic.get("ts_code", pd.Series(dtype=str)).dropna().tolist()]
    available = [value for value in available if value]
    rng = random.Random(trade_date)
    codes = rng.sample(available, min(5, len(available))) if len(available) >= 5 else FIXED_CODES
    audit["sampled_stocks"] = codes
    history_start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")

    for name in ("daily", "pro_bar", "daily_basic"):
        frames[name] = []
        for code in codes:
            params = {"ts_code": code, "start_date": history_start, "end_date": trade_date}
            if name == "pro_bar":
                params["adj"] = "qfq"
            frames[name].append(_probe(hub, name, params, pit_safe=True))

    one = codes[0]
    plans = {
        "stk_limit": ({"trade_date": trade_date}, True),
        "limit_list_ths": ({"trade_date": trade_date, "limit_type": "涨停池"}, True),
        "stk_auction_tick": ({"start_date": trade_date, "end_date": trade_date, "ts_code": one}, True),
        "stk_auction_o": ({"trade_date": trade_date, "ts_code": one}, True),
        "stk_auction_c": ({"trade_date": trade_date, "ts_code": one}, True),
        "moneyflow": ({"ts_code": one, "start_date": history_start, "end_date": trade_date}, True),
        "moneyflow_ths": ({"ts_code": one, "start_date": history_start, "end_date": trade_date}, True),
        "moneyflow_ind_ths": ({"trade_date": trade_date}, True),
        "share_float": ({"ts_code": one, "start_date": history_start, "end_date": trade_date}, True),
        "top_list": ({"trade_date": trade_date}, True),
        "block_trade": ({"trade_date": trade_date}, True),
        "margin_detail": ({"trade_date": trade_date}, True),
        "factor_list": ({}, False),
        "factor_value": ({"trade_date": trade_date, "ts_code": one}, True),
        "rt_min": ({"ts_code": one, "freq": "1MIN"}, True),
        "rt_min_daily": ({"ts_code": one, "freq": "1MIN"}, True),
    }
    for name, (params, pit_safe) in plans.items():
        try:
            frames[name] = [_probe(hub, name, params, pit_safe=pit_safe)]
        except Exception as exc:
            frames[name] = [hub._empty(name, "ERROR", f"{type(exc).__name__}: {exc}")]

    for name in INTERFACES:
        audit["interfaces"][name] = _frame_status(name, frames.get(name, []), hub)
    audit["trade_date"] = trade_date
    audit["pit_counts"] = hub.store.counts()
    records = list(audit["interfaces"].values())
    audit["quality"] = {
        "no_crash": True,
        "metadata_complete": all(item["timestamps_ok"] for item in records if item["rows"] > 0),
        "timestamps_ok": all(item["timestamps_ok"] for item in records if item["rows"] > 0),
        "codes_normalized": all(item["codes_normalized"] for item in records if item["rows"] > 0),
        "fields_recognized": all(not item["missing_expected_fields"] for item in records if item["rows"] > 0),
        "silent_fallback": False,
    }
    core_ok = audit["interfaces"]["trade_cal"]["status"] == "OK" and any(audit["interfaces"][name]["status"] == "OK" for name in ("daily", "pro_bar"))
    audit["overall_status"] = "PASS" if token["configured"] and mirror["status"] == "OK" and core_ok else "PARTIAL"
    return _write_audit(audit, output_json, output_txt)


def _write_audit(audit, output_json, output_txt):
    output_json = Path(output_json)
    output_txt = Path(output_txt)
    output_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    status_counts = {}
    for item in audit["interfaces"].values():
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    lines = [
        "A股机会雷达 V8.6.6 真实付费数据接入审计",
        f"生成时间: {audit['generated_at']}",
        f"总体状态: {audit.get('overall_status')}",
        f"Token: {'已配置' if audit['token']['configured'] else '未配置'}（不输出密钥）",
        f"dailyfetch 镜像: {audit['mirror']['status']}，HTTP={audit['mirror'].get('http_status')}，延迟={audit['mirror'].get('latency_ms')}ms",
        f"抽样股票: {', '.join(audit.get('sampled_stocks', [])) or '无'}",
        "接口状态: " + ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items())),
        "",
        "接口明细:",
    ]
    for name, item in audit["interfaces"].items():
        lines.append(f"- {name}: {item['status']} | 权限={item['permission']} | 行={item['rows']} | 字段={item['fields']} | 延迟={item['latency_ms']}ms | 错误={item['last_error'] or '-'}")
    lines += ["", "安全规则: 不交易、不自动调参、不自动晋级、不自动开启A+。", "统计规则: signal_snapshot/outcome 真实样本不足时不生成胜率。"]
    output_txt.write_text("\n".join(lines), encoding="utf-8")
    if output_json.resolve() == AUDIT_JSON.resolve():
        INTEGRATION_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        INTEGRATION_TXT.write_text("\n".join(lines), encoding="utf-8")
    return audit


def main():
    audit = run()
    print(f"LIVE DATA AUDIT: {audit.get('overall_status')}")
    print(AUDIT_JSON)
    print(AUDIT_TXT)


if __name__ == "__main__":
    main()
