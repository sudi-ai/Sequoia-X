# -*- coding: utf-8 -*-
"""Outcome updater for immutable signal_snapshot records."""
from __future__ import annotations

import argparse
import json
import sqlite3

from p0_realtime import ROOT, atomic_json, clean_value, db_connect, iso, normalize_code, now_cn
from sample_domains import table_columns, trade_signal_predicate
from tushare_client import get_pro

SIGNAL_DB = ROOT / "data" / "live_pit.db"
AUDIT_PATH = ROOT / "SIGNAL_OUTCOME_AUDIT.json"
AUDIT_TXT = ROOT / "SIGNAL_OUTCOME_AUDIT.txt"


def write_audit(result: dict) -> None:
    atomic_json(AUDIT_PATH, result)
    counts = result.get("counts") or {}
    lines = ["SIGNAL OUTCOME AUDIT", "=" * 42, f"Status: {result.get('status')}",
             f"Signals checked: {result.get('signals', 0)}", f"Inserted: {counts.get('inserted', 0)}",
             f"Updated: {counts.get('updated', 0)}", f"Waiting for future dates: {counts.get('waiting', 0)}",
             f"Failed: {counts.get('failed', 0)}", f"Fabricated outcomes: {result.get('fabricated_outcomes', 0)}",
             f"Future NULL preserved: {result.get('future_null_preserved', 0)}", "",
             "Rule: future trade dates not yet observed remain NULL; feature snapshots are never modified."]
    AUDIT_TXT.write_text("\n".join(lines), encoding="utf-8")


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def load_signals(conn: sqlite3.Connection, max_dates: int) -> list[dict]:
    available = columns(conn, "signal_snapshot")
    if not {"snapshot_id", "trade_date", "decision_time", "ts_code", "price"}.issubset(available):
        return []
    dates = [row[0] for row in conn.execute("SELECT DISTINCT trade_date FROM signal_snapshot ORDER BY trade_date DESC LIMIT ?", (max_dates,))]
    if not dates:
        return []
    placeholders = ",".join("?" for _ in dates)
    safe = "is_pit_safe" if "is_pit_safe" in available else "0 AS is_pit_safe"
    predicate = trade_signal_predicate("", available)
    return [dict(row) for row in conn.execute(f"SELECT snapshot_id,trade_date,decision_time,ts_code,price,{safe} FROM signal_snapshot WHERE trade_date IN ({placeholders}) AND {predicate}", dates)]


