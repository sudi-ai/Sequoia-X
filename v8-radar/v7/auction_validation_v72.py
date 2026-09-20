from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
AUCTION_START = dtime(9, 15, 0)
# The relay timestamps the final uncrossing result a few seconds after 09:25:00
# (09:25:04 observed on the live feed). Continuous trading starts at 09:30, so
# this narrow grace captures the true auction result without admitting intraday trades.
AUCTION_END = dtime(9, 25, 10)
CODE_FIELDS = ("ts_code", "symbol", "code")
TIME_FIELDS = ("trade_time", "datetime", "time")
PRICE_FIELDS = ("price", "close", "last", "current")
VOLUME_FIELDS = ("vol", "volume")
AMOUNT_FIELDS = ("amount", "turnover", "auction_amount")


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:
            return []
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, Mapping)]
    if isinstance(value, Mapping):
        return [dict(value)]
    return []


def _canonical_requested_code(code: str) -> str:
    return str(code or "").strip().upper()


def _row_code(row: Mapping[str, Any]) -> str:
    for field in CODE_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            return str(value).strip().upper()
    return ""


def _has_any(row: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    return any(row.get(k) not in (None, "") for k in fields)


def validate_realtime_auction_ticks(
    raw: Any,
    *,
    expected_code: str,
    now: datetime,
    market_open_today: bool | None,
    parse_time,
) -> dict[str, Any]:
    """Strictly validate 09:15:00-09:25:10 A-share auction/settlement ticks.

    Validation is intentionally fail-closed for realtime-auction availability.
    A row is eligible only when its trade date is today's confirmed open session,
    its code exactly equals the requested code, its timestamp lies inside the
    inclusive auction window, and it has explicit price plus volume/amount evidence.
    Post-09:25 ordinary trades are never accepted as auction ticks.
    """
    now_cn = now.astimezone(CN_TZ) if now.tzinfo else now.replace(tzinfo=CN_TZ)
    expected = _canonical_requested_code(expected_code)
    rows = _records(raw)
    errors: list[str] = []

    if market_open_today is not True:
        errors.append("trade_cal未确认当日开市")
    if not rows:
        errors.append("空数据")

    valid: list[tuple[datetime, dict[str, Any]]] = []
    seen_reasons: set[str] = set()
    for row in rows:
        code = _row_code(row)
        dt = parse_time(row)
        row_errors: list[str] = []

        if not code:
            row_errors.append("缺股票代码字段")
        elif code != expected:
            row_errors.append("股票代码不一致")

        if dt is None:
            row_errors.append("缺失或无法解析Tick时间")
        else:
            dt = dt.astimezone(CN_TZ) if dt.tzinfo else dt.replace(tzinfo=CN_TZ)
            if dt.date() != now_cn.date():
                row_errors.append("Tick不是当前交易日")
            if not (AUCTION_START <= dt.time() <= AUCTION_END):
                row_errors.append("Tick时间超出09:15-09:25:10竞价结算窗口")

        if not _has_any(row, PRICE_FIELDS):
            row_errors.append("缺价格字段")
        if not (_has_any(row, VOLUME_FIELDS) or _has_any(row, AMOUNT_FIELDS)):
            row_errors.append("缺成交量/金额字段")

        if not row_errors and market_open_today is True and dt is not None:
            valid.append((dt, row))
        else:
            seen_reasons.update(row_errors)

    if not valid:
        errors.extend(sorted(seen_reasons))
        # stable de-dup preserving order
        deduped = list(dict.fromkeys(errors))
        return {
            "realtime_status": "AUCTION_REALTIME_UNAVAILABLE",
            "validation_error": "；".join(deduped) if deduped else "无有效实时竞价Tick",
            "validated_rows": 0,
            "validated_tick_time": None,
            "validated_trade_date": None,
            "latest_row": None,
        }

    valid.sort(key=lambda x: x[0])
    latest_dt, latest_row = valid[-1]
    return {
        "realtime_status": "REALTIME_AVAILABLE",
        "validation_error": "",
        "validated_rows": len(valid),
        "validated_tick_time": latest_dt.isoformat(timespec="seconds"),
        "validated_trade_date": latest_dt.date().isoformat(),
        "latest_row": latest_row,
    }


__all__ = ["validate_realtime_auction_ticks", "AUCTION_START", "AUCTION_END"]
