from __future__ import annotations
import hashlib,json
from datetime import datetime
from typing import Any,Mapping,Sequence

from .decision_engine import decide
from .storage import save_decision,save_early_watch
from .feature_contract import validate_live_features
from .trade_policy import build_entry_plan
from .positioning import final_position_pct
from .research_layers import early_watch_annotation


def event_key(code:str,signal_time:str,source:str)->str:
    return hashlib.sha256(f'{code}|{signal_time}|{source}'.encode()).hexdigest()[:24]


class V8ShadowEngine:
    def evaluate(self,*,candidate:Mapping[str,Any],evidence:Mapping[str,Any],observations:Sequence[Mapping[str,Any]]=(),persist:bool=True):
        stamp=str(candidate.get('signal_time') or datetime.now().astimezone().isoformat(timespec='seconds'))
        cutoff=str(candidate.get('evaluation_time') or stamp)
        validate_live_features(evidence,cutoff)
        code=str(candidate.get('code') or '').split('.')[0].zfill(6); source=str(candidate.get('source') or 'V66_DISCOVERY')
        event={**dict(candidate),'code':code,'signal_time':stamp,'source':source,
               'evaluation_time':cutoff,'event_key':str(candidate.get('event_key') or event_key(code,stamp,source)),'strategy_version':'V8.0-shadow-1'}
        decision=decide(evidence,observations).as_dict()
        entry_input={**event,'risk_score':decision['risk_score'],'position_cap_pct':decision['position_pct'],
          'board_type':evidence.get('board_type'),'persistence_confirmed':decision['persistence']['label']=='CONFIRMED',
          'pullback_confirmed':bool(candidate.get('pullback_confirmed')),
          'second_strength_confirmed':bool(candidate.get('second_strength_confirmed')),
          'hard_veto':bool(decision.get('vetoes'))}
        plan=build_entry_plan(entry_input).as_dict()
        allocation=final_position_pct(decision_cap_pct=min(decision['position_pct'],plan.get('initial_position_pct') or 0),entry_price=event.get('price'),
          stop_price=plan.get('stop_price'),market_cap_remaining=float(candidate.get('market_cap_remaining') or 100),
          sector_cap_remaining=float(candidate.get('sector_cap_remaining') or 100),board_type=str(evidence.get('board_type') or '主板'),
          new_positions_today=int(candidate.get('new_positions_today') or 0))
        if decision['action']=='SHADOW_ENTRY_CONFIRMED' and plan['action']!='SHADOW_ENTRY':
            decision['action']='WATCH'; decision['reasons']=tuple(decision.get('reasons') or ())+tuple(plan['invalidations'])
            allocation={**allocation,'position_pct':0.0,'reason':'入场条件未完成'}
        decision['trade_plan']=plan; decision['position_pct']=allocation['position_pct']; decision['allocation']=allocation
        annotation=early_watch_annotation(decision);decision['early_watch']=annotation
        if persist:
            save_decision(event,decision)
            if annotation['watch_type']!='NONE':save_early_watch(event,annotation)
        return {'event':event,'decision':decision}