def ensure_daily_bars(p0: sqlite3.Connection, pro, dates: list[str], max_fetch: int) -> tuple[dict, list[str]]:
    cached = {row[0] for row in p0.execute("SELECT DISTINCT trade_date FROM eod_daily_bar WHERE trade_date IN (%s)" % ",".join("?" for _ in dates), dates)} if dates else set()
    errors = []
    for trade_date in [value for value in dates if value not in cached][:max_fetch]:
        try:
            frame = pro.daily(trade_date=trade_date)
            if frame is None or frame.empty:
                errors.append(f"{trade_date}: EMPTY"); continue
            observed, values = iso(), []
            for row in frame.to_dict("records"):
                code = normalize_code(row.get("ts_code"))
                if code:
                    values.append((trade_date, code, row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("pre_close"), row.get("pct_chg"), row.get("vol"), row.get("amount"), "Tushare.daily", observed, 1))
            with p0:
                p0.executemany("INSERT OR IGNORE INTO eod_daily_bar VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        except Exception as exc:
            errors.append(f"{trade_date}: {type(exc).__name__}: {str(exc)[:220]}")
    bars = {}
    if dates:
        for row in p0.execute("SELECT * FROM eod_daily_bar WHERE trade_date IN (%s)" % ",".join("?" for _ in dates), dates):
            bars[(row["trade_date"], row["ts_code"])] = dict(row)
    return bars, errors


def calculate(signal: dict, future_dates: list[str], bars: dict) -> dict | None:
    try:
        entry = float(signal.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    code = normalize_code(signal.get("ts_code"))
    path = [(day, bars.get((day, code))) for day in future_dates[:5]]
    available = [(day, bar) for day, bar in path if bar]
    if not available:
        return None
    result = {"signal_snapshot_id": signal["snapshot_id"], "outcome_time": available[-1][0] + "T15:00:00+08:00"}
    for horizon in (1, 3, 5):
        if len(path) >= horizon and path[horizon - 1][1]:
            result[f"t{horizon}"] = (float(path[horizon - 1][1]["close"]) / entry - 1.0) * 100.0
    highs = [float(bar["high"]) for _, bar in available if bar.get("high") is not None]
    lows = [float(bar["low"]) for _, bar in available if bar.get("low") is not None]
    result["max_favorable_excursion"] = (max(highs) / entry - 1.0) * 100.0 if highs else None
    result["max_adverse_excursion"] = (min(lows) / entry - 1.0) * 100.0 if lows else None
    result["triple_barrier_label"], status = None, "WAITING_3_BARS"
    if len(path) >= 3 and all(bar for _, bar in path[:3]):
        label, status = 0, "VERTICAL_BARRIER"
        for day, bar in path[:3]:
            profit, stop = float(bar["high"]) >= entry * 1.04, float(bar["low"]) <= entry * 0.98
            if profit and stop:
                label, status = 0, f"AMBIGUOUS_SAME_BAR:{day}"; break
            if profit:
                label, status = 1, f"PROFIT_FIRST:{day}"; break
            if stop:
                label, status = -1, f"STOP_FIRST:{day}"; break
        result["triple_barrier_label"] = label
    result["payload"] = {"source": "Tushare.daily EOD outcome only", "future_trade_dates": future_dates[:5],
                         "triple_barrier_status": status, "feature_tables_modified": False, "computed_at": iso()}
    return result


def write_outcome(conn: sqlite3.Connection, outcome: dict) -> str:
    available = columns(conn, "signal_outcome")
    existing = conn.execute("SELECT rowid,* FROM signal_outcome WHERE signal_snapshot_id=? ORDER BY rowid LIMIT 1", (outcome["signal_snapshot_id"],)).fetchone()
    mapping = {"t1": "t1_return" if "t1_return" in available else "t1", "t3": "t3_return" if "t3_return" in available else "t3", "t5": "t5_return" if "t5_return" in available else "t5", "max_favorable_excursion": "max_favorable_excursion", "max_adverse_excursion": "max_adverse_excursion", "triple_barrier_label": "triple_barrier_label"}
    if existing:
        updates, values = [], []
        for source, target in mapping.items():
            if target in available and outcome.get(source) is not None and existing[target] is None:
                updates.append(f"{target}=?"); values.append(clean_value(outcome[source]))
        if "outcome_time" in available:
            updates.append("outcome_time=?"); values.append(outcome["outcome_time"])
        if "payload_json" in available:
            try:
                payload = json.loads(existing["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            payload["p0_outcome"] = outcome["payload"]
            updates.append("payload_json=?"); values.append(json.dumps(payload, ensure_ascii=False))
        if updates:
            values.append(existing["rowid"]); conn.execute("UPDATE signal_outcome SET " + ",".join(updates) + " WHERE rowid=?", values); return "updated"
        return "unchanged"
    values = {"signal_snapshot_id": outcome["signal_snapshot_id"]}
    if "outcome_time" in available:
        values["outcome_time"] = outcome["outcome_time"]
    for source, target in mapping.items():
        if target in available:
            values[target] = clean_value(outcome.get(source))
    if "payload_json" in available:
        values["payload_json"] = json.dumps({"p0_outcome": outcome["payload"]}, ensure_ascii=False)
    if "created_at" in available:
        values["created_at"] = iso()
    keys = list(values)
    conn.execute("INSERT INTO signal_outcome(" + ",".join(keys) + ") VALUES(" + ",".join("?" for _ in keys) + ")", [values[key] for key in keys])
    return "inserted"


def update_signal_outcomes(max_fetch_dates: int = 10, max_signal_dates: int = 120) -> dict:
    result = {"status": "NOT_RUN", "generated_at": iso(), "source": "qualified A/B/A+/Primary Signal -> Tushare.daily", "safety": "Only genuine trade signals receive outcomes; full-market observations are excluded. Outcomes are written after the market date only; historical features are never changed."}
    if not SIGNAL_DB.exists():
        result.update(status="NO_SIGNAL_DATABASE", error=str(SIGNAL_DB)); write_audit(result); return result
    signals = sqlite3.connect(str(SIGNAL_DB), timeout=60); signals.row_factory = sqlite3.Row
    tables = {row[0] for row in signals.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"signal_snapshot", "signal_outcome"}.issubset(tables):
        result.update(status="NO_SIGNAL_TABLE"); write_audit(result); signals.close(); return result
    rows = load_signals(signals, max_signal_dates)
    if not rows:
        result.update(status="NO_SIGNALS", signals=0); write_audit(result); signals.close(); return result
    pro, earliest, today = get_pro(), min(str(item["trade_date"]) for item in rows), now_cn().strftime("%Y%m%d")
    try:
        calendar_frame = pro.trade_cal(exchange="", start_date=earliest, end_date=today, is_open="1")
        calendar = sorted(str(value) for value in calendar_frame["cal_date"].tolist())
    except Exception as exc:
        result.update(status="CALENDAR_ERROR", error=f"{type(exc).__name__}: {str(exc)[:300]}"); write_audit(result); signals.close(); return result
    index = {day: pos for pos, day in enumerate(calendar)}
    paths, needed = {}, set()
    for signal in rows:
        pos = index.get(str(signal["trade_date"])); future = calendar[pos + 1:pos + 6] if pos is not None else []
        paths[signal["snapshot_id"]] = future; needed.update(future)
    p0 = db_connect(); bars, errors = ensure_daily_bars(p0, pro, sorted(needed), max_fetch_dates)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "waiting": 0, "failed": 0}
    with signals:
        for signal in rows:
            try:
                outcome = calculate(signal, paths.get(signal["snapshot_id"], []), bars)
                if outcome:
                    counts[write_outcome(signals, outcome)] += 1
                else:
                    counts["waiting"] += 1
            except Exception:
                counts["failed"] += 1
    result.update(status="OK" if not errors else "PARTIAL", signals=len(rows), needed_trade_dates=len(needed), fetched_limit=max_fetch_dates, cached_bars=len(bars), errors=errors, counts=counts, completed_at=iso(), no_feature_backfill=True, no_future_data_in_features=True,
                  future_null_preserved=counts["waiting"], fabricated_outcomes=0, real_win_rate_generated=False,
                  meta_training_started=False, a_plus_enabled=False, auto_trading=False)
    write_audit(result); p0.close(); signals.close(); return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--max-fetch-dates", type=int, default=10); parser.add_argument("--max-signal-dates", type=int, default=120); args = parser.parse_args()
    print(json.dumps(update_signal_outcomes(args.max_fetch_dates, args.max_signal_dates), ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
