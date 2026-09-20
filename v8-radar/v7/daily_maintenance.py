from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from .config import CONFIG
from .performance_tracker import ingest_daily_prices, recompute_outcomes
from .reporting import write_comparison_report
from .signal_lab import record_data_quality
from .tushare_bridge import BRIDGE

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT_DIR / "data_v7" / "maintenance_state.json"
MIN_COVERAGE_RATIO = 0.90
MIN_ABSOLUTE_ROWS = 4000
MAX_INVALID_RATIO = 0.01
RETRY_COOLDOWN_MINUTES = 30
MAX_DAILY_ATTEMPTS = 4


def _load_state():
    try: return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception: return {}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_trade_date(v) -> str:
    s=str(v or "").strip()
    if len(s)==8 and s.isdigit(): return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _validate_daily_rows(rows, target_date: str, expected_rows: int | None = None):
    fields={"ts_code","trade_date","open","high","low","close"}
    if not rows:
        details={"raw_row_count":0,"valid_row_count":0,"unique_code_count":0,"duplicate_count":0,
                 "invalid_count":0,"invalid_ratio":0.0,"date_mismatch_count":0,
                 "missing_field_count":0,"ohlc_error_count":0,"missing_fields":sorted(fields)}
        return False,"empty",None,0,0.0,details,[]
    raw_count=len(rows); valid_by_code={}; duplicate_count=invalid_count=date_mismatch_count=0
    missing_field_count=ohlc_error_count=0; dates=[]
    for raw in rows:
        if not isinstance(raw, dict):
            invalid_count += 1; missing_field_count += 1; continue
        missing=fields-set(raw)
        if missing:
            invalid_count += 1; missing_field_count += 1; continue
        day=_normalize_trade_date(raw.get("trade_date")); dates.append(day)
        if day != target_date:
            invalid_count += 1; date_mismatch_count += 1; continue
        code=str(raw.get("ts_code") or "").strip()
        if not code:
            invalid_count += 1; continue
        try:
            o,h,l,c=(float(raw[k]) for k in ("open","high","low","close"))
            if not all(math.isfinite(x) and x>0 for x in (o,h,l,c)) or h<max(o,l,c) or l>min(o,h,c):
                raise ValueError("invalid OHLC")
        except Exception:
            invalid_count += 1; ohlc_error_count += 1; continue
        if code in valid_by_code: duplicate_count += 1
        valid_by_code[code]=dict(raw)
    cleaned=list(valid_by_code.values()); unique_count=len(cleaned)
    data_date=max(dates) if dates else None
    invalid_ratio=(invalid_count/raw_count) if raw_count else 0.0
    coverage=(unique_count/expected_rows) if expected_rows and expected_rows>0 else None
    date_ok=(data_date==target_date)
    rows_ok=(coverage>=MIN_COVERAGE_RATIO) if coverage is not None else (unique_count>=MIN_ABSOLUTE_ROWS)
    ok=date_ok and rows_ok and invalid_ratio<=MAX_INVALID_RATIO
    status="ok" if ok else "incomplete"
    details={"raw_row_count":raw_count,"valid_row_count":unique_count,"unique_code_count":unique_count,
             "duplicate_count":duplicate_count,"invalid_count":invalid_count,"invalid_ratio":invalid_ratio,
             "date_mismatch_count":date_mismatch_count,"missing_field_count":missing_field_count,
             "ohlc_error_count":ohlc_error_count,"date_ok":date_ok,"rows_ok":rows_ok,
             "minimum_coverage":MIN_COVERAGE_RATIO,"coverage_ratio":coverage,
             "minimum_absolute_rows":MIN_ABSOLUTE_ROWS}
    return ok,status,data_date,unique_count,coverage,details,cleaned


def _parse_iso(value):
    try: return datetime.fromisoformat(str(value))
    except Exception: return None


def _retry_guard(state: dict, day_compact: str, now: datetime):
    if state.get("last_attempt_date") != day_compact:
        state.update({"attempt_count":0,"next_retry_at":None})
        return None
    if state.get("last_attempt_status") == "market_closed":
        return {"ran":False,"reason":"market_closed","date":day_compact,"calendar_verified":True}
    attempts=int(state.get("attempt_count") or 0)
    if attempts >= MAX_DAILY_ATTEMPTS:
        return {"ran":False,"reason":"retry_limit_reached","date":day_compact,"attempt_count":attempts}
    next_retry=_parse_iso(state.get("next_retry_at"))
    comparable=now if now.tzinfo else now.astimezone()
    if next_retry and next_retry.tzinfo is None: next_retry=next_retry.astimezone()
    if next_retry and comparable < next_retry:
        return {"ran":False,"reason":"retry_cooldown","date":day_compact,
                "next_retry_at":next_retry.isoformat(timespec="seconds"),"attempt_count":attempts}
    return None


def _schedule_retry(state: dict, day_compact: str, now: datetime, status: str, **extra):
    aware=now if now.tzinfo else now.astimezone()
    attempts=(int(state.get("attempt_count") or 0) if state.get("last_attempt_date")==day_compact else 0)+1
    next_retry=aware+timedelta(minutes=RETRY_COOLDOWN_MINUTES)
    state.update({"last_attempt_date":day_compact,"last_attempt_at":aware.isoformat(timespec="seconds"),
                  "last_attempt_status":status,"attempt_count":attempts,
                  "next_retry_at":next_retry.isoformat(timespec="seconds"),**extra})
    _save_state(state)
    return attempts,next_retry



