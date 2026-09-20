# -*- coding: utf-8 -*-
"""
Walk-forward 时间序列切分器：禁止随机打乱未来数据。
"""
from __future__ import annotations
def windows(items, train_size, test_size, step=None):
    step=step or test_size
    n=len(items); start=0
    while start+train_size+test_size<=n:
        train=items[start:start+train_size]
        test=items[start+train_size:start+train_size+test_size]
        yield train,test
        start+=step
