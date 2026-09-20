# -*- coding: utf-8 -*-
from __future__ import annotations

def max_drawdown(returns):
    equity=1.0; peak=1.0; worst=0.0
    for r in returns:
        equity*=1+float(r)/100
        peak=max(peak,equity)
        worst=min(worst,(equity/peak-1)*100)
    return round(worst,2)

def evaluate(returns):
    vals=[float(x) for x in returns if x is not None]
    if not vals:
        return {'n':0,'win_rate':None,'avg_win':None,'avg_loss':None,'payoff':None,'expectancy':None,'max_drawdown':None}
    wins=[x for x in vals if x>0]; losses=[x for x in vals if x<=0]
    wr=len(wins)/len(vals)
    aw=sum(wins)/len(wins) if wins else 0.0
    al=abs(sum(losses)/len(losses)) if losses else 0.0
    payoff=aw/al if al else None
    expectancy=wr*aw-(1-wr)*al
    return {'n':len(vals),'win_rate':round(wr,4),'avg_win':round(aw,3),'avg_loss':round(-al,3),
            'payoff':None if payoff is None else round(payoff,3),'expectancy':round(expectancy,3),
            'max_drawdown':max_drawdown(vals)}

def pass_quality_gate(stats,target_win=.80):
    return stats.get('n',0)>=50 and (stats.get('win_rate') or 0)>=target_win and (stats.get('expectancy') or -999)>0 and (stats.get('max_drawdown') or -999)>-12
