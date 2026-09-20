# -*- coding: utf-8 -*-
"""
A股真实执行仿真器（轻量）
重点修正“回测有信号=一定成交”的错误。
包含：
- T+1卖出限制
- 涨停买不到 / 跌停卖不掉
- 手续费/印花税
- 滑点
- 成交量参与率上限
"""
from __future__ import annotations
from datetime import datetime
from config import EXECUTION

def _bps(x):return float(x)/10000.0

def can_fill(order,market):
    side=order.get("side","BUY").upper()
    qty=float(order.get("qty",0))
    if qty<=0:return {"fillable":False,"reason":"数量<=0"}

    # A股T+1：当天买入的普通股票当天不可卖
    if side=="SELL" and order.get("acquired_trade_date") and order.get("trade_date")==order.get("acquired_trade_date"):
        return {"fillable":False,"reason":"T+1限制"}

    if side=="BUY" and market.get("at_limit_up") and not market.get("queue_fill_possible",False):
        return {"fillable":False,"reason":"涨停无可成交卖盘"}
    if side=="SELL" and market.get("at_limit_down") and not market.get("queue_fill_possible",False):
        return {"fillable":False,"reason":"跌停无可成交买盘"}

    bar_volume=float(market.get("available_volume",market.get("volume",0)) or 0)
    if bar_volume>0:
        max_qty=bar_volume*float(EXECUTION["max_participation"])
        if qty>max_qty:
            return {"fillable":True,"reason":"部分成交","max_qty":max_qty}
    return {"fillable":True,"reason":"可成交","max_qty":qty}

def simulate_fill(order,market):
    chk=can_fill(order,market)
    if not chk["fillable"]:
        return {**chk,"filled_qty":0,"fill_price":None,"fees":0}
    side=order.get("side","BUY").upper()
    qty=min(float(order.get("qty",0)),float(chk.get("max_qty",order.get("qty",0))))
    ref=float(market.get("ask" if side=="BUY" else "bid", market.get("price",order.get("price",0))) or 0)
    slip=_bps(EXECUTION["slippage_bps"])
    fill=ref*(1+slip if side=="BUY" else 1-slip)
    value=fill*qty
    commission=max(0.0,value*_bps(EXECUTION["commission_bps"]))
    stamp=value*_bps(EXECUTION["stamp_tax_bps_sell"]) if side=="SELL" else 0.0
    return {
        **chk,"filled_qty":round(qty,2),"fill_price":round(fill,4),
        "value":round(value,2),"fees":round(commission+stamp,2),
        "commission":round(commission,2),"stamp_tax":round(stamp,2)
    }

def trade_return(buy_fill,sell_fill):
    if not buy_fill.get("filled_qty") or not sell_fill.get("filled_qty"):return None
    qty=min(float(buy_fill["filled_qty"]),float(sell_fill["filled_qty"]))
    cost=float(buy_fill["fill_price"])*qty+float(buy_fill.get("fees",0))
    proceeds=float(sell_fill["fill_price"])*qty-float(sell_fill.get("fees",0))
    return round((proceeds/cost-1)*100,4) if cost>0 else None
