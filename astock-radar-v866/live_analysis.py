# -*- coding: utf-8 -*-
"""Safe LIVE analysis built only from standardized DataHub or PIT rows."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from config import DATA_DIR

ANALYSIS_FILE = DATA_DIR / "live_analysis.json"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _num(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except (TypeError, ValueError):
        return float(default)


def _clip(value, low=0.0, high=100.0):
    return round(max(low, min(high, _num(value))), 2)


def _text(value, fallback=""):
    text = str(value or "").strip()
    return fallback if not text or "\ufffd" in text or text.count("?") >= 2 else text


def _records(frame):
    return [] if frame is None or getattr(frame, "empty", True) else frame.to_dict("records")


def _latest_db_rows(table, trade_date="", limit=10000):
    from pit_store import DEFAULT_DB
    path = Path(DEFAULT_DB)
    if not path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            chosen = str(trade_date or "")
            if not chosen:
                row = conn.execute(f"SELECT MAX(trade_date) FROM {table} WHERE trade_date<>''").fetchone()
                chosen = str((row or [""])[0] or "")
            where, params = ("WHERE trade_date=?", (chosen,)) if chosen else ("", ())
            rows = conn.execute(
                f"SELECT payload_json,source,observed_at,effective_at,data_quality,is_pit_safe,trade_date,ts_code FROM {table} {where} ORDER BY id DESC LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []
    output = []
    for payload, source, observed, effective, quality, pit_safe, date, code in rows:
        try:
            item = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            item = {}
        item.update({"source": source, "observed_at": observed, "effective_at": effective,
                     "data_quality": quality, "is_pit_safe": bool(pit_safe),
                     "trade_date": item.get("trade_date") or date, "ts_code": item.get("ts_code") or code})
        output.append(item)
    return output


def _dedupe(rows):
    result = {}
    for row in reversed(list(rows)):
        code = str(row.get("ts_code", "") or "")
        if code and code not in result:
            result[code] = row
    return result


def _completed_trade_dates(hub, now):
    start, end = (now - timedelta(days=18)).strftime("%Y%m%d"), now.strftime("%Y%m%d")
    dates = []
    try:
        for row in _records(hub.trade_calendar(start, end, timeout_seconds=12.0)):
            if str(row.get("is_open", "0")) in ("1", "1.0", "True"):
                dates.append(str(row.get("cal_date") or row.get("trade_date") or ""))
    except Exception:
        pass
    dates = sorted({date for date in dates if len(date) == 8}, reverse=True)
    if now.hour < 15:
        dates = [date for date in dates if date < end]
    return dates or [end]


def _safe_fetch(label, function, warnings):
    try:
        frame = function()
        status = str(getattr(frame, "attrs", {}).get("status", "OK" if len(frame) else "EMPTY"))
        if status not in ("OK", "CACHED"):
            warnings.append(f"{label}: {status}")
        return frame
    except Exception as exc:
        warnings.append(f"{label}: {type(exc).__name__}: {exc}")
        return None


def _flow_scores(rows):
    raw = {}
    for row in rows:
        code = str(row.get("ts_code", "") or "")
        value = row.get("net_amount", row.get("net_mf_amount"))
        if code and value not in (None, ""):
            raw[code] = _num(value)
    ordered = sorted(raw.values())
    if not ordered:
        return {}, raw
    denominator = max(1, len(ordered) - 1)
    return {code: round((sum(1 for item in ordered if item < value) / denominator - 0.5) * 100, 2) for code, value in raw.items()}, raw


def _auction_by_code(rows):
    grouped = {}
    for row in rows:
        code = str(row.get("ts_code", "") or "")
        if code:
            grouped.setdefault(code, []).append(row)
    return grouped


def _build_candidates(daily_rows, basic_rows, name_rows, flow_rows, auction_rows, limit_rows, observed_at):
    from market_phase import classify_market_phase
    from signal_engine import classify_candidate

    daily, basics, names, limits = map(_dedupe, (daily_rows, basic_rows, name_rows, limit_rows))
    auctions = _auction_by_code(auction_rows)
    flow_scores, flow_amounts = _flow_scores(flow_rows)
    sector_members = {}
    for code, row in daily.items():
        sector_members.setdefault(_text(names.get(code, {}).get("industry"), "未分类"), []).append(row)
    sector_stats = {}
    for sector, rows in sector_members.items():
        changes = [_num(row.get("pct_chg")) for row in rows]
        avg_change = sum(changes) / max(1, len(changes))
        advance = 100 * sum(1 for value in changes if value > 0) / max(1, len(changes))
        amount = sum(max(0.0, _num(row.get("amount"))) for row in rows)
        sector_stats[sector] = {"name": sector, "stocks": len(rows), "pct": round(avg_change, 2),
                                "advance_rate": round(advance, 1), "amount": round(amount, 2),
                                "strength": _clip(50 + avg_change * 5.5 + (advance - 50) * 0.32)}

    advances = sum(1 for row in daily.values() if _num(row.get("pct_chg")) > 0)
    declines = sum(1 for row in daily.values() if _num(row.get("pct_chg")) < 0)
    limit_up = limit_down = touched = broken = 0
    for code, row in daily.items():
        bound = limits.get(code, {})
        close, high = _num(row.get("close")), _num(row.get("high"))
        up, down = _num(bound.get("up_limit")), _num(bound.get("down_limit"))
        limit_up += int(up > 0 and close >= up * 0.997)
        limit_down += int(down > 0 and close <= down * 1.003)
        if up > 0 and high >= up * 0.997:
            touched += 1
            broken += int(close < up * 0.997)
    breadth = 100 * advances / max(1, advances + declines)
    market_input = {"advance_rate": breadth, "limit_up": limit_up, "limit_down": limit_down,
                    "broken_rate": 100 * broken / max(1, touched), "advance_height": 2 if limit_up else 1}
    market = {**market_input, **classify_market_phase(market_input), "universe": len(daily)}

    candidates = []
    for code, row in daily.items():
        close = _num(row.get("close")); pre_close = _num(row.get("pre_close"), close)
        high = _num(row.get("high"), close); low = _num(row.get("low"), close)
        if not code or close <= 0 or pre_close <= 0:
            continue
        pct = _num(row.get("pct_chg"), (close / pre_close - 1) * 100)
        close_strength = 50.0 if high <= low else _clip((close - low) / (high - low) * 100)
        meta, basic = names.get(code, {}), basics.get(code, {})
        sector = _text(meta.get("industry"), "未分类")
        sector_info = sector_stats.get(sector, {"strength": 50, "pct": 0, "advance_rate": 50})
        display_name = _text(meta.get("name"), code)
        turnover = _num(basic.get("turnover_rate_f", basic.get("turnover_rate")))
        volume_ratio = _num(basic.get("volume_ratio"), 1.0)
        chip_score = _clip(72 - abs(turnover - 5) * 2.8 - max(0, volume_ratio - 2.5) * 8)
        trend_score = _clip(50 + pct * 3.2 + (close_strength - 50) * 0.28)
        timing_score = _clip(72 - abs(pct - 2.0) * 6.5 + (close_strength - 50) * 0.18)
        distribution = _clip(max(0, pct - 5) * 7 + max(0, turnover - 12) * 2 + max(0, 65 - close_strength) * 0.45)
        st_risk = "ST" in display_name.upper() or "退" in display_name
        event_risk = 85.0 if st_risk else 25.0
        bound = limits.get(code, {}); up_limit = _num(bound.get("up_limit"), close * 1.10); down_limit = _num(bound.get("down_limit"), close * 0.90)
        support = min(close, max(low, min(pre_close, close))); strong_support = min(low, pre_close * 0.98)
        resistance = max(high, min(up_limit, close * 1.045)); strong_resistance = max(resistance, up_limit)
        rr = round(max(0.5, min(4.0, max(0.0, resistance - close) / max(close - support, close * 0.012))), 2)
        auction_group = auctions.get(code, []); auction_quality = 50.0; auction_evidence = "该股竞价记录未返回，按中性值处理"
        if auction_group:
            prices = [_num(item.get("price", item.get("open", item.get("close")))) for item in auction_group]
            prices = [value for value in prices if value > 0]; auction_price = prices[-1] if prices else close
            gap = (auction_price / pre_close - 1) * 100 if pre_close else 0.0
            auction_quality = _clip(58 + min(5, max(-5, gap)) * 4 + min(12, math.log10(max(1, len(auction_group))) * 5))
            auction_evidence = f"竞价记录 {len(auction_group)} 条，参考价差 {gap:+.2f}%"
        main_flow_score, main_flow_amount = flow_scores.get(code, 0.0), flow_amounts.get(code)
        quality = round(min(0.98, 0.58 + (0.12 if basic else 0) + (0.08 if meta else 0) + (0.10 if code in flow_scores else 0) + (0.10 if auction_group else 0)), 2)
        feature = {
            "ts_code": code, "name": display_name, "price": close, "daily_pct": round(pct, 2),
            "market_temp": market["temperature"], "market_phase": market["phase"], "sector": sector,
            "sector_strength": sector_info["strength"], "sector_resonance": round(sector_info.get("advance_rate", 50), 1),
            "auction_quality": auction_quality, "main_flow_score": main_flow_score, "main_flow_amount": main_flow_amount,
            "chip_lock_score": chip_score, "chip_cost": _num(basic.get("close"), close), "chip_concentration": round(max(0.0, 100 - turnover * 4), 2),
            "trend_score": trend_score, "buy_timing_score": timing_score, "close_strength": close_strength,
            "support": round(support, 3), "strong_support": round(strong_support, 3), "resistance": round(resistance, 3), "strong_resistance": round(strong_resistance, 3),
            "rr": rr, "event_risk": event_risk, "distribution_risk": distribution, "st_or_delist": st_risk, "data_quality": quality,
            "open": _num(row.get("open")), "high": high, "low": low, "pre_close": pre_close,
            "amount": _num(row.get("amount")), "volume": _num(row.get("vol")),
            "turnover_rate": round(turnover, 2), "volume_ratio": round(volume_ratio, 2), "up_limit": round(up_limit, 3), "down_limit": round(down_limit, 3),
            "sector_pct": sector_info.get("pct", 0), "auction_available": bool(auction_group),
            "source": "Tushare/dailyfetch standardized + V8 rule engine", "observed_at": observed_at,
            "effective_at": row.get("effective_at") or observed_at, "is_pit_safe": bool(row.get("is_pit_safe", False)),
            "signal_type": "LIVE_PRIMARY_CANDIDATE", "risk_data_available": False,
            "evidence": {
                "daily": f"日线 {row.get('trade_date', '')}，涨跌 {pct:+.2f}%，收盘强度 {close_strength:.1f}",
                "sector": f"{sector} 平均 {sector_info.get('pct', 0):+.2f}%，强度 {sector_info['strength']:.1f}",
                "moneyflow": (f"净流金额 {main_flow_amount:.2f}，横截面分 {main_flow_score:.1f}" if main_flow_amount is not None else "资金接口无该股记录，保持中性"),
                "auction": auction_evidence, "chip": f"换手 {turnover:.2f}%，量比 {volume_ratio:.2f}，筹码代理分 {chip_score:.1f}",
                "risk": "事件接口未返回该股记录，事件风险标记为未知代理值 25；不得据此下单",
            },
        }
        scored = classify_candidate(feature)
        scored["action"] = {"A": "A级候选｜仅验证，不下单", "B": "B级观察｜等待资金/竞价进一步确认", "C": "不执行｜查看风险否决与缺失证据"}[scored["pool"]]
        candidates.append(scored)
    order = {"A": 0, "B": 1, "C": 2}
    candidates.sort(key=lambda item: (order.get(item.get("pool"), 9), -_num(item.get("score")), -_num(item.get("t1_probability"))))
    return market, candidates, sorted(sector_stats.values(), key=lambda item: (-item["strength"], -item["amount"]))[:20]


def refresh_live_analysis():
    from paid_data_hub import DataHub
    from pit_store import PITStore
    now = datetime.now().astimezone(); observed_at = now.isoformat(timespec="seconds"); decision_date = now.strftime("%Y%m%d")
    warnings = []; hub = DataHub(); daily_frame = None; market_date = ""
    for date in _completed_trade_dates(hub, now)[:4]:
        frame = _safe_fetch("daily", lambda date=date: hub.daily_cross_section(date, timeout_seconds=20.0), warnings)
        if frame is not None and not frame.empty:
            daily_frame, market_date = frame, date; break
    daily_rows = _records(daily_frame)
    if not daily_rows:
        daily_rows = _latest_db_rows("stock_daily"); market_date = str((daily_rows[0].get("trade_date") if daily_rows else "") or "")
        warnings.append("实时日线横截面为空，使用数据库最后一份真实快照")
    basic_frame = _safe_fetch("daily_basic", lambda: hub.daily_basic(trade_date=market_date, timeout_seconds=20.0), warnings) if market_date else None
    names_frame = _safe_fetch("stock_basic", lambda: hub.stock_basic(limit=6000, timeout_seconds=20.0), warnings)
    flow_frame = _safe_fetch("moneyflow", lambda: hub.moneyflow_cross_section(market_date, timeout_seconds=20.0), warnings) if market_date else None
    limit_frame = _safe_fetch("limitup_pool", lambda: hub.limitup_pool(market_date, timeout_seconds=20.0), warnings) if market_date else None
    auction_frame = _safe_fetch("auction_tick", lambda: hub.auction_tick(decision_date, timeout_seconds=20.0), warnings)
    basic_rows = _records(basic_frame) or _latest_db_rows("daily_basic", market_date); name_rows = _records(names_frame)
    flow_rows = _records(flow_frame) or _latest_db_rows("moneyflow", market_date); limit_rows = _records(limit_frame) or _latest_db_rows("limitup_pool", market_date)
    auction_rows = _records(auction_frame) or _latest_db_rows("auction_snapshot", decision_date)
    market, candidates, sectors = _build_candidates(daily_rows, basic_rows, name_rows, flow_rows, auction_rows, limit_rows, observed_at)
    from winner_discovery import discover, record_snapshot
    discovery = discover(candidates, observed_at, decision_date, market_date)
    candidates = discovery["candidates"]
    record_snapshot(DATA_DIR, discovery)
    pool_counts = {grade: sum(1 for item in candidates if item.get("pool") == grade) for grade in ("A", "B", "C")}
    persisted = 0; persisted_by_grade = {"A": 0, "A+": 0, "B": 0}; store = PITStore(); minute_key = now.strftime("%Y%m%d%H%M")
    for item in [row for row in candidates if row.get("pool") in ("A", "A+", "B")]:
        signal = dict(item); signal.update({"snapshot_id": f"live-{minute_key}-{item['ts_code']}", "trade_date": decision_date,
            "decision_time": observed_at, "source": "V8.6.6_LIVE_ANALYSIS", "observed_at": observed_at,
            "effective_at": observed_at, "is_pit_safe": bool(item.get("is_pit_safe")),
            "signal_type": "PRIMARY_SIGNAL", "sample_domain": "TRADE_SIGNAL", "is_trade_signal": True})
        try:
            store.append_signal_snapshot(signal); persisted += 1; persisted_by_grade[item.get("pool")] += 1
        except sqlite3.IntegrityError:
            pass
    if market_date and market_date != decision_date:
        warnings.append(f"完整日线基准为 {market_date}；未把 {decision_date} 的未完成日线当作收盘数据")
    warnings.extend(["T+1 为现有 V8 规则估计，不是历史样本外胜率", "事件风险数据不足，所有候选仅供验证，禁止据此自动交易"])
    payload = {"version": "V8.6.6 Rich Workbench LIVE", "status": "READY" if len(daily_rows) >= 100 else "DEGRADED",
        "generated_at": _now(), "decision_time": observed_at, "trade_date": decision_date, "market_data_date": market_date,
        "source": "Tushare/dailyfetch standardized PIT snapshot", "universe_rows": len(daily_rows), "basic_rows": len(basic_rows),
        "moneyflow_rows": len(flow_rows), "auction_rows": len(auction_rows), "limit_rows": len(limit_rows), "market": market,
        "discovery": discovery["summary"],
        "pool_counts": pool_counts, "sectors": sectors, "candidates": candidates[:200],
        "trade_signals_persisted": persisted, "trade_signals_persisted_by_grade": persisted_by_grade,
        "a_signals_persisted": persisted_by_grade["A"] + persisted_by_grade["A+"],
        "warnings": list(dict.fromkeys(warnings)), "safety": {"trading": False, "push": False, "auto_parameter_update": False, "auto_a_plus": False}}
    ANALYSIS_FILE.parent.mkdir(parents=True, exist_ok=True); temp = ANALYSIS_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); temp.replace(ANALYSIS_FILE)
    return payload


def load_live_analysis():
    if not ANALYSIS_FILE.exists():
        return {"status": "NOT_REFRESHED", "candidates": [], "sectors": [], "warnings": ["尚未运行真实分析刷新"]}
    try:
        data = json.loads(ANALYSIS_FILE.read_text(encoding="utf-8")); return data if isinstance(data, dict) else {"status": "ERROR", "candidates": []}
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "ERROR", "candidates": [], "sectors": [], "warnings": [f"分析快照读取失败：{exc}"]}


def load_pit_history(limit=80):
    from pit_store import DEFAULT_DB
    from sample_domains import table_columns, trade_signal_predicate
    path = Path(DEFAULT_DB); empty = {"signals": [], "signal_count": 0, "all_record_count": 0, "excluded_non_signals": 0, "outcome_count": 0, "paired_count": 0, "t1_count": 0, "pit_safe_count": 0, "meta_eligible": 0}
    if not path.exists(): return empty
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            outcome_columns = table_columns(conn, "signal_outcome"); t1_expr = "COALESCE(o.t1_return,o.t1)" if "t1_return" in outcome_columns else "o.t1"
            signal_columns = table_columns(conn, "signal_snapshot"); predicate = trade_signal_predicate("s", signal_columns)
            rows = conn.execute(f"SELECT s.trade_date,s.decision_time,s.ts_code,s.name,s.price,s.score,s.pool,s.source,s.data_quality,s.is_pit_safe,{t1_expr} FROM signal_snapshot s LEFT JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id WHERE {predicate} ORDER BY s.decision_time DESC LIMIT ?", (int(limit),)).fetchall()
            all_records = conn.execute("SELECT COUNT(*) FROM signal_snapshot").fetchone()[0]
            signal_count = conn.execute(f"SELECT COUNT(*) FROM signal_snapshot s WHERE {predicate}").fetchone()[0]
            stats = {"signal_count": signal_count, "all_record_count": all_records, "excluded_non_signals": max(0, all_records - signal_count),
                "outcome_count": conn.execute(f"SELECT COUNT(DISTINCT o.id) FROM signal_snapshot s JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id WHERE {predicate}").fetchone()[0],
                "paired_count": conn.execute(f"SELECT COUNT(DISTINCT s.snapshot_id) FROM signal_snapshot s JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id WHERE {predicate}").fetchone()[0],
                "t1_count": conn.execute(f"SELECT COUNT(DISTINCT s.snapshot_id) FROM signal_snapshot s JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id WHERE {predicate} AND {t1_expr} IS NOT NULL").fetchone()[0],
                "pit_safe_count": conn.execute(f"SELECT COUNT(*) FROM signal_snapshot s WHERE {predicate} AND s.is_pit_safe=1").fetchone()[0],
                "meta_eligible": conn.execute(f"SELECT COUNT(DISTINCT s.snapshot_id) FROM signal_snapshot s JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id WHERE {predicate} AND s.is_pit_safe=1 AND s.score IS NOT NULL AND {t1_expr} IS NOT NULL").fetchone()[0]}
    except sqlite3.Error: return empty
    stats["signals"] = [{"trade_date": r[0], "decision_time": r[1], "ts_code": r[2], "name": r[3], "price": r[4], "score": r[5], "pool": r[6], "source": r[7], "data_quality": r[8], "is_pit_safe": bool(r[9]), "t1_return": r[10]} for r in rows]
    return stats


if __name__ == "__main__":
    result = refresh_live_analysis()
    print(json.dumps({"status": result.get("status"), "generated_at": result.get("generated_at"), "market_data_date": result.get("market_data_date"),
        "universe_rows": result.get("universe_rows"), "pool_counts": result.get("pool_counts"), "a_signals_persisted": result.get("a_signals_persisted"), "warnings": result.get("warnings")}, ensure_ascii=False, indent=2))
