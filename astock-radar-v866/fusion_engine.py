"""Pure research rules: no provider, filesystem, probability or order side effects."""
import datetime as dt
import math
import statistics

VERSION = 'FUSION_SHADOW_1'
TZ = dt.timezone(dt.timedelta(hours=8))
FAMILIES = {'LATENT_BASE': '低位蓄势', 'TREND_PULLBACK': '趋势回踩', 'EARLY_IGNITION': '启动早期'}


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def timestamp(value):
    raw = str(value or '').strip()
    # Never manufacture a date for an HH:MM:SS-only quote.
    if len(raw) < 10:
        return None
    try:
        t = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)
    except ValueError:
        try:
            return dt.datetime.strptime(raw, '%Y%m%d%H%M%S').replace(tzinfo=TZ)
        except ValueError:
            return None


def quote_issues(quote, now, calendar_open):
    issues = []
    source_time = timestamp(quote.get('source_trade_time') or quote.get('trade_time'))
    receipt = timestamp(quote.get('observed_at'))
    for label, t in (('个股源时间', source_time), ('接收时间', receipt)):
        if t is None or t.date() != now.date() or not -30 <= (now-t).total_seconds() <= 180:
            issues.append(label + '缺失或过期')
    if source_time and receipt and source_time > receipt + dt.timedelta(seconds=30):
        issues.append('行情源时间晚于接收时间')
    if calendar_open is not True:
        issues.append('未确认今天为交易日')
    minute = now.hour*60 + now.minute
    if not (570 <= minute < 690 or 780 <= minute < 900):
        issues.append('非连续交易时段')
    source = str(quote.get('source') or '').upper()
    if not source or any(tag in source for tag in ('DEMO', 'MOCK', 'LEGACY', 'SIM')):
        issues.append('不是已确认的真实行情来源')
    if (number(quote.get('data_quality')) or 0) < .75:
        issues.append('行情字段质量不足')
    if (number(quote.get('close')) or 0) <= 0 or (number(quote.get('pre_close')) or 0) <= 0:
        issues.append('价格或昨收无效')
    return issues


def ordered_pullback(points, current_vwap, min_drop_pct=.35):
    valid = []
    for point in points:
        t, price = timestamp(point.get('source_trade_time')), number(point.get('price'))
        if t and price and price > 0 and (not valid or t > valid[-1][0]):
            valid.append((t, price))
    if len(valid) < 4 or not current_vwap or current_vwap <= 0:
        return {'confirmed': False, 'reason': '独立时间点不足'}
    peak = valid[0]
    trough = None
    for point in valid[1:-1]:
        if point[1] > peak[1]:
            peak, trough = point, None
        elif point[1] < peak[1] and (trough is None or point[1] < trough[1]):
            trough = point
    last = valid[-1]
    drop = (1-trough[1]/peak[1])*100 if trough else 0
    confirmed = bool(trough and peak[0] < trough[0] < last[0] and drop >= min_drop_pct
                     and last[1] > valid[-2][1] and last[1] >= peak[1] and last[1] >= current_vwap)
    return {'confirmed': confirmed, 'drop_pct': drop, 'peak_at': peak[0].isoformat(),
            'trough_at': trough[0].isoformat() if trough else None,
            'reason': '先高点、后回撤、再收复高点与VWAP' if confirmed else '未形成完整回踩收复序列'}


def historical_structure(bars, quote, now):
    # Backfilled bars are historical context available NOW, not reconstructed PIT features.
    rows = sorted(bars, key=lambda r: str(r.get('trade_date', '')))
    rows = [r for r in rows if str(r.get('trade_date', '')).replace('-', '') < now.strftime('%Y%m%d')]
    rows = list({str(r.get('trade_date')): r for r in rows}.values())
    if len(rows) < 60:
        return {'status': 'INSUFFICIENT', 'sample_n': len(rows)}
    required = ('close', 'high', 'low', 'vol', 'adj_factor')
    if any(any(number(r.get(k)) is None or number(r[k]) <= 0 for k in required) for r in rows[-60:]):
        return {'status': 'INCOMPLETE', 'sample_n': len(rows)}
    rows = rows[-60:]
    last_close = float(rows[-1]['close'])
    pre_close = number(quote.get('pre_close'))
    # Ex-date or stale daily context must be reconciled, not compared on mismatched scales.
    if not pre_close or abs(pre_close/last_close-1) > .003:
        return {'status': 'PRICE_BASIS_UNCONFIRMED', 'sample_n': len(rows)}
    base_factor = float(rows[-1]['adj_factor'])
    closes = [float(r['close'])*float(r['adj_factor'])/base_factor for r in rows]
    highs = [float(r['high'])*float(r['adj_factor'])/base_factor for r in rows]
    lows = [float(r['low'])*float(r['adj_factor'])/base_factor for r in rows]
    price = number(quote.get('close'))
    lo, hi = min(lows), max(highs)
    ma20, ma60 = statistics.mean(closes[-20:]), statistics.mean(closes)
    old_ma20 = statistics.mean(closes[-25:-5])
    return {'status': 'OK', 'sample_n': len(rows), 'last_trade_date': rows[-1]['trade_date'],
            'ma20': ma20, 'ma60': ma60, 'ma20_slope_pct': (ma20/old_ma20-1)*100,
            'return20_pct': (closes[-1]/closes[-21]-1)*100,
            'position60': (price-lo)/(hi-lo) if hi > lo and price else None,
            'range20_pct': (max(highs[-20:])/min(lows[-20:])-1)*100,
            'volume_contraction': statistics.mean(float(r['vol']) for r in rows[-5:]) / statistics.mean(float(r['vol']) for r in rows[-20:-5]),
            'higher_lows': min(lows[-5:]) >= min(lows[-10:-5])*.98,
            'support': min(lows[-10:]), 'resistance': max(highs[-20:]),
            'historical_context_only': True}


