# -*- coding: utf-8 -*-
"""Observable event-response evidence. News never creates a buy signal."""
from __future__ import annotations


def evaluate(event=None, response=None):
    if not event:
        return {"status": "NO_EVENT_SOURCE", "event_surprise_score": None, "state": "UNKNOWN",
                "reason": "未接入带发布时间和历史可见性的事件源"}
    response = dict(response or {})
    price = float(response.get("price_response", 0) or 0)
    sector = float(response.get("sector_response", 0) or 0)
    flow = float(response.get("active_flow_response", 0) or 0)
    score = max(0.0, min(100.0, 50 + price * 5 + sector * 3 + flow * 0.25))
    state = "NEGATIVE_SURPRISE" if event.get("positive") and score < 40 else ("POSITIVE_SURPRISE" if score >= 65 else "UNCONFIRMED")
    return {"status": "OBSERVED_RESPONSE_ONLY", "event_surprise_score": round(score, 2), "state": state,
            "reason": "事件只经价格、板块和资金响应确认，不直接产生买点"}
