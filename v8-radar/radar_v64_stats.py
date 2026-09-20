"""V6.4独立影子统计：与V6.3数据完全隔离，不参与行情发现。"""
from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime


STATS_DIR = "统计分析_V6.4"
DB_FILE = os.path.join(STATS_DIR, "V6.4深度确认数据库.json")
DETAIL_FILE = os.path.join(STATS_DIR, "V6.4候选明细.csv")
SUMMARY_FILE = os.path.join(STATS_DIR, "V6.4分组胜率.csv")
HORIZONS = (5, 15, 30, 60)
_lock = threading.RLock()


def _ensure_dir():
    os.makedirs(STATS_DIR, exist_ok=True)


def _load():
    _ensure_dir()
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    _ensure_dir()
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB_FILE)


def _key(source, code, date=None):
    day = date or datetime.now().strftime("%Y-%m-%d")
    return f"{day}|{source}|{str(code).zfill(6)}"


def record_discovery(candidate, source, **extra):
    """同日同来源同股票只保留首次发现价，同时累积发现次数。"""
    price = float(candidate.get("price", 0) or 0)
    code = str(candidate.get("code", "")).zfill(6)
    if not code or price <= 0:
        return
    now = datetime.now()
    with _lock:
        data = _load()
        key = _key(source, code)
        row = data.get(key, {})
        row.update({
            "key": key, "date": now.strftime("%Y-%m-%d"), "source": source,
            "code": code, "name": candidate.get("name", row.get("name", "")),
            "discovery_time": row.get("discovery_time", now.strftime("%H:%M:%S")),
            "discovery_ts": row.get("discovery_ts", now.timestamp()),
            "discovery_price": float(row.get("discovery_price", price) or price),
            "latest_price": price, "discoveries": int(row.get("discoveries", 0)) + 1,
            "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
        row.update({k: v for k, v in extra.items() if v is not None})
        data[key] = row
        _save(data)


def record_decision(candidate, source, confirmed, reasons, metrics):
    """登记V6.4深度确认结果；确认价作为新策略模拟入场价。"""
    record_discovery(candidate, source)
    now = datetime.now()
    code = str(candidate.get("code", "")).zfill(6)
    price = float(candidate.get("price", 0) or 0)
    with _lock:
        data = _load()
        key = _key(source, code)
        row = data.get(key, {})
        previous_confirmed = bool(row.get("confirmed"))
        is_confirmed = previous_confirmed or bool(confirmed)
        row.update({
            "deep_checked": True, "confirmed": is_confirmed,
            "decision": "深度通过" if is_confirmed else "深度拦截",
            "reasons": list(reasons) if not previous_confirmed else row.get("reasons", ["曾通过深度确认"]),
            "decision_time": now.strftime("%H:%M:%S"),
            "decision_ts": now.timestamp(), "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
        row.update(metrics or {})
        if confirmed and not previous_confirmed:
            row["confirm_time"] = now.strftime("%H:%M:%S")
            row["confirm_ts"] = now.timestamp()
            row["confirm_price"] = price
            row["done"] = []
        data[key] = row
        _save(data)
    generate_reports()


def update_outcomes(quote_getter):
    """跟踪发现组和确认组；重启后仍可继续补齐5/15/30/60分钟。"""
    now = datetime.now()
    changed = False
    with _lock:
        data = _load()
        for row in data.values():
            base_ts = float(row.get("confirm_ts") if row.get("confirmed") else row.get("discovery_ts", 0) or 0)
            base_price = float(row.get("confirm_price") if row.get("confirmed") else row.get("discovery_price", 0) or 0)
            if base_ts <= 0 or base_price <= 0:
                continue
            age = (now.timestamp() - base_ts) / 60
            done = set(row.get("done", []))
            due = [m for m in HORIZONS if age >= m and m not in done]
            need_close = row.get("date") == now.strftime("%Y-%m-%d") and (now.hour, now.minute) >= (15, 5) and row.get("close_return_pct") is None
            if not due and not need_close:
                continue
            try:
                quote = quote_getter(row.get("code"))
            except Exception:
                quote = None
            if not quote or float(quote.get("price", 0) or 0) <= 0:
                continue
            price = float(quote["price"])
            ret = round((price / base_price - 1) * 100, 4)
            for m in due:
                row[f"return_{m}m"] = ret
                done.add(m)
            if need_close:
                row["close_price"] = price
                row["close_return_pct"] = ret
            row["latest_price"] = price
            row["max_return_pct"] = max(float(row.get("max_return_pct", ret)), ret)
            row["min_return_pct"] = min(float(row.get("min_return_pct", ret)), ret)
            row["done"] = sorted(done)
            row["updated"] = now.strftime("%Y-%m-%d %H:%M:%S")
            changed = True
        if changed:
            _save(data)
    if changed:
        generate_reports()


def _write_csv(path, fields, rows):
    _ensure_dir()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate_reports():
    with _lock:
        rows = sorted(_load().values(), key=lambda x: (x.get("date", ""), x.get("discovery_time", ""), x.get("code", "")))
    fields = [
        "date", "discovery_time", "confirm_time", "source", "name", "code",
        "discovery_price", "confirm_price", "deep_checked", "confirmed", "decision", "reasons",
        "individual_score", "trend_score", "force_score", "buy_score", "rr", "atr", "stop_price",
        "return_5m", "return_15m", "return_30m", "return_60m", "close_return_pct",
        "max_return_pct", "min_return_pct", "discoveries",
    ]
    detail = []
    for row in rows:
        x = dict(row)
        x["reasons"] = "；".join(row.get("reasons", []))
        detail.append(x)
    _write_csv(DETAIL_FILE, fields, detail)

    groups = {}
    for row in rows:
        labels = [
            ("确认状态", "深度通过" if row.get("confirmed") else "未通过/待确认"),
            ("信号来源", str(row.get("source", "未知"))),
        ]
        for dimension, label in labels:
            groups.setdefault((dimension, label), []).append(row)
    summary = []
    for (dimension, label), ds in sorted(groups.items()):
        out = {"分组维度": dimension, "分组名称": label, "信号数": len(ds)}
        for field, title in (("return_60m", "60分钟"), ("close_return_pct", "到收盘")):
            vals = [float(x[field]) for x in ds if x.get(field) is not None]
            out[f"{title}样本"] = len(vals)
            out[f"{title}胜率%"] = round(sum(v > 0 for v in vals) / len(vals) * 100, 1) if vals else 0
            out[f"{title}平均收益%"] = round(sum(vals) / len(vals), 4) if vals else 0
        summary.append(out)
    _write_csv(SUMMARY_FILE, ["分组维度", "分组名称", "信号数", "60分钟样本", "60分钟胜率%", "60分钟平均收益%", "到收盘样本", "到收盘胜率%", "到收盘平均收益%"], summary)
