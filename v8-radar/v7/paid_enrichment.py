from __future__ import annotations

import os
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .config import CONFIG
from .data_layer import DATA_LAYER

CN_TZ = ZoneInfo("Asia/Shanghai")
_ENRICH_PRIORITY = "normal"

HARD_ANNOUNCEMENT_KEYWORDS = (
    "立案调查", "立案", "退市风险", "终止上市", "重大违法", "清仓式减持", "清仓减持",
    "资金占用", "债务逾期",
)
HIGH_ANNOUNCEMENT_KEYWORDS = (
    "减持", "行政处罚", "监管措施", "重大诉讼", "重大担保", "业绩预亏", "预亏",
    "大幅下降", "大幅预减", "控制权变更风险", "停牌风险",
)
POSITIVE_ANNOUNCEMENT_KEYWORDS = (
    "预增", "扭亏", "回购", "增持", "中标", "重大合同", "重大订单", "并购", "重组",
)
NEWS_RISK_KEYWORDS = ("立案", "处罚", "退市", "减持", "预亏", "暴雷", "诉讼", "停产", "事故")
NEWS_CATALYST_KEYWORDS = ("中标", "订单", "回购", "增持", "突破", "涨价", "政策", "国产替代", "扩产", "业绩增长")


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    fmts = (
        "%Y%m%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%d",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%H:%M:%S", "%H:%M",
    )
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone(CN_TZ) if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)
    except Exception:
        pass
    for fmt in fmts:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt.startswith("%H"):
                parsed = datetime.combine(_now().date(), parsed.time())
            return parsed.replace(tzinfo=CN_TZ)
        except Exception:
            continue
    return None


def _row_time(row: Mapping[str, Any]) -> datetime | None:
    # Announcement relays commonly use ann_date/pub_time; intraday feeds tend to use the others.
    direct_fields = ("pub_time", "datetime", "trade_time")
    for key in direct_fields:
        parsed = _parse_time(row.get(key))
        if parsed:
            return parsed

    date_value = row.get("ann_date") or row.get("trade_date") or row.get("date")
    clock_value = row.get("time")
    date_text = str(date_value or "").strip()
    clock_text = str(clock_value or "").strip()
    if date_text and clock_text:
        parsed = _parse_time(f"{date_text} {clock_text}")
        if parsed:
            return parsed
    return _parse_time(date_value) or _parse_time(clock_value)


def _freshness(data_time: datetime | None, *, max_age_seconds: int, now: datetime | None = None) -> dict[str, Any]:
    fetched = (now or _now()).astimezone(CN_TZ)
    if data_time is None:
        return {"data_time": None, "fetched_at": fetched.isoformat(timespec="seconds"),
                "age_seconds": None, "freshness_status": "UNKNOWN"}
    comparable = data_time.astimezone(CN_TZ) if data_time.tzinfo else data_time.replace(tzinfo=CN_TZ)
    age = max(0.0, (fetched - comparable).total_seconds())
    return {"data_time": comparable.isoformat(timespec="seconds"),
            "fetched_at": fetched.isoformat(timespec="seconds"),
            "age_seconds": round(age, 1),
            "freshness_status": "FRESH" if age <= max_age_seconds else "STALE"}


def _announcement_freshness(data_time: datetime | None, now: datetime | None = None) -> dict[str, Any]:
    fetched = (now or _now()).astimezone(CN_TZ)
    base = _freshness(data_time, max_age_seconds=3 * 86400, now=fetched)
    if data_time is None:
        return base
    data_day = data_time.astimezone(CN_TZ).date()
    delta = (fetched.date() - data_day).days
    if delta <= 0:
        base["freshness_status"] = "FRESH"
    elif delta <= 3:
        base["freshness_status"] = "RECENT"
    else:
        base["freshness_status"] = "STALE"
    return base


