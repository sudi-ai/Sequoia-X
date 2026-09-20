# -*- coding: utf-8 -*-
"""
Point-in-time 数据血缘。所有可用于信号的关键字段都可附：
value / source / observed_at / effective_at / quality。
避免回测用到“当时看不到”的数据。
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

@dataclass
class ObservedValue:
    value: Any
    source: str
    observed_at: str
    effective_at: str = ""
    quality: float = 1.0

    def to_dict(self):
        return asdict(self)

def observed(value, source, effective_at="", quality=1.0, observed_at=None):
    return ObservedValue(
        value=value,
        source=source,
        observed_at=observed_at or datetime.now().isoformat(timespec="milliseconds"),
        effective_at=effective_at,
        quality=float(quality),
    )

def unwrap(x):
    if isinstance(x, ObservedValue):
        return x.value
    if isinstance(x, dict) and "value" in x and "source" in x:
        return x["value"]
    return x

def pit_safe(values, decision_time: str):
    """
    严格检查 observed_at <= decision_time。
    effective_at 为空时不做额外约束；存在时也必须 <= decision_time。
    """
    bad=[]
    for name, x in values.items():
        if isinstance(x, ObservedValue):
            if x.observed_at > decision_time:
                bad.append(f"{name}:观察时间晚于决策时间")
            if x.effective_at and x.effective_at > decision_time:
                bad.append(f"{name}:生效时间晚于决策时间")
    return bad
