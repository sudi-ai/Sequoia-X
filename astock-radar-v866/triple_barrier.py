# -*- coding: utf-8 -*-
"""
V8.6 Triple Barrier 标签
让“模型预测目标”和真实交易计划一致：
- 上障碍：止盈
- 下障碍：止损
- 垂直障碍：最大持有周期
哪一个先触发就用哪一个定义结果。
"""
from __future__ import annotations
from config import TRIPLE_BARRIER

def label_path(entry_price, future_bars, profit_take_pct=None, stop_loss_pct=None, max_holding_bars=None):
    entry=float(entry_price or 0)
    if entry<=0:return {"label":None,"reason":"invalid_entry"}
    pt=float(profit_take_pct if profit_take_pct is not None else TRIPLE_BARRIER["profit_take_pct"])/100
    sl=float(stop_loss_pct if stop_loss_pct is not None else TRIPLE_BARRIER["stop_loss_pct"])/100
    max_bars=int(max_holding_bars or TRIPLE_BARRIER["max_holding_bars"])
    up=entry*(1+pt);down=entry*(1-sl)

    bars=list(future_bars or [])[:max_bars]
    for i,b in enumerate(bars,1):
        high=float(b.get("high",b.get("close",0)) or 0)
        low=float(b.get("low",b.get("close",0)) or 0)

        hit_up=high>=up
        hit_down=low<=down

        # 同一根bar上下障碍都触及时，日线无法知道先后；
        # 为防回测乐观，按“保守优先止损”处理。
        if hit_up and hit_down:
            return {"label":0,"barrier":"BOTH_CONSERVATIVE_STOP","bars":i,
                    "exit_price":round(down,4),"return_pct":round(-sl*100,4)}
        if hit_down:
            return {"label":0,"barrier":"STOP","bars":i,
                    "exit_price":round(down,4),"return_pct":round(-sl*100,4)}
        if hit_up:
            return {"label":1,"barrier":"PROFIT","bars":i,
                    "exit_price":round(up,4),"return_pct":round(pt*100,4)}

    if not bars:
        return {"label":None,"reason":"no_future_bars"}

    close=float(bars[-1].get("close",entry) or entry)
    ret=(close/entry-1)*100
    # Meta-label：时间到期时只有正收益才记1，否则0
    return {"label":1 if ret>0 else 0,"barrier":"TIME","bars":len(bars),
            "exit_price":round(close,4),"return_pct":round(ret,4)}

def label_events(events, bars_by_code):
    """
    events:
      ts_code / entry_price / event_index
    bars_by_code:
      {code: ordered list of bars}; event_index 指entry bar索引，future从index+1开始。
    """
    out=[]
    for e in events:
        code=e["ts_code"];idx=int(e["event_index"])
        bars=bars_by_code.get(code,[])
        future=bars[idx+1:idx+1+TRIPLE_BARRIER["max_holding_bars"]]
        x=label_path(e["entry_price"],future)
        out.append({**e,**x})
    return out
