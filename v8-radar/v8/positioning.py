from __future__ import annotations

from typing import Any, Mapping

from .config import CONFIG
from .decision_engine import num


def final_position_pct(*, decision_cap_pct:float, entry_price:float|None, stop_price:float|None,
                       market_cap_remaining:float, sector_cap_remaining:float, board_type:str="主板",
                       new_positions_today:int=0)->dict[str,Any]:
    if new_positions_today >= CONFIG.max_new_positions_per_day:
        return {"position_pct":0.0,"reason":"达到每日新建仓上限"}
    entry=num(entry_price); stop=num(stop_price)
    if not entry or not stop or stop>=entry:
        return {"position_pct":0.0,"reason":"缺少有效入场价或防守价"}
    stop_distance_pct=(entry-stop)/entry*100
    risk_budget_cap=CONFIG.risk_per_trade_pct/stop_distance_pct*100
    board_factor=.7 if board_type in {"创业板","科创板"} else 1.0
    value=max(0,min(float(decision_cap_pct),risk_budget_cap*board_factor,
                    float(market_cap_remaining),float(sector_cap_remaining),CONFIG.max_single_position_pct))
    return {"position_pct":round(value,2),"stop_distance_pct":round(stop_distance_pct,3),
            "risk_budget_cap_pct":round(risk_budget_cap*board_factor,2),"reason":"多重上限取最小值"}
