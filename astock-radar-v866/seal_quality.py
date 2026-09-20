# -*- coding: utf-8 -*-
"""
首板封板质量特征。
"""
from __future__ import annotations
import re

def hhmm_to_minutes(s):
    s=str(s or "")
    nums=re.findall(r"\d+",s)
    if len(nums)>=2:
        h,m=int(nums[0]),int(nums[1])
        return h*60+m
    if len(s)>=4 and s[:4].isdigit():
        return int(s[:2])*60+int(s[2:4])
    return None

def first_seal_score(first_time):
    m=hhmm_to_minutes(first_time)
    if m is None:return 50.0
    # 9:25-9:35 很强；过早一字不额外无限加分；尾盘板质量下降
    if m<=9*60+35:return 92.0
    if m<=10*60:return 85.0
    if m<=11*60:return 76.0
    if m<=13*60+30:return 66.0
    if m<=14*60+30:return 55.0
    return 40.0

def reseal_quality(open_times,last_time):
    n=int(open_times or 0)
    score=88-n*12
    lm=hhmm_to_minutes(last_time)
    if lm and lm>=14*60+40:score-=12
    return round(max(0,min(100,score)),1)

def turnover_quality(turnover):
    t=float(turnover or 0)
    if 4<=t<=16:return 88.0
    if 2<=t<4:return 72.0
    if 16<t<=25:return 70.0
    if t>35:return 35.0
    if t<1:return 55.0
    return 60.0

def seal_strength(seal_amount,amount,seal_ratio=0):
    if seal_ratio:
        r=float(seal_ratio)
        if r>=60:return 94.0
        if r>=35:return 85.0
        if r>=20:return 72.0
        return 50.0
    amt=float(amount or 0); seal=float(seal_amount or 0)
    if amt<=0:return 50.0
    ratio=seal/amt*100
    if ratio>=20:return 90.0
    if ratio>=10:return 80.0
    if ratio>=5:return 68.0
    return 48.0
