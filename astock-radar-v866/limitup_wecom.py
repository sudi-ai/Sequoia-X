# -*- coding: utf-8 -*-
"""
连板生态企业微信摘要：只推结构变化，不逐只刷屏。
"""
from __future__ import annotations
from wecom_push import send_text

def build_ecology_summary(result,max_first=5):
    eco=result["ecology"]
    lines=[
        f"🔥 A股机会雷达 V8.4｜连板生态 {result.get('trade_date','')}",
        f"阶段：{eco.get('phase')}｜生态分{eco.get('score'):.0f}｜空间{eco.get('max_board')}板",
        f"连板数：{eco.get('multi_board_count')}｜涨停总数：{eco.get('total_limitups')}",
    ]
    promo=(eco.get("promotion") or {}).get("rates",{})
    if promo:
        text="｜".join(f"{k}:{v*100:.0f}%" for k,v in promo.items() if v is not None)
        lines.append("晋级："+text)
    suc=eco.get("succession") or {}
    if suc.get("happened"):
        lines.append(f"🔄 高标接班：旧{','.join(suc.get('old_leaders',[]))} → 新{','.join(suc.get('new_leaders',[]))}")

    good=[x for x in result.get("first_board_potential",[]) if x.get("board_grade") in ("A","B")][:max_first]
    if good:
        lines+=["","🌱 首板连板潜力"]
        for i,x in enumerate(good,1):
            lines.append(f"{i}. {x.get('name')} {x.get('ts_code')}｜潜力{x.get('board_potential'):.0f}｜{x.get('board_grade')}")
    risks=result.get("high_board_risk",[])[:3]
    if risks:
        lines+=["","⚠️ 高标风险"]
        for x in risks:
            lines.append(f"{x.get('name')} {x.get('board')}板｜风险{x.get('leader_risk'):.0f}")
    return "\n".join(lines)

def push_ecology(result):
    return send_text(build_ecology_summary(result))
