# -*- coding: utf-8 -*-
"""
V8.4 推荐调度：
- 盘前：昨晚潜伏池 + 今日竞价确认；
- 盘中：连板生态只做结构变化监测，不高频拉全历史；
- 收盘后：完整连板梯队入库、首板样本、错失机会复盘。
"""
from __future__ import annotations
from daily_scheduler import DailyScheduler

def recommended_jobs(preopen_fn, midmorning_fn, afternoon_fn, eod_limitup_fn, latent_fn, missed_fn):
    s=DailyScheduler()
    s.add("盘前潜伏竞价确认","09:24",preopen_fn)
    s.add("上午连板生态","10:05",midmorning_fn)
    s.add("下午连板生态","14:30",afternoon_fn)
    s.add("收盘连板梯队归档","15:10",eod_limitup_fn)
    s.add("收盘潜伏结构扫描","15:20",latent_fn)
    s.add("错失机会复盘","15:35",missed_fn)
    return s
