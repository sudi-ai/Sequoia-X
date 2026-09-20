from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-root", required=True)
    parser.add_argument("--trade-date", default="")
    args = parser.parse_args()
    root = Path(args.live_root).resolve(); sys.path.insert(0, str(root))
    from v8.env_loader import load_v8_env
    load_v8_env(root / ".env.v8", override=True)
    from v7.tushare_bridge import TushareBridge
    bridge = TushareBridge(); day = args.trade_date or datetime.now().astimezone().strftime("%Y%m%d")
    probes = (
        ("daily", {"trade_date": day}),
        ("daily_basic", {"trade_date": day}),
        ("moneyflow", {"trade_date": day}),
        ("forecast", {"ann_date": day}),
        ("stock_basic", {"list_status": "L", "limit": 10000}),
    )
    results = []
    for api, params in probes:
        started = time.perf_counter()
        try:
            value = bridge.call(api, **params)
            rows = len(value) if value is not None else 0
            results.append({"api": api, "status": "OK", "rows": int(rows),
                            "latency_ms": round((time.perf_counter()-started)*1000, 1)})
        except Exception as exc:
            results.append({"api": api, "status": "FAILED", "rows": 0,
                            "latency_ms": round((time.perf_counter()-started)*1000, 1),
                            "error": type(exc).__name__})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(x["status"] == "OK" for x in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
