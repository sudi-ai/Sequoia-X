from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .signal_lab import DEFAULT_DB, _connect, _LOCK

HORIZONS = {"D0": 0, "D1": 1, "D3": 3, "D5": 5, "D10": 10}


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _round_trip_cost_pct() -> float:
    try:
        return max(0.0, float(os.getenv("V7_ROUND_TRIP_COST_BPS", "0"))) / 100.0
    except Exception:
        return 0.0


def _extract_trade_levels(snapshot_json: str, evidence_json: str) -> dict[str, Optional[float]]:
    def obj(raw):
        try: return json.loads(raw or "{}")
        except Exception: return {}
    snap, ev = obj(snapshot_json), obj(evidence_json)
    trade = {}
    deep = snap.get("v64_deep") or {}
    if isinstance(deep, dict): trade = deep.get("trade") or {}
    if not trade and isinstance(ev, dict): trade = ev.get("trade_plan") or {}
    return {"stop": _f(trade.get("defense")), "target1": _f(trade.get("target1")),
            "target2": _f(trade.get("trend_target") or trade.get("target2"))}


def observe_quotes(quotes: Mapping[str, Mapping[str, Any]], *, observed_at: Optional[str] = None,
                   source: str = "realtime_snapshot", db_path: Path = DEFAULT_DB) -> int:
    stamp = observed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    day = stamp[:10]; inserted = 0
    with _LOCK:
        conn = _connect(db_path)
        try:
            signals = conn.execute("SELECT id,code,signal_time FROM signal_events WHERE signal_time<=?", (stamp,)).fetchall()
            for s in signals:
                q = quotes.get(str(s["code"]).zfill(6)) or {}
                px = _f(q.get("price") if isinstance(q, Mapping) else None)
                if px is None or px <= 0: continue
                cur = conn.execute("""INSERT OR IGNORE INTO signal_observations
                    (signal_id,observed_at,observed_date,price,source) VALUES (?,?,?,?,?)""",
                    (int(s["id"]), stamp, day, px, source))
                inserted += 1 if cur.rowcount == 1 else 0
            conn.commit()
        finally: conn.close()
    recompute_outcomes(db_path=db_path)
    if source == "V6全A共享快照":
        try:
            from v8.market_audit_runtime import safe_observe_full_market
            safe_observe_full_market(quotes, observed_at=stamp)
        except Exception:
            pass
    return inserted


def ingest_daily_prices(rows: Iterable[Mapping[str, Any]], *, source: str = "daily_bar",
                        db_path: Path = DEFAULT_DB) -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds"); n = 0
    with _LOCK:
        conn = _connect(db_path)
        try:
            for row in rows:
                raw_code = str(row.get("code") or row.get("ts_code") or "").strip()
                code = raw_code.split(".")[0].zfill(6)
                raw_date = str(row.get("trade_date") or row.get("date") or "").strip()
                day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}" if len(raw_date)==8 and raw_date.isdigit() else raw_date[:10]
                close = _f(row.get("close"))
                if not code.strip("0") or len(day)!=10 or close is None: continue
                conn.execute("""INSERT INTO daily_prices(code,trade_date,open,high,low,close,source,raw_json,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(code,trade_date) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    source=excluded.source,raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                    (code, day, _f(row.get("open")), _f(row.get("high")), _f(row.get("low")), close,
                     source, json.dumps(dict(row), ensure_ascii=False, default=str), now))
                n += 1
            conn.commit()
        finally: conn.close()
    recompute_outcomes(db_path=db_path)
    return n



def ingest_minute_prices(rows: Iterable[Mapping[str, Any]], *, source: str = "minute_bar",
                         db_path: Path = DEFAULT_DB) -> int:
    """Store minute bars so same-day stop/target ambiguity can be resolved by actual path."""
    now = datetime.now().astimezone().isoformat(timespec="seconds"); n = 0
    with _LOCK:
        conn = _connect(db_path)
        try:
            for row in rows:
                raw_code = str(row.get("code") or row.get("ts_code") or "").strip()
                code = raw_code.split(".")[0].zfill(6)
                raw_time = str(row.get("trade_time") or row.get("datetime") or row.get("time") or row.get("trade_date") or "").strip()
                if len(raw_time) == 14 and raw_time.isdigit():
                    stamp = f"{raw_time[:4]}-{raw_time[4:6]}-{raw_time[6:8]}T{raw_time[8:10]}:{raw_time[10:12]}:{raw_time[12:14]}"
                elif len(raw_time) >= 19:
                    stamp = raw_time[:19].replace(" ", "T")
                elif len(raw_time) == 12 and raw_time.isdigit():
                    stamp = f"{raw_time[:4]}-{raw_time[4:6]}-{raw_time[6:8]}T{raw_time[8:10]}:{raw_time[10:12]}:00"
                else:
                    continue
                day = stamp[:10]
                close = _f(row.get("close") if row.get("close") is not None else row.get("price"))
                if not code.strip("0") or close is None:
                    continue
                conn.execute("""INSERT INTO minute_prices(code,observed_at,trade_date,open,high,low,close,source,raw_json,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code,observed_at) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    source=excluded.source,raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                    (code, stamp, day, _f(row.get("open")), _f(row.get("high")), _f(row.get("low")), close,
                     source, json.dumps(dict(row), ensure_ascii=False, default=str), now))
                n += 1
            conn.commit()
        finally:
            conn.close()
    recompute_outcomes(db_path=db_path)
    return n


