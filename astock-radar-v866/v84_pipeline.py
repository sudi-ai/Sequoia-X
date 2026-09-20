# -*- coding: utf-8 -*-
"""
V8.4 统一专业管线：
潜伏启动 -> 连板生态/首板潜力 -> 竞价确认 -> 原V8买点/T+1 -> 风控 -> 复盘。
"""
from __future__ import annotations
from v83_pipeline import V83Pipeline
from limitup_pipeline import LimitupPipeline

class V84Pipeline:
    def __init__(self):
        self.base=V83Pipeline()
        self.limitup=LimitupPipeline()

    def eod_latent_scan(self,items):
        return self.base.eod_latent_scan(items)

    def limitup_ecology(self,raw_today,trade_date=None,raw_yesterday=None,broken_rate=None,
                        limit_down=0,extra_provider=None,previous_sector_ladders=None):
        return self.limitup.analyze(
            raw_today,trade_date,raw_yesterday,broken_rate,limit_down,
            extra_provider,previous_sector_ladders
        )

    def preopen_confirm(self,latent_item,auction_tick,prev_tick=None):
        return self.base.preopen_confirm(latent_item,auction_tick,prev_tick)

    def intraday_candidate(self,candidate,observed_values=None,portfolio=None,push=False):
        return self.base.intraday_candidate(candidate,observed_values,portfolio,push)

    def postclose_missed_review(self,stock_histories,latent_outcomes=None):
        return self.base.postclose_missed_review(stock_histories,latent_outcomes)
