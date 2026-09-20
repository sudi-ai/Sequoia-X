from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CN = ZoneInfo("Asia/Shanghai")
REPORT_DIR = ROOT / "reports_v72"


def records(v: Any) -> list[dict[str, Any]]:
    if v is None: return []
    if hasattr(v, "to_dict"):
        try: return [dict(x) for x in v.to_dict(orient="records")]
        except Exception: return []
    if isinstance(v, list): return [dict(x) for x in v if isinstance(x, Mapping)]
    if isinstance(v, Mapping): return [dict(v)]
    return []


def parse_time(row: Mapping[str, Any]):
    from v7.paid_enrichment import _row_time
    return _row_time(row)


def probe(name: str, fn, now: datetime) -> dict[str, Any]:
    st = time.perf_counter()
    try:
        v = fn(); rs = records(v); latency = round((time.perf_counter()-st)*1000,2)
        fields = sorted({str(k) for r in rs[:50] for k in r.keys()})
        times = [parse_time(r) for r in rs[:200]]
        times = [x for x in times if x is not None]
        dt = max(times) if times else None
        fresh = "UNKNOWN"
        if dt:
            age = max(0,(now-dt.astimezone(CN)).total_seconds())
            fresh = "FRESH" if age <= 300 else ("RECENT" if age <= 86400*3 else "STALE")
        return {"api_name":name,"status":"OK","row_count":len(rs),"latency_ms":latency,"data_time":dt.isoformat(timespec='seconds') if dt else None,
                "freshness":fresh,"fields":fields,"error_summary":""}
    except Exception as exc:
        return {"api_name":name,"status":"UNAVAILABLE","row_count":0,"latency_ms":round((time.perf_counter()-st)*1000,2),"data_time":None,
                "freshness":"UNKNOWN","fields":[],"error_summary":type(exc).__name__}


def validate_auction_tick(result: dict[str, Any], raw: Any, now: datetime, expected_code: str, market_open_today: bool | None) -> dict[str, Any]:
    from v7.auction_validation_v72 import validate_realtime_auction_ticks
    if result.get("status") != "OK":
        result["realtime_status"] = "AUCTION_REALTIME_UNAVAILABLE"
        result["validation_error"] = "接口调用失败或不可用"
        return result
    validation = validate_realtime_auction_ticks(
        raw, expected_code=expected_code, now=now, market_open_today=market_open_today, parse_time=parse_time
    )
    result.update({k: v for k, v in validation.items() if k != "latest_row"})
    return result


def main() -> int:
    ap=argparse.ArgumentParser(description='V7.2真实付费接口安全验收（只读、小流量）')
    ap.add_argument('--code',default='000001.SZ')
    args=ap.parse_args()
    from v7.env_loader import load_project_env
    load_project_env()
    from v7.tushare_bridge import TushareBridge
    bridge=TushareBridge(); now=datetime.now(CN); code=args.code; day=now.strftime('%Y%m%d')
    start_day=(now-timedelta(days=5)).strftime('%Y%m%d'); start_dt=(now-timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'); end_dt=now.strftime('%Y-%m-%d %H:%M:%S')
    specs=[
      ('daily',lambda:bridge.call('daily',trade_date=day)),('trade_cal',lambda:bridge.call('trade_cal',exchange='SSE',start_date=day,end_date=day)),
      ('rt_k',lambda:bridge.call('rt_k',ts_code=code)),('rt_min',lambda:bridge.call('rt_min',ts_code=code,freq='1MIN')),
      ('rt_min_daily',lambda:bridge.call('rt_min_daily',ts_code=code,freq='1MIN')),('anns_d',lambda:bridge.call('anns_d',ts_code=code,start_date=start_day,end_date=day)),
      ('news',lambda:bridge.call('news',src='sina',start_date=start_dt,end_date=end_dt)),('major_news',lambda:bridge.call('major_news',src='sina',start_date=start_dt,end_date=end_dt)),
      ('report_rc',lambda:bridge.call('report_rc',ts_code=code,start_date=start_day,end_date=day)),('stk_auction_o',lambda:bridge.call('stk_auction_o',ts_code=code,trade_date=day)),
      ('stk_auction_tick',lambda:bridge.call('stk_auction_tick',ts_code=code,start_date=day,end_date=day)),('us_tbr',lambda:bridge.call('us_tbr',start_date=start_day,end_date=day)),
      ('pro_bar',lambda:bridge.pro_bar(ts_code=code,freq='1min',start_date=start_dt,end_date=end_dt))]
    results=[]; raw_tick=None; market_open_today=None
    for name,fn in specs:
        st=time.perf_counter()
        try:
            raw=fn(); r=probe(name,lambda raw=raw:raw,now)
            # Preserve actual latency including interface call.
            r['latency_ms']=round((time.perf_counter()-st)*1000,2)
            if name=='trade_cal':
                rs=records(raw)
                if rs:
                    flag=rs[0].get('is_open'); market_open_today=str(flag) in {'1','True','true'} or flag==1
            if name=='stk_auction_tick': raw_tick=raw
        except Exception as exc:
            r={"api_name":name,"status":"UNAVAILABLE","row_count":0,"latency_ms":round((time.perf_counter()-st)*1000,2),"data_time":None,"freshness":"UNKNOWN","fields":[],"error_summary":type(exc).__name__}
        if name=='stk_auction_o': r['note']='仅9:30开盘集合竞价汇总；不得作为09:15-09:25实时竞价过程。'
        if name=='stk_auction_tick': r=validate_auction_tick(r,raw_tick,now,code,market_open_today)
        results.append(r)
    report={"checked_at":now.isoformat(timespec='seconds'),"code":code,"token_present":bool(bridge.token),"relay_configured":bool(bridge.http_url),
            "market_open_today":market_open_today,"interfaces":results,
            "safety_note":"报告不包含Token或完整Webhook；接口失败/未购买按UNAVAILABLE或UNKNOWN记录，不伪造成功。"}
    REPORT_DIR.mkdir(parents=True,exist_ok=True); path=REPORT_DIR/f"V72真实接口验收_{now.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2,default=str)); print('REPORT=',path)
    return 0

if __name__=='__main__': raise SystemExit(main())
