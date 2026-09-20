# -*- coding: utf-8 -*-
"""V8.2 正式运行编排器：候选->PIT->分层->A级复核->组合风控->落库->事件->微信。"""
from __future__ import annotations
from datetime import datetime
from event_bus import EventBus
from data_provenance import pit_safe
from signal_engine import classify_candidate
from deep_review import DeepReviewer
from portfolio_risk import evaluate_order
from data_store import Store
from feature_store import FeatureStore
from wecom_push import push_a_signal

class RuntimeEngine:
    def __init__(self, store=None, feature_store=None, reviewer=None, bus=None):
        self.store = store or Store()
        self.feature_store = feature_store or FeatureStore()
        self.reviewer = reviewer or DeepReviewer()
        self.bus = bus or EventBus().start()

    def process_candidate(self, candidate, observed_values=None, portfolio=None, push=False):
        decision_time = candidate.get("decision_time") or datetime.now().isoformat(timespec="milliseconds")
        f = dict(candidate)
        f["decision_time"] = decision_time
        f["pit_violations"] = pit_safe(observed_values or {}, decision_time)

        source_map = {k: getattr(v, "source", "") for k, v in (observed_values or {}).items()}
        self.feature_store.save(
            f.get("ts_code", ""), decision_time, f.get("feature_set", "core"), f, source_map=source_map
        )
        signal = classify_candidate(f)

        review = None
        if signal["pool"] == "A":
            review = self.reviewer.review(signal)
            if review.downgrade:
                signal["pool"] = "B"
                signal["deep_review"] = "降级"
                signal["deep_review_reasons"] = "；".join(review.reasons)
            else:
                signal["deep_review"] = "通过"

        risk = None
        if signal["pool"] == "A" and portfolio:
            order = {
                "value": signal.get("planned_value", 0),
                "stop_loss_pct": signal.get("stop_loss_pct", 0),
                "sector": signal.get("sector", ""),
            }
            risk = evaluate_order(order, portfolio)
            if not risk["allowed"]:
                signal["pool"] = "B"
                signal["portfolio_veto"] = "；".join(risk["reasons"])

        sid = self.store.put_signal(signal)
        signal["signal_id"] = sid
        self.bus.emit("SIGNAL_CLASSIFIED", signal)
        if signal["pool"] == "A":
            self.bus.emit("A_SIGNAL", signal)
            if push:
                push_a_signal(signal)
        return {"signal": signal, "review": review, "portfolio_risk": risk}
