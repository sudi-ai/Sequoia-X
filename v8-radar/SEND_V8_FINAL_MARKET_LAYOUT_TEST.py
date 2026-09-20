from __future__ import annotations

import os
import random
from datetime import datetime

from v8.env_loader import load_v8_env


def value(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def main() -> int:
    load_v8_env()
    os.environ["V8_PUSH_ENABLED"] = "true"
    os.environ["V7_TUSHARE_ENABLED"] = "true"
    os.environ["V7_PAID_PROVIDER_ENABLED"] = "true"

    from v8.push import send_once
    from v8.tushare_init import call

    candidates = [
        "002837.SZ", "600563.SH", "000001.SZ", "601088.SH",
        "300750.SZ", "688800.SH", "002371.SZ", "601899.SH",
    ]
    code_with_market = random.choice(candidates)
    frame = call("rt_k", ts_code=code_with_market)
    if frame is None or getattr(frame, "empty", True):
        print("FAILED: EMPTY_RT_K")
        return 1
    row = frame.iloc[0].to_dict()
    name = str(row.get("name") or "名称待确认")
    code = str(row.get("ts_code") or code_with_market).split(".")[0]
    previous = value(row, "pre_close")
    latest = value(row, "close")
    percent = ((latest / previous - 1) * 100) if previous > 0 and latest > 0 else None
    percent_text = f"{percent:+.2f}%" if percent is not None else "无法计算"
    amount = value(row, "amount")
    amount_text = f"约 {amount / 1e8:.2f}亿元" if amount > 0 else "接口未提供"

    message = "\n".join([
        "【排版验收｜真实市场快照｜不构成交易信号】",
        "🔵 A股机会雷达 V8｜市场信息测试",
        "",
        f"📌 {name} {code}",
        f"💰 最新返回 {latest:.2f}｜涨跌 {percent_text}",
        f"📊 今开 {value(row, 'open'):.2f}｜最高 {value(row, 'high'):.2f}｜最低 {value(row, 'low'):.2f}",
        f"💵 成交额 {amount_text}",
        "",
        "【数据状态】",
        "✅ 来源：已购买实时日线接口 rt_k",
        "⚠ 行情时间：接口本次未返回，不能确认实时新鲜度",
        "⚪ 市场/板块/趋势/资金评分：本次未计算",
        "",
        "👉 操作",
        "仅用于微信最终排版与数据通道验收，不观察、不建仓、不追高。",
        "",
        "📌 正式信号仍须经过市场、板块、趋势、资金、持续度和风险门共同确认。",
        "V8 Research Shadow｜不自动下单｜不构成投资建议",
    ])
    key = "LAYOUT_UTF8_MARKET_TEST|" + datetime.now().astimezone().strftime("%Y%m%d%H%M%S")
    result = send_once(key, message)
    print({"sent": result[0], "detail": result[1], "code": code, "name": name})
    return 0 if result[0] else 2


if __name__ == "__main__":
    raise SystemExit(main())
