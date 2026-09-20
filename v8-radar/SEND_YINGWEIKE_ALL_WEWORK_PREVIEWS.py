from __future__ import annotations

import argparse
import time
from datetime import datetime

from v7.env_loader import load_project_env

load_project_env()

from v7.v66_message_adapter import _build_message as build_intraday_message
from v7.v66_message_adapter import _build_pullback_message
from v7.catalyst_shadow_v72 import build_catalyst_message
from v7.wework_shadow_push import (
    build_evening_review,
    build_portfolio_message,
    build_state_message,
    build_v72_evening_review_message,
    send_message,
)


NAME = "英维克"
CODE = "002837"
STAMP = "2026-08-15T10:05:00+08:00"


def paid(minute_quality="OK", ann_risk="LOW"):
    return {
        "announcement": {
            "announcement_risk_level": ann_risk,
            "announcement_latest_time": "2026-08-15 08:30:00",
        },
        "auction": {
            "status": "AUCTION_REALTIME_UNAVAILABLE",
            "auction_realtime_available": False,
        },
        "realtime_minute": {
            "last": 30.20,
            "quality": minute_quality,
            "freshness_status": "FRESH",
            "data_time": "2026-08-15 10:04:00",
        },
        "realtime_daily": {
            "status": "OK",
            "current_price": 30.20,
            "amount": 328000000,
            "data_time": "2026-08-15 10:04:00",
        },
    }


ROW = {
    "trade_date": "2026-08-15",
    "name": NAME,
    "code": CODE,
    "v66_score": 82,
    "v71_quality_score": 79,
    "potential_label": "中",
    "next_day_potential_score": 76.5,
    "snapshot_json": {
        "candidate": {"_v64": {"trade": {"zone": "29.80—30.30"}}}
    },
}


def portfolio(state, evidence, pnl=3.42):
    return {
        "position": {
            "position_id": "PREVIEW-YWK",
            "position_type": "ACTUAL",
            "status": "HOLDING",
            "name": NAME,
            "ts_code": CODE,
            "cost_price": 29.20,
            "shares": 1000,
        },
        "current_price": 30.20,
        "unrealized_pct": pnl,
        "research": {
            "main_rise_similarity": 0.72,
            "washout_similarity": 0.64,
            "true_decline_risk_similarity": 0.18,
        },
        "classification": {
            "state": state,
            "confidence": 0.78,
            "evidence_strength": "MEDIUM",
            "reasons": evidence,
        },
        "state": {"current_state": state},
        "paid": paid(),
    }


def tagged(title, body):
    return f"【模拟排版验收 {title}｜不构成交易信号】\n" + body


messages = []
messages.append(("01 次日观察", build_state_message("WATCH", ROW, paid=paid(), now=STAMP)))
messages.append(("02 等待确认", build_state_message("PREOPEN_CHECK", ROW, reasons=["等待09:35后实时分钟确认"], paid=paid(), now=STAMP)))
messages.append(("03 买点确认", build_state_message("BUY_CONFIRMED", ROW, paid=paid(), now=STAMP)))
messages.append(("04 信号取消", build_state_message("CANCELLED", ROW, reasons=["分钟结构转弱", "板块同步走弱"], paid=paid("BAD"), now=STAMP)))
messages.append(("05 候选过期", build_state_message("EXPIRED", ROW, reasons=["确认窗口结束"], paid=paid(), now=STAMP)))

event = {
    "candidate": {"name": NAME, "code": CODE, "price": 30.20, "pct": 2.35, "move": 1.10, "amount_delta": 36800000},
    "deep": {
        "buy_score": 82, "individual_score": 78, "trend_score": 76,
        "force": {"score": 74}, "flow": {"net": 43360000},
        "trade": {"zone": "29.80—30.30", "defense": 28.90, "target1": 33.80},
    },
    "tier": "confirm", "source": "V6.4板块深度确认", "sector": "液冷服务器", "market_regime": "中性",
}
messages.append(("06 盘中观察", build_intraday_message(event, paid(), confirmed=False, reasons=["等待连续两轮确认"], now=datetime.fromisoformat(STAMP))))
messages.append(("07 盘中确认", build_intraday_message(event, paid(), confirmed=True, reasons=[], now=datetime.fromisoformat(STAMP))))