def recompute_outcomes(*, db_path: Path = DEFAULT_DB) -> int:
    """D3/D5/D10 use the full holding path from trading day 1 through N.

    If stop and target are both crossed on the same daily bar, the result is
    marked ambiguous. A later minute-path resolver can replace that ambiguity.
    """
    updated = 0; now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _LOCK:
        conn = _connect(db_path)
        try:
            signals = conn.execute("SELECT * FROM signal_events WHERE price IS NOT NULL AND price>0").fetchall()
            for s in signals:
                levels = _extract_trade_levels(s["snapshot_json"], s["evidence_json"]); signal_day = s["trade_date"]
                obs = conn.execute("""SELECT observed_at,observed_date,price FROM signal_observations
                    WHERE signal_id=? AND observed_date=? AND observed_at>=? ORDER BY observed_at""",
                    (int(s["id"]), signal_day, s["signal_time"])).fetchall()
                if obs:
                    prices=[float(r["price"]) for r in obs]
                    _upsert_path_outcome(conn, s, "D0", obs[-1]["observed_date"], prices[-1], max(prices), min(prices),
                                         levels, "signal_observations", {"points":len(prices)}, now)
                    updated += 1
                future = conn.execute("""SELECT trade_date,open,high,low,close,source FROM daily_prices
                    WHERE code=? AND trade_date>? ORDER BY trade_date LIMIT 10""", (s["code"], signal_day)).fetchall()
                for horizon, n_days in HORIZONS.items():
                    if n_days == 0 or len(future) < n_days: continue
                    path = future[:n_days]
                    close = float(path[-1]["close"])
                    highs=[_f(r["high"]) or float(r["close"]) for r in path]
                    lows=[_f(r["low"]) or float(r["close"]) for r in path]
                    trigger = _path_trigger(path, levels)
                    if trigger.get("ambiguous_same_day"):
                        trigger = _resolve_ambiguous_with_minutes(conn, s, path, levels, trigger)
                    raw={"trading_days":[dict(r) for r in path], **trigger}
                    _upsert_path_outcome(conn, s, horizon, path[-1]["trade_date"], close, max(highs), min(lows), levels,
                                         path[-1]["source"] or "daily_prices", raw, now, trigger=trigger)
                    updated += 1
            conn.commit()
        finally: conn.close()
    return updated


def _path_trigger(path, levels):
    first_stop=first_t1=first_t2=None; first_trigger=None; ambiguous=False
    stop, t1, t2 = levels["stop"], levels["target1"], levels["target2"]
    for r in path:
        day=r["trade_date"]; hi=_f(r["high"]) or _f(r["close"]); lo=_f(r["low"]) or _f(r["close"])
        hit_stop = stop is not None and lo is not None and lo <= stop
        hit_t1 = t1 is not None and hi is not None and hi >= t1
        hit_t2 = t2 is not None and hi is not None and hi >= t2
        if hit_stop and first_stop is None: first_stop=day
        if hit_t1 and first_t1 is None: first_t1=day
        if hit_t2 and first_t2 is None: first_t2=day
        if first_trigger is None:
            if hit_stop and (hit_t1 or hit_t2): first_trigger="AMBIGUOUS"; ambiguous=True
            elif hit_stop: first_trigger="STOP"
            elif hit_t2: first_trigger="TARGET2"
            elif hit_t1: first_trigger="TARGET1"
    return {"first_stop_date":first_stop,"first_target1_date":first_t1,"first_target2_date":first_t2,
            "first_trigger":first_trigger,"ambiguous_same_day":ambiguous}



