# -*- coding: utf-8 -*-
"""
V8.4 连板生态统一 Facade。
"""
from __future__ import annotations
from limitup_runtime import LimitupRuntime
from mainline_rotation import rank_sectors,early_mainline_candidates
from limitup_wecom import build_ecology_summary

class LimitupPipeline:
    def __init__(self,store=None):
        self.runtime=LimitupRuntime(store=store)

    def analyze(self,raw_today,trade_date=None,raw_yesterday=None,broken_rate=None,limit_down=0,
                extra_provider=None,previous_sector_ladders=None):
        r=self.runtime.analyze_day(raw_today,trade_date,raw_yesterday,broken_rate,limit_down,extra_provider)
        ranked=rank_sectors(r["ecology"]["sectors"],previous_sector_ladders)
        r["sector_rotation"]=ranked
        r["early_mainlines"]=early_mainline_candidates(ranked)
        r["wecom_preview"]=build_ecology_summary(r)
        return r