deep_confirm = "\n".join([
    "🟢 A股机会雷达 V6.6 Pro｜可建仓·深度确认", "", f"{NAME} {CODE}｜液冷服务器",
    "💰 现价 30.20｜涨幅 +2.35%｜市场 中性", "⭐ 个股 78｜趋势 76｜主力 74",
    "💵 资金 +4336万｜盈亏比 2.5:1", "🎯 观察 29.80—30.30｜防守 28.90｜目标 33.80", "",
    "【系统状态：首次确认·建立持仓跟踪】", "【模拟成本：30.20｜持有日：D0】",
    "👉 操作：允许测试仓建仓；若看到消息时已明显拉升，请等待回踩，不追高。",
    "🛡 退出防守：28.90｜🎯 参考目标：33.80", "📌 后续洗盘、转弱、失效和止盈提醒将关联本次信号。",
])
messages.append(("08 深度确认", deep_confirm))

states = [
    ("09 主升延续", "MAIN_RISE", ["趋势结构保持，量价与板块未明显转弱"], 3.42),
    ("10 健康洗盘", "WASHOUT", ["关键结构未破坏，回撤处于正常ATR区间并有承接"], 3.42),
    ("11 主升衰竭", "EXHAUSTION", ["高位振幅扩大，推进效率下降并出现冲高回落"], 3.42),
    ("12 真实转弱", "TRUE_WEAKNESS", ["趋势结构破坏，反抽偏弱且板块同步转弱"], 3.42),
    ("13 二次转强", "SECOND_STRENGTH", ["退出后重新站回结构，量价和板块共振恢复"], 3.42),
]
for title, state, evidence, pnl in states:
    messages.append((title, build_portfolio_message(portfolio(state, evidence, pnl))))

messages.append(("14 V7.1晚间复盘", build_evening_review({
    "market_risk": "中性", "strong_sectors": "液冷服务器、算力设备", "v66_count": 12,
    "v71_pass_count": 5, "filtered_count": 7, "watch_count": 3, "buy_confirmed_count": 2,
    "cancelled_count": 1, "d0": "样本未成熟", "history_performance": "等待D1/D3/D5",
    "top_watch": [{"name": NAME, "potential_label": "中"}], "api_health": "主要接口正常",
    "data_anomalies": "竞价Tick不可用，采用降级确认",
})))
messages.append(("15 V7.2晚间复盘", build_v72_evening_review_message({
    "portfolio": {"holding": 1, "simulated": 0, "pending_fill": 0, "states": {"MAIN_RISE": 1}},
    "research": {"n": 5, "mode": "ANNOTATION_ONLY"},
    "forward": {"matured": 0, "immature": 5, "by_horizon": {"3": {"n": 0}, "5": {"n": 0}}},
    "second_strength": {},
})))

catalyst = {
    "score": 84, "name": NAME, "code": CODE, "sector": "液冷服务器",
    "title": "公司中标重大液冷项目（模拟事件）", "source_type": "ANNOUNCEMENT",
    "directness": "DIRECT", "ret5": 2.10, "ma20_gap_pct": 1.20,
    "price_notes": ["价格尚未显示明显提前兑现"],
    "event_time": "2026-08-15T10:00:00+08:00",
}
messages.append(("16 盘中催化观察", build_catalyst_message(catalyst)))
messages.append(("17 隔夜催化观察", build_catalyst_message({
    **catalyst, "event_time": "2026-08-15T19:30:00+08:00",
    "title": "晚间发布重大液冷订单（模拟事件）",
})))
messages.append(("18 回踩承接确认", _build_pullback_message(
    event,
    {**paid(), "realtime_minute": {**paid()["realtime_minute"], "vwap": 30.10}},
    datetime.fromisoformat(STAMP),
)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--v72-only', action='store_true', help='仅发送09—15条持仓与复盘新版排版')
    args = parser.parse_args()
    selected = messages[8:] if args.v72_only else messages
    failures = []
    for title, body in selected:
        ok, detail = send_message(tagged(title, body))
        print(f"{title}: {'SENT' if ok else 'FAILED'} {detail[:80]}")
        if not ok:
            failures.append(title)
        time.sleep(1.2)
    print(f"TOTAL={len(selected)} SENT={len(selected)-len(failures)} FAILED={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
