from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping


@dataclass(order=True)
class DueCheck:
    due_at: datetime
    event_key: str=field(compare=False)
    checkpoint_minute: int=field(compare=False)
    payload: dict[str,Any]=field(compare=False,default_factory=dict)


class PersistenceScheduler:
    """Non-blocking due queue: the 20-second main loop polls it; it never sleeps."""
    def __init__(self, checkpoints=(3,6,10)):
        self.checkpoints=tuple(sorted(set(int(x) for x in checkpoints)))
        self._heap:list[DueCheck]=[]; self._scheduled:set[tuple[str,int]]=set()
        self._lock=threading.RLock()

    def register(self,event_key:str,signal_time:datetime,payload:Mapping[str,Any]|None=None)->int:
        added=0
        with self._lock:
            for minute in self.checkpoints:
                key=(event_key,minute)
                if key in self._scheduled: continue
                heapq.heappush(self._heap,DueCheck(signal_time+timedelta(minutes=minute),event_key,minute,dict(payload or {})))
                self._scheduled.add(key); added+=1
        return added

    def register_at(self,event_key:str,due_at:datetime,checkpoint_minute:int,payload:Mapping[str,Any]|None=None)->bool:
        key=(event_key,int(checkpoint_minute))
        with self._lock:
            if key in self._scheduled:return False
            heapq.heappush(self._heap,DueCheck(due_at,event_key,int(checkpoint_minute),dict(payload or {})))
            self._scheduled.add(key);return True

    def run_due(self,now:datetime,runner:Callable[[DueCheck],Any],max_jobs:int=8)->list[Any]:
        out=[]; jobs=[]
        with self._lock:
            while self._heap and self._heap[0].due_at<=now and len(jobs)<max_jobs:
                jobs.append(heapq.heappop(self._heap))
        for job in jobs:
            try: out.append(runner(job))
            except Exception as exc: out.append({'event_key':job.event_key,'checkpoint':job.checkpoint_minute,'error':type(exc).__name__})
        return out

    def retry(self,job:DueCheck,now:datetime,delay_seconds:int=30)->None:
        with self._lock:
            heapq.heappush(self._heap,DueCheck(now+timedelta(seconds=max(1,delay_seconds)),job.event_key,job.checkpoint_minute,job.payload))

    def pending(self)->int:
        with self._lock: return len(self._heap)
