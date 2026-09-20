"""V8 independent full-market discovery and Shadow confirmation runtime.

This module never imports V6/V7 candidate or snapshot files.  V6/V7 may be
used later by offline reports as baselines, but they are not discovery input.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from .config import CONFIG, ROOT
from .independent_message import build_candidate_message, build_scan_status
from .push import send_once
from .storage import (connect, initialize, save_decision, save_module_health, save_observation,
                      save_signal_delivery)

CN_SOURCE = "AKSHARE_SINA_FULL_MARKET"
STATE_FILE = ROOT / "data_v8" / "independent_discovery_state.json"
PROFILE_FILE = ROOT / "data_v8" / "v8_stock_profiles.json"


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return round(max(low, min(high, value)), 2)


def _scale(value: float | None, bad: float, good: float, neutral: float = 50) -> float:
    if value is None or good == bad:
        return neutral
    return _clamp((value - bad) / (good - bad) * 100)


def _weighted(parts: list[tuple[float, float]]) -> float:
    total = sum(w for _, w in parts)
    return _clamp(sum(v * w for v, w in parts) / total) if total else 50


def _code(raw: Any) -> str:
    text = str(raw or "").lower().strip()
    for prefix in ("sh", "sz", "bj"):
        if text.startswith(prefix):
            text = text[2:]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _ts_code(code: str) -> str:
    if code.startswith(("6", "68")):
        return code + ".SH"
    if code.startswith(("8", "4", "92")):
        return code + ".BJ"
    return code + ".SZ"


def _event_key(code: str, trade_date: str, channel: str) -> str:
    # Channel may evolve from early-watch to acceleration; that must stay one episode.
    return "v8i_" + hashlib.sha256(f"{trade_date}|{code}|INDEPENDENT".encode()).hexdigest()[:20]


def _add_trading_minutes(stamp: datetime, minutes: int) -> datetime | None:
    """Add A-share trading minutes, skipping lunch and never crossing 15:00."""
    cursor=stamp;remaining=max(0,int(minutes))
    while True:
        clock=cursor.hour*60+cursor.minute
        if clock<570:
            cursor=cursor.replace(hour=9,minute=30,second=0,microsecond=0);clock=570
        elif 690<=clock<780:
            cursor=cursor.replace(hour=13,minute=0,second=0,microsecond=0);clock=780
        elif clock>=900:
            return cursor if remaining==0 and clock==900 else None
        session_end=690 if clock<690 else 900
        available=session_end-clock
        if remaining<=available:
            return cursor+timedelta(minutes=remaining)
        remaining-=available
        if session_end==690:
            cursor=cursor.replace(hour=13,minute=0,second=0,microsecond=0)
        else:
            return None


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        try:
            return list(frame.to_dict("records"))
        except Exception:
            return []
    return list(frame) if isinstance(frame, list) else []


def _fetch_full_market() -> tuple[list[dict[str, Any]], str]:
    import akshare as ak
    frame = ak.stock_zh_a_spot()
    rows = []
    source_time = ""
    for raw in _records(frame):
        code = _code(raw.get("代码"))
        if not code:
            continue
        stamp = str(raw.get("时间戳") or "")
        source_time = max(source_time, stamp)
        rows.append({
            "code": code, "name": str(raw.get("名称") or ""),
            "price": _f(raw.get("最新价")), "pct_chg": _f(raw.get("涨跌幅")),
            "pre_close": _f(raw.get("昨收")), "open": _f(raw.get("今开")),
            "high": _f(raw.get("最高")), "low": _f(raw.get("最低")),
            "bid": _f(raw.get("买入")), "ask": _f(raw.get("卖出")),
            "vol": _f(raw.get("成交量"), 0), "amount": _f(raw.get("成交额"), 0),
            "source_time": stamp,
        })
    if not rows:
        raise RuntimeError("EMPTY_FULL_MARKET_SNAPSHOT")
    return rows, source_time


def _load_profiles(now: datetime) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        if payload.get("date") == now.date().isoformat() and payload.get("profiles"):
            return dict(payload["profiles"])
    except Exception:
        pass
    profiles: dict[str, dict[str, Any]] = {}
    try:
        from .paid_data import PAID_DATA
        frame = PAID_DATA.call("stock_basic", cache_key=f"v8:stock_basic:{now:%Y%m%d}", ttl_seconds=86400,
                               priority="NORMAL",
                               exchange="", list_status="L",
                               fields="ts_code,name,industry,market,list_date")
        for raw in _records(frame):
            code = _code(raw.get("ts_code"))
            if code:
                profiles[code] = {"industry": raw.get("industry") or "行业待确认",
                                  "market": raw.get("market") or "", "list_date": raw.get("list_date")}
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_FILE.write_text(json.dumps({"date": now.date().isoformat(), "profiles": profiles},
                                           ensure_ascii=False), encoding="utf-8")
    except Exception:
        try:
            payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            profiles = dict(payload.get("profiles") or {})
        except Exception:
            profiles = {}
    return profiles


def _market_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r["pct_chg"] is not None and r["price"] and r["price"] > 0]
    up = sum((r["pct_chg"] or 0) > 0 for r in valid)
    advance = up / max(1, len(valid))
    limit_up = sum((r["pct_chg"] or 0) >= 9.5 for r in valid)
    limit_down = sum((r["pct_chg"] or 0) <= -9.5 for r in valid)
    median = sorted((r["pct_chg"] or 0) for r in valid)[len(valid)//2] if valid else 0
    score = _weighted([(_scale(advance, .35, .65), 3), (_scale(limit_up, 10, 80), 1),
                       (100 - _scale(limit_down, 3, 35), 1), (_scale(median, -1.2, 1.2), 2)])
    regime = "进攻" if score >= 68 else ("防守" if score < 42 else "正常")
    return {"score": score, "regime": regime, "advance_ratio": advance,
            "limit_up": limit_up, "limit_down": limit_down, "median_pct": median}


def _sector_context(rows: list[dict[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        industry = str((profiles.get(row["code"]) or {}).get("industry") or "行业待确认")
        groups.setdefault(industry, []).append(row)
    result = {}
    for industry, items in groups.items():
        valid = [x for x in items if x["pct_chg"] is not None]
        if not valid:
            continue
        values = sorted(float(x["pct_chg"]) for x in valid)
        up_ratio = sum(x > 0 for x in values) / len(values)
        median = values[len(values)//2]
        top3 = sum(values[-3:]) / min(3, len(values))
        amount = sum(float(x.get("amount") or 0) for x in valid)
        vwap_flags=[]
        for item in valid:
            volume=float(item.get('vol') or 0);price=float(item.get('price') or 0);item_amount=float(item.get('amount') or 0)
            if volume>0 and price>0:vwap_flags.append(price>=item_amount/volume)
        above_vwap_ratio=sum(vwap_flags)/len(vwap_flags) if vwap_flags else None
        score = _weighted([(_scale(up_ratio, .3, .72), 3), (_scale(median, -1, 2.5), 2),
                           (_scale(top3, 1, 7), 1.5), (_scale(math.log10(max(amount, 1)), 7, 10.5), 1)])
        result[industry] = {"score": score, "up_ratio": up_ratio, "median_pct": median,
                            "top3_pct": top3, "count": len(valid), "amount": amount,
                            "above_vwap_ratio":above_vwap_ratio}
    return result


def _eligible(row: Mapping[str, Any]) -> bool:
    name = str(row.get("name") or "").upper()
    price, pct, amount = _f(row.get("price")), _f(row.get("pct_chg")), _f(row.get("amount"), 0) or 0
    if not price or price < 2 or pct is None or amount < CONFIG.independent_min_amount:
        return False
    if "ST" in name or "退" in name or "N" == name[:1] or "C" == name[:1]:
        return False
    if pct <= -4.5 or pct >= 9.7:
        return False
    bid, ask = _f(row.get("bid")), _f(row.get("ask"))
    if bid and ask and ask > 0 and (ask - bid) / ask > .012:
        return False
    return True


def _discovery_score(row: Mapping[str, Any], previous: Mapping[str, Any] | None, sector: Mapping[str, Any]) -> tuple[float, str, list[str]]:
    price = _f(row.get("price"), 0) or 0
    pct = _f(row.get("pct_chg"), 0) or 0
    amount = _f(row.get("amount"), 0) or 0
    high, low = _f(row.get("high")), _f(row.get("low"))
    position = .5 if high is None or low is None or high <= low else (price - low) / (high - low)
    vwap = amount / max(1, _f(row.get("vol"), 0) or 0)
    above_vwap = price >= vwap if vwap > 0 else None
    prev_amount = _f((previous or {}).get("amount"), 0) or 0
    prev_price = _f((previous or {}).get("price"), price) or price
    amount_delta = max(0, amount - prev_amount) if previous else 0
    price_delta = (price / prev_price - 1) * 100 if previous and prev_price else 0
    acceleration_score = 50 if not previous else _weighted([(_scale(amount_delta, 2_000_000, 50_000_000), 2),
                                                              (_scale(price_delta, -.25, .8), 1)])
    overheat_penalty = max(0, pct - 6) * 5 + max(0, position - .98) * 20
    score = _weighted([(_scale(pct, -.5, 5), 2), (_scale(position, .35, .9), 1.5),
                       (_scale(math.log10(max(amount, 1)), 7, 10.5), 1.5),
                       (75 if above_vwap else 35, 1), (acceleration_score, 2),
                       (_f(sector.get("score"), 50) or 50, 1.5)]) - overheat_penalty
    score = _clamp(score)
    channel = "启动加速" if previous and amount_delta >= 5_000_000 and price_delta > .1 else "潜伏准备"
    reasons = [f"日内位置{position:.0%}", f"行业扩散{_f(sector.get('score'),50):.0f}"]
    if above_vwap is True:
        reasons.append("价格位于日内均价上方")
    if previous:
        reasons.append(f"本轮新增成交{amount_delta/10000:.0f}万")
    return score, channel, reasons


def _daily_features(code: str, now: datetime, *, name: str = "", industry: str = "行业待确认") -> dict[str, Any]:
    from .paid_data import PAID_DATA
    try:
        start = (now - timedelta(days=130)).strftime("%Y%m%d")
        frame = PAID_DATA.call("daily", cache_key=f"v8:daily:{code}:{now:%Y%m%d}", ttl_seconds=21600,
                               priority="CHECKPOINT",
                               ts_code=_ts_code(code), start_date=start, end_date=now.strftime("%Y%m%d"))
        rows = sorted(_records(frame), key=lambda x: str(x.get("trade_date") or ""))
        from .market_archive import archive_daily
        archive_daily(_ts_code(code), rows, now)
        closes = [_f(x.get("close")) for x in rows]
        closes = [x for x in closes if x is not None]
        if len(closes) < 20:
            return {"status": "UNKNOWN"}
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / min(60, len(closes))
        prev20 = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma20
        slope = (ma20 / prev20 - 1) * 100 if prev20 else 0
        ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        trend = _weighted([(_scale(slope, -2, 4), 2), (_scale((closes[-1]/ma20-1)*100, -5, 8), 2),
                           (_scale((ma20/ma60-1)*100, -4, 8), 2), (_scale(ret5, -5, 8), 1)])
        true_ranges=[]
        for index,item in enumerate(rows[-15:]):
            high=_f(item.get("high"));low=_f(item.get("low"))
            previous=_f(item.get("pre_close"))
            if previous is None and index:
                previous=_f(rows[-15:][index-1].get("close"))
            if high is not None and low is not None:
                true_ranges.append(max(high-low,abs(high-previous) if previous else 0,
                                       abs(low-previous) if previous else 0))
        atr14=sum(true_ranges[-14:])/len(true_ranges[-14:]) if true_ranges else None
        recent_lows=[_f(x.get("low")) for x in rows[-10:]]
        recent_lows=[x for x in recent_lows if x is not None]
        recent_highs=[_f(x.get("high")) for x in rows[-20:]]
        recent_highs=[x for x in recent_highs if x is not None]
        last_row = rows[-1]
        # Tushare daily amount uses thousand yuan; normalize to yuan.
        last_amount_yuan = (_f(last_row.get("amount"), 0) or 0) * 1000
        try:
            from .strategy_shadow import evaluate_all
            shadow_factors = evaluate_all(code, name, industry, rows)
        except Exception as factor_error:
            shadow_factors = {"status": "UNKNOWN", "error": type(factor_error).__name__}
        return {"status": "OK", "trend_score": trend, "ma20": ma20, "ma60": ma60,
                "ma20_slope": slope, "ret5": ret5, "last_close": closes[-1],
                "atr14":atr14,"recent_support":min(recent_lows) if recent_lows else None,
                "recent_resistance":max(recent_highs) if recent_highs else None,
                "last_amount_yuan": last_amount_yuan, "last_trade_date": last_row.get("trade_date"),
                "strategy_shadow_factors": shadow_factors}
    except Exception as exc:
        return {"status": "UNKNOWN", "error": type(exc).__name__}


def _minute_features(code: str, now: datetime) -> dict[str, Any]:
    from .paid_data import PAID_DATA
    try:
        frame = PAID_DATA.call("rt_min", cache_key=f"v8:min:{code}:{now:%Y%m%d%H%M}", ttl_seconds=45,
                               priority="CHECKPOINT",
                               ts_code=_ts_code(code), freq="1MIN")
        rows = sorted(_records(frame), key=lambda x: str(x.get("time") or ""))
        from .market_archive import archive_minute
        archive_minute(_ts_code(code), rows, now)
        rows = [x for x in rows if _f(x.get("close")) is not None]
        if len(rows) < 4:
            return {"status": "UNKNOWN", "data_limitations": ["分钟样本不足"]}
        recent = rows[-3:]
        earlier = rows[-13:-3] or rows[:-3]
        total_amount = sum(_f(x.get("amount"), 0) or 0 for x in rows)
        total_vol = sum(_f(x.get("vol"), 0) or 0 for x in rows)
        vwap = total_amount / max(total_vol, 1)
        close = _f(rows[-1].get("close"), 0) or 0
        recent_amount = sum(_f(x.get("amount"), 0) or 0 for x in recent) / len(recent)
        base_amount = sum(_f(x.get("amount"), 0) or 0 for x in earlier) / max(1, len(earlier))
        amount_accel = recent_amount / max(base_amount, 1)
        progress = (close / (_f(recent[0].get("open"), close) or close) - 1) * 100 if close else 0
        high = max(_f(x.get("high"), close) or close for x in recent)
        blowoff = bool(high and close < high * .985 and progress < 0)
        fund = _weighted([(80 if close >= vwap else 30, 2), (_scale(amount_accel, .7, 2), 2),
                          (_scale(progress, -.8, 1.2), 1)]) - (25 if blowoff else 0)
        recent_low=min(_f(x.get("low"),close) or close for x in recent)
        recent_high=max(_f(x.get("high"),close) or close for x in recent)
        return {"status": "OK", "fund_score": _clamp(fund), "price": close, "vwap": vwap,
                "above_vwap": close >= vwap, "amount_acceleration": amount_accel,
                "amount_delta": recent_amount, "price_progress_pct": progress,
                "recent_low":recent_low,"recent_high":recent_high,
                "blowoff_reversal": blowoff, "data_time": rows[-1].get("time"),
                "data_limitations": ["普通分钟行情不含逐笔主动买卖和Level-2委托"]}
    except Exception as exc:
        return {"status": "UNKNOWN", "error": type(exc).__name__,
                "data_limitations": ["实时分钟接口暂不可用"]}


def _chip_score(distance_pct: float | None, winner_rate_pct: float | None) -> float:
    return _weighted([(_scale(distance_pct, -8, 10), 2), (_scale(winner_rate_pct, 15, 85), 1)])


def _chip_features(code: str, now: datetime, price: float | None) -> dict[str, Any]:
    """Daily chip evidence. Missing data is neutral and never vetoes a candidate."""
    from .paid_data import PAID_DATA
    try:
        frame = PAID_DATA.call("cyq_perf", cache_key=f"v8:chip:{code}:{now:%Y%m%d}", ttl_seconds=86400,
                               priority="NORMAL",
                               ts_code=_ts_code(code), start_date=(now-timedelta(days=20)).strftime("%Y%m%d"),
                               end_date=now.strftime("%Y%m%d"))
        rows = sorted(_records(frame), key=lambda x: str(x.get("trade_date") or ""))
        if not rows:
            return {"status": "UNKNOWN", "score": 50, "risk": "UNKNOWN"}
        row = rows[-1]
        avg = _f(row.get("weight_avg")) or _f(row.get("cost_50pct"))
        winner = _f(row.get("winner_rate"))
        distance = (price / avg - 1) * 100 if price and avg else None
        # Tushare cyq_perf winner_rate is a percentage (0..100), not a ratio (0..1).
        score = _chip_score(distance, winner)
        # Very large deviation means crowded/overheated; keep it a soft risk only.
        crowded = distance is not None and distance > 18
        if crowded:
            score = _clamp(score - min(20, (distance - 18) * 1.5))
        risk = "HIGH" if crowded else ("LOW" if distance is not None else "UNKNOWN")
        return {"status": "OK", "score": score, "risk": risk, "trade_date": row.get("trade_date"),
                "weight_avg": avg, "winner_rate": winner, "price_to_cost_pct": distance}
    except Exception as exc:
        return {"status": "UNKNOWN", "score": 50, "risk": "UNKNOWN", "error": type(exc).__name__}


_MACRO_CACHE: dict[str, Any] = {}


def _macro_features(now: datetime) -> dict[str, Any]:
    """Archive verified macro evidence without inventing a directional A-share rule."""
    key = now.date().isoformat()
    if _MACRO_CACHE.get("date") == key:
        return dict(_MACRO_CACHE["value"])
    from .paid_data import PAID_DATA
    try:
        frame = PAID_DATA.call("us_tbr", cache_key=f"v8:macro:{now:%Y%m%d}", ttl_seconds=86400,
                               priority="BACKGROUND",
                               start_date=(now-timedelta(days=10)).strftime("%Y%m%d"),
                               end_date=now.strftime("%Y%m%d"))
        rows = _records(frame)
        value = {"status": "OK", "risk": "UNKNOWN", "score": 50,
                 "latest": rows[-1] if rows else {},
                 "note": "宏观数据已归档；方向规则未经Forward验证，暂不改变个股分数"} if rows else {
                 "status": "UNKNOWN", "risk": "UNKNOWN", "score": 50}
    except Exception as exc:
        value = {"status": "UNKNOWN", "risk": "UNKNOWN", "score": 50, "error": type(exc).__name__}
    _MACRO_CACHE.update({"date": key, "value": value})
    return dict(value)


def _quote_time(value:Any,now:datetime)->datetime|None:
    raw=str(value or '').strip()
    if not raw:return None
    if len(raw)<=8 and ':' in raw:raw=f'{now.date().isoformat()} {raw}'
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y%m%d %H:%M:%S','%Y%m%d%H%M%S'):
        try:return datetime.strptime(raw,fmt).replace(tzinfo=now.tzinfo)
        except ValueError:pass
    try:
        stamp=datetime.fromisoformat(raw.replace('Z','+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=now.tzinfo)
    except ValueError:return None


def _cross_validate(code: str, public_price: float, now: datetime,
                    public_time:Any=None) -> dict[str, Any]:
    from .paid_data import PAID_DATA
    try:
        frame = PAID_DATA.call("rt_k", cache_key=f"v8:rtk:{code}:{now:%Y%m%d%H%M}", ttl_seconds=45,
                               priority="CHECKPOINT",
                               ts_code=_ts_code(code))
        rows = _records(frame)
        def row_stamp(row:Mapping[str,Any])->datetime|None:
            return _quote_time(row.get('trade_time') or row.get('time') or row.get('datetime'),now)
        rows.sort(key=lambda row:row_stamp(row) or datetime.min.replace(tzinfo=now.tzinfo),reverse=True)
        latest=rows[0] if rows else {}
        paid = _f(latest.get("close") or latest.get('price')) if rows else None
        deviation = abs(paid/public_price - 1) * 100 if paid and public_price else None
        paid_time=row_stamp(latest);primary_time=_quote_time(public_time,now)
        if deviation is None:
            status='UNKNOWN'
        elif paid_time is None or primary_time is None:
            # A daily-style snapshot without a comparable timestamp is useful
            # evidence, but cannot safely veto a minute-level candidate.
            status='UNVERIFIED_TIME'
        elif abs((paid_time-primary_time).total_seconds())>180:
            status='STALE_TIME'
        else:
            status='OK' if deviation<=.8 else 'WARNING'
        return {"status":status,"paid_price":paid,"deviation_pct":deviation,
                "primary_time":str(public_time or ''),"paid_time":paid_time.isoformat() if paid_time else None,
                "comparable":status in {'OK','WARNING'}}
    except Exception as exc:
        return {"status": "UNKNOWN", "error": type(exc).__name__}


def _event_context(code: str, now: datetime) -> dict[str, Any]:
    """Use V8's own event radar output; events may adjust risk/watch, never buy alone."""
    try:
        db = connect()
        rows = db.execute("""SELECT direction,risk_level,alert_type,title,published_at FROM v8_event_radar
          WHERE code=? AND COALESCE(published_at,created_at)>=? ORDER BY COALESCE(published_at,created_at) DESC LIMIT 10""",
          (code, (now - timedelta(days=7)).isoformat(timespec="seconds"))).fetchall()
        db.close()
    except Exception:
        rows = []
    positive = [dict(x) for x in rows if str(x["direction"]).upper() == "POSITIVE"]
    negative = [dict(x) for x in rows if str(x["direction"]).upper() == "NEGATIVE"]
    hard = [x for x in negative if str(x.get("risk_level") or "").upper() in {"HIGH", "HARD_BLOCK"}]
    return {"positive_count": len(positive), "negative_count": len(negative), "hard_risk": bool(hard),
            "positive_titles": [x.get("title") for x in positive[:2]],
            "negative_titles": [x.get("title") for x in negative[:2]]}


