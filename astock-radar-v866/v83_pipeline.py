# -*- coding: utf-8 -*-
"""
V8.3 推荐正式链路（适配现有V8，不替换它）：

收盘:
  本地/缓存日线 -> 潜伏启动扫描 -> WATCH/READY/START
次日竞价:
  stk_auction_tick -> 竞价确认 -> PREOPEN_PRIORITY
盘中:
  原V8全A/板块/资金/主升/买点 -> A/B/C -> T+1 -> 风控
收盘后:
  错失机会自动复盘 -> Precision/Recall -> Challenger阈值建议

这里提供统一Facade，开发时只需要接这几个方法。
"""
from __future__ import annotations
from latent_runtime import LatentRuntime
from latent_auction_confirm import confirm as auction_confirm
from runtime_engine import RuntimeEngine
from missed_review_runner import MissedReviewRunner
from threshold_advisor import advise

class V83Pipeline:
    def __init__(self):
        self.latent=LatentRuntime()
        self.intraday=RuntimeEngine()
        self.missed=MissedReviewRunner(store=self.latent.store)

    def eod_latent_scan(self,items):
        return self.latent.scan(items)

    def preopen_confirm(self,latent_item,auction_tick,prev_tick=None):
        return auction_confirm(latent_item,auction_tick,prev_tick)

    def intraday_candidate(self,candidate,observed_values=None,portfolio=None,push=False):
        return self.intraday.process_candidate(candidate,observed_values,portfolio,push)

    def postclose_missed_review(self,stock_histories,latent_outcomes=None):
        r=self.missed.review_histories(stock_histories)
        if latent_outcomes is not None:
            from latent_metrics import evaluate
            metrics=evaluate(latent_outcomes,r["cases"])
            r["quality_metrics"]=metrics
            r["threshold_advice"]=advise(metrics)
        return r
