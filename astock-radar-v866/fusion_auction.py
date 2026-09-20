"""Current-day opening auction brief; raw-date validation before normalization.

Contract: https://tushare.pro/document/2?doc_id=369
stk_auction amount is CNY, vol is shares; historic stk_auction_o is never a fallback.
"""
import datetime as dt
import json
import re
import sqlite3
import statistics
from pathlib import Path

from fusion_engine import TZ, number


class AuctionFeed:
    def __init__(self, hub):
        self.hub = hub

    def fetch(self, day):
        if self.hub.pro is None:
            return [], 'NO_TOKEN'
        missing = object()
        try:
            frame = self.hub.guard.call('fusion_stk_auction',
                lambda: self.hub.pro.stk_auction(trade_date=day, ts_type='STK'),
                default=missing, min_interval=.1, timeout_seconds=8.0)
            if frame is missing:
                return [], 'INTERFACE_UNAVAILABLE'
            required = {'ts_code','trade_date','price','pre_close','amount','vol'}
            if not required.issubset(frame.columns):
                return [], 'FIELDS_MISSING'
            return frame.to_dict('records'), 'OK'
        except Exception:
            return [], 'INTERFACE_ERROR'


def valid_rows(rows, day):
    result = {}
    for row in rows:
        code = str(row.get('ts_code',''))
        if str(row.get('trade_date','')) != day:
            continue
        if not re.fullmatch(r'(?:[036][0-9]{5}\.(?:SH|SZ)|[489][0-9]{5}\.BJ)', code):
            continue
        price, previous, amount, volume = [number(row.get(k)) for k in ('price','pre_close','amount','vol')]
        if not all(x is not None and x > 0 for x in (price,previous,amount,volume)):
            continue
        if abs(amount / volume / price - 1) > .05:
            continue
        result[code] = dict(ts_code=code, trade_date=day, price=price,
                            pre_close=previous, amount=amount, vol=volume,
                            pct_chg=(price/previous-1)*100)
    return result


