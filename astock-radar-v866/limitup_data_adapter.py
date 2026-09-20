# -*- coding: utf-8 -*-
"""
涨停/连板数据统一适配层。
兼容不同数据源常见字段名，避免业务层直接依赖某个平台列名。
"""
from __future__ import annotations
from datetime import datetime

ALIASES={
    "code":["ts_code","代码","证券代码","code","symbol"],
    "name":["name","名称","股票名称","证券简称"],
    "pct":["pct_chg","涨跌幅","涨幅","pct_change"],
    "board":["board","连板数","连板高度","连板","boards","board_num","limit_times","num"],
    "first_time":["首次封板时间","首封时间","first_time","first_limit_time","first_time_hhmm"],
    "last_time":["最后封板时间","末次封板时间","last_time","last_limit_time"],
    "open_times":["炸板次数","开板次数","open_times","break_times"],
    "amount":["成交额","amount","turnover"],
    "turnover":["换手率","turnover_rate","turnover"],
    "seal_amount":["封单额","封单金额","seal_amount","order_amount"],
    "seal_ratio":["封单占比","封成比","seal_ratio","order_ratio"],
    "sector":["所属行业","行业","板块","题材","sector","industry"],
    "reason":["涨停原因类别","涨停原因","原因","reason","tag"],
}

def _pick(r,key,default=None):
    for k in ALIASES[key]:
        if k in r and r[k] not in (None,""):
            return r[k]
    return default

def _num(v,default=0.0):
    if v in (None,""):return float(default)
    s=str(v).replace("%","").replace(",","").strip()
    try:return float(s)
    except Exception:return float(default)

def _board_num(v):
    if v in (None,""):return 1
    s=str(v)
    # 例如 "4天4板"/"3连板"/"4板"
    import re
    nums=[int(x) for x in re.findall(r"\d+",s)]
    if not nums:return 1
    if "天" in s and "板" in s and len(nums)>=2:return nums[-1]
    return nums[-1]

def normalize_rows(df,trade_date=None):
    if df is None:return []
    if hasattr(df,"to_dict"):
        rows=df.to_dict("records")
    elif isinstance(df,list):
        rows=df
    else:
        return []
    out=[]
    td=trade_date or datetime.now().strftime("%Y%m%d")
    for r in rows:
        code=str(_pick(r,"code","")).strip()
        if not code:continue
        out.append({
            "trade_date":td,
            "ts_code":code,
            "name":str(_pick(r,"name","")).strip(),
            "pct":_num(_pick(r,"pct",0)),
            "board":_board_num(_pick(r,"board",1)),
            "first_time":str(_pick(r,"first_time","")),
            "last_time":str(_pick(r,"last_time","")),
            "open_times":int(_num(_pick(r,"open_times",0))),
            "amount":_num(_pick(r,"amount",0)),
            "turnover":_num(_pick(r,"turnover",0)),
            "seal_amount":_num(_pick(r,"seal_amount",0)),
            "seal_ratio":_num(_pick(r,"seal_ratio",0)),
            "sector":str(_pick(r,"sector","")).strip(),
            "reason":str(_pick(r,"reason","")).strip(),
            "raw":r,
        })
    return out
