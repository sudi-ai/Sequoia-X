# -*- coding: utf-8 -*-
"""V6.6 Pro 次日板块预测与候选股最终评分引擎。

本模块只消费调用方已经取得的真实数据，不发起网络请求、不读写数据库，
也不会生成模拟行情。主程序可以分别调用 :func:`forecast_sectors` 和
:func:`score_candidates`，也可以通过 :func:`run_nextday_engine` 一次完成。

数据缺失规则：

* 板块预测只使用已提供的因子重新归一化计算，并降低 ``confidence``；
* 个股固定权重公式中，缺失项使用公开的中性基线 50 分，且该项证据置信度为 0；
* 风险资料缺失不会被当作“没有风险”，只会令风险数据置信度下降；
* 只有历史预测分和真实次日结果同存且同一分箱不少于 30 条时，才输出
  ``calibrated_probability``。

所有分值范围均为 0~100。``score`` 是模型续强分，并非未经校准的概率。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any, Dict, List, Optional, Sequence, Tuple


ENGINE_VERSION = "6.6.0-nextday"
MIN_CALIBRATION_SAMPLES = 30
NEUTRAL_MISSING_SCORE = 50.0

# 七项板块预测因子。缺项时只在已有因子内按相对权重计算。
SECTOR_FACTOR_WEIGHTS = {
    "today_change": 18.0,
    "amount_change": 14.0,
    "capital_flow": 20.0,
    "limit_up_count": 12.0,
    "leader_strength": 14.0,
    "diffusion": 12.0,
    "historical_persistence": 10.0,
}

# 用户确认的最终个股评分公式。
CANDIDATE_COMPONENT_WEIGHTS = {
    "individual": 30.0,
    "trend": 20.0,
    "main_force": 10.0,
    "chip_proxy": 10.0,
    "sector_forecast": 25.0,
    "market_sentiment": 5.0,
}

_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
_MISSING = object()


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _number(value: Any) -> Optional[float]:
    """宽容地读取数值；不猜测“亿/万”等单位换算。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip().replace(",", "").replace("％", "%")
    if not text or text.lower() in {"none", "null", "nan", "-", "--"}:
        return None
    text = re.sub(r"(?:亿元|亿|万元|万|元|%)$", "", text).strip()
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "有", "成功", "延续", "通过"}:
        return True
    if text in {"0", "false", "no", "n", "否", "无", "失败", "未延续", "不通过"}:
        return False
    return None


def _path_get(source: Any, path: Any) -> Any:
    if not isinstance(source, Mapping):
        return _MISSING
    keys = path if isinstance(path, tuple) else (path,)
    current: Any = source
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _first_value(sources: Sequence[Any], aliases: Sequence[Any]) -> Tuple[Any, str, bool]:
    for source_index, source in enumerate(sources):
        for alias in aliases:
            value = _path_get(source, alias)
            if value is not _MISSING and value is not None and value != "":
                label = ".".join(alias) if isinstance(alias, tuple) else str(alias)
                return value, f"source[{source_index}].{label}", True
    return None, "", False


def _first_number(sources: Sequence[Any], aliases: Sequence[Any]) -> Tuple[Optional[float], str]:
    for source_index, source in enumerate(sources):
        for alias in aliases:
            raw = _path_get(source, alias)
            if raw is _MISSING:
                continue
            value = _number(raw)
            if value is not None:
                label = ".".join(alias) if isinstance(alias, tuple) else str(alias)
                return value, f"source[{source_index}].{label}"
    return None, ""