class AuctionBrief:
    def __init__(self, directory, feed, operations):
        self.root = Path(directory)
        self.feed, self.operations = feed, operations
        self.db = sqlite3.connect(str(self.root/'fusion_auction.sqlite3'))
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS history(day TEXT,code TEXT,amount REAL,PRIMARY KEY(day,code));
        CREATE TABLE IF NOT EXISTS batch(day TEXT PRIMARY KEY,rows INTEGER,observed_at TEXT);
        CREATE TABLE IF NOT EXISTS pool(day TEXT PRIMARY KEY,frozen_at TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS report(day TEXT PRIMARY KEY,observed_at TEXT,payload TEXT);
        ''')
        self.status = {'status':'WAITING_WINDOW','source':'stk_auction','execution_eligible':False}
        self.last_attempt = None
        self.history_days = []
        self.history_target = None

    def prepare(self, now, previous_day, calendar_fetch):
        # Never spend preopen time backfilling history or substitute it for today's auction.
        minute = now.hour*60+now.minute
        if 540 <= minute < 576 or not previous_day:
            return
        if self.history_target != previous_day:
            rows,status = calendar_fetch('trade_cal',dict(exchange='SSE',
                start_date=(now-dt.timedelta(days=60)).strftime('%Y%m%d'),end_date=previous_day))
            if status != 'OK':return
            self.history_days = sorted({str(x.get('cal_date')) for x in rows
                if str(x.get('is_open')) in ('1','1.0') and str(x.get('cal_date','')) <= previous_day})[-20:]
            self.history_target = previous_day
        done = {x[0] for x in self.db.execute('SELECT day FROM batch')}
        day = next((x for x in reversed(self.history_days) if x not in done),None)
        if day is None:return
        rows,status = self.feed.fetch(day)
        valid = valid_rows(rows,day) if status == 'OK' else {}
        if len(valid) < 3000:
            self.status['baseline_status'] = 'HISTORY_INCOMPLETE'
            return
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO history VALUES (?,?,?)',[(day,k,v['amount']) for k,v in valid.items()])
            self.db.execute('INSERT OR IGNORE INTO batch VALUES (?,?,?)',(day,len(valid),now.isoformat()))
        self.status['baseline_status'] = 'PREPARING_SAME_INTERFACE_HISTORY'

    def freeze(self, now, origins):
        day = now.strftime('%Y%m%d')
        if now.hour*60+now.minute >= 565:return
        pool = {}
        for origin in origins:
            if origin.get('family') != 'LATENT_BASE':continue
            try:
                evidence = json.loads(origin.get('evidence_json') or '{}')
            except (ValueError,TypeError):continue
            pool[origin['code']] = {'name':evidence.get('name') or origin['code'],
                'structure':(evidence.get('evidence') or {}).get('structure') or {}}
        with self.db:
            self.db.execute('INSERT INTO pool VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET frozen_at=excluded.frozen_at,payload=excluded.payload',
                (day,now.isoformat(),json.dumps(pool,ensure_ascii=False)))

    def enrich(self, rows, day, pool):
        enriched = []
        for code,row in rows.items():
            history = [x[0] for x in self.db.execute('SELECT amount FROM history WHERE code=? AND day<? ORDER BY day DESC LIMIT 20',(code,day))]
            ratio = row['amount']/statistics.median(history) if len(history)>=5 else None
            item = dict(row, history_samples=len(history), amount_ratio=ratio,
                        in_latent_pool=code in pool, confirmation='不属于冻结潜伏池',name=code)
            if code in pool:
                item['name'] = pool[code]['name']
                s = pool[code]['structure'];support=number(s.get('support'));resistance=number(s.get('resistance'))
                if ratio is None or support is None or resistance is None:
                    item['confirmation']='证据不足，待复核'
                elif 0 <= row['pct_chg'] <= 3 and ratio >= 1.5 and support <= row['price'] <= resistance:
                    item['confirmation']='竞价观察条件满足，非买点'
                else:item['confirmation']='竞价观察条件未满足'
            enriched.append(item)
        return enriched

    def finish(self, now, status, rows, pool_status, enabled):
        day=now.strftime('%Y%m%d')
        report=dict(status=status,trade_date=day,observed_at=now.isoformat(),source='stk_auction',
                    source_time_policy='接口返回交易日，无逐笔源时间；接收时间单独记录',
                    pool_status=pool_status,rows=rows,execution_eligible=False)
        with self.db:
            added=self.db.execute('INSERT OR IGNORE INTO report VALUES (?,?,?)',
                (day,now.isoformat(),json.dumps(report,ensure_ascii=False))).rowcount
        if not added:return self.status
        lines=['🟡 A股机会雷达 新版｜当日竞价简报',
               '日期：'+day+'｜接收：'+now.strftime('%H:%M:%S'),
               '来源：stk_auction｜仅竞价结果，不是当前连续交易价格',
               '状态：'+status+'｜有效样本 '+str(len(rows)),
               '潜伏池：'+pool_status]
        ordered=sorted(rows,key=lambda x:(not x['in_latent_pool'],-x['amount']))[:5]
        for x in ordered:
            ratio=format(x['amount_ratio'],'.2f')+'倍' if x['amount_ratio'] is not None else '样本不足'
            lines += ['', '📌 '+str(x['name'])[:16]+(' '+x['ts_code'] if x['name']!=x['ts_code'] else ''),
                      '竞价 '+format(x['price'],'.2f')+'元｜'+format(x['pct_chg'],'+.2f')+'%｜成交额 '+format(x['amount']/10000,'.1f')+'万元',
                      '历史成交额比 '+ratio+'（'+str(x['history_samples'])+'日）',x['confirmation']]
        if not rows:lines.append('⚠ 当天数据未就绪、权限/字段异常或错过窗口；不使用昨日数据替代。')
        lines += ['', '👉 仅复核竞价响应，不直接买入。条件满足不等于完整策略确认，仍缺公告、成交与样本外验证。',
                  '历史比=本次金额/此前同接口最多20日中位数，至少5日；缺少名称则显示代码。']
        text='\n'.join(lines)
        delivery=self.operations.emit('AUCTION:'+day,'AUCTION_BRIEF',text,now,enabled)
        self.status={k:v for k,v in report.items() if k!='rows'}
        self.status.update(row_count=len(rows),delivery=delivery)
        return self.status

    def step(self, now, calendar, previous_day, calendar_fetch, origins, enabled, clock=None):
        day=now.strftime('%Y%m%d');minute=now.hour*60+now.minute
        if calendar is not True:
            self.status.update(status='MARKET_CLOSED' if calendar is False else 'CALENDAR_UNKNOWN')
            if calendar is False:self.prepare(now,previous_day,calendar_fetch)
            return self.status
        self.freeze(now,origins)
        prior=self.db.execute('SELECT payload FROM report WHERE day=?',(day,)).fetchone()
        if prior:
            old=json.loads(prior[0]);self.status={k:v for k,v in old.items() if k!='rows'};self.status['row_count']=len(old['rows']);return self.status
        if not 566 <= minute < 575:
            self.prepare(now,previous_day,calendar_fetch)
            self.status.update(status='WAITING_WINDOW');return self.status
        pool_row=self.db.execute('SELECT payload FROM pool WHERE day=?',(day,)).fetchone()
        pool=json.loads(pool_row[0]) if pool_row else {}
        pool_status=('开盘前冻结 '+str(len(pool))+'只') if pool_row else '无开盘前冻结记录，不追溯补造'
        if minute>=570:return self.finish(now,'MISSED_OR_UNAVAILABLE',[],pool_status,enabled)
        if self.last_attempt and (now-self.last_attempt).total_seconds()<45:return self.status
        self.last_attempt=now
        raw,status=self.feed.fetch(day)
        received=clock() if clock else dt.datetime.now(TZ)
        rows=valid_rows(raw,day) if status=='OK' else {}
        if received.date()!=now.date() or received.hour*60+received.minute>=570:
            return self.finish(received,'MISSED_OR_UNAVAILABLE',[],pool_status,enabled)
        if not rows:
            self.status.update(status='WAITING_TODAY_DATA',interface_status=status)
            if minute>=569:return self.finish(received,'DATA_UNAVAILABLE',[],pool_status,enabled)
            return self.status
        return self.finish(received,'PARTIAL_COVERAGE' if len(rows)<3000 else 'AVAILABLE',self.enrich(rows,day,pool),pool_status,enabled)

    def close(self):
        self.db.close()
