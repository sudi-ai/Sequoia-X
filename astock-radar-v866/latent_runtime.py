# -*- coding: utf-8 -*-
"""
潜伏扫描运行器。
它不改变原V8买点逻辑，只负责提前把“安静但结构变好”的票送入 WATCH/READY/START 潜伏池。
"""
from __future__ import annotations
from datetime import datetime
from latent_startup import analyze_kline
from latent_store import LatentStore
from event_bus import EventBus

class LatentRuntime:
    def __init__(self,store=None,bus=None):
        self.store=store or LatentStore()
        self.bus=bus or EventBus().start()

    def evaluate(self,ts_code,name,kline,extra=None,save=True):
        x=analyze_kline(kline,extra)
        x.update({
            "ts_code":ts_code,"name":name,
            "trade_date":str(kline[-1].get("date","")).replace("-","") if kline else datetime.now().strftime("%Y%m%d"),
            "observed_at":datetime.now().isoformat(timespec="seconds"),
        })
        if save and x["latent_stage"] in ("WATCH","READY","START"):
            x["latent_id"]=self.store.save_latent(x)
            self.bus.emit("LATENT_"+x["latent_stage"],x)
        return x

    def scan(self,items):
        """
        items:
        [{"ts_code","name","kline","extra"}, ...]
        """
        out=[]
        for item in items:
            try:
                x=self.evaluate(item["ts_code"],item.get("name",""),item["kline"],item.get("extra"))
                if x["latent_stage"] in ("WATCH","READY","START"):
                    out.append(x)
            except Exception:
                continue
        return sorted(out,key=lambda x:x["latent_score"],reverse=True)
