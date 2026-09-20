"""独立策略研究因子。

这些因子只提供可复盘的 Shadow 标签，不参与 V8 正式确认分数、风控否决或下单。
实现为 V8 自有代码，不复制外部仓库实现。
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import CONFIG, ROOT


FACTOR_VERSION = "V8.4_STRATEGY_SHADOW_2"
PROFILE_FILE = ROOT / "data_v8" / "v8_stock_profiles.json"
_RPS_CACHE: dict[str, Any] = {}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _rows(value: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in value), key=lambda row: str(row.get("trade_date") or ""))


def _percentile(value: float, values: list[float]) -> float | None:
    clean = sorted(number for number in values if math.isfinite(number))
    if len(clean) < 2:
        return None
    below = sum(number < value for number in clean)
    equal = sum(number == value for number in clean)
    return round((below + max(0, equal - 1) / 2) / (len(clean) - 1) * 100, 2)


def _limit_pct(code: str, name: str) -> float:
    upper = str(name or "").upper()
    if "ST" in upper:
        return 5.0
    clean = str(code).split(".")[0].zfill(6)
    if clean.startswith(("4", "8", "92")):
        return 30.0
    if clean.startswith(("300", "301", "688")):
        return 20.0
    return 10.0


def high_tight_flag(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """识别“先上涨、后缩量收敛”的蓄势结构，避免仅按窗口高低点误判暴跌。"""
    bars = _rows(rows)
    if len(bars) < 50:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "历史日线不足50根"}
    impulse = bars[-50:-10]
    base = bars[-10:]
    lows = [_num(row.get("low")) for row in impulse]
    highs = [_num(row.get("high")) for row in impulse]
    if any(value is None or value <= 0 for value in lows + highs):
        return {"status": "UNKNOWN", "score": 50.0, "reason": "历史高低价缺失"}
    low_index = min(range(len(lows)), key=lambda index: float(lows[index]))
    later = [(index, float(highs[index])) for index in range(low_index + 5, len(highs))]
    if not later:
        return {"status": "NOT_TRIGGERED", "score": 20.0, "reason": "未形成先低后高的上升段"}
    peak_index, peak = max(later, key=lambda item: item[1])
    impulse_low = float(lows[low_index])
    gain_pct = (peak / impulse_low - 1) * 100
    base_highs = [_num(row.get("high")) for row in base]
    base_lows = [_num(row.get("low")) for row in base]
    closes = [_num(row.get("close")) for row in base]
    if any(value is None or value <= 0 for value in base_highs + base_lows + closes):
        return {"status": "UNKNOWN", "score": 50.0, "reason": "整理期价格缺失"}
    base_high = max(float(value) for value in base_highs)
    base_low = min(float(value) for value in base_lows)
    width_pct = (base_high / base_low - 1) * 100
    floor_ratio = base_low / peak
    close_location = (float(closes[-1]) - base_low) / max(base_high - base_low, 1e-9)
    old_volumes = [_num(row.get("vol")) for row in bars[-30:-10]]
    new_volumes = [_num(row.get("vol")) for row in base]
    old_volume = sum(value for value in old_volumes if value is not None) / max(1, sum(value is not None for value in old_volumes))
    new_volume = sum(value for value in new_volumes if value is not None) / max(1, sum(value is not None for value in new_volumes))
    volume_ratio = new_volume / old_volume if old_volume > 0 else None
    old_ranges = [
        (float(high) - float(low)) / max(float(close), 1e-9)
        for high, low, close in zip(
            [_num(row.get("high")) for row in bars[-30:-10]],
            [_num(row.get("low")) for row in bars[-30:-10]],
            [_num(row.get("close")) for row in bars[-30:-10]],
        )
        if high is not None and low is not None and close is not None and close > 0
    ]
    new_ranges = [
        (float(high) - float(low)) / max(float(close), 1e-9)
        for high, low, close in zip(base_highs, base_lows, closes)
    ]
    contraction = (
        (sum(new_ranges) / len(new_ranges)) / (sum(old_ranges) / len(old_ranges))
        if old_ranges and new_ranges and sum(old_ranges) > 0 else None
    )
    checks = {
        "先涨后整": gain_pct >= 45 and peak_index > low_index,
        "整理紧密": width_pct <= 15,
        "守住高位": floor_ratio >= 0.82,
        "成交缩量": volume_ratio is not None and volume_ratio <= 0.72,
        "波动收敛": contraction is not None and contraction <= 0.82,
        "收盘偏强": close_location >= 0.45,
    }
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    status = "TRIGGERED" if all(checks.values()) else "NOT_TRIGGERED"
    return {
        "status": status, "score": score,
        "reason": "先上涨后缩量收敛，形成启动前蓄势" if status == "TRIGGERED" else "蓄势条件尚未完整",
        "gain_pct": round(gain_pct, 2), "base_width_pct": round(width_pct, 2),
        "base_floor_to_peak": round(floor_ratio, 4),
        "volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
        "range_contraction": round(contraction, 4) if contraction is not None else None,
        "close_location": round(close_location, 4), "checks": checks,
    }


def limit_up_shakeout(code: str, name: str, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """按证券板块涨停制度识别涨停后换手承接；只做研究标签。"""
    bars = _rows(rows)
    if len(bars) < 3:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "历史日线不足3根"}
    before, limit_bar, current = bars[-3], bars[-2], bars[-1]
    before_close = _num(before.get("close"))
    limit_close = _num(limit_bar.get("close"))
    open_price = _num(current.get("open"))
    close = _num(current.get("close"))
    low = _num(current.get("low"))
    current_vol = _num(current.get("vol"))
    limit_vol = _num(limit_bar.get("vol"))
    if None in (before_close, limit_close, open_price, close, low, current_vol, limit_vol) or before_close <= 0:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "涨停承接所需字段缺失"}
    limit_pct = _limit_pct(code, name)
    actual_gain = (limit_close / before_close - 1) * 100
    tolerance = 0.45 if limit_pct >= 20 else 0.3
    checks = {
        "前日涨停": actual_gain >= limit_pct - tolerance,
        "当日换手": current_vol >= limit_vol * 1.35,
        "收阴换手": close < open_price,
        "守住涨停基准": low >= limit_close * 0.985,
        "未深度回落": close >= limit_close * 0.99,
    }
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    status = "TRIGGERED" if all(checks.values()) else "NOT_TRIGGERED"
    return {
        "status": status, "score": score,
        "reason": "涨停后放量换手但关键承接仍在" if status == "TRIGGERED" else "涨停后承接条件尚未完整",
        "board_limit_pct": limit_pct, "actual_limit_day_gain_pct": round(actual_gain, 2),
        "volume_ratio": round(current_vol / max(limit_vol, 1e-9), 4), "checks": checks,
    }


def relative_strength(candidate_return: float | None, market_values: list[float],
                      sector_values: list[float]) -> dict[str, Any]:
    if candidate_return is None:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "缺少120日收益"}
    market_pct = _percentile(candidate_return, market_values) if len(market_values) >= 20 else None
    sector_pct = _percentile(candidate_return, sector_values) if len(sector_values) >= 5 else None
    if market_pct is None:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "横截面样本尚未积累完成",
                "return_120d_pct": round(candidate_return, 2), "market_sample": len(market_values),
                "sector_sample": len(sector_values)}
    score = market_pct if sector_pct is None else round(market_pct * 0.6 + sector_pct * 0.4, 2)
    status = "TRIGGERED" if score >= 90 else "NOT_TRIGGERED"
    return {"status": status, "score": score,
            "reason": "个股120日强度位于市场及行业前列" if status == "TRIGGERED" else "相对强度未进入前列",
            "return_120d_pct": round(candidate_return, 2), "market_percentile": market_pct,
            "sector_percentile": sector_pct, "market_sample": len(market_values),
            "sector_sample": len(sector_values)}


def turtle_breakout(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """20-day breakout with liquidity and close-location checks."""
    bars = _rows(rows)
    if len(bars) < 22:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "历史日线不足22根"}
    current = bars[-1]
    close = _num(current.get("close")); high = _num(current.get("high")); low = _num(current.get("low"))
    previous_highs = [_num(row.get("high")) for row in bars[-21:-1]]
    amount = _num(current.get("amount"))
    if close is None or high is None or low is None or any(value is None for value in previous_highs):
        return {"status": "UNKNOWN", "score": 50.0, "reason": "突破字段缺失"}
    range_size = max(high - low, 1e-9)
    checks = {
        "20日突破": close > max(float(value) for value in previous_highs),
        "收盘偏强": (close - low) / range_size >= .65,
        "流动性达标": amount is None or amount * 1000 >= 100_000_000,
    }
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    status = "TRIGGERED" if all(checks.values()) else "NOT_TRIGGERED"
    return {"status": status, "score": score, "reason": "20日放量突破" if status == "TRIGGERED" else "突破条件未完整",
            "checks": checks, "breakout_level": round(max(float(value) for value in previous_highs), 4)}


def ma_volume_breakout(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Trend/volume consensus instead of a price-only moving-average cross."""
    bars = _rows(rows)
    if len(bars) < 61:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "历史日线不足61根"}
    closes = [_num(row.get("close")) for row in bars]
    volumes = [_num(row.get("vol")) for row in bars]
    if any(value is None or value <= 0 for value in closes[-61:]) or any(value is None for value in volumes[-21:]):
        return {"status": "UNKNOWN", "score": 50.0, "reason": "均线或成交量缺失"}
    close = float(closes[-1]); ma20 = sum(float(value) for value in closes[-20:]) / 20
    ma60 = sum(float(value) for value in closes[-60:]) / 60
    base_volume = sum(float(value) for value in volumes[-21:-1]) / 20
    volume_ratio = float(volumes[-1]) / max(base_volume, 1e-9)
    checks = {"多头均线": close > ma20 > ma60, "成交放大": volume_ratio >= 1.35,
              "未过度乖离": close / ma20 <= 1.08}
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    status = "TRIGGERED" if all(checks.values()) else "NOT_TRIGGERED"
    return {"status": status, "score": score, "reason": "均线多头与成交放大共振" if status == "TRIGGERED" else "均线放量共振未完整",
            "ma20": round(ma20, 4), "ma60": round(ma60, 4), "volume_ratio": round(volume_ratio, 4), "checks": checks}


