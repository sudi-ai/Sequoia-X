# -*- coding: utf-8 -*-
"""
轻量日内任务调度器。
不强行嵌入现有V8主循环，可在桌面工作台或服务进程中调用 tick()。
"""
from __future__ import annotations
from datetime import datetime

class DailyScheduler:
    def __init__(self):
        self.jobs=[]; self.done=set()

    def add(self,name,hhmm,fn):
        self.jobs.append((name,hhmm,fn))

    def tick(self,now=None):
        now=now or datetime.now()
        day=now.strftime("%Y%m%d"); cur=now.strftime("%H:%M")
        for name,hhmm,fn in self.jobs:
            key=(day,name)
            if key in self.done:continue
            if cur>=hhmm:
                try:fn()
                finally:self.done.add(key)

def recommended_jobs(latent_scan, missed_review):
    s=DailyScheduler()
    # 潜伏结构主要来自日线，收盘后/盘前扫描即可，不需要20秒全A重复拉日线。
    s.add("潜伏结构收盘扫描","15:20",latent_scan)
    s.add("错失机会复盘","15:35",missed_review)
    return s
