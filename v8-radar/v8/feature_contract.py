from __future__ import annotations
from datetime import datetime
from typing import Any,Mapping

FORBIDDEN_PREFIXES=('future','forward_return','return_t','outcome','exit_','mfe','mae','max_favorable','max_adverse')


def validate_live_features(features:Mapping[str,Any],signal_time:str|datetime)->None:
    cutoff=datetime.fromisoformat(signal_time) if isinstance(signal_time,str) else signal_time
    def walk(value,prefix=''):
      if isinstance(value,Mapping):
        for k,v in value.items(): yield from walk(v,f'{prefix}.{k}' if prefix else str(k))
      else: yield prefix,value
    for path,value in walk(features):
        key=path.rsplit('.',1)[-1]
        low=str(key).lower()
        if any(low.startswith(p) for p in FORBIDDEN_PREFIXES): raise ValueError(f'FUTURE_FIELD_BLOCKED:{key}')
        if low.endswith(('_time','_at','_date')) and value not in (None,''):
            try:
                stamp=datetime.fromisoformat(str(value))
                if stamp.tzinfo is None and cutoff.tzinfo is not None: stamp=stamp.replace(tzinfo=cutoff.tzinfo)
                if stamp>cutoff: raise ValueError(f'FUTURE_TIMESTAMP_BLOCKED:{key}')
            except ValueError as exc:
                if str(exc).startswith('FUTURE_'): raise


def strip_outcomes(features:Mapping[str,Any])->dict[str,Any]:
    return {k:v for k,v in features.items() if not any(str(k).lower().startswith(p) for p in FORBIDDEN_PREFIXES)}
