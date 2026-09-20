"""Isolated paid-data research worker; notifications opt-in, no trading API."""
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time

from fusion_engine import TZ, assess, number, quote_issues, timestamp
from fusion_journal_v2 import ResearchJournal, VERSION
from fusion_outcomes import Settlement
from fusion_decision_plan import attach_plan, DESIGN_VERSION
from fusion_operations import Operations
from fusion_preparation import Preparation
from fusion_auction import AuctionBrief, AuctionFeed

ROOT = Path(__file__).resolve().parent


class Runtime:
    def __init__(self, hub=None, directory=None, notify_research=False, sender=None):
        if hub is None:
            from paid_data_hub import DataHub
            hub = DataHub()
        self.hub = hub
        self.directory = Path(directory) if directory else ROOT / 'data'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / 'fusion_runtime.json'
        self.journal = ResearchJournal(self.directory / 'fusion_shadow.sqlite3')
        self.journal.recover()
        self.settlement = Settlement(self.journal, self.fetch)
        self.notify_research = notify_research
        self.sender = sender
        self.day = ''
        self.calendar = None
        self.previous_day = None
        self.calendar_checked = None
        self.history = {}
        self.retry_at = {}
        self.points = collections.defaultdict(lambda: collections.deque(maxlen=30))
        self.operations = Operations(self.directory)
        self.preparation = Preparation(self.directory)
        self.prepared_codes = None
        self.latest_quotes = {}
        self.auction = AuctionBrief(self.directory, AuctionFeed(self.hub), self.operations)

    def fetch(self, name, params):
        try:
            frame = self.hub.fetch_interface(name, params, use_cache=False, pit_safe=False, timeout_seconds=10.0)
            status = frame.attrs.get('status', 'OK')
            if frame.empty or status not in ('OK', 'CACHED'):
                return [], status if status not in ('OK','CACHED') else 'EMPTY'
            return frame.to_dict('records'), 'OK'
        except Exception as exc:
            return [], type(exc).__name__

    def _calendar(self, now):
        interval = 60 if self.calendar is None else 1800
        if self.calendar_checked and (now-self.calendar_checked).total_seconds() < interval:
            return
        self.calendar_checked = now
        rows, status = self.fetch('trade_cal', dict(exchange='SSE', start_date=(now-dt.timedelta(days=20)).strftime('%Y%m%d'), end_date=self.day))
        today = next((r for r in rows if str(r.get('cal_date')) == self.day), None)
        self.calendar = str(today['is_open']) in ('1','1.0') if status == 'OK' and today and str(today.get('is_open')) in ('0','1','0.0','1.0') else None
        previous = sorted(str(r['cal_date']) for r in rows if str(r.get('is_open')) in ('1','1.0') and str(r.get('cal_date','')) < self.day)
        self.previous_day = previous[-1] if previous else None

    def publish(self, now, status, candidates=(), **extra):
        try:
            operation_status = self.operations.run(now, status, candidates, self.calendar,
                                                   self.notify_research, self.latest_quotes)
        except Exception as exc:
            operation_status = {'status':'OPERATIONS_ERROR','error':type(exc).__name__}
        self.journal.expire_pending(now)
        delivery = None
        if self.notify_research:
            from fusion_notifications import send_research
            delivery = self.journal.deliver_one(now, self.sender or send_research)
        payload = dict(version=VERSION, generated_at=now.isoformat(), status=status,
                       design_version=DESIGN_VERSION, legacy_policy='READ_ONLY_REFERENCE',
                       data_mode='LIVE_RESEARCH', push_enabled=self.notify_research,
                       candidates=list(candidates), context_ready=len(self.history),
                       outbox=self.journal.counts(), lifecycle=self.journal.summary(),
                       outcomes=self.settlement.summary(), delivery=delivery,
                       execution_eligible=False, **extra)
        payload['operations'] = operation_status
        payload['preparation'] = self.preparation.status
        payload['auction'] = self.auction.status
        temporary = self.state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str), encoding='utf-8')
        os.replace(temporary, self.state_path)
        return payload

    def tick(self, now=None):
        supplied = now is not None
        now = now or dt.datetime.now(TZ)
        now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
        day = now.strftime('%Y%m%d')
        if day != self.day:
            self.day = day
            self.calendar = self.calendar_checked = self.previous_day = None
            self.history.clear()
            self.retry_at.clear()
            self.points.clear()
            self.prepared_codes = None
            self.latest_quotes = {}
        self._calendar(now)
        try:
            self.auction.step(now, self.calendar, self.previous_day, self.fetch,
                              self.journal.active(), self.notify_research)
        except Exception as exc:
            self.auction.status = {'status':'AUCTION_ERROR','error':type(exc).__name__,'execution_eligible':False}
        minute = now.hour*60 + now.minute
        trading = self.calendar is True and (570 <= minute < 690 or 780 <= minute < 900)
        if not trading:
            known_closed = self.calendar is not None
            if known_closed:
                self.preparation.step(now, self.previous_day, self.fetch)
            self.journal.unavailable(now, '非交易时段' if known_closed else '交易日历不可用，等待重试', self.notify_research, paused=known_closed)
            settlement = self.settlement.run(now) if known_closed else {'status':'CALENDAR_UNAVAILABLE'}
            return self.publish(now, 'MARKET_CLOSED' if known_closed else 'CALENDAR_UNKNOWN', settlement=settlement)
        rows, status = self.fetch('rt_k', dict(ts_code='3*.SZ,6*.SH,0*.SZ,9*.BJ',
            fields='ts_code,name,trade_time,pre_close,open,high,low,close,vol,amount,bid_price1,ask_price1'))
        if not rows:
            self.latest_quotes = {}
            self.journal.unavailable(now, '实时行情不可用：'+status, self.notify_research)
            return self.publish(now, 'QUOTE_UNAVAILABLE')
        current = {}
        for raw in rows:
            quote = dict(raw)
            code = str(quote.get('ts_code', ''))
            if not code:
                continue
            price, volume, amount = number(quote.get('close')), number(quote.get('vol')), number(quote.get('amount'))
            quote['source_trade_time'] = quote.get('trade_time')
            quote['vwap'] = next((amount/(volume*d) for d in (1,100) if price and volume and amount and .8*price <= amount/(volume*d) <= 1.2*price), None)
            current[code] = quote
            if not quote_issues(quote, now, True):
                sourced = timestamp(quote.get('source_trade_time'))
                last = self.points[code][-1] if self.points[code] else None
                if sourced and (last is None or sourced > timestamp(last['source_trade_time'])):
                    self.points[code].append(dict(source_trade_time=sourced.isoformat(), price=price, amount=amount))
        eligible = []
        self.latest_quotes = current
        for code, q in current.items():
            price, previous, amount = number(q.get('close')), number(q.get('pre_close')), number(q.get('amount'))
            name = str(q.get('name',''))
            if price and price > 2 and previous and previous > 0 and amount and amount >= 8000000 and not any(x in name.upper() for x in ('ST','退')) and not quote_issues(q,now,True):
                pct = (price/previous-1)*100
                if -1 <= pct <= 4:
                    eligible.append((code,amount,pct))
        quiet = sorted((x for x in eligible if x[2] <= 2.5), key=lambda x:x[1], reverse=True)[:12]
        moving = sorted(eligible, key=lambda x:x[1], reverse=True)[:12]
        active_codes = {r['code'] for r in self.journal.active()}
        selected = list(dict.fromkeys([x[0] for x in quiet+moving] + sorted(active_codes & current.keys())))
        if self.prepared_codes is None:
            self.prepared_codes = self.preparation.shortlist(now, self.previous_day)
        selected = list(dict.fromkeys(selected + [code for code in self.prepared_codes if code in current]))
        for code in selected:
            if code not in self.history and self.previous_day:
                prepared = self.preparation.bars(code, self.previous_day)
                if len(prepared) >= 60 and str(prepared[-1].get('trade_date')) == self.previous_day:
                    self.history[code] = prepared
        for code in selected:
            if code in self.history or self.retry_at.get(code,now) > now:
                continue
            self.retry_at[code] = now + dt.timedelta(minutes=5)
            params = dict(ts_code=code, start_date=(now-dt.timedelta(days=180)).strftime('%Y%m%d'), end_date=self.previous_day or (now-dt.timedelta(days=1)).strftime('%Y%m%d'))
            bars, bs = self.fetch('daily',params)
            factors, fs = self.fetch('adj_factor',params)
            factor_map = {str(r.get('trade_date')):r.get('adj_factor') for r in factors}
            merged = [dict(r, adj_factor=factor_map.get(str(r.get('trade_date')))) for r in bars]
            dates = {str(r.get('trade_date')) for r in merged}
            if bs == fs == 'OK' and len(dates) >= 60 and self.previous_day in dates:
                self.history[code] = merged
            break
        evaluated = now if supplied else dt.datetime.now(TZ)
        snapshot = hashlib.sha256(evaluated.isoformat().encode()).hexdigest()[:24]
        results = []
        for code in selected:
            quote = current[code]
            bars = self.history.get(code, [])
            last_date = max((str(r.get('trade_date','')) for r in bars), default='')
            if self.previous_day is None or last_date != self.previous_day:
                bars = []
            result = assess(quote, bars, list(self.points[code]), evaluated, True)
            result = attach_plan(result, evaluated)
            result['version'] = VERSION
            self.journal.record(result, snapshot, evaluated, self.notify_research)
            origin = next((r for r in self.journal.active() if r['code'] == code and r['family'] == result.get('family')), None)
            if origin:
                result.update(first_seen_at=origin['first_seen_at'], first_seen_price=origin['first_seen_price'])
            results.append(result)
        missing = active_codes - current.keys()
        if missing:
            self.journal.unavailable(evaluated, '本次行情缺少此股票，原观察暂停', self.notify_research, codes=missing)
        return self.publish(evaluated, 'RESEARCH_ONLY', results, previous_trade_date=self.previous_day,
                            universe_rows=len(rows), shortlist_size=len(selected),
                            notice='流动性预筛选研究池，不代表全市场召回；无样本外执行资格。')

    def close(self):
        self.journal.close()
        self.operations.close()
        self.preparation.close()
        self.auction.close()


class WorkerLock:
    def __init__(self, directory):
        self.path = Path(directory) / 'fusion_worker.lock'

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('FUSION_WORKER_ALREADY_RUNNING') from None
        return self

    def __exit__(self,*args):
        self.file.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--loop',action='store_true')
    parser.add_argument('--notify-research',action='store_true',help='Explicit opt-in to real research WeCom messages')
    parser.add_argument('--interval',type=int,default=60)
    args = parser.parse_args()
    with WorkerLock(ROOT/'data'):
        runtime = Runtime(notify_research=args.notify_research)
        try:
            while True:
                try:
                    payload = runtime.tick()
                    print(json.dumps({'status':payload['status'],'time':payload['generated_at']},ensure_ascii=False),flush=True)
                except Exception as exc:
                    now = dt.datetime.now(TZ)
                    runtime.journal.unavailable(now,'研究运行异常：'+type(exc).__name__,False)
                    runtime.publish(now,'WORKER_ERROR',error=type(exc).__name__)
                if not args.loop:
                    break
                time.sleep(max(60,args.interval))
        finally:
            runtime.close()


if __name__ == '__main__':
    main()
