from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .signal_lab import DEFAULT_DB, _LOCK, _connect


def _agg_returns(vals:list[float],maes:list[float]|None=None)->dict[str,Any]:
    maes=maes or []
    if not vals:return {'n':0,'avg_net_return_pct':None,'median_net_return_pct':None,'win_rate':None,'profit_factor':None,'avg_mae_pct':None,'top3_profit_contribution':None}
    wins=[v for v in vals if v>0]; losses=[v for v in vals if v<0]; gross_win=sum(wins); gross_loss=abs(sum(losses))
    pos_sorted=sorted(wins,reverse=True); contribution=(sum(pos_sorted[:3])/gross_win) if gross_win>0 else None
    return {'n':len(vals),'avg_net_return_pct':sum(vals)/len(vals),'median_net_return_pct':statistics.median(vals),'win_rate':sum(v>0 for v in vals)/len(vals),
            'profit_factor':gross_win/gross_loss if gross_loss>0 else None,'avg_mae_pct':sum(maes)/len(maes) if maes else None,'top3_profit_contribution':contribution}


def forward_summary(db_path:Path=DEFAULT_DB)->dict[str,Any]:
    with _LOCK:
        c=_connect(db_path)
        rows=c.execute('''select s.board_type,s.market_regime,o.horizon,o.matured,o.net_return_pct,o.mae_pct,o.mfe_pct,o.holding_trade_days
                          from v72_forward_snapshots s join v72_forward_outcomes o on s.snapshot_id=o.snapshot_id''').fetchall(); c.close()
    out={'matured':0,'immature':0,'by_horizon':{},'by_board':{},'by_market':{},'holding_days':{}}
    groups=defaultdict(list); boards=defaultdict(list); markets=defaultdict(list); days=[]
    for r in rows:
        if r['matured']:
            out['matured']+=1; groups[int(r['horizon'])].append(r); boards[str(r['board_type'] or 'UNKNOWN')].append(r); markets[str(r['market_regime'] or 'UNKNOWN')].append(r)
            if r['holding_trade_days'] is not None: days.append(int(r['holding_trade_days']))
        else: out['immature']+=1
    def agg(rs):
        vals=[float(x['net_return_pct']) for x in rs if x['net_return_pct'] is not None]; maes=[float(x['mae_pct']) for x in rs if x['mae_pct'] is not None]
        return _agg_returns(vals,maes)
    out['by_horizon']={str(k):agg(v) for k,v in groups.items()}; out['by_board']={k:agg(v) for k,v in boards.items()}; out['by_market']={k:agg(v) for k,v in markets.items()}
    if days: out['holding_days']={'n':len(days),'avg':sum(days)/len(days),'median':statistics.median(days),'min':min(days),'max':max(days)}
    return out


def current_engine_summary(db_path:Path=DEFAULT_DB)->dict[str,Any]:
    """Existing CURRENT/V6/V72 engine outcome summary; mature outcomes only."""
    with _LOCK:
        c=_connect(db_path)
        rows=c.execute('''select e.engine,e.strategy_version,o.horizon,o.net_return_pct,o.matured
                          from signal_events e join signal_outcomes o on e.id=o.signal_id
                          where coalesce(o.matured,1)=1''').fetchall() if 'matured' in {r[1] for r in c.execute('pragma table_info(signal_outcomes)').fetchall()} else c.execute('''select e.engine,e.strategy_version,o.horizon,o.net_return_pct,1 matured from signal_events e join signal_outcomes o on e.id=o.signal_id''').fetchall()
        c.close()
    g=defaultdict(list)
    for r in rows:
        if r['net_return_pct'] is not None:g[(r['engine'],r['strategy_version'],r['horizon'])].append(float(r['net_return_pct']))
    return {f'{a}|{b}|{h}':_agg_returns(v) for (a,b,h),v in g.items()}


def second_strength_summary(db_path:Path=DEFAULT_DB)->dict[str,Any]:
    with _LOCK:
        c=_connect(db_path); rows=c.execute("select * from v72_trades where status='CLOSED'").fetchall(); c.close()
    by=defaultdict(list)
    for r in rows:
        if r['realized_return_pct'] is not None:by[str(r['signal_type'])].append(float(r['realized_return_pct']))
    return {k:_agg_returns(v) for k,v in by.items()}


def research_snapshot_summary(db_path:Path=DEFAULT_DB)->dict[str,Any]:
    with _LOCK:
        c=_connect(db_path); rows=c.execute('select research_2000_score,research_json from v72_research_snapshots').fetchall(); c.close()
    vals=[float(r['research_2000_score']) for r in rows if r['research_2000_score'] is not None]
    return {'n':len(rows),'score_avg':sum(vals)/len(vals) if vals else None,'mode':'ANNOTATION_ONLY','note':'RESEARCH_2000当前只记录/排序研究证据，不改变CURRENT建仓或退出。'}


def portfolio_summary(db_path:Path=DEFAULT_DB)->dict[str,Any]:
    with _LOCK:
        c=_connect(db_path); ps=c.execute('select * from v72_positions').fetchall(); hs=c.execute('select current_state from v72_holding_state_current').fetchall(); c.close()
    return {'positions_total':len(ps),'holding':sum(r['status']=='HOLDING' for r in ps),'simulated':sum(r['status']=='SIMULATED' for r in ps),'closed':sum(r['status']=='CLOSED' for r in ps),'pending_fill':sum(r['status']=='PENDING_FILL' for r in ps),'states':dict(Counter(r['current_state'] for r in hs))}


def build_evening_review(db_path:Path=DEFAULT_DB)->dict[str,Any]:
    from .runtime_log_v72 import daily_error_summary
    try:
        from .api_health import HEALTH_PATH
        import json
        health=json.loads(HEALTH_PATH.read_text(encoding='utf-8')) if HEALTH_PATH.exists() else {'status':'UNKNOWN','apis':[]}
    except Exception:
        health={'status':'UNKNOWN','apis':[]}
    return {'portfolio':portfolio_summary(db_path),'research':research_snapshot_summary(db_path),'forward':forward_summary(db_path),
            'current_engines':current_engine_summary(db_path),'second_strength':second_strength_summary(db_path),
            'api_health':health,'runtime_errors':daily_error_summary(),
            'note':'Research Shadow统计；未成熟样本不得计入验收胜率。'}
