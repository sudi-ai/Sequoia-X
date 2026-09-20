"""Persistent notification throttling; does not change candidate evaluation."""
from functools import wraps
from datetime import datetime

def install(cls):
    original = cls.emit
    if getattr(original, '_noise_gate', False):
        return
    @wraps(original)
    def emit(self, identity, kind, text, now, enabled, sender=None):
        interval = 1800 if kind == 'ACTION_FUNNEL' else 0
        if kind == 'EVENT_RESEARCH' and '状态变化' in text:
            interval = 900
        if any(word in text for word in ('风险否决', '事件取消', '多项证据同向', '相对低位结构观察')):
            interval = 0
        key = 'notification_cooldown_v1:' + kind
        held = False
        if interval and enabled:
            try:
                prior = datetime.fromisoformat(self.get(key) or '')
                held = 0 <= (now-prior).total_seconds() < interval
            except (ValueError, TypeError):
                pass
        result = original(self, identity, kind, text, now, enabled and not held, sender)
        if interval and enabled and not held and result == 'SENT':
            self.set(key, now.isoformat())
        return result
    emit._noise_gate = True
    cls.emit = emit
