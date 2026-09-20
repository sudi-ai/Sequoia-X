from __future__ import annotations

import os
import time
from datetime import datetime

from v8.env_loader import load_v8_env


def main() -> int:
    load_v8_env()
    os.environ["V8_PUSH_ENABLED"] = "true"
    from v8.message import build_decision_message, build_holding_message
    from v8.push import send_once

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    event = {
        "name": "英维克", "code": "002837", "sector": "液冷服务器",
        "executable_price": 57.09, "checkpoint_minute": 6,
        "data_time": stamp, "evaluation_time": stamp,
    }
    base = {
        "opportunity_score": 82,
        "market": {"label": "正常", "score": 68},
        "sector": {"label": "共振", "score": 78},
        "trend": {"score": 80},
        "fund": {"score": 76},
        "persistence": {"score": 74},
        "risk_score": 22,
        "position_pct": 8,
        "trade_plan": {"entry_low": 56.50, "entry_high": 57.20, "stop_price": 54.80},
        "reasons": ["板块扩散正常", "趋势结构保持", "资金持续度等待复核"],
        "vetoes": [],
    }
    decisions = [
        ("01 继续观察", {**base, "action": "WATCH"}),
        ("02 风险拦截", {**base, "action": "BLOCKED", "position_pct": 0,
                         "risk_score": 88, "reasons": ["出现重大风险证据"],
                         "vetoes": ["公告硬风险否决"]}),
        ("03 建仓条件确认", {**base, "action": "SHADOW_ENTRY_CONFIRMED",
                             "reasons": ["持续度确认", "回踩承接确认", "风险预算匹配"]}),
    ]

    position = {"name": "英维克", "code": "002837", "cost_price": 58.90}
    evidence = {"price": 57.09, "state": "真实转弱", "pnl_pct": -3.07, "trailing_stop": 56.80}
    holdings = [
        ("04 T+1锁定风险", {"action": "T1_LOCKED_RISK_ALERT",
                            "reason": "A股T+1不可当日卖出；记录次日优先处理"}),
        ("05 减仓提醒", {"action": "REDUCE", "reason": "主升衰竭，并获得分钟资金转弱佐证"}),
        ("06 退出提醒", {"action": "EXIT", "reason": "关键结构与分钟资金共振转弱，防守优先"}),
    ]

    messages = [(title, build_decision_message(event, decision)) for title, decision in decisions]
    messages += [(title, build_holding_message(position, action, evidence)) for title, action in holdings]
    failed = []
    batch = datetime.now().astimezone().strftime("%Y%m%d%H%M%S")
    for index, (title, body) in enumerate(messages, 1):
        tagged = f"【全类型排版验收 {title}｜模拟内容｜不构成交易信号】\n" + body
        ok, detail = send_once(f"V8_ALL_LAYOUT|{batch}|{index}", tagged)
        print({"title": title, "sent": ok, "detail": detail})
        if not ok:
            failed.append(title)
        time.sleep(0.8)
    print({"total": len(messages), "sent": len(messages) - len(failed), "failed": failed})
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
