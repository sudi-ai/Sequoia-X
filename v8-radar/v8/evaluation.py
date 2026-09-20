from __future__ import annotations
import math,random,statistics
from collections import defaultdict
from typing import Any,Iterable,Mapping


def summarize(rows:Iterable[Mapping[str,Any]],return_key='net_return_pct')->dict[str,Any]:
    mature=[r for r in rows if bool(r.get('matured')) and r.get(return_key) is not None]
    xs=[float(r[return_key]) for r in mature]; pos=[x for x in xs if x>0]; neg=[x for x in xs if x<0]
    return {'mature_n':len(xs),'win_rate':round(len(pos)/len(xs)*100,2) if xs else None,
      'mean':round(statistics.mean(xs),4) if xs else None,'median':round(statistics.median(xs),4) if xs else None,
      'profit_factor':round(sum(pos)/abs(sum(neg)),3) if neg else None,
      'largest_winner_contribution_pct':round(max(pos)/sum(pos)*100,2) if pos else None,
      'worst_trade':round(min(xs),4) if xs else None,'best_trade':round(max(xs),4) if xs else None,
      'immature_n':sum(1 for r in rows if not bool(r.get('matured')))}


def bootstrap_mean_ci(rows:Iterable[Mapping[str,Any]],return_key='net_return_pct',iterations=1000,seed=20260816):
    xs=[float(r[return_key]) for r in rows if bool(r.get('matured')) and r.get(return_key) is not None]
    if len(xs)<20: return {'available':False,'reason':'need_at_least_20_mature_independent_samples'}
    rng=random.Random(seed); means=[]
    for _ in range(iterations): means.append(statistics.mean(rng.choice(xs) for _ in xs))
    means.sort(); return {'available':True,'low':round(means[int(.025*iterations)],4),'high':round(means[int(.975*iterations)-1],4)}


def compare_groups(rows:Iterable[Mapping[str,Any]])->dict[str,Any]:
    groups=defaultdict(list)
    for row in rows: groups[str(row.get('group_name') or 'UNKNOWN')].append(row)
    return {k:summarize(v) for k,v in groups.items()}


def max_consecutive_losses(rows:Iterable[Mapping[str,Any]],return_key='net_return_pct')->int:
    worst=run=0
    for r in sorted((x for x in rows if x.get('matured') and x.get(return_key) is not None),key=lambda x:str(x.get('exit_time') or x.get('mature_date') or '')):
        run=run+1 if float(r[return_key])<0 else 0; worst=max(worst,run)
    return worst