def _verify_trade_day(day_compact: str) -> tuple[bool | None, dict]:
    """Verify against Tushare trade_cal. None means calendar unavailable, not closed."""
    try:
        df=BRIDGE.call("trade_cal", exchange="SSE", start_date=day_compact, end_date=day_compact,
                       fields="exchange,cal_date,is_open,pretrade_date")
        rows=df.to_dict(orient="records") if hasattr(df,"to_dict") else []
        if not rows:
            return None,{"calendar_status":"empty"}
        is_open=int(rows[0].get("is_open") or 0)==1
        return is_open,{"calendar_status":"verified","calendar_row":rows[0]}
    except Exception as exc:
        return None,{"calendar_status":"unavailable","calendar_error":repr(exc)}


def _expected_listed_rows(state: dict) -> tuple[int | None, dict]:
    try:
        df=BRIDGE.call("stock_basic", exchange="", list_status="L", fields="ts_code")
        rows=df.to_dict(orient="records") if hasattr(df,"to_dict") else []
        count=len(rows)
        if count>=MIN_ABSOLUTE_ROWS:
            return count,{"expected_rows_source":"stock_basic"}
    except Exception as exc:
        return state.get("last_good_row_count"),{"expected_rows_source":"last_good_row_count","expected_rows_error":repr(exc)}
    return state.get("last_good_row_count"),{"expected_rows_source":"last_good_row_count"}


def run_after_close_if_due(now: datetime | None = None) -> dict:
    now=now or datetime.now(); result={"ran":False,"reason":"not_due"}
    if not CONFIG.shadow_enabled: return {"ran":False,"reason":"shadow_disabled"}
    if now.weekday()>=5 or now.hour*60+now.minute<15*60+10: return result
    day_compact=now.strftime("%Y%m%d"); day_iso=now.strftime("%Y-%m-%d")
    state=_load_state()
    if state.get("last_settlement_date")==day_compact: return {"ran":False,"reason":"already_done","date":day_compact}
    if not BRIDGE.enabled: return {"ran":False,"reason":"bridge_unavailable","date":day_compact}
    guarded=_retry_guard(state,day_compact,now)
    if guarded: return guarded

    requested_at=datetime.now().astimezone().isoformat(timespec="seconds")
    is_open,calendar_details=_verify_trade_day(day_compact)
    if is_open is False:
        state.update({"last_attempt_date":day_compact,"last_attempt_at":requested_at,"last_attempt_status":"market_closed"})
        _save_state(state)
        return {"ran":False,"reason":"market_closed","date":day_compact,"calendar_verified":True}
    expected_rows,expected_details=_expected_listed_rows(state)
    try:
        df=BRIDGE.call("daily",trade_date=day_compact)
        received_at=datetime.now().astimezone().isoformat(timespec="seconds")
        rows=df.to_dict(orient="records") if hasattr(df,"to_dict") else []
        ok,status,data_date,row_count,coverage,details,cleaned=_validate_daily_rows(rows,day_iso,expected_rows)
        details.update(calendar_details); details.update(expected_details); details["calendar_verified"]=(is_open is not None)
        record_data_quality(dataset="daily",source="tushare_relay.daily",requested_at=requested_at,received_at=received_at,
                            requested_trade_date=day_iso,data_trade_date=data_date,expected_rows=expected_rows,
                            row_count=row_count,coverage_ratio=coverage,is_complete=ok,status=status,details=details)
        if not ok:
            attempts,next_retry=_schedule_retry(state,day_compact,now,"incomplete",
                last_attempt_rows=row_count,last_attempt_data_date=data_date)
            return {"ran":False,"reason":"data_incomplete","date":day_compact,"rows":row_count,
                    "data_trade_date":data_date,"coverage_ratio":coverage,"retry_allowed":attempts<MAX_DAILY_ATTEMPTS,
                    "attempt_count":attempts,"next_retry_at":next_retry.isoformat(timespec="seconds")}

        count=ingest_daily_prices(cleaned,source="tushare_relay.daily")
        outcomes=recompute_outcomes(); report=write_comparison_report(signal_date=day_iso)
        state.update({"last_settlement_date":day_compact,"last_settlement_at":received_at,"last_rows":count,
                      "last_good_row_count":row_count,"last_outcomes_recomputed":outcomes,"last_report":report,
                      "last_attempt_status":"complete","attempt_count":0,"next_retry_at":None})
        _save_state(state)
        return {"ran":True,"date":day_compact,"rows":count,"outcomes":outcomes,"report":report,"data_complete":True}
    except Exception as exc:
        record_data_quality(dataset="daily",source="tushare_relay.daily",requested_at=requested_at,received_at=None,
                            requested_trade_date=day_iso,data_trade_date=None,expected_rows=expected_rows,row_count=0,
                            coverage_ratio=None,is_complete=False,status="error",details={"error":repr(exc)})
        attempts,next_retry=_schedule_retry(state,day_compact,now,"error",last_error=repr(exc))
        return {"ran":False,"reason":"error","date":day_compact,"error":repr(exc),
                "retry_allowed":attempts<MAX_DAILY_ATTEMPTS,"attempt_count":attempts,
                "next_retry_at":next_retry.isoformat(timespec="seconds")}
