"""
A股机会雷达 V6.6 自适应优化模块
加入：
1. 信号来源胜率自适应
2. 板块延续评分框架
3. D1隔夜风险评分

作为V6.5测试增强模块，不破坏原核心。
"""
from collections import defaultdict

SOURCE_STATS = defaultdict(lambda: {"count":0,"win":0,"avg":0.0})


def update_source_result(source, pnl):
    s=SOURCE_STATS[source]
    s["count"] += 1
    s["win"] += 1 if pnl>0 else 0
    s["avg"] = (s["avg"]*(s["count"]-1)+pnl)/s["count"]


def source_weight(source):
    s=SOURCE_STATS[source]
    if s["count"] < 10:
        return 1.0
    win=s["win"]/s["count"]
    if win>=0.6:
        return 1.10
    if win<0.35:
        return 0.85
    return 1.0


def d1_risk_score(change_pct=0, ma_bias=0, tail_flow=0, profit_ratio=0, sector_score=50):
    risk=0
    if change_pct>8: risk+=20
    elif change_pct>5: risk+=10
    if ma_bias>10: risk+=20
    elif ma_bias>6: risk+=10
    if tail_flow<0: risk+=15
    if profit_ratio>80: risk+=15
    if sector_score<50: risk+=15
    return min(100,risk)


def sector_next_day_score(capital=0, breadth=0, leader=0, tail=0, news=0):
    return round(capital*0.30+breadth*0.20+leader*0.20+tail*0.15+news*0.15,2)