def _minute_freshness(data_time: datetime | None, *, now: datetime | None = None,
                      market_is_open: bool | None = None) -> dict[str, Any]:
    """A-share session-aware freshness.

    Lunch break (11:30-13:00): the 11:30 bar remains FRESH.
    After 15:00: same-day 15:00 close data remains FRESH for end-of-day validation.
    Weekend/confirmed closed day: returns MARKET_CLOSED rather than repeatedly calling live feeds.
    Holidays require caller-provided market_is_open=False when a verified trade calendar is available.
    """
    fetched = (now or _now()).astimezone(CN_TZ)
    if data_time is None:
        return {"data_time": None, "fetched_at": fetched.isoformat(timespec="seconds"),
                "age_seconds": None, "freshness_status": "UNKNOWN", "market_session": "UNKNOWN"}
    dt = data_time.astimezone(CN_TZ) if data_time.tzinfo else data_time.replace(tzinfo=CN_TZ)
    age = max(0.0, (fetched - dt).total_seconds())
    meta = {"data_time": dt.isoformat(timespec="seconds"), "fetched_at": fetched.isoformat(timespec="seconds"),
            "age_seconds": round(age, 1), "freshness_status": "UNKNOWN", "market_session": "UNKNOWN"}

    if market_is_open is False or fetched.weekday() >= 5:
        meta.update(freshness_status="MARKET_CLOSED", market_session="CLOSED")
        return meta

    now_t = fetched.time().replace(tzinfo=None)
    data_t = dt.time().replace(tzinfo=None)
    same_day = fetched.date() == dt.date()
    if dtime(9, 30) <= now_t <= dtime(11, 30):
        meta["market_session"] = "MORNING"
        meta["freshness_status"] = "FRESH" if same_day and age <= 300 else "STALE"
    elif dtime(11, 30) < now_t < dtime(13, 0):
        meta["market_session"] = "LUNCH"
        meta["freshness_status"] = "FRESH" if same_day and data_t >= dtime(11, 29) else "STALE"
    elif dtime(13, 0) <= now_t <= dtime(15, 0):
        meta["market_session"] = "AFTERNOON"
        meta["freshness_status"] = "FRESH" if same_day and age <= 300 else "STALE"
    elif now_t > dtime(15, 0):
        meta["market_session"] = "AFTER_CLOSE"
        # The final bar may be timestamped 14:59/15:00 by different relays.
        meta["freshness_status"] = "FRESH" if same_day and data_t >= dtime(14, 58) else "STALE"
    else:
        meta["market_session"] = "PREOPEN"
        # Previous completed trading-day data is reference evidence, not a live confirmation.
        meta["freshness_status"] = "RECENT" if 0 <= (fetched.date() - dt.date()).days <= 3 else "STALE"
    return meta


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:
            pass
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, Mapping)]
    return []


