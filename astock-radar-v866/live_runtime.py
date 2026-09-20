# -*- coding: utf-8 -*-
"""工作台只读运行状态。导入本模块不会触发付费接口调用。"""
from __future__ import annotations

import json

from config import META_LABEL
from paid_data_hub import INTERFACES
from pit_store import DEFAULT_DB
from pit_store import PITStore
from sample_domains import sample_domain_status
from tushare_client import HTTP_URL, token_status
from intraday_workbench import load_intraday_snapshot

AUDIT_FILE = __import__("pathlib").Path(__file__).with_name("LIVE_DATA_AUDIT.json")


def _read_audit(path=AUDIT_FILE):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def dashboard_status(audit=None, store=None):
    audit = _read_audit() if audit is None else audit
    token = token_status()
    try:
        counts = (store or PITStore()).counts()
    except Exception:
        counts = {"signal_snapshot": 0, "signal_outcome": 0}
    interfaces = dict(audit.get("interfaces", {}))
    for name in INTERFACES:
        interfaces.setdefault(name, {"permission": "UNKNOWN", "last_call_time": "", "latency_ms": 0, "rows": 0, "fields": 0, "status": "NOT_TESTED", "last_error": ""})
    live_rows = sum(int(item.get("rows", 0) or 0) for item in interfaces.values() if item.get("status") in ("OK", "CACHED"))
    mirror = audit.get("mirror", {"status": "NOT_TESTED", "url": HTTP_URL, "error": "尚未运行 VERIFY_LIVE_DATA.bat"})
    live_ready = bool(token.get("configured") and mirror.get("status") == "OK" and live_rows > 0)
    intraday = load_intraday_snapshot(limit=1)
    intraday_ready = bool(intraday.get("ready"))
    pit_ready = (sum(int(value or 0) for key, value in counts.items() if key not in ("signal_snapshot", "signal_outcome")) > 0) or intraday_ready
    domains = sample_domain_status()
    signal_samples = domains["trade_signal"]
    sample_n = signal_samples["meta_eligible"]
    min_samples = int(META_LABEL.get("min_train_rows", 600))
    return {"token": token, "mirror": mirror, "interfaces": interfaces,
            "live_ready": live_ready or intraday_ready, "pit_ready": pit_ready,
            "live_rows": live_rows, "pit_counts": counts, "intraday": intraday,
            "intraday_ready": intraday_ready, "sample_domains": domains,
            "real_sample_n": sample_n, "paired_sample_n": signal_samples["paired"],
            "t1_labeled_n": signal_samples["t1_labeled"], "min_samples": min_samples,
            "statistics_ready": signal_samples["statistics_ready"],
            "updated_at": intraday.get("snapshot", {}).get("observed_at") or audit.get("generated_at", "尚未验证"),
            "safety": audit.get("safety", {"trading_enabled": False})}


def resolve_mode(requested=None, status=None):
    status = status or dashboard_status()
    requested = str(requested or "AUTO").upper()
    if requested == "DEMO":
        return "DEMO"
    if requested == "PIT":
        return "PIT" if status.get("pit_ready") else "DEMO"
    if requested == "LIVE":
        return "LIVE" if status.get("live_ready") else "DEMO"
    return "LIVE" if status.get("live_ready") else "DEMO"


def sample_message(status):
    signal = status.get("sample_domains", {}).get("trade_signal", {})
    return (f"交易信号样本不足，暂不生成真实胜率（真实A/B/A+/Primary Signal {signal.get('trade_signals', 0)}，"
            f"带T+1结果 {signal.get('t1_labeled', 0)}，PIT安全且可用于Meta {signal.get('meta_eligible', 0)}，"
            f"最低要求 {signal.get('min_meta_samples', status.get('min_samples', 600))}）。全市场普通股票不计入胜率样本。")


def discovery_sample_message(status):
    discovery = status.get("sample_domains", {}).get("discovery", {})
    return (f"全市场发现样本：真实PIT行 {discovery.get('pit_market_rows', 0)}，完整交易日 {discovery.get('complete_trade_days', 0)}，"
            f"已生成收盘赢家标签 {discovery.get('recall_labeled_days', 0)} 天。{discovery.get('status', '尚未采集')}。")
