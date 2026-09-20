# -*- coding: utf-8 -*-
"""
市场阶段模型路由。
不是简单“弱市减5分”，而是为不同阶段指定不同Champion/Challenger。
"""
from __future__ import annotations
DEFAULT_ROUTE={
 "冰点":{"champion":"rules_defensive","challenger":"ml_ice"},
 "修复":{"champion":"rules_repair","challenger":"ml_repair"},
 "启动":{"champion":"rules_startup","challenger":"ml_startup"},
 "主升":{"champion":"rules_trend","challenger":"ml_trend"},
 "高潮":{"champion":"rules_anti_chase","challenger":"ml_climax"},
 "退潮":{"champion":"rules_defensive","challenger":"ml_retreat"},
}
def route(phase,registry=None):
    r=dict(DEFAULT_ROUTE.get(phase,DEFAULT_ROUTE["修复"]))
    if registry:
        # 只有被正式批准的模型才可覆盖challenger/champion名称
        approved=[x for x in registry.list() if x.get("status")=="approved"]
        for x in approved:
            if x.get("config",{}).get("market_phase")==phase:
                if x.get("role")=="champion":r["champion"]=f"{x['name']}:{x['version']}"
                elif x.get("role")=="challenger":r["challenger"]=f"{x['name']}:{x['version']}"
    r["phase"]=phase
    return r