def _live_pct_chg(live_price: float | None, row: Mapping[str, Any],
                  daily: Mapping[str, Any]) -> float:
    """Return the evaluation-time change, never a stale discovery snapshot when a baseline exists."""
    baseline = _f(row.get("pre_close")) or _f(daily.get("last_close"))
    if live_price and baseline and baseline > 0:
        return round((float(live_price) / float(baseline) - 1) * 100, 4)
    return float(_f(row.get("pct_chg"), 0) or 0)


def _early_shadow_signal(*, pct: float, discovery_score: float,
                         market: Mapping[str, Any], sector: Mapping[str, Any],
                         daily: Mapping[str, Any], minute: Mapping[str, Any],
                         event_ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Classify low-extension research candidates without changing the formal entry threshold."""
    if pct < -1.0 or pct > CONFIG.early_watch_max_pct_chg or event_ctx.get("hard_risk"):
        return {"family": "", "score": 0.0, "reason": "不在低位提前观察区"}
    trend = float(_f(daily.get("trend_score"), 50) or 50)
    fund = float(_f(minute.get("fund_score"), 50) or 50)
    sector_score = float(_f(sector.get("score"), 50) or 50)
    sector_up = _f(sector.get("up_ratio"))
    acceleration = float(_f(minute.get("amount_acceleration"), 0) or 0)
    candidates: list[tuple[float, str, str]] = []
    if int(event_ctx.get("positive_count") or 0) > 0 and trend >= 58:
        score = _weighted([(discovery_score, 2), (trend, 2), (sector_score, 1.5), (fund, 1)])
        candidates.append((score, "EVENT_ACCUMULATION", "正向事件与日线蓄势同时出现"))
    if (minute.get("status") == "OK" and minute.get("above_vwap") and acceleration >= 1.1 and
            fund >= 58 and not minute.get("blowoff_reversal")):
        score = _weighted([(discovery_score, 1.5), (fund, 2), (sector_score, 1.5),
                           (_scale(acceleration, .8, 2.0), 1)])
        candidates.append((score, "LOW_EXTENSION_IGNITION", "低涨幅、分时均价承接与量能启动同时出现"))
    if sector_score >= CONFIG.entry_min_sector_score and sector_up is not None and \
            sector_up >= CONFIG.entry_min_sector_up_ratio and trend >= 55:
        score = _weighted([(discovery_score, 1.5), (sector_score, 2), (trend, 1.5),
                           (_scale(sector_up, .4, .75), 1)])
        candidates.append((score, "SECTOR_ROTATION_EARLY", "板块扩散通过且个股仍处低位"))
    if not candidates:
        return {"family": "", "score": 0.0, "reason": "低位但催化、资金或板块证据不足"}
    score, family, reason = max(candidates, key=lambda item: item[0])
    return {"family": family, "score": round(score, 2), "reason": reason,
            "shadow_only": True, "action_permission": "ANNOTATION_ONLY"}


def _dynamic_pool_limits(market: Mapping[str, Any], paid_health: Mapping[str, Any],
                         ranked_count: int) -> dict[str, Any]:
    """Use spare paid capacity aggressively while preserving the critical holding reserve."""
    limit = max(1, int(paid_health.get("limit") or CONFIG.max_paid_calls_per_minute))
    used = max(0, int(paid_health.get("calls_last_minute") or 0))
    utilization = used / limit
    market_score = float(_f(market.get("score"), 50) or 50)
    if market_score >= 68:
        watch_target = CONFIG.independent_watch_top_n
    elif market_score >= 42:
        watch_target = max(CONFIG.independent_watch_min_n,
                           round((CONFIG.independent_watch_min_n + CONFIG.independent_watch_top_n) / 2))
    else:
        watch_target = CONFIG.independent_watch_min_n
    maximum = max(CONFIG.independent_enrich_min_n, CONFIG.max_enriched_candidates)
    baseline = min(maximum, max(CONFIG.independent_enrich_min_n, CONFIG.independent_enrich_top_n))
    if paid_health.get("circuit_open") or utilization >= .85:
        enrich_target = CONFIG.independent_enrich_min_n
    elif utilization >= .70:
        enrich_target = min(maximum, max(CONFIG.independent_enrich_min_n, 10))
    elif utilization >= .50:
        enrich_target = baseline
    elif utilization >= .25:
        enrich_target = min(maximum, max(baseline, 15))
    else:
        enrich_target = maximum
    if str(market.get("regime")) == "防守":
        enrich_target = min(enrich_target, 10)
    target_ratio = CONFIG.paid_target_utilization_pct / 100
    if utilization >= target_ratio:
        enrich_target = CONFIG.independent_enrich_min_n
    return {"watch_limit": min(max(0, ranked_count), watch_target),
            "enrich_limit": min(max(0, ranked_count), enrich_target),
            "paid_calls_last_minute": used, "paid_limit": limit,
            "paid_utilization_pct": round(utilization * 100, 1),
            "critical_reserve": CONFIG.paid_reserve_critical}


def _moneyflow_features(code:str,now:datetime)->dict[str,Any]:
    """Prior-day evidence only.  It is recorded for validation and never becomes a buy veto by itself."""
    from .paid_data import PAID_DATA
    try:
        frame=PAID_DATA.call("moneyflow",cache_key=f"v8:moneyflow:{code}:{now:%Y%m%d}",
          ttl_seconds=21600,priority="NORMAL",ts_code=_ts_code(code),
          start_date=(now-timedelta(days=15)).strftime("%Y%m%d"),end_date=now.strftime("%Y%m%d"))
        rows=sorted(_records(frame),key=lambda x:str(x.get("trade_date") or ""))[-5:]
        values=[_f(x.get("net_mf_amount")) for x in rows]
        values=[x for x in values if x is not None]
        if not values:return {"status":"UNKNOWN","score":50}
        positive=sum(x>0 for x in values);net_sum=sum(values)
        score=_weighted([(_scale(positive,1,len(values)),2),(70 if net_sum>0 else 30,1)])
        return {"status":"OK","score":score,"positive_days":positive,"sample_days":len(values),
                "net_mf_amount_sum":net_sum,"note":"日频资金流仅作研究证据，不代表盘中主动买盘"}
    except Exception as exc:
        return {"status":"UNKNOWN","score":50,"error":type(exc).__name__}


def _entry_setup(*,market:Mapping[str,Any],sector:Mapping[str,Any],pct:float,
                 live_price:float,vwap:float|None,trend:float,fund:float,pass_count:int,
                 observations:list[Mapping[str,Any]],minute:Mapping[str,Any])->dict[str,Any]:
    """Separate opportunity quality from executable entry quality.

    A low-extension candidate may confirm after two fresh checkpoints.  A more
    extended candidate must first show an observable pullback and reclaim; a
    straight-line pulse remains WAIT_PULLBACK even when its opportunity score is high.
    """
    vwap_distance=((live_price/vwap-1)*100) if live_price and vwap else None
    chase_risk=_clamp(max(0.0,pct-2.0)*12+max(0.0,(vwap_distance or 0)-1.0)*15+
                      (25 if minute.get("blowoff_reversal") else 0))
    entry_quality=_clamp(100-chase_risk)
    entry_low=(vwap*(1-CONFIG.entry_zone_floor_pct/100)) if vwap else None
    entry_high=(vwap*(1+CONFIG.entry_max_vwap_distance_pct/100)) if vwap else None
    sector_score=_f(sector.get("score"),50) or 50
    sector_up=_f(sector.get("up_ratio"))
    market_ok=(str(market.get("regime"))!="防守" and
               (_f(market.get("score"),0) or 0)>=CONFIG.entry_min_market_score)
    sector_ok=(sector_score>=CONFIG.entry_min_sector_score and sector_up is not None and
               sector_up>=CONFIG.entry_min_sector_up_ratio)
    prices=[_f(item.get("price")) for item in observations]
    prices=[x for x in prices if x is not None and x>0]
    pullback_reclaim=False
    pullback_drop_pct=0.0
    if len(prices)>=3:
        pre_peak=max(prices[:-1]);trough=min(prices[1:-1] or prices[:-1])
        pullback_drop_pct=(pre_peak/trough-1)*100 if trough else 0.0
        pullback_reclaim=(pullback_drop_pct>=CONFIG.entry_pullback_min_drop_pct and
                          prices[-1]>trough and bool(minute.get("above_vwap")) and
                          not bool(minute.get("blowoff_reversal")))
    controlled=(pct<=CONFIG.entry_max_pct_chg and vwap_distance is not None and
                0<=vwap_distance<=CONFIG.entry_max_vwap_distance_pct)
    persistence_ok=pass_count>=CONFIG.min_persistence_rounds
    quality_ok=trend>=58 and fund>=58 and persistence_ok
    executable=bool(quality_ok and market_ok and sector_ok and (controlled or pullback_reclaim))
    return {"market_ok":market_ok,"sector_ok":sector_ok,"persistence_ok":persistence_ok,
            "controlled_entry":controlled,"pullback_reclaim":pullback_reclaim,
            "pullback_drop_pct":round(pullback_drop_pct,3),"vwap_distance_pct":vwap_distance,
            "entry_low":entry_low,"entry_high":entry_high,"entry_quality_score":entry_quality,
            "chase_risk_score":chase_risk,"executable":executable}


@dataclass
class ActiveCandidate:
    event_key: str
    trade_date: str
    code: str
    name: str
    industry: str
    channel: str
    first_seen_at: datetime
    last_seen_at: datetime
    discovery_score: float
    row: dict[str, Any]
    reasons: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    last_checkpoint: int = 0
    last_state: str = ""
    recheck_started_at: datetime | None = None
    price_mismatch_count: int = 0
    initial_price: float | None = None
    initial_pct_chg: float | None = None
    first_watch_at: datetime | None = None
    first_watch_price: float | None = None
    first_watch_pushed_at: datetime | None = None
    signal_family: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


def _preopen_candidate_pool(db: Any, now: datetime) -> list[tuple[str, str, str]]:
    """Build a fresh pre-open pool without reviving stale historical leaders."""
    today = now.date().isoformat()
    latest_row = db.execute(
        "SELECT MAX(trade_date) FROM v8_discovery_candidates WHERE trade_date < ?", (today,)
    ).fetchone()
    latest_day = str(latest_row[0] or "") if latest_row else ""
    prior = []
    if latest_day:
        prior = db.execute("""SELECT code,name,MAX(discovery_score) score
          FROM v8_discovery_candidates
          WHERE trade_date=? AND action IN ('WATCH','WAIT_PULLBACK','CONFIRMED','DEFENSE_STRONG')
          GROUP BY code,name ORDER BY score DESC LIMIT 8""", (latest_day,)).fetchall()
    events = db.execute("""SELECT code,MAX(COALESCE(name,'')) name,COUNT(*) n FROM v8_event_radar
      WHERE code IS NOT NULL AND code<>'' AND direction='POSITIVE'
        AND substr(COALESCE(published_at,created_at),1,10)>=?
      GROUP BY code ORDER BY n DESC LIMIT 5""",
      ((now-timedelta(days=5)).date().isoformat(),)).fetchall()
    pool: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in prior:
        code = str(row[0] or "").split(".")[0].zfill(6)
        if code and code not in seen:
            seen.add(code)
            pool.append((code, str(row[1] or ""), f"前一交易日候选 {latest_day}"))
    for row in events:
        code = str(row[0] or "").split(".")[0].zfill(6)
        if code and code not in seen:
            seen.add(code)
            pool.append((code, str(row[1] or ""), "近5日正向事件"))
    return pool


class IndependentDiscoveryRuntime:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._active: dict[str, ActiveCandidate] = {}
        self._previous: dict[str, dict[str, Any]] = {}
        self._last_status_push = ""
        self._last_auction_day = ""
        self._active_lock = threading.RLock()
        self._last_market: dict[str, Any] = {}
        self._last_sectors: dict[str, dict[str, Any]] = {}
        self._recheck_thread: threading.Thread | None = None

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _is_market_window(now: datetime) -> bool:
        clock = now.hour * 60 + now.minute
        return now.weekday() < 5 and (570 <= clock <= 690 or 780 <= clock <= 900)

    def run_forever(self) -> None:
        self._restore_runtime(datetime.now().astimezone())
        if self._recheck_thread is None or not self._recheck_thread.is_alive():
            self._recheck_thread = threading.Thread(target=self.run_recheck_forever,
                                                     name="V8CandidateRecheck", daemon=True)
            self._recheck_thread.start()
        while not self._stop.is_set():
            now = datetime.now().astimezone()
            clock = now.hour * 60 + now.minute
            if now.weekday() < 5 and 565 <= clock <= 568 and self._last_auction_day != now.date().isoformat():
                try:
                    self.run_preopen_auction(now)
                    self._last_auction_day = now.date().isoformat()
                except Exception as exc:
                    save_module_health("AUCTION_RADAR", "DEGRADED", now.isoformat(timespec="seconds"),
                                       {"error": type(exc).__name__, "message": str(exc)[:300]})
            if self._is_market_window(now):
                try:
                    self.scan_once(now)
                except Exception as exc:
                    save_module_health("INDEPENDENT_DISCOVERY", "DEGRADED", now.isoformat(timespec="seconds"),
                                       {"error": type(exc).__name__, "message": str(exc)[:300]})
            self._stop.wait(CONFIG.independent_scan_seconds)

    @staticmethod
    def _parse_time(value: Any, fallback: datetime) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone() if parsed.tzinfo else parsed.replace(tzinfo=fallback.tzinfo)
        except Exception:
            return fallback

    def _restore_runtime(self, now: datetime) -> None:
        """Restore same-day active candidates and release jobs left RUNNING by a crash."""
        initialize()
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if str(payload.get("observed_at") or "")[:10] == now.date().isoformat():
                self._last_market = dict(payload.get("market") or {})
                self._last_sectors = dict(payload.get("sectors") or {})
        except Exception:
            pass
        db = connect()
        db.execute("""UPDATE v8_candidate_recheck_queue SET status='RETRY',
          updated_at=? WHERE status='RUNNING'""", (now.isoformat(timespec="seconds"),))
        rows = db.execute("""SELECT * FROM v8_discovery_candidates
          WHERE trade_date=? AND action NOT IN ('CONFIRMED','BLOCKED','EXPIRED')""",
          (now.date().isoformat(),)).fetchall()
        restored=[]
        for row in rows:
            last_seen = self._parse_time(row["last_seen_at"], now)
            if (now-last_seen).total_seconds() > CONFIG.candidate_retention_minutes*60:
                continue
            try:
                details = json.loads(row["details_json"] or "{}")
            except Exception:
                details = {}
            evidence = details.get("evidence") if isinstance(details.get("evidence"), Mapping) else {}
            reasons = details.get("reasons") if isinstance(details.get("reasons"), list) else []
            observations=[]
            for obs_row in db.execute("""SELECT checkpoint_minute,evidence_json FROM v8_persistence_observations
              WHERE event_key=? ORDER BY checkpoint_minute""",(row["event_key"],)).fetchall():
                try: observation=json.loads(obs_row["evidence_json"] or "{}")
                except Exception: observation={}
                observation["checkpoint_minute"]=int(obs_row["checkpoint_minute"])
                observations.append(observation)
            started = details.get("recheck_started_at") or details.get("signal_time") or now.isoformat(timespec="seconds")
            candidate = ActiveCandidate(
                str(row["event_key"]),str(row["trade_date"]),str(row["code"]),str(row["name"] or ""),
                str(row["industry"] or "行业待确认"),str(row["channel"] or "独立发现"),
                self._parse_time(row["first_seen_at"],now),last_seen,float(row["discovery_score"] or 0),
                {"code":row["code"],"name":row["name"],"price":row["price"],"pct_chg":row["pct_chg"],
                 "amount":row["amount"],"source_time":row["source_time"]},list(reasons),observations,
                max([int(x.get("checkpoint_minute") or 0) for x in observations] or [0]),
                str(row["last_push_state"] or ""),self._parse_time(started,now) if started else None)
            candidate.initial_price=_f(row["first_seen_price"]) or _f(details.get("first_seen_price")) or _f(row["price"])
            candidate.initial_pct_chg=_f(row["first_seen_pct_chg"])
            if candidate.initial_pct_chg is None:
                candidate.initial_pct_chg=_f(details.get("first_seen_pct_chg"))
            if candidate.initial_pct_chg is None:
                candidate.initial_pct_chg=_f(row["pct_chg"])
            candidate.first_watch_at=self._parse_time(row["first_watch_at"],now) if row["first_watch_at"] else None
            candidate.first_watch_price=_f(row["first_watch_price"])
            candidate.first_watch_pushed_at=self._parse_time(row["first_watch_pushed_at"],now) if row["first_watch_pushed_at"] else None
            candidate.signal_family=str(row["signal_family"] or details.get("early_signal_family") or "")
            with self._active_lock:
                self._active[candidate.event_key]=candidate
            if candidate.recheck_started_at:
                restored.append(candidate)
        db.commit();db.close()
        for candidate in restored:
            self._schedule_rechecks(candidate, allow_initial_push=False)

    def _schedule_rechecks(self, candidate: ActiveCandidate, *, allow_initial_push: bool) -> None:
        with candidate.lock:
            if candidate.recheck_started_at is None:
                candidate.recheck_started_at=candidate.last_seen_at
            started=candidate.recheck_started_at
        db=connect();created=datetime.now().astimezone().isoformat(timespec="seconds")
        initial_pct=float(candidate.initial_pct_chg if candidate.initial_pct_chg is not None else
                          (_f(candidate.row.get("pct_chg"), 99) or 99))
        checkpoints=(0,1,2,3,6,10,CONFIG.candidate_retention_minutes) if \
          -1.0 <= initial_pct <= CONFIG.early_watch_max_pct_chg else \
          (0,3,6,10,CONFIG.candidate_retention_minutes)
        for checkpoint in dict.fromkeys(checkpoints):
            due_time=_add_trading_minutes(started,checkpoint)
            if due_time is None and checkpoint!=CONFIG.candidate_retention_minutes:
                continue
            if due_time is None:
                due_time=started.replace(hour=15,minute=0,second=0,microsecond=0)
            due=due_time.isoformat(timespec="seconds")
            priority=105 if checkpoint in (1,2) else (100 if checkpoint in (3,6,10) else (90 if checkpoint==0 else 60))
            db.execute("""INSERT OR IGNORE INTO v8_candidate_recheck_queue(event_key,checkpoint_minute,due_at,
              priority,status,attempts,allow_initial_push,created_at,updated_at,details_json)
              VALUES(?,?,?,?, 'PENDING',0,?,?,?,?)""",
              (candidate.event_key,checkpoint,due,priority,int(allow_initial_push),created,created,
               json.dumps({"code":candidate.code,"name":candidate.name},ensure_ascii=False)))
        db.commit();db.close()

    def _claim_due_rechecks(self, now: datetime) -> list[dict[str, Any]]:
        db=connect();stamp=now.isoformat(timespec="seconds")
        stale=(now-timedelta(minutes=2)).isoformat(timespec="seconds")
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE v8_candidate_recheck_queue SET status='RETRY',updated_at=?
              WHERE status='RUNNING' AND updated_at<?""",(stamp,stale))
            rows=db.execute("""SELECT * FROM v8_candidate_recheck_queue
              WHERE status IN ('PENDING','RETRY') AND due_at<=?
              ORDER BY priority DESC,due_at LIMIT ?""",(stamp,CONFIG.recheck_max_jobs_per_cycle)).fetchall()
            for row in rows:
                db.execute("""UPDATE v8_candidate_recheck_queue SET status='RUNNING',attempts=attempts+1,
                  updated_at=? WHERE event_key=? AND checkpoint_minute=?""",
                  (stamp,row["event_key"],row["checkpoint_minute"]))
            db.commit();return [dict(row) for row in rows]
        finally:db.close()

    @staticmethod
    def _complete_recheck(job: Mapping[str, Any], now: datetime, *, error: str | None=None,
                          retry: bool=False) -> None:
        db=connect();attempts=int(job.get("attempts") or 0)+1
        if retry and attempts<4:
            status="RETRY";due=(now+timedelta(seconds=30)).isoformat(timespec="seconds")
        else:
            status="FAILED" if error else "DONE";due=str(job.get("due_at") or now.isoformat(timespec="seconds"))
        db.execute("""UPDATE v8_candidate_recheck_queue SET status=?,due_at=?,last_error=?,updated_at=?
          WHERE event_key=? AND checkpoint_minute=?""",
          (status,due,error,now.isoformat(timespec="seconds"),job["event_key"],job["checkpoint_minute"]))
        db.commit();db.close()

    @staticmethod
    def _cancel_future_rechecks(event_key:str,checkpoint:int,now:datetime)->None:
        db=connect();db.execute("""UPDATE v8_candidate_recheck_queue SET status='CANCELLED',updated_at=?
          WHERE event_key=? AND checkpoint_minute>? AND status IN ('PENDING','RETRY')""",
          (now.isoformat(timespec="seconds"),event_key,int(checkpoint)))
        db.commit();db.close()

    def process_due_once(self, now: datetime | None=None) -> list[dict[str, Any]]:
        now=(now or datetime.now().astimezone()).astimezone();results=[]
        for job in self._claim_due_rechecks(now):
            try:
                with self._active_lock:candidate=self._active.get(str(job["event_key"]))
                if candidate is None:
                    self._complete_recheck(job,now,error="CANDIDATE_NOT_ACTIVE")
                    results.append({"event_key":job["event_key"],"status":"FAILED"});continue
                checkpoint=int(job["checkpoint_minute"])
                latest=self._previous.get(candidate.code)
                if latest:
                    with candidate.lock:candidate.row=dict(latest)
                if checkpoint==CONFIG.candidate_retention_minutes:
                    result=self._expire_candidate(candidate,now)
                else:
                    market=dict(self._last_market or {"score":50,"regime":"正常"})
                    sector=dict(self._last_sectors.get(candidate.industry) or {})
                    result=self._evaluate(candidate,market,sector,now,checkpoint_minute=checkpoint,
                                          allow_initial_push=bool(job.get("allow_initial_push")))
                retry=(checkpoint in (1,2,3,6,10) and not bool(result.get("checkpoint_recorded")) and
                       str(result.get("status")) not in {"CONFIRMED","BLOCKED","EXPIRED","DEFENSE_STRONG"})
                self._complete_recheck(job,now,error=result.get("error"),retry=retry)
                if str(result.get("status")) in {"CONFIRMED","BLOCKED","EXPIRED"}:
                    self._cancel_future_rechecks(candidate.event_key,checkpoint,now)
                results.append({"event_key":candidate.event_key,"checkpoint":checkpoint,**result})
            except Exception as exc:
                self._complete_recheck(job,now,error=type(exc).__name__,retry=True)
                results.append({"event_key":job["event_key"],"status":"RETRY","error":type(exc).__name__})
        if results:
            save_module_health("CANDIDATE_RECHECK","OK",now.isoformat(timespec="seconds"),
                               {"processed":len(results),"results":results[-8:]})
        return results

    def run_recheck_forever(self) -> None:
        while not self._stop.is_set():
            now=datetime.now().astimezone()
            if self._is_market_window(now):
                try:self.process_due_once(now)
                except Exception as exc:
                    save_module_health("CANDIDATE_RECHECK","DEGRADED",now.isoformat(timespec="seconds"),
                      {"error":type(exc).__name__,"message":str(exc)[:300]})
            self._stop.wait(CONFIG.recheck_poll_seconds)

    def run_preopen_auction(self, now: datetime | None = None) -> dict[str, Any]:
        """Assess prior V8 candidates and current positive-event stocks after 09:25."""
        now = (now or datetime.now().astimezone()).astimezone()
        initialize(); db=connect()
        pool = _preopen_candidate_pool(db, now)
        db.close()
        results=[]
        from .auction_runtime import fetch_assessment,build_summary
        for code,name,source in pool[:6]:
            daily=_daily_features(code,now)
            result=dict(fetch_assessment(code,name,now,daily.get('last_close'),daily.get('last_amount_yuan')))
            result["candidate_source"] = source
            results.append((code,name,result))
        if results:
            send_once(f"v8-auction-summary:{now:%Y%m%d}",build_summary(results))
        ok=sum(x[2].get('status')=='OK' for x in results)
        stats={"pool":len(pool),"assessed":len(results),"ok":ok,"unknown":len(results)-ok}
        save_module_health("AUCTION_RADAR","OK" if ok else "DEGRADED",now.isoformat(timespec="seconds"),stats)
        return stats

    def scan_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now().astimezone()).astimezone()
        initialize()
        rows, source_time = _fetch_full_market()
        profiles = _load_profiles(now)
        market = _market_context(rows)
        sectors = _sector_context(rows, profiles)
        self._last_market = dict(market)
        self._last_sectors = {str(key):dict(value) for key,value in sectors.items()}
        from .sector_baseline import save_sector_snapshots
        save_sector_snapshots(now,sectors)
        eligible = [r for r in rows if _eligible(r)]
        ranked = []
        for row in eligible:
            profile = profiles.get(row["code"]) or {}
            industry = str(profile.get("industry") or "行业待确认")
            score, channel, reasons = _discovery_score(row, self._previous.get(row["code"]), sectors.get(industry) or {})
            if score >= 56:
                ranked.append((score, channel, reasons, industry, row))
        ranked.sort(key=lambda x: x[0], reverse=True)
        from .paid_data import PAID_DATA
        capacity = _dynamic_pool_limits(market, PAID_DATA.health(), len(ranked))
        selected = ranked[:int(capacity["watch_limit"])]
        dynamic_limit = int(capacity["enrich_limit"])
        enriched = 0
        for index, (score, channel, reasons, industry, row) in enumerate(selected):
            candidate = self._upsert_candidate(now, row, industry, channel, score, reasons)
            if index < dynamic_limit:
                enriched += 1
                self._schedule_rechecks(candidate,allow_initial_push=index<CONFIG.independent_push_top_n)
        self._expire_missing(now, {r[4]["code"] for r in selected})
        self._previous = {r["code"]: r for r in rows}
        self._save_run(now, source_time, rows, eligible, selected, market, enriched, capacity)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"observed_at": now.isoformat(timespec="seconds"),
                                          "source_time": source_time, "source": CN_SOURCE,
                                          "market": market, "sectors": sectors}, ensure_ascii=False,
                                         default=str), encoding="utf-8")
        try:
            from .market_audit_runtime import safe_observe_full_market
            safe_observe_full_market({r["code"]: {"name": r["name"], "pct": r["pct_chg"],
                                                   "price": r["price"]} for r in rows},
                                     observed_at=now.isoformat(timespec="seconds"))
        except Exception:
            pass
        stats = {"universe_count": len(rows), "eligible_count": len(eligible), "candidate_count": len(selected),
                 "enriched_count": enriched, "market_score": market["score"], "market_regime": market["regime"],
                 "source": CN_SOURCE, "source_time": source_time, **capacity}
        hour_key = now.strftime("%Y%m%d%H")
        if hour_key != self._last_status_push:
            send_once(f"v8-independent-status:{hour_key}", build_scan_status(stats))
            self._last_status_push = hour_key
        save_module_health("INDEPENDENT_DISCOVERY", "OK", now.isoformat(timespec="seconds"), stats)
        return stats

    def _upsert_candidate(self, now: datetime, row: dict[str, Any], industry: str, channel: str,
                          score: float, reasons: list[str]) -> ActiveCandidate:
        trade_date = now.date().isoformat()
        key = _event_key(row["code"], trade_date, channel)
        with self._active_lock:
            existing = self._active.get(key)
            if existing:
                with existing.lock:
                    existing.last_seen_at = now; existing.row = row; existing.discovery_score = score
                    existing.reasons = reasons; existing.channel = channel;existing.industry=industry
                return existing
            candidate = ActiveCandidate(key, trade_date, row["code"], row["name"], industry, channel,
                                        now, now, score, row, reasons)
            candidate.initial_price=_f(row.get("price"))
            candidate.initial_pct_chg=_f(row.get("pct_chg"))
            self._active[key] = candidate
        return candidate

    def _evaluate(self, c: ActiveCandidate, market: Mapping[str, Any], sector: Mapping[str, Any], now: datetime,
                  *, checkpoint_minute: int, allow_initial_push: bool = False) -> dict[str, Any]:
        with c.lock:
            row=dict(c.row);base_reasons=list(c.reasons)
            started=c.recheck_started_at or c.first_seen_at
            existing_times={str(x.get("data_time")) for x in c.observations if x.get("data_time")}
        elapsed = int((now - started).total_seconds() // 60)
        checkpoint=int(checkpoint_minute)
        daily = _daily_features(c.code, now, name=c.name, industry=c.industry)
        minute = _minute_features(c.code, now)
        live_price=_f(minute.get("price")) or _f(row.get("price"),0) or 0
        cross = _cross_validate(c.code, live_price, now, minute.get("data_time"))
        event_ctx = _event_context(c.code, now)
        chip = _chip_features(c.code, now, live_price)
        macro = _macro_features(now)
        moneyflow = _moneyflow_features(c.code,now)
        from .auction_runtime import fetch_assessment,load_assessment
        auction = load_assessment(c.code,now)
        if auction is None:
            auction = fetch_assessment(c.code,c.name,now,daily.get('last_close'),daily.get('last_amount_yuan'))
        checkpoint_recorded=False
        data_time=str(minute.get("data_time") or "")
        fresh_checkpoint=bool(data_time and data_time not in existing_times)
        if checkpoint in (1,2,3,6,10) and checkpoint > c.last_checkpoint and minute.get("status") == "OK" and fresh_checkpoint:
            passed = bool(minute.get("above_vwap") and not minute.get("blowoff_reversal") and
                          (_f(minute.get("amount_acceleration"), 0) or 0) >= .75)
            obs = {"checkpoint_minute": checkpoint, "passed": passed, "above_vwap": minute.get("above_vwap"),
                   "amount_acceleration": minute.get("amount_acceleration"), "blowoff_reversal": minute.get("blowoff_reversal"),
                   "data_time": minute.get("data_time"),"price":minute.get("price"),"vwap":minute.get("vwap")}
            with c.lock:
                c.observations.append(obs); c.last_checkpoint = checkpoint
            checkpoint_recorded=True
            save_observation(c.event_key, checkpoint, now.isoformat(timespec="seconds"),
                             {"price": minute.get("price"), "vwap": minute.get("vwap"),
                              "order_imbalance": None, "amount_delta": minute.get("amount_delta"),
                              "sector_score": sector.get("score"), "blowoff_reversal": minute.get("blowoff_reversal"),
                              "data_time": minute.get("data_time"), "passed": passed})
        with c.lock:observations=list(c.observations)
        pass_count = sum(bool(x.get("passed")) for x in observations)
        persistence = 45 if not observations else _weighted([(_scale(pass_count, 0, 2), 2),
                                                                 (_scale(len(observations), 0, 3), 1)])
        trend = _f(daily.get("trend_score"), 50) or 50
        fund = _f(minute.get("fund_score"), 50) or 50
        sector_score = _f(sector.get("score"), 50) or 50
        auction_score = _f(auction.get('quality_score'),50) or 50
        chip_score = _f(chip.get("score"), 50) or 50
        opportunity = _weighted([(market["score"], 1.2), (sector_score, 1.5), (trend, 2),
                                 (fund, 2), (persistence, 2), (c.discovery_score, 1.3),
                                 (auction_score,1), (chip_score,.8)])
        opportunity = _clamp(opportunity + min(5, event_ctx["positive_count"] * 2) - min(8, event_ctx["negative_count"] * 3))
        pct = _live_pct_chg(live_price, row, daily)
        vwap = _f(minute.get("vwap"))
        entry=_entry_setup(market=market,sector=sector,pct=pct,live_price=live_price,vwap=vwap,
          trend=trend,fund=fund,pass_count=pass_count,observations=observations,minute=minute)
        vwap_distance_pct=entry["vwap_distance_pct"]
        chase_risk=float(entry["chase_risk_score"]);entry_quality=float(entry["entry_quality_score"])
        probability_shadow = {"status": "DISABLED", "probability_pct": None,
                              "action_permission": "ANNOTATION_ONLY"}
        portfolio_correlation = {"status": "DISABLED", "risk": "UNKNOWN",
                                 "action_permission": "ANNOTATION_ONLY"}
        point_in_time = {"status": "UNKNOWN", "violations": [],
                         "action_permission": "ANNOTATION_ONLY"}
        if CONFIG.predictive_shadow_enabled:
            try:
                from .predictive_shadow import (audit_point_in_time, estimate_entry_probability,
                                                portfolio_correlation_shadow)
                predictive_features = {
                    "opportunity_score": opportunity, "entry_quality_score": entry_quality,
                    "market_regime": market.get("regime"), "sector_score": sector_score,
                    "trend_score": trend, "fund_score": fund, "persistence_score": persistence,
                }
                probability_shadow = estimate_entry_probability(
                    predictive_features, horizon=CONFIG.predictive_horizon,
                    minimum_samples=CONFIG.predictive_min_samples,
                    neighbor_limit=CONFIG.predictive_neighbor_limit)
                portfolio_correlation = portfolio_correlation_shadow(
                    c.code, lookback=CONFIG.portfolio_correlation_lookback)
                point_in_time = audit_point_in_time(now.isoformat(timespec="seconds"), {
                    "daily": daily, "minute": minute, "event_context": event_ctx,
                    "auction": auction, "chip": chip, "macro": macro, "moneyflow": moneyflow,
                    "source_time": row.get("source_time"),
                })
            except Exception as predictive_error:
                probability_shadow = {"status": "DEGRADED", "probability_pct": None,
                                      "error": type(predictive_error).__name__,
                                      "action_permission": "ANNOTATION_ONLY"}
        risk = 0.0
        vetoes = []
        if cross.get("status") == "WARNING":
            with c.lock:c.price_mismatch_count+=1;mismatch_count=c.price_mismatch_count
            risk += min(35,12*mismatch_count)
            if mismatch_count>=2:vetoes.append("两次独立复核价格仍明显不一致")
        else:
            with c.lock:c.price_mismatch_count=0;mismatch_count=0
        if pct >= 8:
            risk += 25; vetoes.append("涨幅过高，成交可执行性差")
        if minute.get("blowoff_reversal"):
            risk += 25
        if event_ctx["hard_risk"]:
            risk += 50; vetoes.append("V8事件雷达发现近期重大负面事件")
        if auction.get('status')=='OK' and str(auction.get('risk_level'))=='HIGH':
            risk += 30
            if (_f(auction.get('gap_pct'),0) or 0)>=9.5:
                vetoes.append("竞价接近涨停且存在成交可执行风险")
        if chip.get("risk") == "HIGH":
            risk += 10
        if point_in_time.get("status") == "INVALID":
            risk += 50
            vetoes.append("点时数据审计发现未来证据，禁止本轮确认")
        state = "WATCH"
        if vetoes:
            state = "BLOCKED"
        elif pass_count >= CONFIG.min_persistence_rounds and trend >= 58 and fund >= 58:
            if market["regime"] == "防守" and opportunity >= 82:
                state = "DEFENSE_STRONG"
            elif market["regime"] != "防守" and opportunity >= 74:
                state = "CONFIRMED" if entry["executable"] else "WAIT_PULLBACK"
        reasons = base_reasons
        shadow_factors = daily.get("strategy_shadow_factors") if isinstance(daily.get("strategy_shadow_factors"), Mapping) else {}
        factor_labels = {"RPS_120_SECTOR_NEUTRAL": "板块中性相对强度",
                         "HIGH_TIGHT_FLAG_REFINED": "缩量紧密蓄势",
                         "LIMITUP_SHAKEOUT_REFINED": "涨停后承接",
                         "TURTLE_BREAKOUT_LIQUID": "流动性20日突破",
                         "MA_VOLUME_CONSENSUS": "均线放量共振",
                         "VOLATILITY_CONTRACTION": "趋势内波动收缩",
                         "MOMENTUM_QUALITY_GUARDED": "防过热中期动量"}
        triggered_labels = [factor_labels.get(key, key) for key, value in shadow_factors.items()
                            if isinstance(value, Mapping) and str(value.get("status")) == "TRIGGERED"]
        if triggered_labels:
            reasons.append("研究标签：" + "、".join(triggered_labels) + "（仅做影子统计）")
        if daily.get("status") == "OK":
            reasons.append(f"日线趋势{trend:.0f}")
        if minute.get("status") == "OK":
            reasons.append(f"分钟资金质量{fund:.0f}")
            reasons.append(f"买点质量{entry_quality:.0f}，追高风险{chase_risk:.0f}")
        if cross.get("status") == "WARNING" and mismatch_count<2:
            reasons.append("行情价格首次不一致，已转入复核而非直接误杀")
        if state == "WAIT_PULLBACK":
            if not entry["market_ok"]:reasons.append("市场环境未达到建仓确认线")
            if not entry["sector_ok"]:reasons.append("板块扩散未达到建仓确认线")
            if not entry["controlled_entry"] and not entry["pullback_reclaim"]:
                reasons.append("当前位置偏离合理买点，尚未形成回踩后二次转强")
        if entry["pullback_reclaim"]:
            reasons.append(f"已识别回踩后二次转强，回踩幅度{entry['pullback_drop_pct']:.2f}%")
        if event_ctx["positive_count"]:
            reasons.append(f"事件催化{event_ctx['positive_count']}条，仅作加分证据")
        if event_ctx["negative_count"]:
            reasons.append(f"负面事件{event_ctx['negative_count']}条")
        if auction.get('status')=='OK':
            reasons.append(f"竞价质量{auction_score:.0f}，风险{auction.get('risk_level')}")
        else:
            reasons.append("竞价数据待确认，按中性降级")
        if chip.get("status") == "OK":
            reasons.append(f"筹码质量{chip_score:.0f}，风险{chip.get('risk')}")
        else:
            reasons.append("筹码数据待确认，按中性处理")
        if moneyflow.get("status")=="OK":
            reasons.append(f"近{moneyflow.get('sample_days')}日资金流为正{moneyflow.get('positive_days')}日，仅作研究证据")
        if probability_shadow.get("status") == "AVAILABLE":
            reasons.append(f"相似成熟样本{probability_shadow.get('sample_n')}笔，"
                           f"校准概率{float(probability_shadow.get('probability_pct') or 0):.1f}%（仅Shadow）")
        elif probability_shadow.get("status") == "INSUFFICIENT_SAMPLE":
            reasons.append("历史相似成熟样本不足，不伪造胜率")
        if portfolio_correlation.get("risk") in {"MEDIUM", "HIGH"}:
            reasons.append(f"与现有持仓最高相关"
                           f"{float(portfolio_correlation.get('max_correlation') or 0):.2f}，需防同风险暴露")
        reasons.extend(vetoes)
        early_shadow=_early_shadow_signal(pct=pct,discovery_score=c.discovery_score,market=market,
          sector=sector,daily=daily,minute=minute,event_ctx=event_ctx)
        if early_shadow.get("family"):
            c.signal_family=str(early_shadow["family"])
            reasons.append(str(early_shadow.get("reason")) + "（提前观察，不是建仓确认）")
        event = {"event_key": c.event_key, "strategy_version": "V8.4-predictive-factor-shadow",
                 "signal_time": c.first_seen_at.isoformat(timespec="seconds"), "evaluation_time": now.isoformat(timespec="seconds"),
                 "code": c.code, "name": c.name, "industry": c.industry, "source": "V8_INDEPENDENT_DISCOVERY",
                 "executable_price": live_price}
        decision = {"action": "SHADOW_ENTRY_CONFIRMED" if state == "CONFIRMED" else state,
                    "opportunity_score": opportunity, "market": {"score": market["score"], "label": market["regime"]},
                    "sector": {"score": sector_score, "label": "独立扩散"}, "trend": {"score": trend, "label": daily.get("status")},
                    "fund": {"score": fund, "label": minute.get("status")},
                    "persistence": {"score": persistence, "label": "CONFIRMED" if pass_count >= 2 else "WAIT"},
                    "risk_score": _clamp(risk), "position_pct": 0.0, "reasons": reasons, "vetoes": vetoes,
                    "entry_quality_score":entry_quality,"chase_risk_score":chase_risk,
                    "entry_setup":entry,
                    "trade_plan":{"entry_low":entry.get("entry_low"),"entry_high":entry.get("entry_high"),
                                  "stop_price":None},
                    "probability_shadow": probability_shadow,
                    "portfolio_correlation_shadow": portfolio_correlation,
                    "point_in_time_audit": point_in_time,
                    "shadow_only": True, "data_limitations": minute.get("data_limitations") or []}
        save_decision(event, decision)
        if CONFIG.predictive_shadow_enabled:
            try:
                from .storage import save_candidate_risk_shadow,save_probability_shadow
                save_probability_shadow(event,probability_shadow)
                save_candidate_risk_shadow(event,"PORTFOLIO_CORRELATION",portfolio_correlation)
                save_candidate_risk_shadow(event,"POINT_IN_TIME",{
                    **point_in_time,"risk":"HIGH" if point_in_time.get("status")=="INVALID" else "LOW"})
            except Exception as predictive_persist_error:
                save_module_health("PREDICTIVE_SHADOW", "DEGRADED", now.isoformat(timespec="seconds"),
                                   {"error": type(predictive_persist_error).__name__, "code": c.code})
        if shadow_factors:
            try:
                from .strategy_shadow import persist as persist_strategy_shadow
                persist_strategy_shadow(event, shadow_factors, checkpoint_minute=checkpoint,
                                        entry_price=live_price)
            except Exception as factor_error:
                save_module_health("STRATEGY_FACTOR_SHADOW", "DEGRADED", now.isoformat(timespec="seconds"),
                                   {"error": type(factor_error).__name__, "code": c.code})
        if state == "CONFIRMED":
            from .forward_runtime import register_decision_outcomes
            register_decision_outcomes(c.event_key,live_price)
        payload = {"code": c.code, "name": c.name, "industry": c.industry, "channel": c.channel,
                   "price": live_price, "pct_chg": pct, "amount": row.get("amount"),
                   "evaluation_time":now.isoformat(timespec="seconds"),
                   "source_time": row.get("source_time"), "discovery_score": c.discovery_score,
                   "opportunity_score": opportunity, "market_score": market["score"], "market_regime": market["regime"],
                   "sector_score": sector_score, "trend_score": trend, "fund_score": fund,
                   "persistence_score": persistence, "auction_score": auction_score,
                   "auction_status": auction.get('status'), "auction_risk": auction.get('risk_level'),
                   "chip_score": chip_score, "chip_status": chip.get("status"), "chip_risk": chip.get("risk"),
                   "macro_status": macro.get("status"), "macro_risk": macro.get("risk"),
                   "entry_quality_score":entry_quality,"chase_risk_score":chase_risk,
                   "vwap_distance_pct":vwap_distance_pct,"price_mismatch_count":mismatch_count,
                   "entry_low":entry.get("entry_low"),"entry_high":entry.get("entry_high"),
                   "pullback_reclaim":entry.get("pullback_reclaim"),
                   "first_seen_price":c.initial_price,
                   "first_seen_pct_chg":c.initial_pct_chg,
                   "detected_at":c.first_seen_at.isoformat(timespec="seconds"),
                   "early_signal_family":c.signal_family,
                   "early_shadow":early_shadow,
                   "probability_shadow":probability_shadow,
                   "portfolio_correlation_shadow":portfolio_correlation,
                   "point_in_time_audit":point_in_time,
                   "risk_score": risk, "reasons": reasons,"checkpoint_minute":checkpoint,
                   "recheck_started_at":started.isoformat(timespec="seconds"),
                   "evidence": {"daily": daily, "minute": minute, "cross_validation": cross,
                                "event_context": event_ctx,
                                "auction": auction,
                                "chip": chip, "macro": macro,"moneyflow":moneyflow,"entry_setup":entry,
                                "strategy_shadow_factors": shadow_factors,
                                "probability_shadow":probability_shadow,
                                "portfolio_correlation_shadow":portfolio_correlation,
                                "point_in_time_audit":point_in_time,
                                "data_limitations": minute.get("data_limitations") or []}}
        with c.lock:previous_state=c.last_state
        early_push=bool(early_shadow.get("family") and
                        float(early_shadow.get("score") or 0)>=CONFIG.early_watch_min_score)
        push_allowed = state != previous_state and (state in {"CONFIRMED", "WAIT_PULLBACK", "BLOCKED", "EXPIRED"} or
                       (state == "WATCH" and ((allow_initial_push and c.discovery_score >= 65) or early_push)))
        pushed=False;detail="SUPPRESSED"
        if push_allowed:
            if state=="WATCH" and c.first_watch_at is None:
                c.first_watch_at=now;c.first_watch_price=live_price
            push_result=send_once(f"v8-independent:{c.event_key}:{state}", build_candidate_message(payload, state))
            pushed,detail=push_result if isinstance(push_result,tuple) and len(push_result)>=2 else (False,"unknown")
            if state=="WATCH" and pushed:
                c.first_watch_pushed_at=now
        if state != previous_state:
            save_signal_delivery(c.event_key,state,now.isoformat(timespec="seconds"),c.initial_price,live_price,
              "SENT" if pushed else str(detail),now.isoformat(timespec="seconds") if pushed else None,
              {"checkpoint_minute":checkpoint,"entry_setup":entry,"market":dict(market),"sector":dict(sector),
               "early_shadow":early_shadow,"source_time":row.get("source_time"),
               "detected_at":c.first_seen_at.isoformat(timespec="seconds")})
        with c.lock:
            c.last_state = state
        self._persist_candidate(c, payload, state, pass_count)
        error=None
        if checkpoint in (1,2,3,6,10) and not checkpoint_recorded:
            error="MINUTE_DATA_UNAVAILABLE" if minute.get("status") != "OK" else "MINUTE_DATA_NOT_FRESH"
        return {"status":state,"checkpoint_recorded":checkpoint_recorded,"error":error,
                "opportunity_score":opportunity,"pass_count":pass_count,"elapsed_minutes":elapsed}

    def _persist_candidate(self, c: ActiveCandidate, payload: Mapping[str, Any], state: str, pass_count: int) -> None:
        db = connect()
        confirmed_at=str(payload.get("evaluation_time")) if state=="CONFIRMED" else None
        confirmed_price=payload.get("price") if state=="CONFIRMED" else None
        db.execute("""INSERT INTO v8_discovery_candidates(event_key,trade_date,code,name,industry,channel,first_seen_at,
          last_seen_at,source_time,price,pct_chg,amount,discovery_score,opportunity_score,action,checkpoint_count,
          pass_count,last_push_state,details_json,first_seen_price,first_seen_pct_chg,latest_live_pct_chg,
          first_watch_at,first_watch_price,first_watch_pushed_at,signal_family,confirmed_at,confirmed_price)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(event_key) DO UPDATE SET last_seen_at=excluded.last_seen_at,source_time=excluded.source_time,
          price=excluded.price,pct_chg=excluded.pct_chg,amount=excluded.amount,discovery_score=excluded.discovery_score,
          opportunity_score=excluded.opportunity_score,action=excluded.action,checkpoint_count=excluded.checkpoint_count,
          pass_count=excluded.pass_count,last_push_state=excluded.last_push_state,details_json=excluded.details_json,
          first_seen_price=COALESCE(v8_discovery_candidates.first_seen_price,excluded.first_seen_price),
          first_seen_pct_chg=COALESCE(v8_discovery_candidates.first_seen_pct_chg,excluded.first_seen_pct_chg),
          latest_live_pct_chg=excluded.latest_live_pct_chg,
          first_watch_at=COALESCE(v8_discovery_candidates.first_watch_at,excluded.first_watch_at),
          first_watch_price=COALESCE(v8_discovery_candidates.first_watch_price,excluded.first_watch_price),
          first_watch_pushed_at=COALESCE(v8_discovery_candidates.first_watch_pushed_at,excluded.first_watch_pushed_at),
          signal_family=COALESCE(NULLIF(excluded.signal_family,''),v8_discovery_candidates.signal_family),
          confirmed_at=COALESCE(v8_discovery_candidates.confirmed_at,excluded.confirmed_at),
          confirmed_price=COALESCE(v8_discovery_candidates.confirmed_price,excluded.confirmed_price)""",
          (c.event_key,c.trade_date,c.code,c.name,c.industry,c.channel,c.first_seen_at.isoformat(timespec="seconds"),
           c.last_seen_at.isoformat(timespec="seconds"),c.row.get("source_time"),payload.get("price"),payload.get("pct_chg"),
           c.row.get("amount"),c.discovery_score,payload.get("opportunity_score"),state,len(c.observations),pass_count,
           c.last_state,json.dumps(dict(payload),ensure_ascii=False,default=str),c.initial_price,c.initial_pct_chg,
           payload.get("pct_chg"),c.first_watch_at.isoformat(timespec="seconds") if c.first_watch_at else None,
           c.first_watch_price,c.first_watch_pushed_at.isoformat(timespec="seconds") if c.first_watch_pushed_at else None,
           c.signal_family,confirmed_at,confirmed_price))
        db.commit(); db.close()

    def _expire_candidate(self,candidate:ActiveCandidate,now:datetime)->dict[str,Any]:
        with candidate.lock:
            if candidate.last_state in {"CONFIRMED","BLOCKED","EXPIRED","DEFENSE_STRONG"}:
                return {"status":candidate.last_state,"checkpoint_recorded":True}
            previous_state=candidate.last_state
            candidate.last_state="EXPIRED"
        db=connect()
        row=db.execute("SELECT details_json FROM v8_discovery_candidates WHERE event_key=?",
                       (candidate.event_key,)).fetchone()
        try:payload=json.loads(row[0] or "{}") if row else {}
        except Exception:payload={}
        payload.update({"code":candidate.code,"name":candidate.name,"industry":candidate.industry,
          "channel":candidate.channel,"price":candidate.row.get("price"),
          "pct_chg":candidate.row.get("pct_chg"),"amount":candidate.row.get("amount"),
          "source_time":candidate.row.get("source_time"),"evaluation_time":now.isoformat(timespec="seconds"),
          "discovery_score":candidate.discovery_score})
        old_reasons=payload.get("reasons") if isinstance(payload.get("reasons"),list) else []
        payload["reasons"]=["已完成15分钟保留与复核，持续度仍未通过",*old_reasons[:3]]
        visible=db.execute("""SELECT 1 FROM v8_signal_delivery WHERE event_key=? AND push_status='SENT'
          AND state IN ('WATCH','WAIT_PULLBACK','CONFIRMED','DEFENSE_STRONG') LIMIT 1""",
          (candidate.event_key,)).fetchone()
        pushed=False;detail="internal_expiry_suppressed"
        if visible:
            result=send_once(f"v8-independent:{candidate.event_key}:EXPIRED",
                             build_candidate_message(payload,"EXPIRED"))
            pushed,detail=result if isinstance(result,tuple) and len(result)>=2 else (False,"unknown")
            save_signal_delivery(candidate.event_key,"EXPIRED",now.isoformat(timespec="seconds"),
              candidate.initial_price,payload.get("price"),"SENT" if pushed else str(detail),
              now.isoformat(timespec="seconds") if pushed else None,
              {"previous_state":previous_state,"reason":"RETENTION_EXPIRED"})
        db.execute("""UPDATE v8_discovery_candidates SET action='EXPIRED',last_push_state='EXPIRED',
          last_seen_at=?,details_json=? WHERE event_key=?""",
          (now.isoformat(timespec="seconds"),json.dumps(payload,ensure_ascii=False,default=str),candidate.event_key))
        db.commit();db.close()
        return {"status":"EXPIRED","checkpoint_recorded":True,"pushed":pushed,"push_detail":detail}

    def _expire_missing(self, now: datetime, selected_codes: set[str]) -> None:
        with self._active_lock:candidates=list(self._active.values())
        for candidate in candidates:
            if candidate.code in selected_codes:
                continue
            if (now - candidate.last_seen_at).total_seconds() < CONFIG.candidate_retention_minutes*60:
                continue
            if candidate.recheck_started_at is not None:
                continue
            if candidate.last_state not in {"EXPIRED", "BLOCKED"}:
                candidate.last_state = "EXPIRED"

    @staticmethod
    def _save_run(now: datetime, source_time: str, rows: list[dict[str, Any]], eligible: list[dict[str, Any]],
                  selected: list[Any], market: Mapping[str, Any], enriched: int,
                  capacity: Mapping[str, Any] | None = None) -> None:
        run_id = now.strftime("%Y%m%d%H%M%S")
        details = {"enriched_count": enriched, "market": dict(market), "independent": True,
                   "v66_input": False, "v7_input": False, "dynamic_capacity": dict(capacity or {})}
        db = connect()
        db.execute("""INSERT OR REPLACE INTO v8_discovery_runs(run_id,observed_at,source,universe_count,eligible_count,
          candidate_count,market_score,market_regime,source_time,status,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (run_id,now.isoformat(timespec="seconds"),CN_SOURCE,len(rows),len(eligible),len(selected),market["score"],
           market["regime"],source_time,"OK",json.dumps(details,ensure_ascii=False)))
        db.commit(); db.close()


RUNTIME = IndependentDiscoveryRuntime()


def run_once(now: datetime | None = None) -> dict[str, Any]:
    stamp=(now or datetime.now().astimezone()).astimezone()
    result=RUNTIME.scan_once(stamp)
    result["initial_rechecks"]=len(RUNTIME.process_due_once(stamp))
    return result
