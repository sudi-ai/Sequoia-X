# -*- coding: utf-8 -*-
"""
收盘后错失机会复盘运行器。
真实生产建议：
- 每日收盘后读取最近10~20个交易日所有股票日线；
- 找出过去5/10日大涨样本；
- 反查潜伏池是否提前覆盖；
- 统计 Big Mover Recall + Miss Reason；
- 不自动修改正式阈值，只生成 Challenger 建议。
"""
from __future__ import annotations
from missed_opportunity import review_case,summary
from latent_store import LatentStore

class MissedReviewRunner:
    def __init__(self,store=None):
        self.store=store or LatentStore()

    def review_histories(self,stocks):
        """
        stocks:
        [{"ts_code","name","kline","extra_history": optional callable}, ...]
        """
        cases=[]
        for s in stocks:
            try:
                x=review_case(
                    s["ts_code"],s.get("name",""),s["kline"],
                    extra_history=s.get("extra_history"),store=self.store
                )
                if x:cases.append(x)
            except Exception:
                continue
        return {"cases":cases,"summary":summary(cases),"store_metrics":self.store.metrics()}