def volatility_contraction(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Low-volatility contraction with trend support; useful before ignition."""
    bars = _rows(rows)
    if len(bars) < 45:
        return {"status": "UNKNOWN", "score": 50.0, "reason": "历史日线不足45根"}
    ranges = []
    closes = []
    for row in bars[-45:]:
        high = _num(row.get("high")); low = _num(row.get("low")); close = _num(row.get("close"))
        if high is None or low is None or close is None or close <= 0:
            return {"status": "UNKNOWN", "score": 50.0, "reason": "波动字段缺失"}
        ranges.append((high - low) / close); closes.append(close)
    old = sum(ranges[-30:-10]) / 20; new = sum(ranges[-10:]) / 10
    ratio = new / max(old, 1e-9)
    ma20 = sum(closes[-20:]) / 20
    drawdown = (closes[-1] / max(closes[-20:]) - 1) * 100
    checks = {"波动收缩": ratio <= .72, "趋势未破": closes[-1] >= ma20, "回撤可控": drawdown >= -8}
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    status = "TRIGGERED" if all(checks.values()) else "NOT_TRIGGERED"
    return {"status": status, "score": score, "reason": "趋势内波动明显收缩" if status == "TRIGGERED" else "波动收缩条件未完整",
            "range_ratio": round(ratio, 4), "drawdown_20d_pct": round(drawdown, 2), "checks": checks}


def momentum_quality(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Medium-term strength with a short-term overheat guard."""
    bars = _rows(rows)
    closes = [_num(row.get("close")) for row in bars]
    if len(closes) < 61 or any(value is None or value <= 0 for value in closes[-61:]):
        return {"status": "UNKNOWN", "score": 50.0, "reason": "动量历史不足"}
    current = float(closes[-1])
    ret5 = (current / float(closes[-6]) - 1) * 100
    ret20 = (current / float(closes[-21]) - 1) * 100
    ret60 = (current / float(closes[-61]) - 1) * 100
    checks = {"20日动量为正": ret20 > 3, "60日动量为正": ret60 > 8,
              "短期未过热": -3 <= ret5 <= 10, "动量不反转": ret20 <= ret60 + 25}
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    status = "TRIGGERED" if all(checks.values()) else "NOT_TRIGGERED"
    return {"status": status, "score": score, "reason": "中期强度保持且短期未过热" if status == "TRIGGERED" else "动量质量未完整",
            "return_5d_pct": round(ret5, 2), "return_20d_pct": round(ret20, 2),
            "return_60d_pct": round(ret60, 2), "checks": checks}


def _profile_industries() -> dict[str, str]:
    try:
        payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        return {str(code).split(".")[0].zfill(6): str(row.get("industry") or "行业待确认")
                for code, row in (payload.get("profiles") or {}).items()}
    except Exception:
        return {}


def _archive_returns(path: Path | None = None) -> dict[str, float]:
    """从V8已归档日线构建横截面；样本不足时保持UNKNOWN，不额外抢占接口。"""
    from .storage import connect, initialize
    cached_at=float(_RPS_CACHE.get("loaded_at") or 0)
    if _RPS_CACHE.get("returns") is not None and time.monotonic()-cached_at<900:
        return dict(_RPS_CACHE.get("returns") or {})
    initialize(path); db = connect(path)
    signature_row=db.execute("SELECT COUNT(*) n,MAX(trade_date) latest FROM v8_daily_bars").fetchone()
    signature=f"{signature_row['n']}|{signature_row['latest']}"
    rows = db.execute("""SELECT ts_code,trade_date,close FROM v8_daily_bars
      WHERE close IS NOT NULL ORDER BY ts_code,trade_date""").fetchall()
    db.close()
    grouped: dict[str, list[float]] = {}
    for row in rows:
        close = _num(row["close"])
        if close is not None and close > 0:
            grouped.setdefault(str(row["ts_code"]).split(".")[0].zfill(6), []).append(close)
    result={code: (values[-1] / values[-121] - 1) * 100
            for code, values in grouped.items() if len(values) >= 121 and values[-121] > 0}
    _RPS_CACHE.update({"signature":signature,"returns":result,"loaded_at":time.monotonic()})
    return result


def evaluate_all(code: str, name: str, industry: str, rows: Iterable[Mapping[str, Any]],
                 *, path: Path | None = None) -> dict[str, dict[str, Any]]:
    bars = _rows(rows)
    own_return = None
    closes = [_num(row.get("close")) for row in bars]
    closes = [value for value in closes if value is not None and value > 0]
    if len(closes) >= 121:
        own_return = (closes[-1] / closes[-121] - 1) * 100
    returns = _archive_returns(path)
    if own_return is not None:
        returns[str(code).split(".")[0].zfill(6)] = own_return
    industries = _profile_industries()
    sector_values = [value for symbol, value in returns.items() if industries.get(symbol) == industry]
    factors = {
        "RPS_120_SECTOR_NEUTRAL": relative_strength(own_return, list(returns.values()), sector_values),
        "HIGH_TIGHT_FLAG_REFINED": high_tight_flag(bars),
        "LIMITUP_SHAKEOUT_REFINED": limit_up_shakeout(code, name, bars),
        "TURTLE_BREAKOUT_LIQUID": turtle_breakout(bars),
        "MA_VOLUME_CONSENSUS": ma_volume_breakout(bars),
        "VOLATILITY_CONTRACTION": volatility_contraction(bars),
        "MOMENTUM_QUALITY_GUARDED": momentum_quality(bars),
    }
    for value in factors.values():
        value["factor_version"] = FACTOR_VERSION
        value["action_permission"] = "ANNOTATION_ONLY"
    return factors


def persist(event: Mapping[str, Any], factors: Mapping[str, Mapping[str, Any]], *,
            checkpoint_minute: int, entry_price: float | None, path: Path | None = None) -> dict[str, int]:
    """保存因子及独立Forward样本；绝不把因子升级成正式确认。"""
    if not CONFIG.strategy_shadow_enabled:
        return {"saved": 0, "registered": 0}
    from .storage import connect, save_decision, save_strategy_factor_shadow
    saved = registered = 0
    for factor_name, payload in factors.items():
        save_strategy_factor_shadow(event, factor_name, checkpoint_minute, payload, path=path)
        saved += 1
        # Register every observed score, not only triggers.  The complete
        # cross-section is required for IC/quantile/decay research.  All rows
        # remain zero-position Shadow decisions.
        if not entry_price:
            continue
        factor_key = f"{event['event_key']}:{factor_name}"
        factor_event = {**dict(event), "event_key": factor_key,
                        "strategy_version": FACTOR_VERSION,
                        "source": "V8_STRATEGY_FACTOR_SHADOW",
                        "executable_price": float(entry_price)}
        score = float(payload.get("score") or 50)
        decision = {"action": "FACTOR_SHADOW", "opportunity_score": score,
                    "market": {"score": 50, "label": "仅记录"},
                    "sector": {"score": 50, "label": "仅记录"},
                    "trend": {"score": score, "label": factor_name},
                    "fund": {"score": 50, "label": "未用于因子"},
                    "persistence": {"score": 50, "label": "待Forward验证"},
                    "risk_score": 0, "position_pct": 0, "reasons": [str(payload.get("reason") or "")],
                    "vetoes": [], "shadow_only": True, "action_permission": "ANNOTATION_ONLY"}
        save_decision(factor_event, decision, path=path)
        # Factor research needs medium-horizon labels only.  Registering all
        # 14 formal/intraday horizons here would multiply background paid-data
        # settlement work without improving the IC/quantile study.
        db = connect(path)
        for horizon in ("D1", "D3", "D5"):
            cursor = db.execute("""insert or ignore into v8_execution_outcomes
              (event_key,track,horizon,matured,entry_price,details_json)
              values(?,?,?,?,?,?)""",
              (factor_key, "FIXED", horizon, 0, float(entry_price), "{}"))
            registered += cursor.rowcount
        db.commit()
        db.close()
    return {"saved": saved, "registered": registered}