def _piecewise(value: float, points: Sequence[Tuple[float, float]]) -> float:
    ordered = sorted(points)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= value <= x1:
            ratio = (value - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return 50.0


def _as_rows(rows: Any) -> List[Dict[str, Any]]:
    """接受 records 列表、单条字典或 pandas DataFrame。"""
    if rows is None:
        return []
    if isinstance(rows, Mapping):
        return [dict(rows)]
    to_dict = getattr(rows, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
            if isinstance(records, list):
                return [dict(row) for row in records if isinstance(row, Mapping)]
        except (TypeError, ValueError):
            pass
    if isinstance(rows, Iterable) and not isinstance(rows, (str, bytes)):
        return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _looks_like_history_record(value: Mapping[str, Any]) -> bool:
    keys = set(value)
    evidence = {
        "sector", "板块", "板块名称", "date", "日期", "forecast_score",
        "prediction_score", "next_day_pct", "次日涨跌幅", "continued",
        "success", "is_success", "pct", "涨跌幅",
    }
    return bool(keys & evidence) and "rows" not in keys


def _flatten_history(
    value: Any,
    inherited_sector: str = "",
    inherited_date: str = "",
    depth: int = 0,
) -> List[Dict[str, Any]]:
    """兼容按日期、按板块或 records/rows 存放的历史记录。"""
    if depth > 8 or value is None:
        return []
    result: List[Dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            result.extend(_flatten_history(item, inherited_sector, inherited_date, depth + 1))
        return result
    if not isinstance(value, Mapping):
        return result

    if _looks_like_history_record(value):
        row = dict(value)
        if inherited_sector and not any(k in row for k in ("sector", "板块", "板块名称")):
            row["sector"] = inherited_sector
        if inherited_date and not any(k in row for k in ("date", "日期", "trade_date")):
            row["date"] = inherited_date
        result.append(row)
        return result

    for key, child in value.items():
        if not isinstance(child, (list, Mapping)):
            continue
        key_text = str(key)
        next_date = inherited_date
        next_sector = inherited_sector
        if _DATE_RE.match(key_text):
            next_date = key_text[:10].replace("/", "-")
        elif key_text not in {
            "records", "history", "data", "rows", "latest", "morning", "close",
            "sector_forecasts", "forecasts",
        }:
            next_sector = key_text
        result.extend(_flatten_history(child, next_sector, next_date, depth + 1))
    return result


def _record_sector(row: Mapping[str, Any]) -> str:
    value, _, found = _first_value([row], ("sector", "板块", "板块名称", "行业", "name"))
    return str(value).strip() if found else ""


def _record_date(row: Mapping[str, Any]) -> str:
    value, _, found = _first_value([row], ("date", "日期", "trade_date", "forecast_date"))
    return str(value)[:10].replace("/", "-") if found else ""


def _history_outcome(row: Mapping[str, Any]) -> Tuple[Optional[bool], Optional[float]]:
    raw, _, found = _first_value(
        [row],
        ("continued", "success", "is_success", "win", "次日延续", "是否成功", "结果"),
    )
    outcome = _bool(raw) if found else None
    next_pct, _ = _first_number(
        [row],
        ("next_day_pct", "next_pct", "next_return_pct", "realized_return_pct", "次日涨跌幅", "次日收益率"),
    )
    if outcome is None and next_pct is not None:
        outcome = next_pct > 0.0
    return outcome, next_pct


def _dedupe_history(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for source in rows:
        row = dict(source)
        predicted, _ = _first_number(
            [row], ("forecast_score", "prediction_score", "sector_forecast_score", "续强评分", "预测评分")
        )
        outcome, next_pct = _history_outcome(row)
        key = (_record_sector(row), _record_date(row), predicted, outcome, next_pct)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _history_persistence(
    sector: str,
    history_rows: Sequence[Mapping[str, Any]],
) -> Tuple[Optional[float], int, int, str]:
    rows = [row for row in history_rows if _record_sector(row) == sector]
    if not rows:
        return None, 0, 0, ""

    actual: List[Tuple[bool, Optional[float]]] = []
    for row in rows:
        outcome, next_pct = _history_outcome(row)
        if outcome is not None:
            actual.append((outcome, next_pct))
    if actual:
        wins = sum(1 for outcome, _ in actual if outcome)
        # Beta(2,2) 收缩，避免少量样本制造极端结论。
        posterior = (wins + 2.0) / (len(actual) + 4.0) * 100.0
        returns = [ret for _, ret in actual if ret is not None]
        if returns:
            posterior += _clamp(sum(returns) / len(returns) * 2.0, -8.0, 8.0)
        ordered = sorted(rows, key=_record_date)
        streak = 0
        for row in reversed(ordered):
            outcome, _ = _history_outcome(row)
            if outcome is True:
                streak += 1
            elif outcome is False:
                break
        return _clamp(posterior), len(actual), streak, "真实次日结果"

    # 尚无次日结果时，只用历史每日板块强弱衡量连续活跃度，不生成概率。
    daily: List[Tuple[str, bool]] = []
    for row in rows:
        pct, _ = _first_number([row], ("pct", "涨跌幅", "change_pct"))
        if pct is not None:
            daily.append((_record_date(row), pct > 0.0))
    if not daily:
        return None, 0, 0, ""
    daily.sort(key=lambda item: item[0])
    recent = daily[-5:]
    positive_ratio = sum(1 for _, active in recent if active) / len(recent)
    streak = 0
    for _, active in reversed(daily):
        if active:
            streak += 1
        else:
            break
    score = 35.0 + positive_ratio * 35.0 + min(streak, 5) * 5.0
    return _clamp(score), len(daily), streak, "历史每日强弱"


def _calibrate_probability(
    current_score: float,
    history_rows: Sequence[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """按十分制预测分箱，用过去真实结果做经验校准。"""
    lower = min(90, int(current_score // 10) * 10)
    upper = lower + 10
    observations: List[bool] = []
    for row in history_rows:
        predicted, _ = _first_number(
            [row], ("forecast_score", "prediction_score", "sector_forecast_score", "续强评分", "预测评分")
        )
        outcome, _ = _history_outcome(row)
        if predicted is None or outcome is None:
            continue
        if lower <= predicted <= upper if upper == 100 else lower <= predicted < upper:
            observations.append(outcome)
    if len(observations) < MIN_CALIBRATION_SAMPLES:
        return None
    wins = sum(1 for item in observations if item)
    probability = (wins + 1.0) / (len(observations) + 2.0) * 100.0
    return {
        "calibrated_probability": round(probability, 1),
        "calibration_samples": len(observations),
        "calibration_bin": f"{lower}-{upper}",
        "calibration_method": "同分箱真实次日结果 + Beta(1,1)收缩",
    }


def _latest_history_number(
    sector: str,
    history_rows: Sequence[Mapping[str, Any]],
    aliases: Sequence[Any],
) -> Tuple[Optional[float], str]:
    matching = [row for row in history_rows if _record_sector(row) == sector]
    matching.sort(key=_record_date, reverse=True)
    for row in matching:
        value, source = _first_number([row], aliases)
        if value is not None:
            return value, f"history.{source}"
    return None, ""


def _sector_component(
    score: Optional[float],
    value: Any,
    source: str,
    reliability: float = 1.0,
) -> Dict[str, Any]:
    return {
        "available": score is not None,
        "value": value,
        "score": round(_clamp(score), 1) if score is not None else None,
        "source": source,
        "reliability": round(_clamp(reliability, 0.0, 1.0), 2) if score is not None else 0.0,
    }


def forecast_sector(
    sector_row: Mapping[str, Any],
    historical_sector_records: Any = None,
) -> Dict[str, Any]:
    """计算单个板块的次日续强分、置信度、生命周期和证据明细。"""
    row = dict(sector_row or {})
    sector = _record_sector(row)
    history_rows = _dedupe_history(_flatten_history(historical_sector_records))

    pct, pct_source = _first_number([row], ("pct", "涨跌幅", "change_pct", "today_change_pct"))
    pct_score = None if pct is None else _piecewise(
        pct,
        ((-5, 8), (-1, 25), (0, 43), (0.5, 60), (2, 88), (4, 76), (7, 35), (12, 10)),
    )

    amount_change, amount_change_source = _first_number(
        [row],
        ("amount_change_pct", "turnover_amount_change_pct", "成交金额变化率", "成交额变化率"),
    )
    amount, amount_source = _first_number([row], ("amount", "总成交额", "turnover_amount", "成交金额"))
    if amount_change is None and amount is not None:
        previous, previous_source = _first_number(
            [row], ("previous_amount", "prev_amount", "昨日成交额", "前一日成交额")
        )
        if previous is None:
            previous, previous_source = _latest_history_number(
                sector, history_rows, ("amount", "总成交额", "turnover_amount", "成交金额")
            )
        if previous is not None and previous > 0:
            amount_change = (amount / previous - 1.0) * 100.0
            amount_change_source = f"{amount_source}/{previous_source}"
    amount_score = None if amount_change is None else 50.0 + 42.0 * math.tanh(amount_change / 25.0)

    net, net_source = _first_number([row], ("net", "净流入", "net_inflow", "capital_flow"))
    flow_ratio, flow_ratio_source = _first_number(
        [row], ("net_flow_ratio_pct", "capital_flow_ratio_pct", "净流入占比", "主力净流入占比")
    )
    if flow_ratio is None and net is not None and amount not in (None, 0):
        # 主程序的板块净流入和总成交额使用相同单位，可直接求占比。
        flow_ratio = net / amount * 100.0
        flow_ratio_source = f"{net_source}/{amount_source}"
    if flow_ratio is not None:
        flow_score = 50.0 + 44.0 * math.tanh(flow_ratio / 8.0)
        flow_value: Any = {"ratio_pct": round(flow_ratio, 3), "net": net}
        flow_source = flow_ratio_source
    elif net is not None:
        flow_score = 50.0 + 44.0 * math.tanh(net / 3.0)
        flow_value = net
        flow_source = net_source
    else:
        flow_score, flow_value, flow_source = None, None, ""

    limit_ups, limit_source = _first_number(
        [row], ("limit_up_count", "涨停数量", "涨停家数", "limit_ups")
    )
    constituent_count, _ = _first_number([row], ("constituent_count", "成分股数量", "股票家数"))
    if limit_ups is None:
        limit_score = None
        limit_value: Any = None
    elif constituent_count and constituent_count > 0:
        ratio = limit_ups / constituent_count * 100.0
        limit_score = _piecewise(ratio, ((0, 30), (1, 50), (3, 72), (6, 90), (12, 98)))
        limit_value = {"count": int(limit_ups), "ratio_pct": round(ratio, 2)}
    else:
        limit_score = _piecewise(limit_ups, ((0, 32), (1, 55), (2, 70), (3, 82), (5, 94), (8, 98)))
        limit_value = int(limit_ups)

    leader_score_direct, leader_direct_source = _first_number(
        [row], ("leader_score", "leader_strength_score", "龙头股强度", "龙头强度分")
    )
    leader_pct, leader_pct_source = _first_number(
        [row], ("leader_pct", "领涨股-涨跌幅", "龙头股涨跌幅", "leader_change_pct")
    )
    if leader_score_direct is not None:
        leader_score = _clamp(leader_score_direct)
        leader_value: Any = {"explicit_score": leader_score_direct, "leader_pct": leader_pct}
        leader_source = leader_direct_source
    elif leader_pct is not None:
        leader_score = _piecewise(
            leader_pct, ((-5, 10), (0, 35), (2, 58), (5, 90), (7, 96), (9.5, 72), (12, 30), (20, 10))
        )
        leader_value = leader_pct
        leader_source = leader_pct_source
    else:
        leader_score, leader_value, leader_source = None, None, ""

    breadth, breadth_source = _first_number([row], ("breadth", "上涨下跌比", "up_down_ratio"))
    up, up_source = _first_number([row], ("up", "上涨家数", "up_count"))
    down, down_source = _first_number([row], ("down", "下跌家数", "down_count"))
    if breadth is None and up is not None and down is not None:
        breadth = up / max(down, 1.0)
        breadth_source = f"{up_source}/{down_source}"
    if breadth is not None:
        diffusion_score = _piecewise(
            breadth, ((0, 8), (0.5, 25), (1, 45), (1.5, 65), (2, 78), (3, 90), (5, 97))
        )
        diffusion_value: Any = breadth
    elif up is not None and down is not None and up + down > 0:
        up_ratio = up / (up + down) * 100.0
        diffusion_score = _piecewise(up_ratio, ((20, 15), (40, 35), (50, 50), (65, 75), (80, 94)))
        diffusion_value = {"up_ratio_pct": round(up_ratio, 2)}
        breadth_source = f"{up_source}/{down_source}"
    else:
        diffusion_score, diffusion_value = None, None

    explicit_history, explicit_history_source = _first_number(
        [row], ("historical_persistence_score", "history_persistence_score", "历史板块持续性")
    )
    if explicit_history is not None:
        history_score = _clamp(explicit_history)
        history_samples = 0
        history_streak = 0
        history_basis = "调用方提供的历史持续性分"
        history_reliability = 1.0
        history_source = explicit_history_source
    else:
        history_score, history_samples, history_streak, history_basis = _history_persistence(sector, history_rows)
        history_reliability = min(1.0, history_samples / 10.0) if history_score is not None else 0.0
        history_source = "historical_sector_records" if history_score is not None else ""

    components = {
        "today_change": _sector_component(pct_score, pct, pct_source),
        "amount_change": _sector_component(amount_score, amount_change, amount_change_source),
        "capital_flow": _sector_component(flow_score, flow_value, flow_source),
        "limit_up_count": _sector_component(limit_score, limit_value, limit_source),
        "leader_strength": _sector_component(leader_score, leader_value, leader_source),
        "diffusion": _sector_component(diffusion_score, diffusion_value, breadth_source),
        "historical_persistence": _sector_component(
            history_score,
            {"samples": history_samples, "streak": history_streak, "basis": history_basis}
            if history_score is not None else None,
            history_source,
            history_reliability,
        ),
    }

    available_weight = sum(
        SECTOR_FACTOR_WEIGHTS[name] for name, item in components.items() if item["available"]
    )
    if available_weight:
        score = sum(
            item["score"] * SECTOR_FACTOR_WEIGHTS[name]
            for name, item in components.items() if item["available"]
        ) / available_weight
    else:
        score = NEUTRAL_MISSING_SCORE
    evidence_weight = sum(
        SECTOR_FACTOR_WEIGHTS[name] * item["reliability"]
        for name, item in components.items() if item["available"]
    )
    confidence = _clamp(evidence_weight)
    missing_fields = [name for name, item in components.items() if not item["available"]]
    if confidence >= 65:
        status = "ready"
    elif confidence >= 40:
        status = "low_confidence"
    else:
        status = "insufficient_data"

    if history_streak >= 5 and score >= 75:
        lifecycle = "高潮延续期（注意退潮）"
    elif history_streak >= 3 and score >= 68:
        lifecycle = "加速期"
    elif history_streak >= 1 and score >= 60:
        lifecycle = "启动/延续期"
    elif score >= 55:
        lifecycle = "启动观察期"
    else:
        lifecycle = "震荡/退潮期"

    result: Dict[str, Any] = {
        "sector": sector,
        "score": round(_clamp(score), 1),
        "confidence": round(confidence, 1),
        "status": status,
        "lifecycle": lifecycle,
        "history_samples": history_samples,
        "history_streak": history_streak,
        "components": components,
        "missing_fields": missing_fields,
        "available_factor_weight": round(available_weight, 1),
        "score_label": "次日板块续强评分（非概率）",
    }
    calibrated = _calibrate_probability(result["score"], history_rows)
    if calibrated:
        result.update(calibrated)
    return result


def forecast_sectors(
    sector_rows: Any,
    historical_sector_records: Any = None,
) -> List[Dict[str, Any]]:
    """批量预测并按 score、confidence 排序。"""
    forecasts = [forecast_sector(row, historical_sector_records) for row in _as_rows(sector_rows)]
    forecasts.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
    for rank, item in enumerate(forecasts, 1):
        item["rank"] = rank
    return forecasts


def _resolve_sector_forecast(candidate: Mapping[str, Any], forecasts: Any) -> Optional[Dict[str, Any]]:
    if not forecasts:
        return None
    sector = _record_sector(candidate)
    if isinstance(forecasts, Mapping):
        if "score" in forecasts and (not sector or _record_sector(forecasts) in {"", sector}):
            return dict(forecasts)
        direct = forecasts.get(sector)
        if isinstance(direct, Mapping):
            return dict(direct)
        iterable = forecasts.values()
    else:
        iterable = forecasts
    for item in iterable:
        if isinstance(item, Mapping) and _record_sector(item) == sector:
            return dict(item)
    return None


def _chip_proxy_score(candidate: Mapping[str, Any], deep: Mapping[str, Any]) -> Tuple[Optional[float], str, Any]:
    explicit, source = _first_number(
        [deep, candidate], ("chip_score", "chip_proxy_score", "筹码评分", "筹码代理分")
    )
    if explicit is not None:
        return _clamp(explicit), source, {"explicit_score": explicit}

    measures: List[Tuple[str, float, float]] = []
    profit_ratio, profit_source = _first_number(
        [deep, candidate], ("profit_ratio", "获利比例", ("tech", "profit_ratio"))
    )
    if profit_ratio is not None:
        score = _piecewise(profit_ratio, ((0, 30), (20, 48), (45, 72), (65, 84), (80, 68), (95, 35), (100, 20)))
        measures.append((profit_source, profit_ratio, score))
    turnover, turnover_source = _first_number(
        [candidate, deep], ("turnover", "turnover_rate", "换手率", "累计换手率")
    )
    if turnover is not None:
        score = _piecewise(turnover, ((0, 25), (1, 45), (3, 68), (8, 82), (20, 72), (35, 45), (60, 20)))
        measures.append((turnover_source, turnover, score))
    volume_ratio, volume_source = _first_number(
        [deep, candidate], (("tech", "volume_ratio"), "volume_ratio", "量比")
    )
    if volume_ratio is not None:
        score = _piecewise(volume_ratio, ((0, 20), (0.6, 40), (1, 65), (1.5, 82), (2.4, 78), (3.2, 55), (5, 25)))
        measures.append((volume_source, volume_ratio, score))
    if not measures:
        return None, "", None
    return (
        sum(item[2] for item in measures) / len(measures),
        "+".join(item[0] for item in measures),
        [{"source": source_name, "value": value, "score": round(score, 1)} for source_name, value, score in measures],
    )


def _component(value: Optional[float], weight: float, source: str, evidence: Any = None, reliability: float = 1.0) -> Dict[str, Any]:
    missing = value is None
    used = NEUTRAL_MISSING_SCORE if missing else _clamp(value)
    return {
        "score": round(used, 1),
        "weight": weight,
        "contribution": round(used * weight / 100.0, 2),
        "missing": missing,
        "source": source if not missing else "missing_neutral_baseline",
        "evidence": evidence,
        "reliability": 0.0 if missing else round(_clamp(reliability, 0.0, 1.0), 2),
    }


def _direct_risk_penalty(
    sources: Sequence[Any], aliases: Sequence[Any], maximum: float
) -> Tuple[float, bool, str, Optional[float]]:
    score, source = _first_number(sources, aliases)
    if score is None:
        return 0.0, False, "", None
    score = _clamp(score)
    return score / 100.0 * maximum, True, source, score


def _risk_assessment(
    candidate: Mapping[str, Any],
    deep: Mapping[str, Any],
    risk_context: Mapping[str, Any],
) -> Dict[str, Any]:
    sources = [risk_context, deep, candidate]
    hard_reasons: List[str] = []
    risk_reasons: List[str] = []
    evidence_categories = set()

    custom, _, custom_found = _first_value(sources, ("hard_risk_reasons", "hard_veto_reasons"))
    if custom_found:
        if isinstance(custom, (list, tuple, set)):
            hard_reasons.extend(str(item) for item in custom if str(item).strip())
        elif str(custom).strip():
            hard_reasons.append(str(custom).strip())

    hard_flags = (
        (("delisting_risk", "退市风险"), "退市风险"),
        (("major_illegal", "重大违法"), "重大违法风险"),
        (("regulatory_investigation", "立案调查"), "监管立案调查"),
        (("major_negative_announcement", "重大利空公告"), "重大利空公告"),
        (("suspended", "停牌"), "股票停牌"),
        (("cannot_buy", "无法买入"), "当前无法买入"),
        (("limit_up_locked", "一字涨停", "涨停封死"), "涨停封死，无法按计划成交"),
    )
    for aliases, reason in hard_flags:
        raw, _, found = _first_value(sources, aliases)
        if found:
            if aliases[0] in {"regulatory_investigation", "major_illegal"}:
                evidence_categories.add("regulatory")
            elif aliases[0] == "major_negative_announcement":
                evidence_categories.add("announcement")
            if _bool(raw) is True:
                hard_reasons.append(reason)

    confirmed_raw, _, confirmed_found = _first_value([deep], ("confirmed", "v65_confirmed", "深度确认通过"))
    if confirmed_found and _bool(confirmed_raw) is False:
        hard_reasons.append("V6.5深度确认未通过")

    name, _, _ = _first_value([candidate], ("name", "股票名称", "股票简称"))
    upper_name = str(name or "").upper().replace(" ", "")
    if upper_name.startswith(("ST", "*ST")):
        hard_reasons.append("ST股票硬性过滤")

    price, _ = _first_number(sources, ("price", "close", "收盘价", "最新价"))
    if price is not None and price <= 0:
        hard_reasons.append("价格数据无效")

    penalties: Dict[str, float] = {}
    details: Dict[str, Any] = {}

    regulatory, found, source, raw_score = _direct_risk_penalty(
        sources, ("regulatory_risk_score", "监管风险评分"), 15.0
    )
    reg_flags, _, reg_flags_found = _first_value(sources, ("regulatory_flags", "监管风险项"))
    if reg_flags_found:
        evidence_categories.add("regulatory")
        count = len(reg_flags) if isinstance(reg_flags, (list, tuple, set)) else int(bool(str(reg_flags).strip()))
        regulatory = max(regulatory, min(15.0, count * 5.0))
    if found:
        evidence_categories.add("regulatory")
    penalties["regulatory"] = round(regulatory, 1)
    details["regulatory"] = {"source": source, "input_score": raw_score}
    if regulatory:
        risk_reasons.append(f"监管风险 -{regulatory:.1f}")

    announcement, found, source, raw_score = _direct_risk_penalty(
        sources, ("announcement_risk_score", "公告风险评分"), 15.0
    )
    announcement_negative, _, announcement_found = _first_value(
        sources, ("negative_announcement", "公告利空", "announcement_negative")
    )
    if announcement_found:
        evidence_categories.add("announcement")
        if _bool(announcement_negative) is True:
            announcement = max(announcement, 8.0)
    if found:
        evidence_categories.add("announcement")
    penalties["announcement"] = round(announcement, 1)
    details["announcement"] = {"source": source, "input_score": raw_score}
    if announcement:
        risk_reasons.append(f"公告风险 -{announcement:.1f}")

    high_penalty, found, source, raw_score = _direct_risk_penalty(
        sources, ("high_position_risk_score", "short_term_overheat_score", "高位风险评分", "短线过热评分"), 18.0
    )
    high_inputs: Dict[str, float] = {}
    for key, aliases in {
        "deviation_ma20": ("deviation_ma20", "ma20_bias", "偏离MA20"),
        "change_5d": ("change_5d", "五日涨幅"),
        "change_10d": ("change_10d", "十日涨幅"),
        "change_20d": ("change_20d", "二十日涨幅"),
        "limit_up_count": ("recent_limit_up_count", "limit_up_count", "近期涨停次数"),
    }.items():
        value, _ = _first_number(sources, aliases)
        if value is not None:
            high_inputs[key] = value
    if high_inputs:
        evidence_categories.add("high_position")
        calculated = 0.0
        bias = high_inputs.get("deviation_ma20")
        if bias is not None:
            calculated += 8 if bias > 15 else (5 if bias > 10 else (3 if bias > 6 else 0))
        change5 = high_inputs.get("change_5d")
        if change5 is not None:
            calculated += 5 if change5 > 30 else (3 if change5 > 20 else 0)
        change10 = high_inputs.get("change_10d")
        if change10 is not None:
            calculated += 4 if change10 > 50 else (2 if change10 > 35 else 0)
        change20 = high_inputs.get("change_20d")
        if change20 is not None and change20 > 80:
            calculated += 3
        limit_count = high_inputs.get("limit_up_count")
        if limit_count is not None:
            calculated += 4 if limit_count >= 3 else (2 if limit_count >= 2 else 0)
        high_penalty = max(high_penalty, min(18.0, calculated))
    if found:
        evidence_categories.add("high_position")
    penalties["high_position"] = round(high_penalty, 1)
    details["high_position"] = {"source": source, "input_score": raw_score, "inputs": high_inputs}
    if high_penalty:
        risk_reasons.append(f"高位过热风险 -{high_penalty:.1f}")

    liquidity, found, source, raw_score = _direct_risk_penalty(
        sources, ("liquidity_risk_score", "流动性风险评分"), 15.0
    )
    liquidity_quality, quality_source = _first_number(sources, ("liquidity_score", "流动性评分"))
    if liquidity_quality is not None:
        evidence_categories.add("liquidity")
        liquidity = max(liquidity, (100.0 - _clamp(liquidity_quality)) / 100.0 * 15.0)
    amount_yuan, amount_yuan_source = _first_number(
        sources, ("daily_amount_yuan", "avg_amount_yuan", "成交额_元", "日均成交额_元")
    )
    turnover_rate, turnover_source = _first_number(sources, ("daily_turnover_rate", "当日换手率"))
    volume_ratio, volume_source = _first_number(sources, (("tech", "volume_ratio"), "volume_ratio", "量比"))
    liquidity_inputs: Dict[str, float] = {}
    if amount_yuan is not None:
        evidence_categories.add("liquidity")
        liquidity_inputs[amount_yuan_source] = amount_yuan
        liquidity = max(liquidity, 10.0 if amount_yuan < 30_000_000 else (5.0 if amount_yuan < 100_000_000 else 0.0))
    if turnover_rate is not None:
        evidence_categories.add("liquidity")
        liquidity_inputs[turnover_source] = turnover_rate
        liquidity = max(liquidity, 10.0 if turnover_rate < 0.5 else (6.0 if turnover_rate < 1.0 else (5.0 if turnover_rate > 40 else 0.0)))
    if volume_ratio is not None:
        evidence_categories.add("liquidity")
        liquidity_inputs[volume_source] = volume_ratio
        if volume_ratio < 0.5:
            liquidity = max(liquidity, 5.0)
    if found:
        evidence_categories.add("liquidity")
    penalties["liquidity"] = round(min(15.0, liquidity), 1)
    details["liquidity"] = {
        "source": source or quality_source,
        "input_score": raw_score,
        "inputs": liquidity_inputs,
    }
    if liquidity:
        risk_reasons.append(f"流动性风险 -{min(15.0, liquidity):.1f}")

    negative_news, found, source, raw_score = _direct_risk_penalty(
        sources, ("negative_news_risk_score", "负面消息风险评分"), 15.0
    )
    news_sentiment, news_source = _first_number(sources, ("news_sentiment_score", "消息情绪评分"))
    if news_sentiment is not None:
        evidence_categories.add("negative_news")
        if news_sentiment < 50:
            negative_news = max(negative_news, min(15.0, (50.0 - news_sentiment) * 0.3))
    negative_flag, _, negative_flag_found = _first_value(sources, ("negative_news", "存在负面消息"))
    if negative_flag_found:
        evidence_categories.add("negative_news")
        if _bool(negative_flag) is True:
            negative_news = max(negative_news, 8.0)
    if found:
        evidence_categories.add("negative_news")
    penalties["negative_news"] = round(negative_news, 1)
    details["negative_news"] = {"source": source or news_source, "input_score": raw_score}
    if negative_news:
        risk_reasons.append(f"负面消息风险 -{negative_news:.1f}")

    rr, rr_source = _first_number(sources, (("trade", "rr"), "rr", "risk_reward_ratio", "盈亏比"))
    rr_penalty = 0.0
    if rr is not None:
        evidence_categories.add("risk_reward")
        if rr < 0.8:
            rr_penalty = 18.0
            hard_reasons.append("盈亏比严重不足0.8")
        elif rr < 1.0:
            rr_penalty = 15.0
        elif rr < 1.5:
            rr_penalty = 10.0
        elif rr < 2.0:
            rr_penalty = 4.0
    penalties["risk_reward"] = round(rr_penalty, 1)
    details["risk_reward"] = {"source": rr_source, "rr": rr}
    if rr_penalty:
        risk_reasons.append(f"盈亏比风险 -{rr_penalty:.1f}")

    # 去重同时保留原始顺序。
    hard_reasons = list(dict.fromkeys(hard_reasons))
    total = round(sum(penalties.values()), 1)
    risk_confidence = len(evidence_categories) / 6.0 * 100.0
    return {
        "penalties": penalties,
        "penalty_total": total,
        "risk_score": 0.0 if hard_reasons else round(_clamp(100.0 - total), 1),
        "hard_veto": bool(hard_reasons),
        "hard_veto_reasons": hard_reasons,
        "risk_reasons": risk_reasons,
        "risk_data_confidence": round(risk_confidence, 1),
        "risk_evidence_categories": sorted(evidence_categories),
        "details": details,
    }


def score_candidate(
    candidate: Mapping[str, Any],
    deep_metrics: Optional[Mapping[str, Any]] = None,
    sector_forecast: Optional[Mapping[str, Any]] = None,
    market_sentiment: Any = None,
    risk_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """按固定公式计算一只股票的最终分，风险扣分会真实改变排序分。"""
    candidate_data = dict(candidate or {})
    deep = dict(deep_metrics or {})
    risk_data = dict(risk_context or {})
    sector_data = dict(sector_forecast or {})

    individual, individual_source = _first_number(
        [deep, candidate_data], ("individual_score", "individual", "个股评分", "个股独立分")
    )
    trend, trend_source = _first_number(
        [deep, candidate_data], ("trend_score", "trend", "趋势评分", "趋势结构分")
    )
    main_force, force_source = _first_number(
        [deep, candidate_data], ("force_score", ("force", "score"), "main_force_score", "主力评分")
    )
    chip, chip_source, chip_evidence = _chip_proxy_score(candidate_data, deep)
    sector_score, sector_source = _first_number([sector_data], ("score", "sector_score", "板块评分"))
    sector_confidence, _ = _first_number([sector_data], ("confidence", "置信度"))

    if isinstance(market_sentiment, (int, float, str)):
        sentiment = _number(market_sentiment)
        sentiment_source = "market_sentiment"
        sentiment_confidence = 100.0 if sentiment is not None else 0.0
    else:
        sentiment, sentiment_source = _first_number(
            [market_sentiment or {}], ("score", "sentiment_score", "market_sentiment_score", "市场情绪评分")
        )
        sentiment_confidence, _ = _first_number([market_sentiment or {}], ("confidence", "置信度"))
        if sentiment is not None and sentiment_confidence is None:
            sentiment_confidence = 100.0

    components = {
        "individual": _component(individual, 30.0, individual_source),
        "trend": _component(trend, 20.0, trend_source),
        "main_force": _component(main_force, 10.0, force_source),
        "chip_proxy": _component(chip, 10.0, chip_source, chip_evidence),
        "sector_forecast": _component(
            sector_score,
            25.0,
            sector_source,
            {"sector": sector_data.get("sector"), "status": sector_data.get("status")},
            (sector_confidence if sector_confidence is not None else 100.0) / 100.0,
        ),
        "market_sentiment": _component(
            sentiment,
            5.0,
            sentiment_source,
            None,
            (sentiment_confidence if sentiment_confidence is not None else 0.0) / 100.0,
        ),
    }
    base_score = round(sum(item["contribution"] for item in components.values()), 1)
    core_evidence = sum(
        item["weight"] * item["reliability"] for item in components.values()
    )
    risk = _risk_assessment(candidate_data, deep, risk_data)
    final_score = round(_clamp(base_score - risk["penalty_total"]), 1)
    confidence = round(_clamp(core_evidence * 0.8 + risk["risk_data_confidence"] * 0.2), 1)
    missing = [name for name, item in components.items() if item["missing"]]

    if risk["hard_veto"]:
        grade = "硬性淘汰"
        eligible = False
    elif confidence < 55:
        grade = "数据不足"
        eligible = False
    elif final_score >= 80:
        grade = "重点候选"
        eligible = True
    elif final_score >= 72:
        grade = "普通候选"
        eligible = True
    elif final_score >= 65:
        grade = "观察池"
        eligible = True
    else:
        grade = "不入池"
        eligible = False

    code_value, _, code_found = _first_value([candidate_data], ("code", "股票代码"))
    name_value, _, name_found = _first_value([candidate_data], ("name", "股票名称", "股票简称"))
    sector_value = _record_sector(candidate_data) or str(sector_data.get("sector", ""))
    result = dict(candidate_data)
    result.update({
        "code": str(code_value).zfill(6) if code_found else "",
        "name": str(name_value) if name_found else "",
        "sector": sector_value,
        "base_score": base_score,
        "risk_penalty": risk["penalty_total"],
        "risk_score": risk["risk_score"],
        "final_score": final_score,
        "ranking_score": final_score if not risk["hard_veto"] else -1.0,
        "confidence": confidence,
        "eligible": eligible,
        "grade": grade,
        "hard_veto": risk["hard_veto"],
        "hard_veto_reasons": risk["hard_veto_reasons"],
        "risk_reasons": risk["risk_reasons"],
        "score_components": components,
        "risk_components": risk["penalties"],
        "risk_details": risk["details"],
        "risk_data_confidence": risk["risk_data_confidence"],
        "missing_score_components": missing,
        "formula": "个股30%+趋势20%+主力10%+筹码代理10%+板块预测25%+市场情绪5%-风险扣分",
    })
    return result


def _by_code(data: Any, code: str) -> Dict[str, Any]:
    if not data:
        return {}
    if isinstance(data, Mapping):
        direct = data.get(code) or data.get(code.lstrip("0"))
        if isinstance(direct, Mapping):
            return dict(direct)
        # 调用者也可直接传单只股票的 metrics 字典。
        if any(key in data for key in ("individual_score", "confirmed", "risk_reward_ratio", "rr")):
            return dict(data)
        iterable = data.values()
    else:
        iterable = data
    if isinstance(iterable, Iterable) and not isinstance(iterable, (str, bytes)):
        for item in iterable:
            if not isinstance(item, Mapping):
                continue
            item_code, _, found = _first_value([item], ("code", "股票代码"))
            if found and str(item_code).zfill(6) == code:
                return dict(item)
    return {}


def score_candidates(
    candidates: Any,
    sector_forecasts: Any,
    market_sentiment: Any,
    deep_metrics_by_code: Any = None,
    risk_contexts_by_code: Any = None,
) -> List[Dict[str, Any]]:
    """批量评分；硬性风险候选永远排在可排序候选之后。"""
    results: List[Dict[str, Any]] = []
    for candidate in _as_rows(candidates):
        code_value, _, found = _first_value([candidate], ("code", "股票代码"))
        code = str(code_value).zfill(6) if found else ""
        deep = _by_code(deep_metrics_by_code, code)
        risks = _by_code(risk_contexts_by_code, code)
        forecast = _resolve_sector_forecast(candidate, sector_forecasts)
        results.append(score_candidate(candidate, deep, forecast, market_sentiment, risks))
    results.sort(
        key=lambda item: (
            not item["hard_veto"],
            item["eligible"],
            item["ranking_score"],
            item["confidence"],
        ),
        reverse=True,
    )
    for rank, item in enumerate(results, 1):
        item["rank"] = rank if not item["hard_veto"] else None
    return results


def run_nextday_engine(
    sector_rows: Any,
    candidates: Any,
    market_sentiment: Any,
    historical_sector_records: Any = None,
    deep_metrics_by_code: Any = None,
    risk_contexts_by_code: Any = None,
) -> Dict[str, Any]:
    """主流程入口：板块预测 -> 个股固定权重评分 -> 风险扣分和排序。"""
    forecasts = forecast_sectors(sector_rows, historical_sector_records)
    scored = score_candidates(
        candidates,
        forecasts,
        market_sentiment,
        deep_metrics_by_code=deep_metrics_by_code,
        risk_contexts_by_code=risk_contexts_by_code,
    )
    return {
        "engine_version": ENGINE_VERSION,
        "sector_forecasts": forecasts,
        "candidate_scores": scored,
        "eligible_candidates": [item for item in scored if item["eligible"]],
        "rejected_candidates": [item for item in scored if not item["eligible"]],
        "formula_weights": dict(CANDIDATE_COMPONENT_WEIGHTS),
        "sector_factor_weights": dict(SECTOR_FACTOR_WEIGHTS),
        "uses_network": False,
        "uses_simulated_data": False,
    }


__all__ = [
    "ENGINE_VERSION",
    "MIN_CALIBRATION_SAMPLES",
    "SECTOR_FACTOR_WEIGHTS",
    "CANDIDATE_COMPONENT_WEIGHTS",
    "forecast_sector",
    "forecast_sectors",
    "score_candidate",
    "score_candidates",
    "run_nextday_engine",
]
