from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .config import ROOT
from .research_layers import audit_market_movers
from .runtime_log import log_exception, log_warning


STATE_PATH = ROOT / "data_v8" / "full_market_audit_state.json"
REPORT_DIR = ROOT / "reports_v8"


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


def observe_full_market(quotes: Mapping[str, Mapping[str, Any]], *, observed_at: str | None = None,
                        state_path: Path = STATE_PATH, minimum_rows: int = 4000) -> dict[str, Any]:
    """Observe V8's own independent full-market snapshot for miss auditing."""
    stamp = observed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    day = stamp[:10]
    if len(quotes) < minimum_rows:
        log_warning("market_audit_observe", "incomplete_full_market_snapshot", rows=len(quotes), minimum=minimum_rows)
        return {"status": "INCOMPLETE", "rows": len(quotes), "minimum_rows": minimum_rows}
    state = _load(state_path)
    if state.get("trade_date") != day:
        state = {"trade_date": day, "first_observed_at": stamp, "stocks": {}}
    stocks = state.setdefault("stocks", {})
    accepted = 0
    for raw_code, quote in quotes.items():
        if not isinstance(quote, Mapping):
            continue
        code = str(raw_code).split(".")[0].zfill(6)
        pct = _num(quote.get("pct") if quote.get("pct") is not None else quote.get("change_pct"))
        if not code.strip("0") or pct is None:
            continue
        old = stocks.get(code) if isinstance(stocks.get(code), dict) else {}
        old_max = _num(old.get("max_return_pct"))
        stocks[code] = {"name": quote.get("name") or old.get("name"), "actual_return_pct": round(pct, 4),
                        "max_return_pct": round(max(pct, old_max if old_max is not None else pct), 4),
                        "price": _num(quote.get("price")), "last_observed_at": stamp}
        accepted += 1
    state.update({"last_observed_at": stamp, "source": "V8_INDEPENDENT_FULL_MARKET_SNAPSHOT",
                  "snapshot_rows": len(quotes), "accepted_rows": accepted})
    _atomic_json(state_path, state)
    return {"status": "RECORDED", "trade_date": day, "rows": len(quotes), "accepted": accepted}


def settle_daily_market_audit(*, trade_date: str | None = None, state_path: Path = STATE_PATH,
                              report_dir: Path = REPORT_DIR, top_n: int = 100,
                              db_path: Path | None = None) -> dict[str, Any]:
    day = trade_date or date.today().isoformat()
    state = _load(state_path)
    if state.get("trade_date") != day:
        return {"status": "NO_CURRENT_SNAPSHOT", "trade_date": day}
    stocks = state.get("stocks") if isinstance(state.get("stocks"), dict) else {}
    rows = [{"code": code, **dict(values)} for code, values in stocks.items() if isinstance(values, Mapping)]
    rows.sort(key=lambda x: max(_num(x.get("actual_return_pct")) or -99,
                                _num(x.get("max_return_pct")) or -99), reverse=True)
    selected = rows[:max(1, top_n)]
    selected_codes = {x["code"] for x in selected}
    for row in rows:
        close_pct = _num(row.get("actual_return_pct")) or -99
        max_pct = _num(row.get("max_return_pct")) or -99
        if (close_pct >= 4 or max_pct >= 6) and row["code"] not in selected_codes and len(selected) < 200:
            selected.append(row); selected_codes.add(row["code"])
    result = audit_market_movers(selected, trade_date=day, path=db_path)
    result.update({"status": "SETTLED", "snapshot_rows": state.get("snapshot_rows"),
                   "strong_pool_size": len(selected), "selection": "TOP100_PLUS_CLOSE4_OR_MAX6"})
    report_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(report_dir / f"v8_miss_audit_{day.replace('-', '')}.json", result)
    return result


def safe_observe_full_market(quotes: Mapping[str, Mapping[str, Any]], *, observed_at: str | None = None) -> None:
    try:
        observe_full_market(quotes, observed_at=observed_at)
    except Exception as exc:
        log_exception("market_audit_observe", exc, rows=len(quotes))
