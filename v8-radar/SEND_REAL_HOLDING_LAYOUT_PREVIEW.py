from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from v7.env_loader import load_project_env

load_project_env()

from v7.portfolio import create_pending_fill, confirm_fill, list_positions
from v7.portfolio_runtime_v72 import scan_position
from v7.wework_shadow_push import build_portfolio_message, send_message


def main() -> int:
    holdings = [p for p in list_positions() if p.get("status") == "HOLDING"]
    position = next((p for p in holdings if p.get("ts_code") == "002837"), None)
    if not position:
        raise SystemExit("英维克实际持仓不存在")
    with tempfile.TemporaryDirectory(prefix="v72_holding_preview_") as tmp:
        preview_db = Path(tmp) / "preview.sqlite3"
        pending = create_pending_fill(position["ts_code"], position["name"], "模拟排版临时记录", db_path=preview_db)
        preview_position = confirm_fill(
            pending["position_id"], float(position["shares"]), float(position["cost_price"]), db_path=preview_db
        )
        result = scan_position(preview_position, now=datetime.now().astimezone(), db_path=preview_db)
    text = "【模拟持仓排版验收｜不改变真实持仓状态】\n" + build_portfolio_message(result)
    ok, detail = send_message(text)
    print("SENT" if ok else "FAILED", detail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
