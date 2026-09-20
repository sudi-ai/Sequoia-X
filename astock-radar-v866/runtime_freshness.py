"""Shared receipt-time guard. Receipt time is NOT an exchange timestamp."""
import math
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def parse_cn(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)
    except (ValueError, TypeError):
        return None


def market_session(now=None):
    current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    hhmm = current.strftime('%H:%M')
    return current.weekday() < 5 and ('09:30' <= hhmm <= '11:30' or '13:00' <= hhmm <= '15:00')


def quote_guard(record, now=None, max_age=180):
    current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    observed = parse_cn(record.get('observed_at') or record.get('price_observed_at'))
    if observed is None:
        return False, 'MISSING_RECEIPT_TIME'
    if observed.date() != current.date():
        return False, 'PREVIOUS_DAY_QUOTE'
    trade_date = str(record.get('trade_date') or '').replace('-', '')
    if trade_date and trade_date != current.strftime('%Y%m%d'):
        return False, 'TRADE_DATE_MISMATCH'
    age = (current - observed).total_seconds()
    if age < -60 or age > max_age:
        return False, 'FUTURE_QUOTE' if age < -60 else 'STALE_QUOTE'
    try:
        quality = float(record.get('data_quality', 0) or 0)
        safe = int(record.get('is_pit_safe', 0) or 0) == 1
    except (TypeError, ValueError):
        return False, 'INVALID_QUALITY'
    if not safe or not math.isfinite(quality) or quality < .75:
        return False, 'UNSAFE_QUOTE'
    source_raw = record.get('source_trade_time')
    if source_raw:
        source = parse_cn(source_raw)
        if source is None or source.date() != current.date():
            return False, 'INVALID_SOURCE_TIME'
        source_age = (current - source).total_seconds()
        if source_age < -60 or source_age > max_age:
            return False, 'STALE_SOURCE_TIME'
    return True, 'SOURCE_TIME_CHECKED' if source_raw else 'RECEIPT_ONLY_SOURCE_TIME_UNVERIFIED'
