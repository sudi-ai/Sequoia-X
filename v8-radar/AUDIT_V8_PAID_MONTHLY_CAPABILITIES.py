from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable


def _rows(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(len(value))
    except Exception:
        return 1


def _columns(value: Any) -> list[str]:
    columns = getattr(value, "columns", None)
    if columns is None:
        return []
    return [str(x) for x in list(columns)[:40]]


def _safe_error(exc: Exception, token: str) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if token:
        message = message.replace(token, "***")
    return message[:500]


def main() -> int:
    parser = argparse.ArgumentParser(description="V8 paid relay one-call-per-endpoint audit")
    parser.add_argument("--live-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.live_root).resolve()
    output = Path(args.output).resolve()
    sys.path.insert(0, str(root))

    # Load secrets before importing v7.config/tushare_bridge. Never print values.
    from v8.env_loader import load_v8_env

    load_v8_env(root / ".env.v8", override=True)
    from v7.tushare_bridge import TushareBridge

    bridge = TushareBridge()
    token = os.getenv("TUSHARE_TOKEN", "")
    now = datetime.now().astimezone()
    day = now.strftime("%Y%m%d")
    start_30d = (now - timedelta(days=30)).strftime("%Y%m%d")
    start_1y = (now - timedelta(days=365)).strftime("%Y%m%d")
    start_dt = (now - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    end_dt = now.strftime("%Y-%m-%d %H:%M:%S")
    code = "000001.SZ"

    probes: list[tuple[str, str, Callable[[], Any]]] = [
        ("index_basic", "指数基础", lambda: bridge.call("index_basic", limit=5)),
        ("stock_basic", "股票基础", lambda: bridge.call("stock_basic", list_status="L", limit=5)),
        ("stock_company", "公司资料", lambda: bridge.call("stock_company", exchange="SSE", limit=5)),
        ("trade_cal", "交易日历", lambda: bridge.call("trade_cal", exchange="SSE", start_date=start_30d, end_date=day)),
        ("daily", "历史日线", lambda: bridge.call("daily", ts_code=code, start_date=start_30d, end_date=day)),
        ("daily_basic", "每日估值指标", lambda: bridge.call("daily_basic", ts_code=code, start_date=start_30d, end_date=day)),
        ("moneyflow", "个股资金流", lambda: bridge.call("moneyflow", ts_code=code, start_date=start_30d, end_date=day)),
        ("forecast", "业绩预告", lambda: bridge.call("forecast", ts_code=code, start_date=start_1y, end_date=day)),
        ("rt_k", "实时日线", lambda: bridge.call("rt_k", ts_code=code)),
        ("rt_min", "实时分钟", lambda: bridge.call("rt_min", ts_code=code, freq="1MIN")),
        ("rt_min_daily", "当日实时分钟", lambda: bridge.call("rt_min_daily", ts_code=code, freq="1MIN")),
        ("anns_d", "公告", lambda: bridge.call("anns_d", ts_code=code, start_date=start_30d, end_date=day)),
        ("news", "新闻快讯", lambda: bridge.call("news", src="sina", start_date=start_dt, end_date=end_dt)),
        ("major_news", "重大新闻", lambda: bridge.call("major_news", src="sina", start_date=start_dt, end_date=end_dt)),
        ("report_rc", "研报", lambda: bridge.call("report_rc", ts_code=code, start_date=start_1y, end_date=day)),
        ("cyq_perf", "筹码成本", lambda: bridge.call("cyq_perf", ts_code=code, start_date=start_30d, end_date=day)),
        ("cyq_chips", "筹码分布", lambda: bridge.call("cyq_chips", ts_code=code, start_date=start_30d, end_date=day)),
        ("stk_auction_o", "竞价汇总", lambda: bridge.call("stk_auction_o", ts_code=code, trade_date=day)),
        ("stk_auction_tick", "竞价过程", lambda: bridge.call("stk_auction_tick", ts_code=code, start_date=day, end_date=day)),
        ("us_tbr", "美国国债利率", lambda: bridge.call("us_tbr", start_date=start_30d, end_date=day)),
        ("index_member_all", "行业成分", lambda: bridge.call("index_member_all", ts_code=code, limit=5)),
        ("fina_mainbz", "主营构成", lambda: bridge.call("fina_mainbz", ts_code=code, start_date=start_1y, end_date=day)),
        ("pro_bar", "历史1分钟", lambda: bridge.pro_bar(ts_code=code, freq="1min", start_date=(now - timedelta(days=7)).strftime("%Y-%m-%d 09:30:00"), end_date=end_dt)),
    ]

    results: list[dict[str, Any]] = []
    for api, label, func in probes:
        started = time.perf_counter()
        try:
            value = func()
            count = _rows(value)
            results.append({
                "api": api,
                "label": label,
                "status": "AVAILABLE" if count else "AVAILABLE_EMPTY",
                "row_count": count,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "columns": _columns(value),
                "error": "",
            })
        except Exception as exc:
            results.append({
                "api": api,
                "label": label,
                "status": "FAILED",
                "row_count": 0,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "columns": [],
                "error": _safe_error(exc, token),
            })

    payload = {
        "checked_at": now.isoformat(timespec="seconds"),
        "live_root": str(root),
        "token_configured": bool(token),
        "relay_configured": bool(os.getenv("TUSHARE_HTTP_URL")),
        "probe_policy": "one_call_per_endpoint_small_sample",
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "available": sum(x["status"] == "AVAILABLE" for x in results),
        "available_empty": sum(x["status"] == "AVAILABLE_EMPTY" for x in results),
        "failed": sum(x["status"] == "FAILED" for x in results),
        "total": len(results),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
