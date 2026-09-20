import contextlib
import datetime as dt
import json
import pathlib
import sqlite3
import tempfile
import unittest

from fusion_evidence_audit import TZ, build, ledger, lineage


class EvidenceAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.old = self.root / 'old.sqlite3'
        self.new = self.root / 'fusion_v8_baseline.sqlite3'
        self.journal = self.root / 'fusion_shadow.sqlite3'
        with contextlib.closing(sqlite3.connect(self.old)) as db, db:
            db.execute('CREATE TABLE v8_discovery_candidates(event_key,first_seen_at,first_seen_price)')
            db.execute("INSERT INTO v8_discovery_candidates VALUES ('one','2026-09-01T09:32:00+08:00',10)")
        with contextlib.closing(sqlite3.connect(self.new)) as db, db:
            db.execute('CREATE TABLE original(event_key,first_seen_at,first_seen_price)')
            db.execute("INSERT INTO original VALUES ('one','2026-09-01T09:32:00+08:00',10)")
        with contextlib.closing(sqlite3.connect(self.journal)) as db, db:
            db.executescript('CREATE TABLE origin(signal_id,version,first_seen_at,family); CREATE TABLE outbox(status); CREATE TABLE research_outcome(signal_id,horizon,target_date,settled_at,gross_pct,assumed_net_pct);')
            db.execute("INSERT INTO origin VALUES ('one','FUSION_SHADOW_2','2026-09-01T09:32:00+08:00','LATENT_BASE')")
        (self.root / 'fusion_v8_source.json').write_text(json.dumps({'database': str(self.old)}))
        self.now = dt.datetime(2026, 9, 5, 16, tzinfo=TZ)

    def outcome(self, **kw):
        values = dict(signal='one', horizon='T+1', target='2026-09-02', settled='2026-09-02T16:00:00+08:00', gross=2.0, net=1.8)
        values.update(kw)
        with contextlib.closing(sqlite3.connect(self.journal)) as db, db:
            db.execute('INSERT INTO research_outcome VALUES (?,?,?,?,?,?)', tuple(values.values()))

    def test_matching_origins_and_no_writes(self):
        before = self.old.read_bytes(), self.new.read_bytes(), self.journal.read_bytes()
        report = build(self.root, self.now)
        self.assertEqual(report['lineage']['matched'], 1)
        self.assertFalse(report['actual_profit_validated'])
        self.assertEqual(before, (self.old.read_bytes(), self.new.read_bytes(), self.journal.read_bytes()))

    def test_changed_original_is_failure(self):
        with contextlib.closing(sqlite3.connect(self.old)) as db, db:
            db.execute('UPDATE v8_discovery_candidates SET first_seen_price=11')
        self.assertEqual(lineage(self.old, self.new)['status'], 'FAIL')

    def test_missing_database_is_not_created(self):
        missing = self.root / 'missing'
        report = build(missing, self.now)
        self.assertEqual(report['research_ledger']['status'], 'UNAVAILABLE')
        self.assertFalse(missing.exists())

    def test_empty_outcomes_are_not_zero_win_rate(self):
        report = ledger(self.journal, self.now)
        self.assertEqual(report['status'], 'INSUFFICIENT_MATURE_DATA')
        self.assertEqual(report['horizons'], {})

    def test_research_not_actual_performance(self):
        self.outcome()
        report = build(self.root, self.now)
        self.assertEqual(report['research_ledger']['horizons']['T+1']['mean_assumed_net_pct'], 1.8)
        self.assertFalse(report['ready_for_performance_claims'])

    def test_future_outcome_excluded(self):
        self.outcome(target='2026-09-07', settled='2026-09-07T16:00:00+08:00')
        self.assertEqual(ledger(self.journal, self.now)['rejected_outcome_rows'], 1)

    def test_nonfinite_return_excluded(self):
        self.outcome(net=float('inf'))
        self.assertEqual(ledger(self.journal, self.now)['rejected_outcome_rows'], 1)

    def test_unknown_signal_excluded(self):
        self.outcome(signal='missing')
        self.assertEqual(ledger(self.journal, self.now)['rejected_outcome_rows'], 1)

    def test_duplicate_does_not_inflate_sample(self):
        self.outcome()
        self.outcome()
        report = ledger(self.journal, self.now)
        self.assertEqual(report['duplicate_outcome_keys'], 1)
        self.assertEqual(report['horizons']['T+1']['mature_records'], 1)

    def test_naive_audit_time_rejected(self):
        with self.assertRaises(ValueError):
            build(self.root, dt.datetime(2026, 9, 5))


if __name__ == '__main__':
    unittest.main()
