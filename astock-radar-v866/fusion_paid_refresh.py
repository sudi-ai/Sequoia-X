"""Bounded asynchronous refresh into NEW indexed context only; no legacy writes."""
import argparse
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time

from fusion_paid_evidence import TZ, stock_code

ROOT=Path(__file__).resolve().parent
DAILY_CALL_LIMIT=120


def reserve_call(path, day, endpoint, code):
    with closing(sqlite3.connect(path,timeout=3)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        used=db.execute('SELECT COUNT(*) FROM context_calls WHERE day=?',(day,)).fetchone()[0]
        if used>=DAILY_CALL_LIMIT:
            return False
        db.execute('INSERT INTO context_calls(day,endpoint,code) VALUES(?,?,?)',(day,endpoint,code))
        return True


def choose_codes(root,now,limit=3):
    path=Path(root)/'data/fusion_v8_baseline.sqlite3'
    if not path.exists():return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
        rows=db.execute("SELECT o.payload FROM original o JOIN current c ON c.id=o.id WHERE c.state IN ('WATCH','CONFIRMED') AND o.first_seen_at>=? ORDER BY o.first_seen_at DESC LIMIT 60",((now-dt.timedelta(days=7)).isoformat(),)).fetchall()
    codes=[]
    for row in rows:
        try:code=stock_code(json.loads(row[0]).get('code'))
        except (ValueError,TypeError,AttributeError):continue
        if code and code not in codes:codes.append(code)
    path=Path(root)/'data/fusion_paid_context.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='context_refresh'").fetchone():
                since=(now-dt.timedelta(hours=6)).isoformat()
                recent={r[0] for r in db.execute('SELECT code FROM context_refresh WHERE completed_at>=?',(since,))}
                codes=[c for c in codes if c not in recent]
    return codes[:limit]


def refresh(root,codes,now=None,hub_factory=None):
    from pit_store import PITStore
    from paid_data_hub import DataHub
    from fusion_ai_worker import atomic_json
    now=now or dt.datetime.now(TZ)
    if now.tzinfo is None:raise ValueError('Timezone required')
    now=now.astimezone(TZ)
    data=Path(root)/'data';data.mkdir(exist_ok=True)
    path=data/'fusion_paid_context.sqlite3'
    store=PITStore(path)
    with closing(sqlite3.connect(path)) as db,db:
        for table in ('stock_daily','daily_basic','moneyflow'):
            db.execute('CREATE INDEX IF NOT EXISTS ix_context_'+table+' ON '+table+'(ts_code,id DESC)')
        db.executescript('CREATE TABLE IF NOT EXISTS context_calls(id INTEGER PRIMARY KEY,day TEXT,endpoint TEXT,code TEXT); CREATE TABLE IF NOT EXISTS context_refresh(code TEXT PRIMARY KEY,completed_at TEXT,result_json TEXT);')
    report={'checked_at':now.isoformat(),'status':'IDLE','target_trade_date':None,'stocks':[],
            'daily_call_limit':DAILY_CALL_LIMIT,'historical_download_not_original_pit':True}
    codes=list(dict.fromkeys(stock_code(c) for c in codes if stock_code(c)))[:3]
    hub=(hub_factory(store) if hub_factory else DataHub(store=store)) if codes else None
    def request(endpoint,params):
        if not reserve_call(path,now.strftime('%Y-%m-%d'),endpoint,params.get('ts_code','')):
            return None
        return hub.fetch_interface(endpoint,params,use_cache=True,timeout_seconds=8,pit_safe=False)
    if codes:
        yesterday=now.date()-dt.timedelta(days=1)
        calendar=request('trade_cal',{'exchange':'SSE','start_date':(yesterday-dt.timedelta(days=30)).strftime('%Y%m%d'),'end_date':yesterday.strftime('%Y%m%d'),'is_open':'1'})
        days=[]
        if calendar is not None and not calendar.empty and {'cal_date','is_open'}<=set(calendar.columns):
            for row in calendar.to_dict('records'):
                value=str(row['cal_date'])
                try:day=dt.datetime.strptime(value,'%Y%m%d').date()
                except ValueError:continue
                if str(row['is_open']) in ('1','1.0') and now.date()-dt.timedelta(days=31)<=day<=yesterday:days.append(day)
        if not days:
            report['status']='WAITING_VERIFIED_TRADE_CALENDAR'
        else:
            target=max(days);end=target.strftime('%Y%m%d');report['target_trade_date']=end
            for code in codes:
                specs=[('daily',{'ts_code':code,'start_date':(target-dt.timedelta(days=75)).strftime('%Y%m%d'),'end_date':end}),
                       ('daily_basic',{'ts_code':code,'trade_date':end}),
                       ('moneyflow_ths',{'ts_code':code,'start_date':(target-dt.timedelta(days=14)).strftime('%Y%m%d'),'end_date':end})]
                results=[]
                for endpoint,params in specs:
                    frame=request(endpoint,params)
                    rows=[] if frame is None or frame.empty else frame.to_dict('records')
                    valid=[r for r in rows if stock_code(r.get('ts_code'))==code and str(r.get('trade_date'))<=end]
                    dates=sorted({str(r.get('trade_date')) for r in valid})
                    results.append({'endpoint':endpoint,'rows':len(valid),'latest_date':dates[-1] if dates else None,
                                    'status':'CALL_LIMIT' if frame is None else str(frame.attrs.get('status','UNKNOWN')),
                                    'target_date_present':end in dates})
                    if endpoint=='moneyflow_ths' and end not in dates:
                        fallback=request('moneyflow',params)
                        records=[] if fallback is None or fallback.empty else fallback.to_dict('records')
                        dates=sorted({str(r.get('trade_date')) for r in records if stock_code(r.get('ts_code'))==code and str(r.get('trade_date'))<=end})
                        results.append({'endpoint':'moneyflow','rows':len(records),'latest_date':dates[-1] if dates else None,'target_date_present':end in dates})
                outcome={'code':code,'endpoints':results}
                report['stocks'].append(outcome)
                with closing(sqlite3.connect(path)) as db,db:
                    db.execute('INSERT INTO context_refresh VALUES(?,?,?) ON CONFLICT(code) DO UPDATE SET completed_at=excluded.completed_at,result_json=excluded.result_json',(code,now.isoformat(),json.dumps(outcome)))
            report['status']='REFRESH_ATTEMPTED'
    atomic_json(data/'fusion_paid_refresh_status.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    import fcntl
    (ROOT/'data').mkdir(exist_ok=True)
    with open(ROOT/'data/fusion_paid_refresh.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            now=dt.datetime.now(TZ)
            try:
                codes=choose_codes(ROOT,now)
                # Unattended collection is restricted to weekday working hours.
                if args.once or (now.weekday()<5 and '08:45'<=now.strftime('%H:%M')<='15:10'):
                    result=refresh(ROOT,codes,now)
                    print(json.dumps(result,ensure_ascii=True),flush=True)
            except Exception as exc:
                print(json.dumps({'status':'REFRESH_FAILED','error_type':type(exc).__name__}),flush=True)
            if args.once:break
            time.sleep(60)


if __name__=='__main__':main()
