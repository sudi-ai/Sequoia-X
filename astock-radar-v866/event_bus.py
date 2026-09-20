# -*- coding: utf-8 -*-
"""
轻量事件总线：借鉴成熟量化引擎“数据/信号/风控/通知解耦”的架构思想。
事件处理失败不会拖垮扫描主循环。
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from queue import Queue, Empty
from threading import Thread, Event as ThreadEvent
from datetime import datetime
from typing import Any, Callable

@dataclass
class Event:
    type: str
    data: Any = None
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))

class EventBus:
    def __init__(self):
        self._handlers = defaultdict(list)
        self._general = []
        self._q = Queue()
        self._stop = ThreadEvent()
        self._worker = Thread(target=self._run, daemon=True)

    def start(self):
        if not self._worker.is_alive():
            self._worker.start()
        return self

    def stop(self):
        self._stop.set()

    def on(self, event_type: str, handler: Callable[[Event], None]):
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def on_any(self, handler):
        if handler not in self._general:
            self._general.append(handler)

    def emit(self, event_type: str, data=None):
        self._q.put(Event(event_type, data))

    def _run(self):
        while not self._stop.is_set():
            try:
                evt = self._q.get(timeout=.5)
            except Empty:
                continue
            for fn in list(self._handlers.get(evt.type, [])) + list(self._general):
                try:
                    fn(evt)
                except Exception:
                    pass
