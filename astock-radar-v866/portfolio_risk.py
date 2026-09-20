# -*- coding: utf-8 -*-
"""
订单/持仓/策略三级风险门：先做“能不能承担这笔风险”，再谈买点。
"""
from __future__ import annotations

DEFAULTS = {
    "max_position_pct": 0.15,
    "max_sector_pct": 0.35,
    "max_total_exposure": 0.80,
    "max_single_trade_risk_pct": 0.012,
    "max_daily_loss_pct": 0.025,
    "max_concurrent_positions": 8,
}

def evaluate_order(order, portfolio, limits=None):
    lim=dict(DEFAULTS); lim.update(limits or {})
    reasons=[]
    nav=max(float(portfolio.get("nav",0)),1.0)
    order_value=float(order.get("value",0))
    stop_loss_pct=abs(float(order.get("stop_loss_pct",0)))/100
    pos_pct=order_value/nav
    trade_risk=order_value*stop_loss_pct/nav

    if pos_pct>lim["max_position_pct"]:reasons.append("单票仓位超限")
    if trade_risk>lim["max_single_trade_risk_pct"]:reasons.append("单笔止损风险超限")
    if float(portfolio.get("exposure_pct",0))+pos_pct>lim["max_total_exposure"]:reasons.append("总仓位超限")
    if int(portfolio.get("positions_count",0))>=lim["max_concurrent_positions"]:reasons.append("持仓数量超限")
    if float(portfolio.get("daily_pnl_pct",0))<=-lim["max_daily_loss_pct"]:reasons.append("达到当日亏损保护线")

    sector=order.get("sector","")
    if sector:
        sector_now=float((portfolio.get("sector_exposure") or {}).get(sector,0))
        if sector_now+pos_pct>lim["max_sector_pct"]:reasons.append("板块集中度超限")

    return {"allowed":not reasons,"reasons":reasons,"position_pct":round(pos_pct,4),"trade_risk_pct":round(trade_risk,4)}