def _ts_code(code: str) -> str:
    c = str(code or "").split(".")[0].zfill(6)
    if c.startswith(("6", "68")):
        return f"{c}.SH"
    if c.startswith(("4", "8", "92")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _safe_call(api: str, cache_key: str, ttl: int, **kwargs) -> tuple[list[dict[str, Any]], str]:
    priority = str(kwargs.pop("_priority", _ENRICH_PRIORITY))
    try:
        data = DATA_LAYER.relay_call(api, cache_key=cache_key, ttl_seconds=ttl, priority=priority, **kwargs)
        rows = _records(data)
        return rows, "OK" if rows else "EMPTY"
    except Exception:
        return [], "DATA_UNAVAILABLE"


def _announcement_factor(code: str, now: datetime | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(CN_TZ)
    end = now.strftime("%Y%m%d")
    start = (now - timedelta(days=14)).strftime("%Y%m%d")
    rows, status = _safe_call("anns_d", f"anns:{code}:{start}:{end}", max(600, CONFIG.notice_cache_seconds),
                              ts_code=_ts_code(code), start_date=start, end_date=end)
    evidence = []
    latest = None
    hard, high, positive = set(), set(), set()
    for row in rows:
        title = str(row.get("title") or row.get("ann_title") or row.get("name") or "").strip()
        ann_type = str(row.get("ann_type") or row.get("type") or row.get("category") or "").strip()
        text = f"{title} {ann_type}"
        when = _row_time(row)
        if when and (latest is None or when > latest):
            latest = when
        hard_hits = [kw for kw in HARD_ANNOUNCEMENT_KEYWORDS if kw in text]
        high_hits = [kw for kw in HIGH_ANNOUNCEMENT_KEYWORDS if kw in text]
        pos_hits = [kw for kw in POSITIVE_ANNOUNCEMENT_KEYWORDS if kw in text]
        hard.update(hard_hits); high.update(high_hits); positive.update(pos_hits)
        if hard_hits or high_hits or pos_hits:
            evidence.append({"title": title[:160], "type": ann_type[:80],
                             "time": when.isoformat(timespec="seconds") if when else None,
                             "risk_hits": hard_hits + high_hits, "positive_hits": pos_hits})
    meta = _announcement_freshness(latest, now)
    if status == "DATA_UNAVAILABLE":
        meta["freshness_status"] = "DATA_UNAVAILABLE"
    if status in {"DATA_UNAVAILABLE", "EMPTY"} or not rows:
        level = "UNKNOWN" if status == "DATA_UNAVAILABLE" else "LOW"
    elif hard:
        level = "HARD_BLOCK"
    elif high:
        level = "HIGH"
    elif positive:
        level = "LOW"
    else:
        level = "LOW"
    return {
        "status": status,
        "announcement_risk_level": level,
        "announcement_risk_reasons": sorted(hard | high),
        "announcement_positive_reasons": sorted(positive),
        "announcement_latest_time": meta.get("data_time"),
        "hard_veto": level in {"HARD_BLOCK", "HIGH"} and meta.get("freshness_status") in {"FRESH", "RECENT"},
        "count": len(rows), "evidence": evidence[:8], "coverage_start": start, "coverage_end": end, **meta,
    }


def _minute_factor(code: str, now: datetime | None = None, *, market_is_open: bool | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(CN_TZ)
    if market_is_open is False or now.weekday() >= 5:
        return {"status": "MARKET_CLOSED", "rows": 0, "quality": "UNKNOWN",
                **_minute_freshness(now - timedelta(days=1), now=now, market_is_open=False)}
    minute_bucket = now.strftime("%Y%m%d%H%M")
    rows, status = _safe_call("rt_min_daily", f"rtmin:{code}:{minute_bucket}", CONFIG.realtime_cache_seconds,
                              ts_code=_ts_code(code), freq="1MIN")
    if not rows:
        rows, status2 = _safe_call("rt_min", f"rtmin2:{code}:{minute_bucket}", CONFIG.realtime_cache_seconds,
                                   ts_code=_ts_code(code), freq="1MIN")
        if rows:
            status = status2
    timed = [(t, r) for r in rows if (t := _row_time(r)) is not None]
    timed.sort(key=lambda item: item[0])
    latest_day = timed[-1][0].date() if timed else None
    day_items = [(t, r) for t, r in timed if t.date() == latest_day] if latest_day else []
    ordered = [r for _, r in day_items]
    meta = _minute_freshness(day_items[-1][0] if day_items else None, now=now, market_is_open=market_is_open)
    if status == "DATA_UNAVAILABLE":
        meta["freshness_status"] = "DATA_UNAVAILABLE"
    closes, amounts, highs, lows = [], [], [], []
    for r in ordered:
        try: closes.append(float(r.get("close") or r.get("price")))
        except Exception: pass
        try: amounts.append(float(r.get("amount") or r.get("turnover") or 0))
        except Exception: amounts.append(0.0)
        try: highs.append(float(r.get("high") or r.get("close") or r.get("price")))
        except Exception: pass
        try: lows.append(float(r.get("low") or r.get("close") or r.get("price")))
        except Exception: pass
    if len(closes) < 3:
        return {"status": status, "rows": len(ordered), "quality": "UNKNOWN", **meta}
    last = closes[-1]
    recent, prior = amounts[-3:], amounts[-6:-3] if len(amounts) >= 6 else []
    accel = (sum(recent) / max(sum(prior), 1e-9)) if prior else None
    peak = max(highs[-5:]) if highs else max(closes[-5:])
    pullback = (last / peak - 1) * 100 if peak else 0.0
    fresh = meta["freshness_status"] == "FRESH"
    blowoff = bool(fresh and accel is not None and accel >= 2.2 and pullback <= -1.2)
    return {"status": status, "rows": len(ordered), "last": last, "amount_accel_3v3": accel,
            "pullback_from_5m_peak_pct": pullback, "blowoff_reversal": blowoff,
            "quality": "BAD" if blowoff else ("OK" if fresh else "UNKNOWN"), **meta}


def _auction_factor(code: str, now: datetime | None = None) -> dict[str, Any]:
    """Strict 09:15-09:25 realtime auction adapter for Shadow only.

    ``stk_auction_o`` is never used as realtime auction evidence. ``stk_auction_tick``
    is accepted only after the shared strict validator confirms: current open trade
    date, exact requested code, inclusive 09:15:00-09:25:00 timestamp, price field
    and volume/amount field. Any uncertainty degrades to AUCTION_REALTIME_UNAVAILABLE.
    """
    from .auction_validation_v72 import validate_realtime_auction_ticks
    now = (now or _now()).astimezone(CN_TZ)
    unavailable = lambda reason, freshness="UNKNOWN": {
        "status":"AUCTION_REALTIME_UNAVAILABLE","freshness_status":freshness,
        "auction_realtime_available":False,"auction_tradeable":None,"auction_strength":"UNKNOWN",
        "auction_gap_pct":None,"auction_amount":None,"auction_volume":None,"auction_turnover":None,
        "auction_risk_reasons":[reason],"validation_error":reason,"data_time":None,
        "fetched_at":now.isoformat(timespec="seconds"),"age_seconds":None,"degraded_confirmation_allowed":True
    }
    # The purchased relay has been verified to provide this endpoint.  Preserve
    # an explicit environment kill switch, but do not silently degrade merely
    # because the optional flag is absent.
    if str(os.getenv("V72_REALTIME_AUCTION_TICK_ENABLED", "true")).lower() not in {"1","true","yes","on"}:
        return unavailable("实时竞价Tick未启用/未验证", "DATA_UNAVAILABLE")

    day = now.strftime("%Y%m%d")
    try:
        cal = DATA_LAYER.relay_call(
            "trade_cal", cache_key=f"auction_trade_cal:{day}", ttl_seconds=12*3600, priority=_ENRICH_PRIORITY,
            exchange="SSE", start_date=day, end_date=day
        )
        cal_rows = _records(cal)
        if not cal_rows:
            return unavailable("trade_cal为空，无法确认当日开市")
        flag = cal_rows[0].get("is_open")
        market_open_today = bool(flag == 1 or str(flag).strip().lower() in {"1","true"})
        if not market_open_today:
            return unavailable("trade_cal确认当日休市", "MARKET_CLOSED")
    except Exception as exc:
        return unavailable(f"trade_cal无法验证:{type(exc).__name__}", "DATA_UNAVAILABLE")

    requested = _ts_code(code)
    rows,status=_safe_call("stk_auction_tick",f"auction_tick:{requested}:{now:%Y%m%d%H%M}",5,
                           ts_code=requested,start_date=day,end_date=day)
    if status == "DATA_UNAVAILABLE":
        return unavailable("stk_auction_tick接口不可用", "DATA_UNAVAILABLE")

    # The seller relay may repeat identical ticks and may also return ordinary
    # post-auction rows. De-duplicate exact records; the strict validator below
    # is solely responsible for accepting the inclusive 09:15-09:25 window.
    unique=[];seen=set()
    for item in rows:
        key=(item.get('ts_code'),item.get('trade_time'),item.get('ts_ms'),item.get('price'),
             item.get('volume',item.get('vol')),item.get('amount'))
        if key in seen:continue
        seen.add(key);unique.append(item)
    validation = validate_realtime_auction_ticks(
        unique, expected_code=requested, now=now, market_open_today=market_open_today, parse_time=_row_time
    )
    if validation["realtime_status"] != "REALTIME_AVAILABLE":
        return unavailable(validation.get("validation_error") or "无有效实时竞价Tick")

    row = dict(validation["latest_row"] or {})
    dt = _row_time(row)
    def num(*keys):
        for k in keys:
            try:
                if row.get(k) not in (None,''): return float(row.get(k))
            except Exception:
                pass
        return None
    gap=num('gap_pct','pct_chg','change_pct'); amount=num('amount','auction_amount','turnover'); vol=num('vol','volume'); turnover=num('turnover_rate')
    tradeable=not bool(row.get('one_word_limit_up') or row.get('is_limit_up_locked') or row.get('cannot_buy'))
    risks=[]
    if gap is not None and gap>CONFIG.auction_max_gap_pct: risks.append('竞价高开幅度过大')
    if amount is not None and amount<CONFIG.auction_min_amount: risks.append('竞价金额过低')
    if not tradeable: risks.append('竞价显示不可交易')
    age=max(0,(now-dt).total_seconds()) if dt is not None else None
    return {"status":"OK","freshness_status":"FRESH","auction_realtime_available":True,
      "auction_tradeable":bool(tradeable and not risks),"auction_strength":"STRONG" if not risks else "WEAK",
      "auction_gap_pct":gap,"auction_amount":amount,"auction_volume":vol,"auction_turnover":turnover,
      "auction_risk_reasons":risks,"validation_error":"","data_time":dt.isoformat(timespec='seconds') if dt else None,
      "fetched_at":now.isoformat(timespec='seconds'),"age_seconds":age,"rows":validation["validated_rows"],
      "source":"stk_auction_tick","degraded_confirmation_allowed":False}


def _realtime_daily_factor(code: str, now: datetime | None = None, *, market_is_open: bool | None = None) -> dict[str, Any]:
    """Small rt_k snapshot used only by the Shadow confirmation layer."""
    now = (now or _now()).astimezone(CN_TZ)
    if market_is_open is False or now.weekday() >= 5:
        return {"status": "MARKET_CLOSED", "freshness_status": "MARKET_CLOSED",
                "data_time": None, "fetched_at": now.isoformat(timespec="seconds")}
    bucket = now.strftime("%Y%m%d%H%M")
    rows, status = _safe_call("rt_k", f"rtk:{code}:{bucket}", CONFIG.realtime_cache_seconds,
                              ts_code=_ts_code(code))
    if not rows:
        return {"status": status, "freshness_status": "DATA_UNAVAILABLE" if status == "DATA_UNAVAILABLE" else "UNKNOWN",
                "data_time": None, "fetched_at": now.isoformat(timespec="seconds")}
    row = rows[-1]
    def num(*keys):
        for key in keys:
            try:
                value = row.get(key)
                if value not in (None, ""):
                    return float(value)
            except Exception:
                continue
        return None
    data_time = _row_time(row)
    # Some realtime daily relays omit a timestamp. During an open session, a successful
    # rt_k response is current transport evidence but the exact data time remains UNKNOWN.
    meta = _minute_freshness(data_time, now=now, market_is_open=market_is_open) if data_time else {
        "data_time": None, "fetched_at": now.isoformat(timespec="seconds"), "age_seconds": None,
        "freshness_status": "UNKNOWN", "market_session": "UNKNOWN"}
    return {
        "status": status,
        "current_price": num("price", "close", "last", "current"),
        "amount": num("amount", "turnover", "成交额"),
        "volume": num("vol", "volume"),
        "bid_ask_spread_pct": num("bid_ask_spread_pct", "spread_pct"),
        "raw_fields": sorted(row.keys()),
        **meta,
    }

def _news_rows(api: str, code: str, now: datetime, ttl: int) -> tuple[list[dict[str, Any]], str]:
    start = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    src = os.getenv("V71_NEWS_SOURCE", "sina")
    kwargs = {"src": src, "start_date": start, "end_date": end}
    rows, status = _safe_call(api, f"{api}:{code}:{now:%Y%m%d%H}", ttl, **kwargs)
    # Some compatible relays support ts_code directly; client-side filtering below remains safe if not.
    return rows, status


def _news_factor(code: str, now: datetime | None = None, name: str = "") -> dict[str, Any]:
    now = (now or _now()).astimezone(CN_TZ)
    all_rows = []
    statuses = []
    for api in ("news", "major_news"):
        rows, status = _news_rows(api, code, now, CONFIG.news_cache_seconds)
        statuses.append(status)
        all_rows.extend((api, r) for r in rows)
    clean_code = code.split(".")[0]
    dedup, seen = [], set()
    for api, row in all_rows:
        title = str(row.get("title") or row.get("headline") or row.get("content") or "").strip()
        content = str(row.get("content") or row.get("summary") or "").strip()
        source = str(row.get("src") or row.get("source") or api).strip()
        text = f"{title} {content}"
        # If code/security field exists, honor it; otherwise retain only clearly mentioning the code/name text is unavailable here.
        row_code = str(row.get("ts_code") or row.get("code") or "")
        if row_code and clean_code not in row_code:
            continue
        if not row_code and clean_code not in text and not (name and name in text):
            continue
        key = (title[:120], source, str(row.get("datetime") or row.get("pub_time") or row.get("time") or ""))
        if not title or key in seen:
            continue
        seen.add(key)
        when = _row_time(row)
        risk_hits = [kw for kw in NEWS_RISK_KEYWORDS if kw in text]
        cat_hits = [kw for kw in NEWS_CATALYST_KEYWORDS if kw in text]
        dedup.append({"title": title[:180], "source": source[:80],
                      "time": when.isoformat(timespec="seconds") if when else None,
                      "risk_hits": risk_hits, "catalyst_hits": cat_hits})
    times = [_parse_time(r.get("time")) for r in dedup]
    latest = max((x for x in times if x), default=None)
    risk = sorted({x for r in dedup for x in r["risk_hits"]})
    catalysts = sorted({x for r in dedup for x in r["catalyst_hits"]})
    freshness = _freshness(latest, max_age_seconds=3 * 86400, now=now)
    if all(s == "DATA_UNAVAILABLE" for s in statuses):
        freshness["freshness_status"] = "DATA_UNAVAILABLE"
    sentiment = "NEGATIVE" if risk else ("POSITIVE" if catalysts else "NEUTRAL")
    risk_level = "HIGH" if any(k in risk for k in ("立案", "退市", "处罚")) else ("MEDIUM" if risk else "LOW")
    return {"status": "OK" if dedup else ("DATA_UNAVAILABLE" if all(s == "DATA_UNAVAILABLE" for s in statuses) else "EMPTY"),
            "news_sentiment": sentiment, "news_risk_level": risk_level, "news_catalysts": catalysts,
            "news_risk_reasons": risk, "news_latest_time": freshness.get("data_time"),
            "news_source_count": len({r["source"] for r in dedup}), "items": dedup[:12], **freshness}


def _report_factor(code: str, now: datetime | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(CN_TZ)
    start = (now - timedelta(days=30)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    rows, status = _safe_call("report_rc", f"report:{code}:{now:%Y%m%d}", 6 * 3600,
                              ts_code=_ts_code(code), start_date=start, end_date=end)
    valid = []
    for row in rows:
        when = _row_time(row)
        if when and (now.date() - when.date()).days <= 30:
            valid.append((when, row))
    valid.sort(key=lambda x: x[0])
    latest = valid[-1][0] if valid else None
    ratings = [str(r.get("rating") or r.get("recommend") or r.get("rating_name") or "") for _, r in valid]
    eps = []
    for _, r in valid:
        for k in ("eps", "eps1", "eps2", "np", "profit_forecast"):
            try:
                if r.get(k) not in (None, ""):
                    eps.append(float(r.get(k))); break
            except Exception:
                pass
    rating_change = "UNKNOWN"
    if len(ratings) >= 2 and ratings[-1] != ratings[0]:
        rating_change = f"{ratings[0]}->{ratings[-1]}"
    profit_trend = "UNKNOWN"
    if len(eps) >= 2:
        profit_trend = "UP" if eps[-1] > eps[0] else ("DOWN" if eps[-1] < eps[0] else "FLAT")
    meta = _freshness(latest, max_age_seconds=30 * 86400, now=now)
    if status == "DATA_UNAVAILABLE": meta["freshness_status"] = "DATA_UNAVAILABLE"
    return {"status": status, "report_count_30d": len(valid), "latest_report_date": meta.get("data_time"),
            "rating_change": rating_change, "profit_forecast_trend": profit_trend,
            "auxiliary_only": True, **meta}


def _chip_factor(code: str, now: datetime | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(CN_TZ)
    end = now.strftime("%Y%m%d")
    start = (now - timedelta(days=20)).strftime("%Y%m%d")
    rows, status = _safe_call("cyq_perf", f"cyq:{code}:{end}", 24 * 3600,
                              ts_code=_ts_code(code), start_date=start, end_date=end)
    if not rows:
        meta = _freshness(None, max_age_seconds=10 * 86400, now=now)
        if status == "DATA_UNAVAILABLE": meta["freshness_status"] = "DATA_UNAVAILABLE"
        return {"status": status, **meta}
    rows_sorted = sorted(rows, key=lambda r: str(r.get("trade_date") or ""))
    row = rows_sorted[-1]
    data_time = _row_time(row)
    meta = _freshness(data_time, max_age_seconds=5 * 86400, now=now)
    vals = []
    for r in rows_sorted[-5:]:
        for k in ("weight_avg", "cost_50pct"):
            try:
                if r.get(k) not in (None, ""):
                    vals.append(float(r.get(k))); break
            except Exception: pass
    concentration = "UNKNOWN"
    if len(vals) >= 2:
        # Falling representative cost dispersion/center is not concentration itself, so label only trend evidence.
        concentration = "STABLE" if abs(vals[-1] - vals[0]) / max(abs(vals[0]), 1e-9) < 0.02 else "CHANGING"
    return {"status": status, "trade_date": row.get("trade_date"), "winner_rate": row.get("winner_rate"),
            "cost_50pct": row.get("cost_50pct"), "weight_avg": row.get("weight_avg"),
            "chip_concentration_trend": concentration, "reference_only": meta.get("freshness_status") != "FRESH", **meta}


def _macro_factor(now: datetime | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(CN_TZ)
    start = (now - timedelta(days=10)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    rows, status = _safe_call("us_tbr", f"us_tbr:{now:%Y%m%d}", 24 * 3600, start_date=start, end_date=end)
    if not rows:
        return {"status": status, "macro_risk": "UNKNOWN"}
    row = rows[-1]
    # Do not invent a directional macro model from relay fields whose semantics have not been verified.
    # Keep the raw latest observation as market-environment evidence and remain UNKNOWN until a frozen rule is approved.
    return {"status": status, "macro_risk": "UNKNOWN", "latest": dict(row), "individual_score_effect": 0,
            "note": "宏观接口已取到数据，但未启用未经验证的方向评分"}


def enrich_candidate(code: str, *, now: datetime | None = None, include_auxiliary: bool = True,
                     name: str = "",
                     market_is_open: bool | None = None, priority: str = "normal") -> dict[str, Any]:
    """Fail-open enrichment snapshot for shortlisted V7.1 Shadow candidates.

    Missing datasets remain UNKNOWN/DATA_UNAVAILABLE and never become negative points. Hard risk decisions
    require explicit, recent evidence. Auction/minute UNKNOWN may block BUY confirmation in the state machine,
    but does not alter the V6.6 formal candidate or formal push.
    """
    if not (CONFIG.shadow_enabled and CONFIG.paid_provider_enabled):
        return {"status": "DISABLED"}
    global _ENRICH_PRIORITY
    now = now or _now()
    previous_priority = _ENRICH_PRIORITY
    _ENRICH_PRIORITY = priority
    out = {
        "announcement": _announcement_factor(code, now),
        "realtime_minute": _minute_factor(code, now, market_is_open=market_is_open),
        "realtime_daily": _realtime_daily_factor(code, now, market_is_open=market_is_open),
        "chip_cost": _chip_factor(code, now),
        "auction": _auction_factor(code, now),
        "news": _news_factor(code, now, name=name),
    }
    if include_auxiliary:
        out["report"] = _report_factor(code, now)
        out["macro"] = _macro_factor(now)
    _ENRICH_PRIORITY = previous_priority
    return out


__all__ = [
    "enrich_candidate", "_parse_time", "_row_time", "_freshness", "_minute_freshness",
    "_announcement_freshness", "_announcement_factor", "_minute_factor", "_auction_factor", "_realtime_daily_factor",
    "_news_factor", "_report_factor", "_chip_factor", "_macro_factor",
]
