from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping

from .config import CONFIG
from .decision_engine import clamp, num


@dataclass(frozen=True)
class TradePlan:
    action: str
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    initial_position_pct: float
    risk_per_trade_pct: float
    take_profit_mode: str
    reasons: tuple[str,...]
    invalidations: tuple[str,...]
    shadow_only: bool=True

    def as_dict(self): return asdict(self)


def board_atr_multiplier(board: str) -> float:
    return 2.2 if board=='科创板' else (2.0 if board=='创业板' else 1.7)


def build_entry_plan(e: Mapping[str,Any]) -> TradePlan:
    price=num(e.get('price')); atr=num(e.get('atr14')); support=num(e.get('structural_support'))
    zone_low=num(e.get('zone_low')); zone_high=num(e.get('zone_high')); board=str(e.get('board_type') or '主板')
    risk_score=num(e.get('risk_score')) or 50; max_position=num(e.get('position_cap_pct')) or CONFIG.max_single_position_pct
    invalid=[]; reasons=[]
    if price is None: invalid.append('缺少可执行价格')
    if e.get('hard_veto'): invalid.append('存在硬风险否决')
    if not e.get('persistence_confirmed'): invalid.append('持续度尚未确认')
    if not (e.get('pullback_confirmed') or e.get('second_strength_confirmed')): invalid.append('回踩承接/二次转强尚未确认')
    if e.get('limit_up_locked'): invalid.append('涨停封死无法合理成交')
    stop_candidates=[]
    if price and atr: stop_candidates.append(price-board_atr_multiplier(board)*atr)
    if support: stop_candidates.append(support)
    stop=max(stop_candidates) if stop_candidates else None
    stop_distance=(price-stop)/price*100 if price and stop and price>stop else None
    if stop_distance is None or stop_distance<=0: invalid.append('无法形成有效防守距离')
    risk_position=CONFIG.risk_per_trade_pct/stop_distance*100 if stop_distance else 0
    size=max(0,min(max_position,risk_position))*(.7 if board in {'创业板','科创板'} else 1)*(max(.2,1-risk_score/100))
    if zone_low and zone_high and price and price>zone_high*1.015: invalid.append('价格脱离观察区，禁止追高')
    if not invalid: reasons.extend(['持续度确认','回踩承接/二次转强确认','风险预算与防守距离匹配'])
    return TradePlan('BLOCKED' if invalid else 'SHADOW_ENTRY',zone_low,zone_high,round(stop,3) if stop else None,
                     round(size,2),CONFIG.risk_per_trade_pct,'DYNAMIC_STATE_WITH_FIXED_FORWARD',tuple(reasons),tuple(invalid),True)


def holding_action(e: Mapping[str,Any]) -> dict[str,Any]:
    """One final action after conflict arbitration. Hard risk never gets hidden by a positive model."""
    if e.get('bought_today'):
        risk=bool(e.get('hard_risk') or e.get('break_confirmed') or e.get('break_warning'))
        return {'action':'T1_LOCKED_RISK_ALERT' if risk else 'HOLD','priority':100 if risk else 20,
                'reason':'A股T+1不可当日卖出；记录次日优先处理' if risk else 'T+1锁定期正常观察'}
    if e.get('hard_risk'): return {'action':'EXIT','priority':100,'reason':'重大公告/监管/不可交易风险'}
    if e.get('auction_warning'): return {'action':'AUCTION_RISK_ALERT','priority':95,'reason':'次日竞价明显弱于自动防守，等待09:35最终确认'}
    if e.get('break_confirmed'): return {'action':'EXIT','priority':90,'reason':'连续有效跌破自动防守并位于VWAP下方'}
    if e.get('support_broken') and e.get('minute_bad'): return {'action':'EXIT','priority':90,'reason':'结构与分钟资金共振转弱'}
    if e.get('break_warning'): return {'action':'DEFENSE_WARNING','priority':80,'reason':'首次有效跌破自动防守，等待下一轮确认'}
    if e.get('profit_stage') in {'R2','R3'} and e.get('exhaustion') and (e.get('minute_bad') or e.get('sector_bad')):
        if int(e.get('suggested_sell_shares') or 0)>0:
            return {'action':'REDUCE','priority':70,'reason':'盈利阶段出现衰竭，启动分级利润保护'}
        return {'action':'PROFIT_PROTECT','priority':65,'reason':'持仓不足200股，不拆分卖出；收紧自动防守'}
    if e.get('main_rise') and not e.get('minute_bad'):
        return {'action':'HOLD','priority':40,'reason':'主升结构保持；采用移动防守，不按固定D5机械退出'}
    if e.get('washout') and not e.get('support_broken'):
        return {'action':'HOLD','priority':35,'reason':'健康洗盘结构仍完整，不补仓，等待承接'}
    return {'action':'OBSERVE','priority':10,'reason':'证据不足，避免频繁交易'}


def trailing_stop(e: Mapping[str,Any]) -> float | None:
    price=num(e.get('price')); peak=num(e.get('peak_price')); atr=num(e.get('atr14')); support=num(e.get('support'))
    if not price or not peak or not atr: return support
    gain=(peak/(num(e.get('entry_price')) or peak)-1)*100
    multiple=2.0 if gain<5 else (1.6 if gain<10 else 1.25)
    candidate=peak-multiple*atr
    return round(max(x for x in (candidate,support) if x is not None),3)
