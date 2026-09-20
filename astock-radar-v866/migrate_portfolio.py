# -*- coding: utf-8 -*-
"""Portable, explicit migration for the manual V8.6.6 portfolio ledger.

It never contacts a server itself. Export creates a local transfer file; import
upserts that file into the portfolio ledger on the machine where it is run.
This keeps holdings private and prevents an accidental background sync.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from portfolio_monitor import PortfolioMonitor


ROOT = Path(__file__).resolve().parent
DEFAULT_TRANSFER = ROOT / "data" / "portfolio_transfer.json"
FIELDS = ("ts_code", "name", "sector", "entry_date", "cost_price", "quantity", "current_price", "stop_price", "target_price", "note")


def export_portfolio(path: Path) -> dict:
    snapshot = PortfolioMonitor().snapshot()
    payload = {
        "format": "AStock_Radar_V866_Portfolio_Transfer",
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "settings": snapshot["settings"],
        "positions": [{field: item.get(field) for field in FIELDS} for item in snapshot["positions"]],
        "safety": "Manual holdings only. No broker credentials, token, push webhook, or trade instruction is included.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "EXPORTED", "path": str(path), "positions": len(payload["positions"])}


def import_portfolio(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "AStock_Radar_V866_Portfolio_Transfer":
        raise ValueError("不是 V8.6.6 持仓迁移文件")
    monitor = PortfolioMonitor()
    settings = payload.get("settings") or {}
    monitor.save_settings(settings.get("nav", 0), settings.get("daily_pnl_pct", 0))
    imported = 0
    for item in payload.get("positions") or []:
        if not isinstance(item, dict):
            continue
        monitor.save_position(item)
        imported += 1
    return {"status": "IMPORTED", "path": str(path), "positions": imported, "mode": "upsert_no_delete"}


def main() -> int:
    parser = argparse.ArgumentParser(description="V8.6.6 手工持仓迁移工具")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export", action="store_true", help="导出本机手工持仓")
    action.add_argument("--import", dest="import_", action="store_true", help="导入到当前机器的手工持仓账本")
    parser.add_argument("--file", type=Path, default=DEFAULT_TRANSFER, help="迁移文件路径")
    args = parser.parse_args()
    result = export_portfolio(args.file) if args.export else import_portfolio(args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
