from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence


def num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def scaled(value: Any, bad: float, good: float, neutral: float = 50.0) -> float:
    x = num(value)
    if x is None or good == bad:
        return neutral
    return clamp((x - bad) / (good - bad) * 100)


def weighted(parts: Sequence[tuple[float, float]]) -> float:
    total = sum(w for _, w in parts)
    return clamp(sum(v * w for v, w in parts) / total) if total else 50.0


@dataclass(frozen=True)
class ComponentScore:
    score: float
    label: str
    reasons: tuple[str, ...]
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class V8Decision:
    action: str
    opportunity_score: float
    market: ComponentScore
    sector: ComponentScore
    trend: ComponentScore
    fund: ComponentScore
    persistence: ComponentScore
    risk_score: float
    position_pct: float
    reasons: tuple[str, ...]
    vetoes: tuple[str, ...]
    shadow_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def market_regime(e: Mapping[str, Any]) -> ComponentScore:
    missing=[]
    def part(key,bad,good,w):
        if num(e.get(key)) is None: missing.append(key)
        return scaled(e.get(key),bad,good),w
    score=weighted([
        part('advance_ratio',.35,.65,2.0), part('limit_up_count',10,80,1.0),
        (100-scaled(e.get('limit_down_count'),2,30),1.0),
        (100-scaled(e.get('break_board_rate'),.15,.55),1.5),
        part('yesterday_strong_feedback',-.03,.03,1.5),
        part('turnover_ratio',.75,1.25,1.0), part('index_trend_score',30,75,1.0),
    ])
    label='ATTACK' if score>=68 else ('DEFENSE' if score<42 else 'NORMAL')
    return ComponentScore(score,label,(f'市场状态{label}',),tuple(missing))


def sector_breadth(e: Mapping[str, Any]) -> ComponentScore:
    missing=[]
    def p(key,bad,good,w):
        if num(e.get(key)) is None: missing.append(key)
        return scaled(e.get(key),bad,good),w
    score=weighted([
        p('up_ratio',.35,.72,2),p('median_return',-.01,.03,1.5),p('amount_acceleration',.8,1.5,1.5),
        p('leader_strength',40,85,1),p('second_third_strength',35,78,1.5),p('fund_persistence',35,80,1.5),
    ])
    label='BROAD' if score>=72 else ('SINGLE_STOCK' if score<45 else 'PARTIAL')
    return ComponentScore(score,label,(f'板块扩散{label}',),tuple(missing))


def trend_quality(e: Mapping[str, Any]) -> ComponentScore:
    missing=[]
    def p(key,bad,good,w):
        if num(e.get(key)) is None: missing.append(key)
        return scaled(e.get(key),bad,good),w
    ma20_gap=num(e.get('ma20_gap_pct'))
    overheat=0 if ma20_gap is None else max(0,ma20_gap-8)*4
    score=weighted([
        p('ma20_slope10',-2,4,1.5),p('ma60_gap_pct',-5,10,1),p('close_position',.2,.75,1),
        p('vwap_support',0,1,1.5),p('pullback_support',0,1,1.5),p('breakout_quality',30,85,1.5),
    ])-overheat
    score=clamp(score)
    label='HIGH' if score>=75 else ('LOW' if score<45 else 'MEDIUM')
    return ComponentScore(score,label,(f'趋势质量{label}',),tuple(missing))


def fund_quality(e: Mapping[str, Any]) -> ComponentScore:
    missing=[]
    def p(key,bad,good,w):
        if num(e.get(key)) is None: missing.append(key)
        return scaled(e.get(key),bad,good),w
    score=weighted([
        p('active_buy_ratio',.42,.62,2),p('order_imbalance',-.12,.18,2),p('amount_acceleration',.8,1.8,1.5),
        p('large_order_direction',-.3,.5,1),p('fund_persistence',30,85,2),
    ])
    label='REAL' if score>=70 else ('WEAK' if score<42 else 'UNCERTAIN')
    return ComponentScore(score,label,(f'资金真实性{label}',),tuple(missing))


def persistence_quality(observations: Sequence[Mapping[str, Any]]) -> ComponentScore:
    if not observations:
        return ComponentScore(50,'UNKNOWN',('尚无持续度复核',),('observations',))
    from .config import CONFIG
    has_time_metadata=any('checkpoint_minute' in x or x.get('data_time') or x.get('evaluation_time') for x in observations)
    usable=[]; seen_data_times=set()
    for obs in observations:
        if has_time_metadata and int(num(obs.get('checkpoint_minute')) or 0)<=0:
            continue
        data_key=str(obs.get('data_time') or obs.get('evaluation_time') or '')
        if has_time_metadata and data_key:
            if data_key in seen_data_times:
                continue
            seen_data_times.add(data_key)
        usable.append(obs)
    if not usable:
        return ComponentScore(50,'WAIT',('等待真实时间推进后的复核',),('fresh_observations',))
    valid=0; scores=[]; reasons=[]
    for obs in usable:
        price_ok=bool(obs.get('above_vwap')) and not bool(obs.get('blowoff_reversal'))
        fund_ok=(num(obs.get('order_imbalance')) or -1)>0 and (num(obs.get('amount_delta')) or 0)>0
        sector_ok=(num(obs.get('sector_score')) or 0)>=55
        round_score=weighted([(100 if price_ok else 0,2),(100 if fund_ok else 0,2),(100 if sector_ok else 0,1)])
        scores.append(round_score); valid += int(round_score>=65)
    required=max(2,CONFIG.min_persistence_rounds)
    score=weighted([(sum(scores)/len(scores),2),(scaled(valid,0,max(required,len(usable))),1)])
    label='CONFIRMED' if valid>=required and score>=70 else ('FAILED' if score<40 else 'WAIT')
    reasons.append(f'{valid}/{len(usable)}轮持续度有效，要求{required}轮')
    return ComponentScore(score,label,tuple(reasons))


