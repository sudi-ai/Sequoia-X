from __future__ import annotations
from typing import Any


def validate_quote(*,primary_price:Any,secondary_price:Any,primary_time:Any=None,secondary_time:Any=None)->dict[str,Any]:
    try:p=float(primary_price);s=float(secondary_price)
    except (TypeError,ValueError):return {'status':'INSUFFICIENT','deviation_pct':None,'sources':1}
    if p<=0 or s<=0:return {'status':'INVALID','deviation_pct':None,'sources':2}
    deviation=abs(p/s-1)*100
    status='VALID' if deviation<=.3 else ('WARNING' if deviation<=1 else 'CONFLICT')
    return {'status':status,'deviation_pct':round(deviation,4),'sources':2,
      'primary_time':primary_time,'secondary_time':secondary_time}
