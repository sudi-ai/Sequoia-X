from __future__ import annotations

import os
import time
from datetime import datetime

from v8.env_loader import load_v8_env


def main() -> int:
    load_v8_env();os.environ["V8_PUSH_ENABLED"]="true"
    from v8.message import (build_evening_review_message,build_heartbeat_message,
      build_portfolio_summary_message,build_state_transition_message)
    from v8.push import send_once
    position={"name":"英维克","code":"002837","cost_price":58.90}
    evidence={"price":60.20,"pnl_pct":2.21,"trailing_stop":57.80,"state_reason":"趋势结构、分钟与板块证据共同确认"}
    summary_positions=[{**position,"current_state":"MAIN_RISE","details":evidence}]
    health={"calls_last_minute":3,"limit":30,"circuit_open":False}
    messages=[
      ("01 主升延续",build_state_transition_message(position,"MAIN_RISE",evidence)),
      ("02 健康洗盘",build_state_transition_message(position,"WASHOUT",{**evidence,"price":58.20,"pnl_pct":-1.19})),
      ("03 二次转强",build_state_transition_message(position,"SECOND_STRENGTH",{**evidence,"price":59.80,"pnl_pct":1.53})),
      ("04 09:30心跳",build_heartbeat_message("上午",1,health)),
      ("05 11:30摘要",build_portfolio_summary_message("午间",summary_positions)),
      ("06 13:00心跳",build_heartbeat_message("下午",1,health)),
      ("07 15:05摘要",build_portfolio_summary_message("收盘",summary_positions)),
      ("08 盘后复盘",build_evening_review_message({"actions":{"WATCH":3,"SHADOW_ENTRY_CONFIRMED":1,"BLOCKED":2},
        "audit_total":6,"discovered":4,"blocked":2,"missed":2,"forward":[]})),
    ]
    batch=datetime.now().astimezone().strftime("%Y%m%d%H%M%S");failed=[]
    for index,(title,body) in enumerate(messages,1):
        text=f"【新增功能排版验收 {title}｜模拟内容】\n"+body
        ok,detail=send_once(f"V8_NEW_NOTICE_PREVIEW|{batch}|{index}",text)
        print({"title":title,"sent":ok,"detail":detail})
        if not ok:failed.append(title)
        time.sleep(.8)
    return 1 if failed else 0


if __name__=="__main__":raise SystemExit(main())
