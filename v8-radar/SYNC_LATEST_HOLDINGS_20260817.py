from __future__ import annotations

import json

from v7.env_loader import load_project_env

load_project_env()

from v8.portfolio import close_position, list_positions, upsert_position


LATEST = {
    "600839": ("四川长虹", 100, 7.271),
    "001696": ("宗申动力", 200, 15.892),
    "002362": ("汉王科技", 100, 16.372),
    "002429": ("兆驰股份", 100, 8.531),
    "002837": ("英维克", 300, 98.902),
    "301293": ("三博脑科", 100, 58.606),
    "301313": ("凡拓数创", 100, 37.804),
}


def main() -> int:
    active = {str(row["code"]).zfill(6): row for row in list_positions()}
    updated = []
    added = []
    closed = []
    for code, (name, shares, cost) in LATEST.items():
        old = active.get(code)
        pid = upsert_position(
            code,
            name,
            shares,
            cost,
            entry_date=str(old.get("entry_date") or "2026-08-17") if old else "2026-08-17",
            manual_stop_price=old.get("manual_stop_price") if old else None,
            notes="2026-08-17 10:00证券账户持仓截图同步",
            position_id=str(old["position_id"]) if old else None,
        )
        (updated if old else added).append({"code": code, "name": name, "shares": shares, "cost": cost, "position_id": pid})
    for code, row in active.items():
        if code not in LATEST:
            close_position(str(row["position_id"]), reason="BROKER_SCREENSHOT_SYNC_REMOVED")
            closed.append({"code": code, "name": row.get("name"), "position_id": row["position_id"]})
    print(json.dumps({"updated": updated, "added": added, "closed_without_exit_price": closed,
                      "active_after": list_positions()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
