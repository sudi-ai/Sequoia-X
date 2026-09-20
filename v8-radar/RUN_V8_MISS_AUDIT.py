from __future__ import annotations

import argparse
import csv
import json

from v8.env_loader import load_v8_env

load_v8_env()

from v8.research_layers import audit_market_movers


def main() -> int:
    parser = argparse.ArgumentParser(description="V8收盘漏选审计（输入CSV，不参与实时决策）")
    parser.add_argument("csv_file", help="至少包含code；可选name,actual_return_pct,max_return_pct")
    parser.add_argument("--trade-date", help="YYYY-MM-DD，默认今天")
    args = parser.parse_args()
    with open(args.csv_file, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    print(json.dumps(audit_market_movers(rows, trade_date=args.trade_date), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
