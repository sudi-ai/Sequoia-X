"""Read-only acceptance audit. Research outcomes are not executable profits."""
import argparse
import contextlib
import datetime as dt
import json
import math
import pathlib
import sqlite3

TZ = dt.timezone(dt.timedelta(hours=8))


def connection(path):
    return contextlib.closing(sqlite3.connect(pathlib.Path(path).resolve().as_uri() + '?mode=ro', uri=True))


def timestamp(value):
    try:
        result = dt.datetime.fromisoformat(str(value))
        return result if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False


def lineage(source, baseline):
    with connection(source) as old, connection(baseline) as new:
        rows = new.execute('SELECT event_key,first_seen_at,first_seen_price FROM original').fetchall()
        result = dict(origins=len(rows), matched=0, source_missing=0, source_changed=0, invalid_origins=0)
        for key, seen, price in rows:
            if not timestamp(seen) or not finite(price) or float(price) <= 0:
                result['invalid_origins'] += 1
                continue
            original = old.execute('SELECT first_seen_at,first_seen_price FROM v8_discovery_candidates WHERE event_key=?', (key,)).fetchall()
            if not original:
                result['source_missing'] += 1
            elif len(original) == 1 and str(original[0][0]) == str(seen) and finite(original[0][1]) and abs(float(original[0][1]) - float(price)) < 1e-6:
                result['matched'] += 1
            else:
                result['source_changed'] += 1
        result['status'] = 'PASS' if rows and result['matched'] == len(rows) else ('NO_SAMPLES' if not rows else 'FAIL')
        return result


def ledger(path, now):
    with connection(path) as db:
        origins = db.execute('SELECT signal_id,version,first_seen_at FROM origin').fetchall()
        versions = {}
        for signal, version, seen in origins:
            versions[version] = versions.get(version, 0) + 1
        delivery = dict(db.execute('SELECT status,COUNT(*) FROM outbox GROUP BY status').fetchall())
        families = dict(db.execute('SELECT family,COUNT(*) FROM origin GROUP BY family').fetchall())
        valid_origins = {signal: timestamp(seen) for signal, version, seen in origins if version == 'FUSION_SHADOW_2' and timestamp(seen) and timestamp(seen) <= now}
        outcomes = db.execute('SELECT signal_id,horizon,target_date,settled_at,gross_pct,assumed_net_pct FROM research_outcome').fetchall()
        horizons = {}
        rejected = 0
        accepted = set()
        duplicates = set()
        for signal, horizon, target, settled, gross, net in outcomes:
            key = (signal, horizon)
            if key in accepted:
                duplicates.add(key)
            try:
                day = dt.date.fromisoformat(str(target))
            except ValueError:
                rejected += 1
                continue
            when = timestamp(settled)
            first = valid_origins.get(signal)
            if first is None or when is None or when > now or when < first or day > now.date() or day < first.astimezone(TZ).date() or not finite(gross) or not finite(net):
                rejected += 1
                continue
            if key in accepted:
                continue
            accepted.add(key)
            horizons.setdefault(str(horizon), []).append(float(net))
        stats = {}
        for horizon, values in horizons.items():
            stats[horizon] = {
                'mature_records': len(values),
                'mean_assumed_net_pct': sum(values) / len(values),
                'positive_assumed_return_pct': 100 * sum(v > 0 for v in values) / len(values),
                'not_counted_as_mature': len(valid_origins) - len(values),
                'interpretation': 'Descriptive assumed-cost research returns only; not actual fills, not out-of-sample win rate.',
            }
        return {
            'origins': len(origins), 'versions': versions, 'families': families,
            'eligible_origin_records': len(valid_origins), 'delivery_status_counts': delivery,
            'outcome_rows': len(outcomes), 'rejected_outcome_rows': rejected,
            'duplicate_outcome_keys': len(duplicates), 'horizons': stats,
            'status': 'RESEARCH_RECORDS_AVAILABLE' if stats else 'INSUFFICIENT_MATURE_DATA',
            'limitation': 'Signals can overlap. Missing horizons are not zero returns. Unmatured and missing-price cases require separate settlement evidence. Delivery totals do not prove next-session end-to-end operation.',
        }


def build(data_dir, now=None):
    data = pathlib.Path(data_dir)
    now = now or dt.datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware audit time required')
    now = now.astimezone(TZ)
    report = {
        'checked_at': now.isoformat(), 'read_only_inputs': True,
        'actual_profit_validated': False, 'out_of_sample_advantage_validated': False,
        'ai_integration': 'NOT_VALIDATED_BY_THIS_AUDIT',
        'full_trading_session': 'REQUIRES_FORWARD_OBSERVATION',
    }
    config = data / 'fusion_v8_source.json'
    try:
        settings = json.loads(config.read_text(encoding='utf-8'))
        source = settings.get('database')
        if not source:
            report['lineage'] = {'status': 'NOT_CONFIGURED'}
        else:
            report['lineage'] = lineage(source, data / 'fusion_v8_baseline.sqlite3')
    except FileNotFoundError:
        report['lineage'] = {'status': 'NOT_CONFIGURED'}
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError) as exc:
        report['lineage'] = {'status': 'UNAVAILABLE', 'error_type': type(exc).__name__}
    try:
        report['research_ledger'] = ledger(data / 'fusion_shadow.sqlite3', now)
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        report['research_ledger'] = {'status': 'UNAVAILABLE', 'error_type': type(exc).__name__}
    report['ready_for_performance_claims'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='data')
    args = parser.parse_args()
    report = build(args.data)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if report['lineage']['status'] in ('FAIL', 'UNAVAILABLE') or report['research_ledger']['status'] == 'UNAVAILABLE' else 0


if __name__ == '__main__':
    raise SystemExit(main())
