from __future__ import annotations

import json

from v7.env_loader import load_project_env

load_project_env()

from v8.portfolio import close_position, list_positions, upsert_position


LATEST = {
    "001696": ("宗申动力", 200, 15.892),
    "002429": ("兆驰股份", 100, 8.531),
    "000070": ("特发信息", 100, 15.412),
    "001390": ("古麒绒材", 100, 18.062),
    "300429": ("强力新材", 100, 11.001),
    "301313": ("凡拓数创", 100, 37.804),
    "600839": ("四川长虹", 100, 7.271),
    "002362": ("汉王科技", 100, 16.372),
    "301293": ("三博脑科", 100, 58.606),
    "002837": ("英维克", 300, 98.902),
}


def main() -> int:
    active = {str(row["code"]).zfill(6): row for row in list_positions()}
    changed, added, closed = [], [], []
    for code, (name, shares, cost) in LATEST.items():
        old = active.get(code)
        pid = upsert_position(
            code, name, shares, cost,
            entry_date=str(old.get("entry_date") or "2026-08-17") if old else "2026-08-17",
            manual_stop_price=old.get("manual_stop_price") if old else None,
            notes="2026-08-17 13:38证券账户持仓截图同步",
            position_id=str(old["position_id"]) if old else None,
        )
        (changed if old else added).append({"code": code, "name": name, "shares": shares,
                                            "cost": cost, "position_id": pid})
    for code, row in active.items():
        if code not in LATEST:
            close_position(str(row["position_id"]), reason="BROKER_SCREENSHOT_SYNC_REMOVED")
            closed.append({"code": code, "name": row.get("name")})
    print(json.dumps({"updated": changed, "added": added, "closed": closed,
                      "active_after": list_positions()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
