# -*- coding: utf-8 -*-
"""P0 winner recall, precision and PIT leakage acceptance report."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime

from p0_realtime import CHECKPOINTS, DISCOVERY_TYPES, ROOT, atomic_json, db_connect, iso, now_cn
from sample_domains import table_columns, trade_signal_predicate

JSON_PATH, TXT_PATH = ROOT / "WINNER_RECALL_AUDIT.json", ROOT / "WINNER_RECALL_AUDIT.txt"
AUCTION_JSON, AUCTION_TXT = ROOT / "AUCTION_0925_AUDIT.json", ROOT / "AUCTION_0925_AUDIT.txt"
SIGNAL_DB = ROOT / "data" / "live_pit.db"


def pct(num: int, den: int) -> float | None:
    return round(num / den * 100.0, 2) if den else None


def checkpoint_snapshot(conn: sqlite3.Connection, trade_date: str, label: str):
    return conn.execute("SELECT snapshot_id,observed_at FROM snapshot_manifest WHERE trade_date=? AND mandatory_label=? AND status LIKE 'OK%' AND is_pit_safe=1 ORDER BY observed_at LIMIT 1", (trade_date, label)).fetchone()


def meta_samples_added(trade_date: str) -> int:
    if not SIGNAL_DB.exists():
        return 0
    conn = sqlite3.connect(str(SIGNAL_DB)); conn.row_factory = sqlite3.Row
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"signal_snapshot", "signal_outcome"}.issubset(tables):
        conn.close(); return 0
    available = table_columns(conn, "signal_outcome")
    signal_columns = table_columns(conn, "signal_snapshot")
    predicate = trade_signal_predicate("s", signal_columns)
    target = "t1_return" if "t1_return" in available else "t1"
    try:
        value = conn.execute(f"SELECT COUNT(DISTINCT s.snapshot_id) FROM signal_snapshot s JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id WHERE s.trade_date=? AND {predicate} AND s.is_pit_safe=1 AND o.{target} IS NOT NULL", (trade_date,)).fetchone()[0]
    except sqlite3.Error:
        value = 0
    conn.close(); return int(value)


def leakage_count(conn: sqlite3.Connection, trade_date: str) -> tuple[int, list[str]]:
    issues = []
    bad_manifest = conn.execute("SELECT COUNT(*) FROM snapshot_manifest WHERE trade_date=? AND is_pit_safe=1 AND (effective_at IS NULL OR effective_at>observed_at)", (trade_date,)).fetchone()[0]
    if bad_manifest:
        issues.append(f"effective_at_after_observed_at:{bad_manifest}")
    bad_quote = conn.execute("SELECT COUNT(*) FROM intraday_quote q JOIN snapshot_manifest m ON m.snapshot_id=q.snapshot_id WHERE q.trade_date=? AND q.is_pit_safe=1 AND q.observed_at<>m.observed_at", (trade_date,)).fetchone()[0]
    if bad_quote:
        issues.append(f"quote_manifest_time_mismatch:{bad_quote}")
    bad_discovery = conn.execute("SELECT COUNT(*) FROM discovery_observation d JOIN snapshot_manifest m ON m.snapshot_id=d.snapshot_id WHERE d.trade_date=? AND d.observed_at<>m.observed_at", (trade_date,)).fetchone()[0]
    if bad_discovery:
        issues.append(f"discovery_time_mismatch:{bad_discovery}")
    return int(bad_manifest + bad_quote + bad_discovery), issues


def compute_day(conn: sqlite3.Connection, trade_date: str) -> dict:
    eod = list(conn.execute("SELECT ts_code,pct_chg,close FROM eod_daily_bar WHERE trade_date=? ORDER BY pct_chg DESC", (trade_date,)))
    safe = conn.execute("SELECT COUNT(*) FROM snapshot_manifest WHERE trade_date=? AND is_pit_safe=1 AND status LIKE 'OK%'", (trade_date,)).fetchone()[0]
    result = {"trade_date": trade_date, "safe_snapshots": int(safe), "status": "SAMPLE_INSUFFICIENT"}
    if len(eod) < 4500:
        result["reason"] = "No complete EOD full-market outcome cross-section"; return result
    top20, top50 = {row["ts_code"] for row in eod[:20]}, {row["ts_code"] for row in eod[:50]}
    result.update(actual_top20=20, actual_top50=50, checkpoints={})
    for label in CHECKPOINTS:
        snap = checkpoint_snapshot(conn, trade_date, label)
        if not snap:
            result["checkpoints"][label] = {"status": "MISSING_PIT_SNAPSHOT", "recall_top20": None, "recall_top50": None}; continue
        found = {row[0] for row in conn.execute("SELECT DISTINCT ts_code FROM discovery_observation WHERE trade_date=? AND observed_at<=? AND is_pit_safe=1", (trade_date, snap["observed_at"]))}
        result["checkpoints"][label] = {"status": "OK", "snapshot_at": snap["observed_at"], "discovered": len(found), "top20_found": len(top20 & found), "recall_top20": pct(len(top20 & found), 20), "top50_found": len(top50 & found), "recall_top50": pct(len(top50 & found), 50)}
    first_discovery = {row["ts_code"]: row["first_at"] for row in conn.execute("SELECT ts_code,MIN(observed_at) first_at FROM discovery_observation WHERE trade_date=? AND is_pit_safe=1 GROUP BY ts_code", (trade_date,))}
    first_top20 = {row["ts_code"]: row["first_at"] for row in conn.execute("SELECT ts_code,MIN(observed_at) first_at FROM intraday_quote WHERE trade_date=? AND is_pit_safe=1 AND current_top20=1 GROUP BY ts_code", (trade_date,))}
    leads = [(datetime.fromisoformat(first_top20[code]) - datetime.fromisoformat(first_discovery[code])).total_seconds() / 60.0 for code in top20 if code in first_discovery and code in first_top20]
    result["average_lead_minutes"], result["lead_sample"] = (round(sum(leads) / len(leads), 2) if leads else None), len(leads)
    outcomes = {}
    for label in DISCOVERY_TYPES:
        observations = list(conn.execute("SELECT ts_code,MIN(observed_at) first_at,price,pct_chg FROM discovery_observation WHERE trade_date=? AND discovery_type=? AND is_pit_safe=1 GROUP BY ts_code", (trade_date, label)))
        codes = {row["ts_code"] for row in observations}
        if label == "REALTIME_WINNERS":
            outcomes[label] = {"signals": len(codes), "became_eod_top50": len(codes & top50), "precision_top50": pct(len(codes & top50), len(codes))}
        elif label == "EARLY_STARTUP":
            success = 0
            for row in observations:
                later = conn.execute("SELECT MAX(pct_chg) max_pct,MIN(market_rank) best_rank FROM intraday_quote WHERE trade_date=? AND ts_code=? AND observed_at>? AND is_pit_safe=1", (trade_date, row["ts_code"], row["first_at"])).fetchone()
                if later and ((later["max_pct"] is not None and row["pct_chg"] is not None and later["max_pct"] >= row["pct_chg"] + 3.0) or (later["best_rank"] is not None and later["best_rank"] <= 50)):
                    success += 1
            outcomes[label] = {"signals": len(codes), "later_started": success, "rate": pct(success, len(codes)), "definition": "Later gain expands by >=3 percentage points or enters intraday Top50"}
        else:
            closes, success = {row["ts_code"]: row["close"] for row in eod}, 0
            for row in observations:
                if closes.get(row["ts_code"]) is not None and row["price"] and closes[row["ts_code"]] >= row["price"]:
                    success += 1
            outcomes[label] = {"signals": len(codes), "held_repair_to_close": success, "rate": pct(success, len(codes)), "definition": "EOD close is not below first repair observation price"}
    result["discovery_outcomes"] = outcomes
    leaks, leak_issues = leakage_count(conn, trade_date)
    result["pit_leakage_count"], result["pit_leakage_issues"] = leaks, leak_issues
    result["meta_samples_added"] = meta_samples_added(trade_date)
    complete = all(result["checkpoints"].get(label, {}).get("status") == "OK" for label in CHECKPOINTS)
    result["status"] = "PASS" if complete and leaks == 0 else "INCOMPLETE"
    return result


def latest_pool_reason() -> dict:
    path = ROOT / "data" / "live_analysis.json"
    if not path.exists():
        return {"A": 0, "B": 0, "reason": "live_analysis.json missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8")); counts = payload.get("pool_counts") or {}; missing = set()
        for row in payload.get("candidates", []):
            for key in ("amount_acceleration_rank", "vwap_strength_rank", "persistence_rank"):
                if row.get(key) is None:
                    missing.add(key)
        reason = "Execution gates remain strict; missing intraday evidence: " + ", ".join(sorted(missing)) if missing else "Current candidates did not pass existing execution gates"
        return {"A": int(counts.get("A", 0)), "B": int(counts.get("B", 0)), "C": int(counts.get("C", 0)), "reason": reason, "parameters_changed": False}
    except Exception as exc:
        return {"A": 0, "B": 0, "reason": f"analysis read error: {type(exc).__name__}"}


def generate_acceptance(trade_date: str | None = None) -> dict:
    conn = db_connect()
    if trade_date is None:
        row = conn.execute("SELECT MAX(trade_date) FROM snapshot_manifest WHERE is_pit_safe=1").fetchone(); trade_date = row[0] if row and row[0] else now_cn().strftime("%Y%m%d")
    day, pool = compute_day(conn, trade_date), latest_pool_reason()
    realtime = day.get("discovery_outcomes", {}).get("REALTIME_WINNERS", {}); startup = day.get("discovery_outcomes", {}).get("EARLY_STARTUP", {}); repair = day.get("discovery_outcomes", {}).get("REVERSAL_REPAIR", {})
    def recall(label):
        value = day.get("checkpoints", {}).get(label, {}).get("recall_top20"); return f"{value}%" if value is not None else "样本不足（缺少该时点真实PIT快照）"
    answers = {"1_0935_top20_recall": recall("09:35"), "2_1000_top20_recall": recall("10:00"), "3_1330_top20_recall": recall("13:30"),
               "4_average_lead_minutes": day.get("average_lead_minutes") if day.get("average_lead_minutes") is not None else "样本不足",
               "5_realtime_winner_precision": realtime.get("precision_top50") if realtime.get("precision_top50") is not None else "样本不足",
               "6_early_startup_rate": startup.get("rate") if startup.get("rate") is not None else "样本不足",
               "7_reversal_repair_rate": repair.get("rate") if repair.get("rate") is not None else "样本不足",
               "8_why_A_B_zero": pool, "9_meta_samples_added_today": day.get("meta_samples_added", 0),
               "10_pit_future_leakage": "未发现" if day.get("pit_leakage_count") == 0 and day.get("safe_snapshots", 0) > 0 else "尚无足够PIT快照可验收" if not day.get("safe_snapshots") else day.get("pit_leakage_issues")}
    counts = {"snapshots": conn.execute("SELECT COUNT(*) FROM snapshot_manifest").fetchone()[0], "pit_safe_snapshots": conn.execute("SELECT COUNT(*) FROM snapshot_manifest WHERE is_pit_safe=1").fetchone()[0], "quote_rows": conn.execute("SELECT COUNT(*) FROM intraday_quote").fetchone()[0], "sectors": conn.execute("SELECT COUNT(*) FROM sector_realtime_snapshot").fetchone()[0], "discovery_observations": conn.execute("SELECT COUNT(*) FROM discovery_observation").fetchone()[0], "sequence_events": conn.execute("SELECT COUNT(*) FROM sequence_event").fetchone()[0], "auction_baseline_dates": conn.execute("SELECT COUNT(DISTINCT trade_date) FROM auction_daily_baseline").fetchone()[0], "auction_baseline_rows": conn.execute("SELECT COUNT(*) FROM auction_daily_baseline").fetchone()[0], "eod_bar_rows": conn.execute("SELECT COUNT(*) FROM eod_daily_bar").fetchone()[0]}
    report = {"version": "V8.6.6 P0 Real-Time Data & Outcome Infrastructure", "generated_at": iso(), "trade_date": trade_date, "gate": "BLOCK_NEXT_MODEL_PHASE" if day.get("status") != "PASS" else "P0_DAY_COMPLETE", "day_audit": day, "answers": answers, "infrastructure_counts": counts,
              "definitions": {"discovery_sample": "Full-market PIT snapshots only; used for Winner Discovery, Top20/Top50 Recall and Alpha Rank", "trade_signal_sample": "Only real A/B/A+/Primary Signal joined to later outcomes; used for win-rate and Meta", "winner_target_for_precision": "EOD Top50 by daily pct_chg", "top20_recall": "Actual EOD Top20 present in any discovery pool by checkpoint", "lead_minutes": "First intraday Top20 time minus first discovery time", "pit_safe": "effective_at <= observed_at; observation and decision timestamps preserved"}, "safety": ["No trading", "No parameter changes", "No model training", "No DEMO substitution", "No 80% win-rate claim", "No full-market rows counted as trade signals"]}
    atomic_json(JSON_PATH, report)
    auction_row = conn.execute("SELECT observed_at,source,status,row_count,is_pit_safe,issues_json FROM snapshot_manifest WHERE trade_date=? AND mandatory_label='09:25' ORDER BY observed_at LIMIT 1", (trade_date,)).fetchone()
    auction = {"generated_at": iso(), "trade_date": trade_date, "required_time": "09:25",
               "forbidden_substitutes": ["stk_auction_o post-close data", "09:30 quote"]}
    if not auction_row:
        auction.update(status="WAITING_REAL_MARKET_TIME", reason="No 09:25 observation exists; no substitute was used", pit_safe=False)
    else:
        observed_hm = str(auction_row["observed_at"])[11:19]
        valid_time = "09:25:00" <= observed_hm <= "09:26:30"
        valid_source = "rt_k" in str(auction_row["source"]) and "auction_o" not in str(auction_row["source"])
        passed = valid_time and valid_source and int(auction_row["is_pit_safe"]) == 1 and int(auction_row["row_count"]) >= 5000
        auction.update(status="PASS" if passed else "FAIL", observed_at=auction_row["observed_at"], source=auction_row["source"],
                       rows=auction_row["row_count"], pit_safe=bool(auction_row["is_pit_safe"]), valid_time=valid_time,
                       no_post_close_substitution=valid_source, provider_status=auction_row["status"], issues=json.loads(auction_row["issues_json"] or "[]"))
    atomic_json(AUCTION_JSON, auction)
    AUCTION_TXT.write_text("\n".join(["AUCTION 09:25 AUDIT", "=" * 40, f"Status: {auction['status']}",
                                      f"Trade date: {trade_date}", f"Observed at: {auction.get('observed_at', 'WAITING')}",
                                      f"Rows: {auction.get('rows', 0)}", f"PIT safe: {auction.get('pit_safe', False)}",
                                      "Post-close/09:30 substitution used: False", str(auction.get("reason", ""))]), encoding="utf-8")
    lines = ["V8.6.6 P0 REAL-TIME DATA & OUTCOME ACCEPTANCE", "=" * 58, f"Generated: {report['generated_at']}", f"Trade date: {trade_date}", f"Gate: {report['gate']}", "", "十项验收回答：",
             f"1. 09:35覆盖当天Top20：{answers['1_0935_top20_recall']}", f"2. 10:00覆盖当天Top20：{answers['2_1000_top20_recall']}", f"3. 13:30覆盖当天Top20：{answers['3_1330_top20_recall']}", f"4. 平均提前发现：{answers['4_average_lead_minutes']}", f"5. REALTIME_WINNERS Precision：{answers['5_realtime_winner_precision']}", f"6. EARLY_STARTUP 后续启动率：{answers['6_early_startup_rate']}", f"7. REVERSAL_REPAIR 真正修复率：{answers['7_reversal_repair_rate']}", f"8. A/B为什么仍为0：{pool.get('reason')}（A={pool.get('A',0)}, B={pool.get('B',0)}）", f"9. 今日新增可训练Meta样本：{answers['9_meta_samples_added_today']}", f"10. PIT未来泄漏：{answers['10_pit_future_leakage']}", "", "基础设施计数："]
    lines.extend(f"- {key}: {value}" for key, value in counts.items())
    lines += ["", "硬性说明：统计样本不足时不生成胜率，不使用DEMO或盘后数据冒充盘中结果。", "在五个必需时点均有真实PIT快照且日终结果闭环前，下一阶段模型研发保持锁定。"]
    TXT_PATH.write_text("\n".join(lines), encoding="utf-8")
    cp = day.get("checkpoints", {})
    with conn:
        conn.execute("INSERT OR REPLACE INTO winner_recall_daily(trade_date,computed_at,actual_top20,actual_top50,recall_0925,recall_0935,recall_1000,recall_1100,recall_1330,recall_1430,average_lead_minutes,realtime_winner_precision,early_startup_rate,reversal_repair_rate,meta_samples_added,pit_leakage_count,status,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (trade_date, report["generated_at"], day.get("actual_top20"), day.get("actual_top50"), cp.get("09:25", {}).get("recall_top20"), cp.get("09:35", {}).get("recall_top20"), cp.get("10:00", {}).get("recall_top20"), cp.get("11:00", {}).get("recall_top20"), cp.get("13:30", {}).get("recall_top20"), cp.get("14:30", {}).get("recall_top20"), day.get("average_lead_minutes"), realtime.get("precision_top50"), startup.get("rate"), repair.get("rate"), day.get("meta_samples_added", 0), day.get("pit_leakage_count", 0), day.get("status"), json.dumps(day, ensure_ascii=False)))
    conn.close(); return report


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--trade-date"); args = parser.parse_args(); print(json.dumps(generate_acceptance(args.trade_date), ensure_ascii=False, indent=2, default=str)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