def assess(quote, bars, points, now, calendar_open, event_coverage=None):
    issues = quote_issues(quote, now, calendar_open)
    result = {'version': VERSION, 'ts_code': quote.get('ts_code'), 'name': quote.get('name'),
              'price': number(quote.get('close')), 'source_trade_time': quote.get('source_trade_time') or quote.get('trade_time'),
              'observed_at': quote.get('observed_at'), 'evaluated_at': now.isoformat(),
              'state': 'WAIT_DATA', 'family': '', 'issues': issues, 'evidence': {},
              'execution_eligible': False, 'validation_status': 'UNVALIDATED', 'probability': None,
              'event_coverage': event_coverage or 'UNKNOWN', 'data_mode': 'LIVE_RESEARCH',
              'next_condition': '补齐时效、结构与风险证据', 'invalidation': '数据过期或结构条件破坏时停止参考'}
    if issues:
        return result
    structure = historical_structure(bars, quote, now)
    result['evidence']['structure'] = structure
    if structure['status'] != 'OK':
        result['issues'].append('历史结构数据不足或价格口径未对齐：' + structure['status'])
        return result
    price, previous = float(quote['close']), float(quote['pre_close'])
    pct = (price/previous-1)*100
    vwap = number(quote.get('vwap'))
    gap = (price/vwap-1)*100 if vwap and vwap > 0 else None
    result['evidence'].update({'pct_chg': pct, 'vwap': vwap, 'vwap_distance_pct': gap})
    if event_coverage == 'HARD_RISK':
        result.update(state='BLOCKED', issues=['已确认重大风险'], next_condition='风险解除后重新评估')
        return result
    result['issues'].append('研究规则尚无样本外执行资格')
    if event_coverage != 'VERIFIED':
        result['issues'].append('公告风险覆盖未知，不代表没有风险')
    families = []
    if (-1 <= pct <= 2.5 and structure['position60'] is not None and 0 <= structure['position60'] <= .60
            and -8 <= structure['return20_pct'] <= 15 and structure['range20_pct'] <= 18
            and structure['ma20'] >= structure['ma60']*.98 and structure['ma20_slope_pct'] >= 0
            and structure['higher_lows'] and structure['volume_contraction'] <= 1.05):
        families.append('LATENT_BASE')
    controlled = gap is not None and 0 <= gap <= 1.5 and 0 <= pct <= 4
    sequence = ordered_pullback(points, vwap)
    result['evidence']['pullback_sequence'] = sequence
    if controlled and structure['ma20'] >= structure['ma60'] and structure['ma20_slope_pct'] > 0 and sequence['confirmed']:
        families.append('TREND_PULLBACK')
    valid_points = []
    for point in points:
        t = timestamp(point.get('source_trade_time'))
        amount = number(point.get('amount'))
        if t and amount is not None and (not valid_points or t > valid_points[-1][0]):
            valid_points.append((t, amount, number(point.get('price'))))
    acceleration = None
    if len(valid_points) >= 3:
        a, b, c = valid_points[-3:]
        d1, d2 = (b[0]-a[0]).total_seconds(), (c[0]-b[0]).total_seconds()
        if 20 <= d1 <= 180 and 20 <= d2 <= 180 and b[1] > a[1] and c[1] >= b[1]:
            acceleration = ((c[1]-b[1])/d2)/((b[1]-a[1])/d1)
    result['evidence']['amount_rate_acceleration'] = acceleration
    if controlled and acceleration is not None and acceleration >= 1.2 and len(valid_points) >= 3:
        p = [r[2] for r in valid_points[-3:]]
        if all(x is not None for x in p) and p[0] < p[1] < p[2] and structure['ma20_slope_pct'] >= 0:
            families.append('EARLY_IGNITION')
    result.update(state='WATCH' if families else 'NO_SETUP', family=families[0] if families else '', matched_families=families)
    if families:
        result['next_condition'] = '继续观察独立行情确认；事件风险、可成交性及样本外验证未完成前不升级执行'
        result['invalidation'] = '行情过期、对应结构条件破坏或确认重大风险时撤销研究观察'
    return result
