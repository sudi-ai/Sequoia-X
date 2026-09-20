# -*- coding: utf-8 -*-
"""
策略插件注册表：把主升/回踩/竞价/洗盘/T+1 等从大文件拆开。
不直接复制任何第三方仓库代码。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Any

@dataclass
class StrategySpec:
    name:str
    version:str
    evaluator:Callable[[dict],dict]
    enabled:bool=True
    description:str=""

class StrategyRegistry:
    def __init__(self):
        self._items:Dict[str,StrategySpec]={}

    def register(self,spec:StrategySpec):
        self._items[spec.name]=spec

    def run(self,features):
        out={}
        for name,spec in self._items.items():
            if not spec.enabled:continue
            try:out[name]=spec.evaluator(dict(features))
            except Exception as e:out[name]={"error":f"{type(e).__name__}: {e}"}
        return out

    def manifest(self):
        return [{"name":x.name,"version":x.version,"enabled":x.enabled,"description":x.description} for x in self._items.values()]