def _resolve_ambiguous_with_minutes(conn, signal, path, levels, trigger):
    ambiguous_days=[]
    stop, t1, t2 = levels["stop"], levels["target1"], levels["target2"]
    for r in path:
        hi=_f(r["high"]) or _f(r["close"]); lo=_f(r["low"]) or _f(r["close"])
        if stop is not None and lo is not None and lo <= stop and ((t1 is not None and hi is not None and hi >= t1) or (t2 is not None and hi is not None and hi >= t2)):
            ambiguous_days.append(r["trade_date"])
    for day in ambiguous_days:
        mins=conn.execute("""SELECT observed_at,high,low,close FROM minute_prices
            WHERE code=? AND trade_date=? ORDER BY observed_at""", (signal["code"], day)).fetchall()
        for m in mins:
            hi=_f(m["high"]) or _f(m["close"]); lo=_f(m["low"]) or _f(m["close"])
            hit_stop = stop is not None and lo is not None and lo <= stop
            hit_t2 = t2 is not None and hi is not None and hi >= t2
            hit_t1 = t1 is not None and hi is not None and hi >= t1
            # A single minute bar can still contain both extremes; leave ambiguous in that case.
            if hit_stop and (hit_t1 or hit_t2):
                return {**trigger, "first_trigger":"AMBIGUOUS", "ambiguous_same_day":True,
                        "minute_resolution":"AMBIGUOUS_WITHIN_MINUTE", "minute_resolution_at":m["observed_at"]}
            if hit_stop:
                return {**trigger, "first_trigger":"STOP", "ambiguous_same_day":False,
                        "minute_resolution":"RESOLVED", "minute_resolution_at":m["observed_at"]}
            if hit_t2:
                return {**trigger, "first_trigger":"TARGET2", "ambiguous_same_day":False,
                        "minute_resolution":"RESOLVED", "minute_resolution_at":m["observed_at"]}
            if hit_t1:
                return {**trigger, "first_trigger":"TARGET1", "ambiguous_same_day":False,
                        "minute_resolution":"RESOLVED", "minute_resolution_at":m["observed_at"]}
    return {**trigger, "minute_resolution":"NO_MINUTE_DATA"}


