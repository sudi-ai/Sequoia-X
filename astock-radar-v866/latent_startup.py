# -*- coding: utf-8 -*-
"""
V8.3 潜伏启动雷达
目标：发现“还没明显加速，但结构已经进入可能启动窗口”的股票。

重要：
1) 潜伏信号不是买点，不主动替代原V8买点模型。
2) 只使用决策时刻可见数据，避免事后看图优化。
3) 评分强调：均线收敛、平台收敛、量能干涸、接近平台上沿、
   中期趋势、筹码锁定、资金潜伏、板块温度、事件风险。
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from statistics import pstdev
from typing import Iterable
from config import LATENT_GATE

def _clip(v,a=0.0,b=100.0):
    return max(a,min(b,float(v)))

def _safe_mean(xs):
    xs=[float(x) for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else 0.0

def _ma(values,n):
    if len(values)<n:return None
    return sum(values[-n:])/n

def _pct(a,b):
    return (a/b-1)*100 if b else 0.0

def ma_cluster_score(close, ma_values):
    """MA5/10/20/30 越靠拢越高；价格本身过度偏离则扣分。"""
    mas=[float(x) for x in ma_values if x and float(x)>0]
    if len(mas)<3 or close<=0:return 0.0
    dispersion=pstdev(mas)/close*100
    score=100-dispersion*28
    mean=_safe_mean(mas)
    gap=abs(_pct(close,mean))
    score-=max(0,gap-2.0)*4
    return round(_clip(score),1)

def platform_score(highs,lows,closes,window=20):
    """
    平台质量：
    - 最近20日振幅越窄越好；
    - 最近10日低点抬高加分；
    - 不能是持续单边下跌。
    """
    if len(closes)<window or len(highs)<window or len(lows)<window:return 0.0
    h=max(highs[-window:]); l=min(lows[-window:]); c=closes[-1]
    width=(h/l-1)*100 if l>0 else 99
    score=100-width*3.2
    if len(lows)>=10:
        first=min(lows[-10:-5] or lows[-10:])
        second=min(lows[-5:])
        if first>0 and second>=first*.99: score+=8
    if closes[-1] < _ma(closes,20)*.97: score-=18
    return round(_clip(score),1)

def volume_dry_score(volumes):
    """潜伏期更偏好近期缩量，而不是已经巨量高潮。"""
    if len(volumes)<20:return 50.0
    v5=_safe_mean(volumes[-5:]); v20=_safe_mean(volumes[-20:])
    if v20<=0:return 50.0
    ratio=v5/v20
    if .45<=ratio<=.85:return 92.0
    if .30<=ratio<.45:return 78.0
    if .85<ratio<=1.05:return 70.0
    if 1.05<ratio<=1.25:return 55.0
    return 30.0

def breakout_proximity_score(close, highs, window=20):
    """
    距平台上沿 0~5% 为潜伏窗口；已经有效突破很多则交给原V8主升扫描。
    """
    if len(highs)<window or close<=0:return 0.0
    top=max(highs[-window:])
    gap=(top/close-1)*100
    if -1.0<=gap<=1.0:return 96.0
    if 1.0<gap<=2.5:return 90.0
    if 2.5<gap<=5.0:return 78.0
    if 5.0<gap<=8.0:return 55.0
    if gap< -1.0:return 45.0
    return 30.0

def midtrend_score(closes):
    """MA60向上 + 价格不明显跌破MA60，更适合潜伏。"""
    if len(closes)<65:return 50.0
    ma60_now=_ma(closes,60)
    ma60_old=sum(closes[-65:-5])/60
    c=closes[-1]
    score=50
    if ma60_now>ma60_old:score+=25
    else:score-=18
    if c>=ma60_now:score+=20
    elif c<ma60_now*.95:score-=20
    return round(_clip(score),1)

def first_expansion_score(volumes, closes):
    """
    识别“安静很久后第一次温和放量”，避免等到爆量才发现。
    """
    if len(volumes)<21 or len(closes)<6:return 50.0
    base=_safe_mean(volumes[-21:-1])
    cur=volumes[-1]
    if base<=0:return 50.0
    vr=cur/base
    ret1=_pct(closes[-1],closes[-2])
    if 1.15<=vr<=1.8 and -.5<=ret1<=4.5:return 90.0
    if .9<=vr<1.15:return 68.0
    if 1.8<vr<=2.5:return 62.0
    if vr>3:return 30.0
    return 45.0

def compute_latent_features(kline, extra=None):
    """
    kline: list[dict] oldest -> newest，要求 high/low/close/volume。
    extra 可传：
      chip_lock_score / accumulation_score / sector_heat /
      event_risk / unlock_risk / distribution_risk / data_quality / daily_pct
    """
    extra=dict(extra or {})
    closes=[float(x["close"]) for x in kline if x.get("close") not in (None,0)]
    highs=[float(x["high"]) for x in kline if x.get("high") not in (None,0)]
    lows=[float(x["low"]) for x in kline if x.get("low") not in (None,0)]
    vols=[float(x.get("volume",x.get("vol",0)) or 0) for x in kline]
    if len(closes)<30:
        return {"data_quality":0.0,"reason":"K线不足30日"}

    c=closes[-1]
    mas=[_ma(closes,5),_ma(closes,10),_ma(closes,20),_ma(closes,30)]
    out={
        "close":c,
        "ma_cluster":ma_cluster_score(c,mas),
        "platform":platform_score(highs,lows,closes),
        "volume_dry":volume_dry_score(vols),
        "breakout_proximity":breakout_proximity_score(c,highs),
        "midtrend":midtrend_score(closes),
        "first_expansion":first_expansion_score(vols,closes),
        "chip_lock":float(extra.get("chip_lock_score",50)),
        "accumulation":float(extra.get("accumulation_score",50)),
        "sector_heat":float(extra.get("sector_heat",50)),
        "event_risk":float(extra.get("event_risk",0)),
        "unlock_risk":float(extra.get("unlock_risk",0)),
        "distribution_risk":float(extra.get("distribution_risk",0)),
        "daily_pct":float(extra.get("daily_pct", _pct(closes[-1],closes[-2]) if len(closes)>1 else 0)),
        "data_quality":float(extra.get("data_quality",1.0)),
    }
    return out

def latent_score(f):
    # 主体结构 72%，资金/筹码/板块 28%，风险做惩罚
    score=(
        f.get("ma_cluster",0)*.17 +
        f.get("platform",0)*.17 +
        f.get("volume_dry",0)*.11 +
        f.get("breakout_proximity",0)*.14 +
        f.get("midtrend",0)*.10 +
        f.get("first_expansion",0)*.09 +
        f.get("chip_lock",50)*.08 +
        f.get("accumulation",50)*.08 +
        f.get("sector_heat",50)*.06
    )
    score-=f.get("event_risk",0)*.08
    score-=f.get("unlock_risk",0)*.06
    score-=f.get("distribution_risk",0)*.08
    if f.get("daily_pct",0)>LATENT_GATE["max_daily_pct"]:
        score-=12+(f["daily_pct"]-LATENT_GATE["max_daily_pct"])*2
    if f.get("data_quality",1)<LATENT_GATE["min_data_quality"]:
        score-=22
    return round(_clip(score),1)

def classify_latent(f):
    s=latent_score(f)
    hard=[]
    if f.get("data_quality",1)<LATENT_GATE["min_data_quality"]:hard.append("数据质量不足")
    if f.get("daily_pct",0)>8.5:hard.append("已明显加速，转主升扫描")
    if f.get("st_or_delist"):hard.append("ST/退市风险")
    if f.get("regulatory_risk",0)>=75:hard.append("监管风险高")
    if f.get("liquidity_score",50)<30:hard.append("流动性不足")
    if f.get("event_risk",0)>=75:hard.append("事件风险高")
    if f.get("unlock_risk",0)>=75:hard.append("解禁风险高")
    if f.get("distribution_risk",0)>=75:hard.append("派发风险高")

    if hard:
        stage="REJECT"
    elif s>=LATENT_GATE["start"]:
        stage="START"
    elif s>=LATENT_GATE["ready"]:
        stage="READY"
    elif s>=LATENT_GATE["watch"]:
        stage="WATCH"
    else:
        stage="NONE"

    return {
        **f,
        "latent_score":s,
        "latent_stage":stage,
        "latent_veto":"；".join(hard),
        "is_buy_signal":False,
        "action":{
            "START":"启动候选：等原V8买点确认，不追价",
            "READY":"重点监控：等放量突破/竞价转强",
            "WATCH":"潜伏观察：不提前下注",
            "REJECT":"不进入潜伏池",
            "NONE":"后台记录"
        }[stage]
    }

def analyze_kline(kline, extra=None):
    return classify_latent(compute_latent_features(kline,extra))
