# -*- coding: utf-8 -*-
"""
Purged K-Fold + Embargo + CPCV
用于金融事件标签，防止训练事件与测试事件的持有区间重叠。
"""
from __future__ import annotations
from itertools import combinations
import numpy as np
from config import CPCV
from sample_domains import SIGNAL_SAMPLE_DOMAIN, require_domain_name

def _overlap(a_start,a_end,b_start,b_end):
    return not (a_end < b_start or a_start > b_end)

def purged_train_indices(event_starts,event_ends,test_indices,embargo_bars=None):
    n=len(event_starts);emb=int(CPCV["embargo_bars"] if embargo_bars is None else embargo_bars)
    test=set(int(x) for x in test_indices)
    test_ranges=[(event_starts[i],event_ends[i]) for i in test]
    max_test=max(test) if test else -1
    embargo=set(range(max_test+1,min(n,max_test+1+emb)))
    train=[]
    for i in range(n):
        if i in test or i in embargo:continue
        rng=(event_starts[i],event_ends[i])
        if any(_overlap(rng[0],rng[1],t0,t1) for t0,t1 in test_ranges):
            continue
        train.append(i)
    return np.asarray(train,dtype=int)

def purged_kfold(event_starts,event_ends,n_splits=5,embargo_bars=None):
    n=len(event_starts)
    folds=np.array_split(np.arange(n),n_splits)
    for test in folds:
        train=purged_train_indices(event_starts,event_ends,test,embargo_bars)
        yield train,np.asarray(test,dtype=int)

def cpcv_splits(event_starts,event_ends,n_groups=None,n_test_groups=None,embargo_bars=None,sample_domain=None):
    require_domain_name(sample_domain, SIGNAL_SAMPLE_DOMAIN, "组合净化交叉验证")
    ng=int(n_groups or CPCV["n_groups"]);nt=int(n_test_groups or CPCV["n_test_groups"])
    n=len(event_starts)
    groups=np.array_split(np.arange(n),ng)
    for combo in combinations(range(ng),nt):
        test=np.concatenate([groups[i] for i in combo]) if combo else np.array([],dtype=int)
        train=purged_train_indices(event_starts,event_ends,test,embargo_bars)
        yield train,np.asarray(sorted(test),dtype=int),combo
