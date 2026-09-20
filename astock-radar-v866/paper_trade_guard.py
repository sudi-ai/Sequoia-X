# -*- coding: utf-8 -*-
"""
新模型上线前模拟盘保护。
"""
from __future__ import annotations
from datetime import datetime

class PaperTradeGuard:
    def __init__(self,min_days=10,min_signals=30):
        self.min_days=min_days;self.min_signals=min_signals

    def evaluate(self,records):
        days=len(set(x.get("trade_date") for x in records if x.get("trade_date")))
        signals=len(records)
        returns=[float(x["return"]) for x in records if x.get("return") is not None]
        wins=sum(1 for x in returns if x>0)
        return {
            "days":days,"signals":signals,
            "win_rate":wins/len(returns) if returns else None,
            "ready":days>=self.min_days and signals>=self.min_signals and len(returns)>=self.min_signals
        }
