# -*- coding: utf-8 -*-
"""
潜伏池 -> 次日竞价确认。
不是竞价买入器，只用于决定“是否升级扫描优先级”。
"""
from __future__ import annotations
from auction_engine import quality_score

def confirm(latent_item, auction_tick, prev_tick=None):
    q=quality_score(auction_tick,prev_tick)
    base=float(latent_item.get("latent_score",0))
    combined=base*.65+q*.35
    if latent_item.get("latent_stage") not in ("READY","START"):
        status="KEEP_WATCH"
    elif combined>=84 and q>=72:
        status="PREOPEN_PRIORITY"
    elif q<45:
        status="AUCTION_WEAKEN"
    else:
        status="KEEP_READY"
    return {
        "auction_quality":round(q,1),
        "combined_preopen_score":round(combined,1),
        "preopen_status":status,
        "is_buy_signal":False,
        "action":{
            "PREOPEN_PRIORITY":"提升到开盘重点监控，仍等待原V8买点",
            "AUCTION_WEAKEN":"竞价弱化，潜伏信号降级",
            "KEEP_READY":"保持READY，等待盘中确认",
            "KEEP_WATCH":"维持观察"
        }[status]
    }