def risk_gate(e: Mapping[str, Any]) -> tuple[float, tuple[str, ...]]:
    veto=[]; score=0.0
    if str(e.get('announcement_risk') or '').upper() in {'HIGH','HARD_BLOCK'}: veto.append('重大公告风险')
    if e.get('st_or_delisting'): veto.append('ST/退市风险')
    if e.get('not_tradeable'): veto.append('当前不可合理成交')
    if e.get('limit_up_locked'): veto.append('涨停封死无法买入')
    if (num(e.get('spread_pct')) or 0)>1: veto.append('买卖价差过大')
    if str(e.get('data_validation_status') or '').upper()=='CONFLICT': veto.append('主行情与备用行情价格冲突')
    if str(e.get('event_semantic_risk') or '').upper()=='HARD_BLOCK': veto.append('公告语义识别为重大风险')
    score += min(40,(num(e.get('true_decline_risk')) or 0)*40)
    score += 20 if e.get('liquidity_bad') else 0
    score += 20 if e.get('news_risk') in {'MEDIUM','HIGH'} else 0
    score += 10 if str(e.get('data_validation_status') or '').upper()=='WARNING' else 0
    score += 20 if str(e.get('event_semantic_risk') or '').upper()=='HIGH' else 0
    score += 20 if veto else 0
    return clamp(score),tuple(veto)


def position_size(*, market_label: str, risk_score: float, board_type: str='主板',
                  current_total_pct: float=0, current_sector_pct: float=0,
                  daily_loss_pct: float=0, portfolio_drawdown_pct: float=0) -> float:
    from .config import CONFIG
    if daily_loss_pct<=-CONFIG.daily_loss_limit_pct or portfolio_drawdown_pct<=-CONFIG.portfolio_drawdown_limit_pct:
        return 0.0
    cap={'ATTACK':CONFIG.max_total_attack_pct,'NORMAL':CONFIG.max_total_normal_pct,'DEFENSE':CONFIG.max_total_defense_pct}.get(market_label,0)
    board_factor=.7 if board_type in {'创业板','科创板'} else 1.0
    risk_factor=max(0,.1,1-risk_score/100)
    single=CONFIG.max_single_position_pct*board_factor*risk_factor
    return round(max(0,min(single,cap-current_total_pct,CONFIG.max_sector_exposure_pct-current_sector_pct)),2)


def decide(e: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]=()) -> V8Decision:
    market=market_regime(e.get('market',{})); sector=sector_breadth(e.get('sector',{}))
    trend=trend_quality(e.get('trend',{})); fund=fund_quality(e.get('fund',{})); persistence=persistence_quality(observations)
    risk,veto=risk_gate(e.get('risk',{}))
    opportunity=weighted([(market.score,1),(sector.score,1.5),(trend.score,2),(fund.score,2),(persistence.score,2)])
    opportunity=clamp(opportunity-risk*.45)
    if veto: action='BLOCKED'
    elif persistence.label!='CONFIRMED': action='WATCH'
    elif market.label=='DEFENSE' and opportunity<82: action='WATCH'
    elif opportunity>=78 and trend.score>=68 and fund.score>=65: action='SHADOW_ENTRY_CONFIRMED'
    else: action='WATCH'
    size=position_size(market_label=market.label,risk_score=risk,board_type=str(e.get('board_type') or '主板'),
                       current_total_pct=num(e.get('current_total_pct')) or 0,current_sector_pct=num(e.get('current_sector_pct')) or 0,
                       daily_loss_pct=num(e.get('daily_loss_pct')) or 0,portfolio_drawdown_pct=num(e.get('portfolio_drawdown_pct')) or 0)
    reasons=(f'机会分{opportunity}',f'市场{market.label}',f'持续度{persistence.label}',f'风险{risk}')
    return V8Decision(action,opportunity,market,sector,trend,fund,persistence,risk,size,reasons,veto,True)


def dynamic_exit(e: Mapping[str, Any]) -> dict[str, Any]:
    """Strategy exit track; fixed D1/D3/D5/D10 settlement remains independent."""
    t1_locked=bool(e.get('bought_today'))
    hard=bool(e.get('hard_risk'))
    support_broken=bool(e.get('support_broken'))
    minute_bad=bool(e.get('minute_bad'))
    sector_bad=bool(e.get('sector_bad'))
    exhaustion=num(e.get('exhaustion_score')) or 0
    if t1_locked:
        return {'action':'T1_LOCKED_RISK_ALERT' if hard or support_broken else 'HOLD_T1_LOCKED','reason':'A股T+1当日不可卖出'}
    if hard: return {'action':'EXIT','reason':'重大风险'}
    if support_broken and minute_bad: return {'action':'EXIT','reason':'关键结构与分钟资金同时转弱'}
    if exhaustion>=.72 and (minute_bad or sector_bad): return {'action':'REDUCE','reason':'主升衰竭获得资金/板块佐证'}
    if bool(e.get('washout_structure_intact')): return {'action':'HOLD','reason':'趋势结构完整，按健康洗盘观察'}
    return {'action':'HOLD','reason':'证据不足，不频繁交易'}