def _upsert_path_outcome(conn, signal, horizon, observed_date, close, high, low, levels, source, raw, now, trigger=None):
    entry=float(signal["price"]); ret=(close/entry-1)*100; max_ret=(high/entry-1)*100; min_ret=(low/entry-1)*100
    max_dd=min(0.0,min_ret); cost=_round_trip_cost_pct(); net=ret-cost
    stop=levels["stop"] is not None and low<=levels["stop"]; t1=levels["target1"] is not None and high>=levels["target1"]
    t2=levels["target2"] is not None and high>=levels["target2"]
    risk_pct=(entry-levels["stop"])/entry*100 if levels["stop"] is not None and levels["stop"]<entry else None
    rr=(max_ret/risk_pct) if risk_pct and risk_pct>0 else None
    trigger=trigger or {"first_stop_date":observed_date if stop else None,"first_target1_date":observed_date if t1 else None,
                        "first_target2_date":observed_date if t2 else None,"first_trigger":None,"ambiguous_same_day":False}
    first=trigger.get("first_trigger")
    exit_reason=first or "HOLD_TO_HORIZON"; exit_date=observed_date; strategy_ret=net
    if first == "STOP" and levels["stop"] is not None:
        strategy_ret=(levels["stop"]/entry-1)*100-cost; exit_date=trigger.get("first_stop_date") or observed_date
    elif first == "TARGET2" and levels["target2"] is not None:
        strategy_ret=(levels["target2"]/entry-1)*100-cost; exit_date=trigger.get("first_target2_date") or observed_date
    elif first == "TARGET1" and levels["target1"] is not None:
        strategy_ret=(levels["target1"]/entry-1)*100-cost; exit_date=trigger.get("first_target1_date") or observed_date
    elif first == "AMBIGUOUS":
        strategy_ret=None; exit_reason="AMBIGUOUS"; exit_date=trigger.get("first_stop_date") or observed_date
    conn.execute("""INSERT INTO signal_outcomes
        (signal_id,horizon,observed_date,close_return_pct,max_return_pct,min_return_pct,max_drawdown_pct,net_return_pct,
         strategy_return_pct,exit_reason,exit_date,reward_risk_ratio,stop_hit,target1_hit,target2_hit,first_stop_date,first_target1_date,first_target2_date,
         first_trigger,ambiguous_same_day,source,raw_json,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signal_id,horizon) DO UPDATE SET observed_date=excluded.observed_date,
        close_return_pct=excluded.close_return_pct,max_return_pct=excluded.max_return_pct,
        min_return_pct=excluded.min_return_pct,max_drawdown_pct=excluded.max_drawdown_pct,
        net_return_pct=excluded.net_return_pct,strategy_return_pct=excluded.strategy_return_pct,
        exit_reason=excluded.exit_reason,exit_date=excluded.exit_date,reward_risk_ratio=excluded.reward_risk_ratio,
        stop_hit=excluded.stop_hit,target1_hit=excluded.target1_hit,target2_hit=excluded.target2_hit,
        first_stop_date=excluded.first_stop_date,first_target1_date=excluded.first_target1_date,
        first_target2_date=excluded.first_target2_date,first_trigger=excluded.first_trigger,
        ambiguous_same_day=excluded.ambiguous_same_day,source=excluded.source,raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
        (int(signal["id"]),horizon,observed_date,round(ret,4),round(max_ret,4),round(min_ret,4),round(max_dd,4),round(net,4),
         round(strategy_ret,4) if strategy_ret is not None else None,exit_reason,exit_date,
         round(rr,4) if rr is not None else None,int(bool(stop)),int(bool(t1)),int(bool(t2)),trigger.get("first_stop_date"),
         trigger.get("first_target1_date"),trigger.get("first_target2_date"),trigger.get("first_trigger"),
         int(bool(trigger.get("ambiguous_same_day"))),source,json.dumps(raw,ensure_ascii=False,default=str),now))


def comparison_stats(*, horizon: str="D1", signal_date: Optional[str]=None, db_path: Path=DEFAULT_DB) -> list[dict[str,Any]]:
    clauses,args=["o.horizon=?"],[horizon]
    if signal_date: clauses.append("s.trade_date=?"); args.append(signal_date)
    sql=f"""SELECT s.engine,s.strategy_version,o.close_return_pct,o.net_return_pct,o.strategy_return_pct,o.exit_reason,o.max_return_pct,o.max_drawdown_pct,
             o.stop_hit,o.target1_hit,o.target2_hit,o.ambiguous_same_day
             FROM signal_events s JOIN signal_outcomes o ON o.signal_id=s.id WHERE {' AND '.join(clauses)}"""
    with _LOCK:
        conn=_connect(db_path)
        try: rows=[dict(r) for r in conn.execute(sql,tuple(args)).fetchall()]
        finally: conn.close()
    grouped={}
    for r in rows: grouped.setdefault((r["engine"],r.get("strategy_version") or ""),[]).append(r)
    out=[]
    for (engine,version), rs in sorted(grouped.items()):
        returns=[float(r["close_return_pct"]) for r in rs if r["close_return_pct"] is not None]
        nets=[float(r["net_return_pct"]) for r in rs if r["net_return_pct"] is not None]
        strategy=[float(r["strategy_return_pct"]) for r in rs if r.get("strategy_return_pct") is not None]
        wins=[x for x in strategy if x>0]; losses=[x for x in strategy if x<0]
        gross_profit=sum(wins); total_positive=max(gross_profit,0)
        top_profit=max(wins) if wins else 0.0
        concentration=(top_profit/total_positive*100) if total_positive>0 else 0.0
        avg_win=statistics.mean(wins) if wins else 0.0; avg_loss=abs(statistics.mean(losses)) if losses else 0.0
        profit_factor=(sum(wins)/abs(sum(losses))) if losses and sum(losses)!=0 else (float('inf') if wins else 0.0)
        out.append({
            "engine":engine,"strategy_version":version,"samples":len(rs),
            "win_rate_pct":sum(1 for x in strategy if x>0)/len(strategy)*100 if strategy else 0,
            "mature_strategy_samples":len(strategy),
            "avg_return_pct":statistics.mean(returns) if returns else 0,
            "median_return_pct":statistics.median(returns) if returns else 0,
            "avg_net_return_pct":statistics.mean(nets) if nets else 0,
            "expectancy_pct":statistics.mean(strategy) if strategy else 0,
            "avg_strategy_return_pct":statistics.mean(strategy) if strategy else 0,
            "payoff_ratio":(avg_win/avg_loss) if avg_loss>0 else (float('inf') if avg_win>0 else 0),
            "profit_factor":profit_factor,
            "avg_max_return_pct":statistics.mean([float(r["max_return_pct"]) for r in rs if r["max_return_pct"] is not None]) if rs else 0,
            "avg_max_drawdown_pct":statistics.mean([float(r["max_drawdown_pct"]) for r in rs if r["max_drawdown_pct"] is not None]) if rs else 0,
            "stop_rate_pct":sum(int(r["stop_hit"] or 0) for r in rs)/len(rs)*100,
            "target1_rate_pct":sum(int(r["target1_hit"] or 0) for r in rs)/len(rs)*100,
            "target2_rate_pct":sum(int(r["target2_hit"] or 0) for r in rs)/len(rs)*100,
            "ambiguous_rate_pct":sum(int(r["ambiguous_same_day"] or 0) for r in rs)/len(rs)*100,
            "top_profit_contribution_pct":concentration,
        })
    return out


def fetch_historical_minutes_for_signal(signal_id: int, *, end_time: str | None = None,
                                        db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Fetch historical minute bars strictly for post-signal outcome verification.

    This function is intentionally located in performance tracking and is never imported by the V7.1
    scoring engine. Bars earlier than the recorded signal timestamp are discarded before persistence,
    preventing the result-validation path from leaking future/post-event information into signal scoring.
    """
    from .data_layer import DATA_LAYER
    from .signal_lab import get_signal
    signal = get_signal(int(signal_id), db_path=db_path)
    if not signal:
        return {"status": "SIGNAL_NOT_FOUND", "rows": 0}
    start = str(signal["signal_time"])
    end = end_time or datetime.now().astimezone().isoformat(timespec="seconds")
    code = str(signal["code"]).zfill(6)
    suffix = ".SH" if code.startswith(("6", "68")) else (".BJ" if code.startswith(("4", "8", "92")) else ".SZ")
    ts_code = code + suffix
    key = f"outcome_minute:{int(signal_id)}:{start[:16]}:{end[:16]}"
    try:
        data = DATA_LAYER.relay_pro_bar(cache_key=key, ttl_seconds=30 * 86400,
                                        ts_code=ts_code, freq="1min", start_date=start, end_date=end)
        rows = data.to_dict(orient="records") if hasattr(data, "to_dict") else (data if isinstance(data, list) else [])
    except Exception as exc:
        return {"status": "DATA_UNAVAILABLE", "rows": 0, "error": type(exc).__name__}
    safe_rows = []
    try:
        start_dt = datetime.fromisoformat(start)
    except Exception:
        start_dt = None
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        row.setdefault("ts_code", ts_code)
        raw_time = str(row.get("trade_time") or row.get("datetime") or row.get("time") or row.get("trade_date") or "")
        parsed = None
        for candidate in (raw_time, raw_time.replace(" ", "T", 1)):
            try:
                parsed = datetime.fromisoformat(candidate)
                break
            except Exception:
                pass
        if start_dt is not None and parsed is not None:
            if start_dt.tzinfo and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=start_dt.tzinfo)
            if parsed < start_dt:
                continue
        safe_rows.append(row)
    stored = ingest_minute_prices(safe_rows, source="tushare_relay.pro_bar_outcome", db_path=db_path)
    return {"status": "OK" if safe_rows else "EMPTY", "rows": len(safe_rows), "stored": stored,
            "signal_time": start, "end_time": end}
