# -*- coding: utf-8 -*-
"""
潜伏池微信策略：
- WATCH：不推；
- READY：只进入工作台/精选跟踪；
- START：批量汇总推送，不逐只刷屏；
- 真正“买点触发”仍由原V8独立发送。
"""
from __future__ import annotations
from wecom_push import send_text

def build_summary(items,max_items=6):
    starts=[x for x in items if x.get("latent_stage")=="START"]
    if not starts:return None
    starts=sorted(starts,key=lambda x:x.get("latent_score",0),reverse=True)[:max_items]
    lines=["◎ A股机会雷达 V8.3｜启动前候选","⚠️ 这不是买点，等待原V8买点确认",""]
    for i,x in enumerate(starts,1):
        lines.append(f"{i}. {x.get('name','')} {x.get('ts_code','')}｜潜伏{x.get('latent_score',0):.1f}")
        lines.append(f"   均线{x.get('ma_cluster',0):.0f}｜平台{x.get('platform',0):.0f}｜距突破{x.get('breakout_proximity',0):.0f}")
        lines.append(f"   👉 {x.get('action','等待确认')}")
    return "\n".join(lines)

def push_summary(items):
    msg=build_summary(items)
    return send_text(msg) if msg else False
