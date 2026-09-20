"""Read-only paid snapshots for prospective AI reviews, never a PIT backfill.

No provider calls, credentials, announcements, or raw data frames leave this
adapter. Numbers are computed deterministically; missing evidence stays missing.
"""
from contextlib import closing
import datetime as dt
import json
import math
from pathlib import Path
import re
import sqlite3
import time

TZ = dt.timezone(dt.timedelta(hours=8))
SOURCES = {
    'stock_daily': {'tushare.daily@dailyfetch'},
    'daily_basic': {'tushare.daily_basic@dailyfetch'},
    'moneyflow': {'tushare.moneyflow_ths@dailyfetch'},
}


def timestamp(value):
    try:
        value = dt.datetime.fromisoformat(str(value))
        return value.astimezone(TZ) if value.tzinfo else None
    except (ValueError, TypeError):
        return None


def number(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and (not positive or result > 0) else None
    except (ValueError, TypeError, OverflowError):
        return None


def stock_code(value):
    value = str(value)
    if not re.fullmatch(r'\d{6}(?:\.(?:SH|SZ|BJ))?', value):
        return None
    bare = value[:6]
    exchange = 'SH' if bare[0] in '569' else 'BJ' if bare[0] in '48' else 'SZ'
    return value if '.' in value else bare+'.'+exchange


def eligible(row, asof, code, table):
    if row['source'] not in SOURCES[table] or row['ts_code'] != code:
        return False
    observed, effective = timestamp(row['observed_at']), timestamp(row['effective_at'])
    quality = number(row['data_quality'])
    if not observed or not effective or observed > asof or effective > asof or not quality or quality <= 0:
        return False
    try:
        day = dt.datetime.strptime(row['trade_date'], '%Y%m%d').date()
    except (ValueError, TypeError):
        return False
    # Daily observations are never allowed to stand in for today's intraday data.
    return (day < asof.date() and 0 < (asof.date()-day).days <= 90
            and observed.date() >= day and effective.date() >= day)


def collect(path, code, asof):
    """Return compact derivatives with per-part provenance and explicit limits."""
    if asof.tzinfo is None:
        raise ValueError('Timezone required')
    asof = asof.astimezone(TZ)
    code = stock_code(code)
    bundle = {'version':'PAID_EVIDENCE_1','asof':asof.isoformat(),
              'use':'prospective_review_not_original_discovery_backtest',
              'parts':{},'missing':[],
              'limits':['NO_CURRENT_AUCTION','NO_CURRENT_MINUTE_QUOTE',
                        'NO_SECTOR_CONFIRMATION','NO_ANNOUNCEMENT_VERIFICATION',
                        'NO_EXECUTION_CONFIRMATION','NO_CALIBRATED_WIN_PROBABILITY']}
    if not code or not Path(path).is_file():
        bundle['missing'].append('INVALID_CODE_OR_SNAPSHOT_DB_MISSING')
        return bundle
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        for table in SOURCES:
            deadline = time.monotonic()+0.75
            db.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
            try:
                rows = db.execute('SELECT id,ts_code,source,trade_date,observed_at,effective_at,data_quality,payload_json FROM '+table+' WHERE ts_code=? ORDER BY id DESC LIMIT 600', (code,)).fetchall()
            except sqlite3.Error:
                bundle['missing'].append(table+':QUERY_UNAVAILABLE')
                continue
            finally:
                db.set_progress_handler(None,0)
            selected = {}
            for row in rows:
                if not eligible(row,asof,code,table):
                    continue
                try:
                    payload=json.loads(row['payload_json'])
                    if not isinstance(payload,dict) or stock_code(payload.get('ts_code')) != code:
                        continue
                    if str(payload.get('trade_date')) != row['trade_date']:
                        continue
                except (ValueError,TypeError):
                    continue
                day=row['trade_date']
                # Prefer the latest observation available by review time, not the largest row id.
                previous=selected.get(day)
                if previous is None or timestamp(row['observed_at'])>timestamp(previous[0]['observed_at']):
                    selected[day]=(row,payload)
            days=sorted(selected)
            if not days or (asof.date()-dt.datetime.strptime(days[-1],'%Y%m%d').date()).days>7:
                bundle['missing'].append(table+':NO_RECENT_ELIGIBLE_OBSERVATION')
                continue
            latest,payload=selected[days[-1]]
            metrics={}
            if table=='stock_daily':
                window=[selected[d] for d in days[-20:]]
                bars=[p for _,p in window if all(number(p.get(k),True) is not None for k in ('close','low','high','vol'))]
                if len(bars)!=20 or any(p['low']>p['high'] for p in bars):
                    bundle['missing'].append(table+':NEED_20_VALID_OBSERVATIONS')
                    continue
                low=min(float(p['low']) for p in bars);high=max(float(p['high']) for p in bars)
                close=float(bars[-1]['close']);base=sum(float(p['vol']) for p in bars[:-1])/19
                metrics={'observation_count':20,'window_start':days[-20],
                         'window_end':days[-1], 'close_position_in_observed_range':round((close-low)/(high-low),4) if high>low else None,
                         'last_volume_over_previous19_mean':round(float(bars[-1]['vol'])/base,4),
                         'return_last5_observation_intervals':round(close/float(bars[-6]['close'])-1,6),
                         'continuous_trading_calendar_verified':False,'adjustment':'unadjusted_no_corporate_action_correction'}
            elif table=='daily_basic':
                for key in ('pe_ttm','pb'):
                    value=number(payload.get(key))
                    if value is not None:metrics[key]=value
                metrics['interpretation']='valuation_ratios_not_an_intrinsic_value_estimate'
            else:
                value=number(payload.get('net_amount'))
                if value is not None:metrics['net_flow_sign']=1 if value>0 else -1 if value<0 else 0
                metrics['interpretation']='provider_flow_classification_not_verified_institutional_intent'
            if len(metrics)<2:
                bundle['missing'].append(table+':NUMERIC_FIELDS_MISSING')
                continue
            bundle['parts'][table]={'source':latest['source'],'trade_date':latest['trade_date'],
                'observed_at':latest['observed_at'],'effective_at':latest['effective_at'],
                'data_quality':latest['data_quality'],'metrics':metrics}
    return bundle
