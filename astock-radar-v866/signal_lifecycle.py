# -*- coding: utf-8 -*-
"""
信号生命周期状态机，防止同一股票多个模块重复推送互相打架。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

ALLOWED = {
    "DISCOVERED":{"WATCH","CANCELLED"},
    "WATCH":{"READY","RISK","CANCELLED"},
    "READY":{"TRIGGERED","WATCH","RISK","CANCELLED"},
    "TRIGGERED":{"HOLD","RISK","CLOSED"},
    "HOLD":{"STRENGTHEN","WASH","RISK","CLOSED"},
    "STRENGTHEN":{"HOLD","WASH","RISK","CLOSED"},
    "WASH":{"HOLD","STRENGTHEN","RISK","CLOSED"},
    "RISK":{"HOLD","CLOSED"},
    "CANCELLED":set(),
    "CLOSED":set(),
}

@dataclass
class SignalState:
    ts_code:str
    state:str="DISCOVERED"
    updated_at:str=""

    def transition(self,new_state):
        if new_state==self.state:
            return False
        if new_state not in ALLOWED.get(self.state,set()):
            return False
        self.state=new_state
        self.updated_at=datetime.now().isoformat(timespec="seconds")
        return True
