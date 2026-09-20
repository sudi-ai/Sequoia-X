"""Strict V8 09:15-09:25 auction assessment.

Only fields actually provided by stk_auction_tick are used.  Unmatched orders
and cancellations remain UNKNOWN because the purchased relay does not expose them.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, time as dtime
from typing import Any, Mapping

from .storage import connect, initialize

START = dtime(9, 15, 0)
END = dtime(9, 25, 10)


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _records(frame: Any) -> list[dict[str, Any]]:
    if hasattr(frame, "to_dict"):
        try:
            return list(frame.to_dict("records"))
        except Exception:
            return []
    return list(frame) if isinstance(frame, list) else []


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def assess_rows(raw: Any, *, code: str, trade_date: str, previous_close: float | None,
                previous_amount_yuan: float | None, assessed_at: datetime | None = None) -> dict[str, Any]:
    assessed_at = assessed_at or datetime.now().astimezone()
    expected = code.upper()
    unique: dict[tuple[Any, ...], tuple[datetime, dict[str, Any]]] = {}
    for row in _records(raw):
        dt = _parse_time(row.get("trade_time") or row.get("datetime") or row.get("time"))
        row_code = str(row.get("ts_code") or row.get("code") or "").upper()
        price = _f(row.get("price") if row.get("price") is not None else row.get("close"))
        volume = _f(row.get("volume") if row.get("volume") is not None else row.get("vol"), 0) or 0
        amount = _f(row.get("amount"), 0) or 0
        if not dt or dt.strftime("%Y%m%d") != trade_date or not (START <= dt.time() <= END):
            continue
        if row_code != expected or price is None or price <= 0:
            continue
        key = (row_code, dt.isoformat(), price, volume, amount)
        unique[key] = (dt, dict(row))
    ordered = sorted(unique.values(), key=lambda x: x[0])
    if not ordered:
        return {"status": "UNKNOWN", "quality_score": 50.0, "risk_level": "UNKNOWN",
                "reasons": ["没有通过日期、代码和09:15—09:25窗口校验的竞价Tick"],
                "data_limitations": ["竞价未匹配量与撤单量接口未提供"]}
    prices = [_f(row.get("price") if row.get("price") is not None else row.get("close"), 0) or 0 for _, row in ordered]
    final_dt, final_row = ordered[-1]
    final_price = prices[-1]
    final_amount = _f(final_row.get("amount"), 0) or 0
    final_volume = _f(final_row.get("volume") if final_row.get("volume") is not None else final_row.get("vol"), 0) or 0
    # Some relays expose amount only on the 09:25 uncrossing row.
    if final_amount <= 0:
        final_amount = max((_f(row.get("amount"), 0) or 0) for _, row in ordered)
    gap = (final_price / previous_close - 1) * 100 if previous_close and previous_close > 0 else None
    peak = max(prices)
    peak_fade = (final_price / peak - 1) * 100 if peak else None
    last_minute = [(_dt, row) for _dt, row in ordered if _dt.time() >= dtime(9, 24, 0)]
    last_start = _f(last_minute[0][1].get("price"), final_price) if last_minute else final_price
    last_change = (final_price / last_start - 1) * 100 if last_start else None
    amount_ratio = final_amount / previous_amount_yuan * 100 if previous_amount_yuan and previous_amount_yuan > 0 else None
    mean = sum(prices) / len(prices)
    volatility = (sum((x-mean)**2 for x in prices) / len(prices)) ** .5 / mean * 100 if mean else 0
    reasons: list[str] = []
    risk = "LOW"
    score = 60.0
    if gap is None:
        reasons.append("昨收数据缺失，高开幅度待确认")
    elif gap > 8:
        score -= 35; risk = "HIGH"; reasons.append(f"竞价高开{gap:.2f}%，追高风险高")
    elif gap > 5:
        score -= 18; risk = "MEDIUM"; reasons.append(f"竞价高开{gap:.2f}%，需等待回踩")
    elif -.5 <= gap <= 4:
        score += 10; reasons.append(f"竞价涨幅{gap:.2f}%，处于可观察区间")
    elif gap < -3:
        score -= 15; risk = "MEDIUM"; reasons.append(f"竞价低开{gap:.2f}%")
    if peak_fade is not None and peak_fade <= -2:
        score -= 25; risk = "HIGH" if peak_fade <= -3.5 else "MEDIUM"; reasons.append(f"竞价峰值回落{peak_fade:.2f}%")
    elif peak_fade is not None and peak_fade >= -.8:
        score += 8; reasons.append("竞价价格保持较稳定")
    if last_change is not None and last_change <= -1.2:
        score -= 18; risk = "MEDIUM" if risk == "LOW" else risk; reasons.append(f"最后一分钟转弱{last_change:.2f}%")
    elif last_change is not None and last_change >= .3:
        score += 8; reasons.append(f"最后一分钟转强{last_change:.2f}%")
    if final_amount < 2_000_000:
        score -= 15; risk = "MEDIUM" if risk == "LOW" else risk; reasons.append("最终竞价成交额低于200万元")
    elif final_amount >= 20_000_000:
        score += 8; reasons.append(f"最终竞价成交额{final_amount/100000000:.2f}亿元")
    if amount_ratio is not None:
        if amount_ratio >= 2:
            score += 8; reasons.append(f"竞价额占昨日成交额{amount_ratio:.2f}%")
        elif amount_ratio < .15:
            score -= 8; reasons.append(f"竞价额占昨日成交额仅{amount_ratio:.2f}%")
    if volatility > 2.5:
        score -= 12; risk = "MEDIUM" if risk == "LOW" else risk; reasons.append(f"竞价价格波动{volatility:.2f}%")
    score = round(max(0, min(100, score)), 2)
    if score < 40 and risk == "LOW":
        risk = "MEDIUM"
    return {"status": "OK", "quality_score": score, "risk_level": risk, "final_price": final_price,
            "final_amount": final_amount, "final_volume": final_volume, "gap_pct": gap,
            "amount_ratio_pct": amount_ratio, "peak_fade_pct": peak_fade,
            "last_minute_change_pct": last_change, "price_volatility_pct": volatility,
            "tick_count": len(ordered), "data_time": final_dt.isoformat(timespec="seconds"),
            "reasons": reasons, "data_limitations": ["接口未提供未匹配委托量和撤单量，相关指标保持待确认"]}


def fetch_assessment(code: str, name: str, now: datetime, previous_close: float | None,
                     previous_amount_yuan: float | None) -> dict[str, Any]:
    from .independent_discovery import _ts_code
    from .paid_data import PAID_DATA
    day = now.strftime("%Y%m%d")
    try:
        frame = PAID_DATA.call("stk_auction_tick", cache_key=f"v8:auction:{code}:{day}", ttl_seconds=6*3600,
                               priority="CHECKPOINT",
                               ts_code=_ts_code(code), start_date=day, end_date=day)
        try:
            from .paid_month_archive import archive_rows
            archive_rows("stk_auction_tick", frame, fetched_at=now)
        except Exception:
            pass
        result = assess_rows(frame, code=_ts_code(code), trade_date=day, previous_close=previous_close,
                             previous_amount_yuan=previous_amount_yuan, assessed_at=now)
    except Exception as exc:
        result = {"status": "UNKNOWN", "quality_score": 50.0, "risk_level": "UNKNOWN",
                  "reasons": [f"竞价接口暂不可用：{type(exc).__name__}"],
                  "data_limitations": ["竞价数据缺失按中性处理，不阻塞盘中独立扫描"]}
    save_assessment(code, name, now, result)
    return result


def save_assessment(code: str, name: str, now: datetime, result: Mapping[str, Any]) -> None:
    initialize(); db = connect()
    db.execute("""INSERT INTO v8_auction_assessments(trade_date,code,name,assessed_at,status,quality_score,gap_pct,
      final_price,final_amount,amount_ratio_pct,peak_fade_pct,last_minute_change_pct,tick_count,risk_level,reasons_json,
      details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,code) DO UPDATE SET
      assessed_at=excluded.assessed_at,status=excluded.status,quality_score=excluded.quality_score,gap_pct=excluded.gap_pct,
      final_price=excluded.final_price,final_amount=excluded.final_amount,amount_ratio_pct=excluded.amount_ratio_pct,
      peak_fade_pct=excluded.peak_fade_pct,last_minute_change_pct=excluded.last_minute_change_pct,
      tick_count=excluded.tick_count,risk_level=excluded.risk_level,reasons_json=excluded.reasons_json,
      details_json=excluded.details_json""", (now.date().isoformat(),code,name,now.isoformat(timespec="seconds"),
      result.get("status","UNKNOWN"),result.get("quality_score"),result.get("gap_pct"),result.get("final_price"),
      result.get("final_amount"),result.get("amount_ratio_pct"),result.get("peak_fade_pct"),
      result.get("last_minute_change_pct"),result.get("tick_count"),result.get("risk_level","UNKNOWN"),
      json.dumps(result.get("reasons") or [],ensure_ascii=False),json.dumps(dict(result),ensure_ascii=False,default=str)))
    db.commit(); db.close()


def load_assessment(code: str, now: datetime) -> dict[str, Any] | None:
    initialize(); db=connect()
    row=db.execute("select details_json from v8_auction_assessments where trade_date=? and code=?",
                   (now.date().isoformat(),code)).fetchone(); db.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def build_summary(items: list[tuple[str,str,Mapping[str,Any]]]) -> str:
    lines=["🌅 A股机会雷达 V8｜独立竞价复核","",f"复核 {len(items)}只｜仅用于开盘候选预审"]
    risk_cn={"LOW":"低","MEDIUM":"中等","HIGH":"高","UNKNOWN":"待确认"}
    risk_icon={"LOW":"🟢","MEDIUM":"🟡","HIGH":"🔴","UNKNOWN":"⚪"}
    for index,(code,name,item) in enumerate(sorted(items,key=lambda x: float(x[2].get('quality_score') or 0),reverse=True),1):
        gap_value=item.get("gap_pct")
        if gap_value is None:gap_text="开盘幅度待确认"
        elif float(gap_value)>.15:gap_text=f"高开 +{float(gap_value):.2f}%"
        elif float(gap_value)<-.15:gap_text=f"低开 {abs(float(gap_value)):.2f}%"
        else:gap_text=f"接近平开 {float(gap_value):+.2f}%"
        risk=str(item.get('risk_level') or 'UNKNOWN').upper()
        score=item.get('quality_score','-')
        try:score=f"{float(score):.0f}"
        except (TypeError,ValueError):pass
        lines += ["",f"{index}. {name} {code}",
          f"   竞价质量 {score}｜{gap_text}",f"   {risk_icon.get(risk,'⚪')} 风险 {risk_cn.get(risk,'待确认')}"]
        if item.get("candidate_source"):
            lines.append(f"   候选来源：{item.get('candidate_source')}")
        reasons=item.get("reasons") or []
        if reasons: lines.append("   依据：" + "；".join(str(x) for x in reasons[:2]))
    lines += ["","👉 操作","竞价强只能进入优先观察；仍需09:30后市场、板块、分钟资金和持续度共同确认，禁止按竞价直接买入。",
              "⚠ 未匹配量和撤单量接口未提供，系统不会伪造判断。",
              "V8研究验证阶段｜不自动下单｜不构成投资建议"]
    return "\n".join(lines)
