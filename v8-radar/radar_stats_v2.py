"""A股雷达独立统计模块。

只记录和汇总，不参与选股、排序、过滤、推送判断。
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime


STATS_DIR = "统计分析"
SIGNALS_FILE = os.path.join(STATS_DIR, "独立信号数据库.json")
DAILY_CSV = os.path.join(STATS_DIR, "每日汇总.csv")
DETAIL_CSV = os.path.join(STATS_DIR, "独立信号明细.csv")
FUNNEL_CSV = os.path.join(STATS_DIR, "候选漏斗明细.csv")
GROUP_CSV = os.path.join(STATS_DIR, "分组效果.csv")
LEGACY_CALIBRATION = "signal_calibration.csv"
HORIZONS = (5, 15, 30, 60)


def _ensure_dir():
    os.makedirs(STATS_DIR, exist_ok=True)


def _load():
    _ensure_dir()
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    _ensure_dir()
    tmp = SIGNALS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SIGNALS_FILE)


def _key(date, source, code):
    return f"{date}|{source}|{str(code).zfill(6)}"


def record_candidate(candidate, source, stage, **extra):
    """登记漏斗阶段。同日、同来源、同股票始终合并为一条。"""
    try:
        now = datetime.now()
        date = now.strftime("%Y-%m-%d")
        code = str(candidate.get("code", "")).zfill(6)
        price = float(candidate.get("price", 0) or 0)
        if not code or price <= 0:
            return
        data = _load()
        key = _key(date, source, code)
        row = data.get(key, {})
        stages = list(row.get("stages", []))
        if stage not in stages:
            stages.append(stage)
        row.update({
            "key": key, "date": date, "source": source, "code": code,
            "name": candidate.get("name", row.get("name", "")),
            "entry_time": row.get("entry_time", now.strftime("%H:%M:%S")),
            "entry_price": float(row.get("entry_price", price) or price),
            "latest_price": price, "stages": stages,
            "qualified": bool(row.get("qualified")) or stage in ("初级候选", "最终候选", "已推送", "买点触发"),
            "selected": bool(row.get("selected")) or stage in ("最终候选", "已推送", "买点触发"),
            "pushed": bool(row.get("pushed")) or stage == "已推送",
            "buy_triggered": bool(row.get("buy_triggered")) or stage == "买点触发",
            "filtered": bool(row.get("filtered")) or stage == "被过滤",
            "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
        for k, v in extra.items():
            if v is not None:
                row[k] = v
        data[key] = row
        _save(data)
    except Exception as e:
        print("[统计V2] 候选登记失败：", repr(e))


def update_evaluation(source, code, signal_time, minutes, eval_price, return_pct):
    try:
        date = str(signal_time)[:10]
        data = _load()
        key = _key(date, source, code)
        row = data.get(key, {
            "key": key, "date": date, "source": source, "code": str(code).zfill(6),
            "entry_time": str(signal_time)[11:19], "stages": ["最终候选"],
            "qualified": True, "selected": True, "pushed": False,
            "buy_triggered": False, "filtered": False,
        })
        row[f"price_{int(minutes)}m"] = round(float(eval_price), 4)
        row[f"return_{int(minutes)}m"] = round(float(return_pct), 4)
        row["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[key] = row
        _save(data)
    except Exception as e:
        print("[统计V2] 分时结果登记失败：", repr(e))


def update_tracking(source, code, signal_date, price, trade_day_age=0, status=""):
    try:
        data = _load()
        key = _key(signal_date, source, code)
        row = data.get(key)
        if not row:
            return
        entry = float(row.get("entry_price", 0) or 0)
        price = float(price)
        row["latest_price"] = price
        row["trade_day_age"] = int(trade_day_age)
        row["track_status"] = status or row.get("track_status", "")
        if entry > 0:
            ret = round((price / entry - 1) * 100, 4)
            row["latest_return_pct"] = ret
            row["max_return_pct"] = max(float(row.get("max_return_pct", ret)), ret)
            row["min_return_pct"] = min(float(row.get("min_return_pct", ret)), ret)
            row[f"t{int(trade_day_age)}_return_pct"] = ret
        row["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[key] = row
        _save(data)
    except Exception as e:
        print("[统计V2] 跟踪结果登记失败：", repr(e))


def migrate_legacy_once():
    """把旧CSV合并为独立信号，不删除、不改写旧文件。"""
    data = _load()
    if any(v.get("legacy_migrated") for v in data.values()):
        return
    if not os.path.exists(LEGACY_CALIBRATION):
        return
    try:
        with open(LEGACY_CALIBRATION, "r", encoding="utf-8-sig", newline="") as f:
            for old in csv.DictReader(f):
                date = str(old.get("信号时间", ""))[:10]
                source, code = old.get("来源", ""), old.get("代码", "")
                if not date or not source or not code:
                    continue
                key = _key(date, source, code)
                row = data.get(key, {
                    "key": key, "date": date, "source": source, "code": str(code).zfill(6),
                    "name": old.get("名称", ""), "entry_time": str(old.get("信号时间", ""))[11:19],
                    "entry_price": float(old.get("信号价", 0) or 0),
                    "score": float(old.get("信号分", 0) or 0), "context": old.get("上下文", ""),
                    "stages": ["最终候选"], "qualified": True, "selected": True,
                    "pushed": False, "buy_triggered": False, "filtered": False,
                })
                m = int(float(old.get("评估分钟", 0) or 0))
                if m:
                    row[f"price_{m}m"] = float(old.get("评估价", 0) or 0)
                    row[f"return_{m}m"] = float(old.get("收益率%", 0) or 0)
                row["legacy_migrated"] = True
                data[key] = row
        _save(data)
    except Exception as e:
        print("[统计V2] 旧数据迁移失败：", repr(e))


def _write_csv(path, fields, rows):
    _ensure_dir()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def generate_reports():
    """重建可用Excel打开的明细和每日汇总；每个信号只计算一次。"""
    migrate_legacy_once()
    data = _load()
    rows = sorted(data.values(), key=lambda x: (x.get("date", ""), x.get("entry_time", ""), x.get("code", "")))
    detail_fields = [
        "date", "entry_time", "source", "name", "code", "entry_price", "score", "buy_score", "rise_score",
        "qualified", "selected", "pushed", "buy_triggered", "filtered", "filter_reason", "stages",
        "return_5m", "return_15m", "return_30m", "return_60m", "latest_return_pct",
        "t1_return_pct", "t3_return_pct", "t5_return_pct", "t7_return_pct",
        "max_return_pct", "min_return_pct", "trade_day_age", "track_status", "market_regime", "sector", "context",
    ]
    csv_rows = []
    for row in rows:
        x = dict(row)
        x["stages"] = "、".join(row.get("stages", []))
        csv_rows.append(x)
    _write_csv(DETAIL_CSV, detail_fields, csv_rows)
    _write_csv(FUNNEL_CSV, detail_fields, csv_rows)

    grouped = {}
    for row in rows:
        grouped.setdefault(row.get("date", ""), []).append(row)
    daily = []
    for date, ds in sorted(grouped.items()):
        selected = [x for x in ds if x.get("selected")]
        pushed = [x for x in ds if x.get("pushed")]
        out = {
            "日期": date, "初级候选数": len(ds), "最终候选数": len(selected),
            "微信推送数": len(pushed), "未推送候选数": len([x for x in selected if not x.get("pushed")]),
            "被过滤数": len([x for x in ds if x.get("filtered")]), "买点触发数": len([x for x in ds if x.get("buy_triggered")]),
        }
        for m in HORIZONS:
            vals = [float(x[f"return_{m}m"]) for x in selected if x.get(f"return_{m}m") is not None]
            out[f"{m}分钟样本"] = len(vals)
            out[f"{m}分钟盈利数"] = sum(v > 0 for v in vals)
            out[f"{m}分钟亏损数"] = sum(v <= 0 for v in vals)
            out[f"{m}分钟胜率%"] = round(sum(v > 0 for v in vals) / len(vals) * 100, 1) if vals else 0
            out[f"{m}分钟平均收益%"] = round(sum(vals) / len(vals), 3) if vals else 0
        daily.append(out)
    daily_fields = ["日期", "初级候选数", "最终候选数", "微信推送数", "未推送候选数", "被过滤数", "买点触发数"]
    for m in HORIZONS:
        daily_fields += [f"{m}分钟样本", f"{m}分钟盈利数", f"{m}分钟亏损数", f"{m}分钟胜率%", f"{m}分钟平均收益%"]
    _write_csv(DAILY_CSV, daily_fields, daily)

    # 按来源、是否推送、评分区间自动对比，便于后续调整但不自动改参数。
    groups = {}
    for row in rows:
        score = float(row.get("buy_score", row.get("score", 0)) or 0)
        score_band = "90分以上" if score >= 90 else ("80-89分" if score >= 80 else ("70-79分" if score >= 70 else "70分以下"))
        labels = [
            ("信号来源", str(row.get("source", "未知"))),
            ("微信状态", "已推送" if row.get("pushed") else "未推送"),
            ("评分区间", score_band),
            ("市场环境", str(row.get("market_regime", "历史未记录"))),
        ]
        for dimension, label in labels:
            groups.setdefault((dimension, label), []).append(row)
    group_rows = []
    for (dimension, label), ds in sorted(groups.items()):
        vals = [float(x["return_60m"]) for x in ds if x.get("return_60m") is not None]
        group_rows.append({
            "分组维度": dimension, "分组名称": label, "独立信号数": len(ds), "60分钟样本数": len(vals),
            "60分钟胜率%": round(sum(v > 0 for v in vals) / len(vals) * 100, 1) if vals else 0,
            "60分钟平均收益%": round(sum(vals) / len(vals), 3) if vals else 0,
        })
    _write_csv(GROUP_CSV, ["分组维度", "分组名称", "独立信号数", "60分钟样本数", "60分钟胜率%", "60分钟平均收益%"], group_rows)
    return daily[-1] if daily else {}
