# -*- coding: utf-8 -*-
REQUIRED_TOP_LEVEL = ("market", "themes", "candidates")
CANDIDATE_RECOMMENDED = (
    "name","ts_code","daily_pct","sector","market_temp","sector_strength","auction_quality",
    "main_flow_score","chip_lock_score","close_strength","event_risk","distribution_risk",
    "trend_score","buy_timing_score","rr","entry_price","action","defense","target"
)

def validate_snapshot(x):
    errors=[]
    if not isinstance(x,dict): return ["snapshot必须是dict"]
    for k in REQUIRED_TOP_LEVEL:
        if k not in x: errors.append(f"缺少顶层字段:{k}")
    if "candidates" in x and not isinstance(x["candidates"],list): errors.append("candidates必须是list")
    return errors
