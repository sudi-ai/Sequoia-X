# -*- coding: utf-8 -*-
"""
连板生态运行器：
原始涨停池 -> 统一适配 -> 梯队/晋级 -> 首板连板潜力 -> 高标风险 -> 数据库存档。
"""
from __future__ import annotations
from datetime import datetime
from limitup_data_adapter import normalize_rows
from limitup_ecology import analyze,leader_risk
from first_board_potential import potential
from seal_quality import first_seal_score,reseal_quality,turnover_quality,seal_strength
from limitup_store import LimitupStore

class LimitupRuntime:
    def __init__(self,store=None):
        self.store=store or LimitupStore()

    def analyze_day(self,raw_today,trade_date=None,raw_yesterday=None,broken_rate=None,limit_down=0,
                    extra_provider=None):
        td=trade_date or datetime.now().strftime("%Y%m%d")
        today=normalize_rows(raw_today,td)
        yesterday=normalize_rows(raw_yesterday,trade_date=None) if raw_yesterday is not None else []
        eco=analyze(today,yesterday or None,broken_rate,limit_down)
        self.store.save_day(td,today)
        self.store.save_ecology(td,eco)

        first_boards=[]
        high_boards=[]
        sector_map={x["sector"]:x for x in eco["sectors"]}

        for x in today:
            b=int(x.get("board",1))
            if b==1:
                extra=dict(extra_provider(x) if extra_provider else {})
                sec=sector_map.get(x.get("sector") or x.get("reason") or "未分类",{})
                features={
                    "latent_score":extra.get("latent_score",50),
                    "first_seal_score":first_seal_score(x.get("first_time")),
                    "reseal_quality":reseal_quality(x.get("open_times"),x.get("last_time")),
                    "seal_strength":seal_strength(x.get("seal_amount"),x.get("amount"),x.get("seal_ratio")),
                    "turnover_quality":turnover_quality(x.get("turnover")),
                    "sector_ladder":min(100,45+sec.get("max_board",1)*8+sec.get("count",1)*5+sec.get("ladder_depth",1)*5),
                    "sector_heat":extra.get("sector_heat",50),
                    "auction_quality":extra.get("auction_quality",50),
                    "main_flow":extra.get("main_flow",50),
                    "catalyst":extra.get("catalyst",50),
                    "crowding_risk":extra.get("crowding_risk",0),
                    "event_risk":extra.get("event_risk",0),
                    "regulatory_risk":extra.get("regulatory_risk",0),
                    "unlock_risk":extra.get("unlock_risk",0),
                    "st_or_delist":extra.get("st_or_delist",False),
                    "broken_rate_market":float(broken_rate or 0),
                }
                r=potential(features)
                r.update({"ts_code":x["ts_code"],"name":x["name"],"trade_date":td,"sector":x.get("sector",""),"limitup":x})
                first_boards.append(r)
            elif b>=4:
                y=dict(x)
                y["leader_risk"]=leader_risk(x,eco)
                high_boards.append(y)

        return {
            "trade_date":td,
            "rows":today,
            "ecology":eco,
            "first_board_potential":sorted(first_boards,key=lambda z:z["board_potential"],reverse=True),
            "high_board_risk":sorted(high_boards,key=lambda z:z["leader_risk"],reverse=True),
        }
