from __future__ import annotations

import json

from v7.env_loader import load_project_env

load_project_env()

from v8.portfolio import list_positions, upsert_position


HOLDINGS = [
    ("002837", "英维克", 300, 98.902, "账户一"),
    ("301293", "三博脑科", 100, 58.606, "账户一"),
    ("301313", "凡拓数创", 100, 37.804, "账户一"),
    ("001696", "宗申动力", 200, 15.892, "账户一"),
    ("002362", "汉王科技", 100, 16.372, "账户一"),
    ("600011", "华能国际", 2000, 7.212, "账户二"),
]


def main() -> int:
    existing = {
        str(row.get("code") or "").split(".")[0].zfill(6): row
        for row in list_positions()
        if row.get("status") == "HOLDING"
    }
    imported = []
    skipped = []
    for code, name, shares, cost, account in HOLDINGS:
        if code in existing:
            skipped.append({"code": code, "reason": "active_position_exists"})
            continue
        notes = f"{account}｜用户2026-08-15持仓截图导入｜真实买入日期待补充｜系统从导入日开始跟踪"
        position_id = upsert_position(
            code, name, float(shares), float(cost), entry_date="2026-08-15", notes=notes
        )
        imported.append({
            "position_id": position_id, "code": code, "name": name,
            "shares": float(shares), "cost_price": float(cost), "status": "HOLDING",
        })
    print(json.dumps({"imported": imported, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
