"""Offline event regression: no real symbols, market calls or notifications."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fusion_engine import TZ
from fusion_event_calendar import calendar_events
from fusion_event_radar import EventRadar, evaluate, evidence_status
from fusion_event_workspace import render_events
from fusion_operations import Operations
from fusion_runtime_v2 import Runtime
from test_fusion_v2 import Hub

NOW=dt.datetime(2026,9,7,10,tzinfo=TZ)
EVENT=dict(id='test:event',name='测试事件',start='2026-09-25',end='2026-09-27',
           themes=['tourism'],source='https://example.invalid/event',published_at='2026-09-01T09:00:00+08:00')
STOCK=dict(ts_code='TEST.SZ',name='测试公司',industry='旅游服务',themes=['tourism'])


def quote(now=NOW, price=10):
    return dict(close=price,pre_close=10,source='verified_fixture',data_quality=1,
                source_trade_time=now.isoformat(),observed_at=now.isoformat())


def bars(now=NOW):
    days=sorted((now-dt.timedelta(days=i)).strftime('%Y%m%d') for i in range(1,100)
                if (now-dt.timedelta(days=i)).weekday()<5)[-65:]
    return [dict(trade_date=d,close=10,high=10.5,low=9.5,vol=1000,adj_factor=1) for d in days]


def evidence(kind, status='SUPPORTS', now=NOW):
    return dict(event_id=EVENT['id'],ts_code=STOCK['ts_code'],kind=kind,status=status,
                source='https://example.invalid/facts',fact='offline fixture',
                published_at=(now-dt.timedelta(hours=1)).isoformat(),observed_at=now.isoformat(),
                valid_until=(now+dt.timedelta(hours=2)).isoformat())


class CalendarTests(unittest.TestCase):
    def test_mid_autumn_and_national_are_both_present(self):
        r=calendar_events(dt.datetime(2026,9,5,tzinfo=TZ))
        self.assertEqual([(e['name'],e['days_to_start']) for e in r['events']], [('中秋假期',20),('国庆假期',26)])

    def test_does_not_invent_next_year_lunar_dates(self):
        r=calendar_events(dt.datetime(2026,12,25,tzinfo=TZ))
        self.assertEqual(r['missing_calendar_years'],[2027])
        self.assertEqual(r['events'],[])

    def test_unpublished_and_invalid_events_do_not_enter(self):
        future=dict(EVENT,id='future',published_at='2026-09-08T00:00:00+08:00')
        r=calendar_events(NOW,extra=[future,dict(EVENT,id='invalid',end='2026-09-01')])
        self.assertFalse(any(e['id'] in ('future','invalid') for e in r['events']))
        self.assertTrue(r['errors'])

    def test_duplicate_id_does_not_overwrite_official(self):
        r=calendar_events(NOW,extra=[dict(EVENT,id='holiday:2026:national',start='2026-09-08')])
        self.assertEqual(next(e for e in r['events'] if e['id']=='holiday:2026:national')['start'],'2026-10-01')


class RuleTests(unittest.TestCase):
    def check(self,q=None,history=None,ev=(),event=None):
        history=bars() if history is None else history
        return evaluate(event or EVENT,STOCK,q if q is not None else quote(),history,NOW,'20260904',ev)

    def test_calendar_and_low_position_never_grant_execution(self):
        r=self.check()
        self.assertEqual(r['state'],'LOW_POSITION_WATCH')
        self.assertFalse(r['execution_eligible'])
        self.assertIsNone(r['probability'])
        self.assertEqual(r['evidence']['demand']['status'],'UNKNOWN')

    def test_stale_or_future_quote_cannot_create_price_origin(self):
        for q in (quote(NOW-dt.timedelta(days=1)),quote(NOW+dt.timedelta(minutes=5)),{}):
            r=self.check(q=q)
            self.assertEqual(r['state'],'WAIT_DATA')
            self.assertIsNone(r['price'])

    def test_missing_or_stale_history_not_called_low(self):
        for history in ([],bars()[:-1]):
            self.assertEqual(self.check(history=history)['state'],'WAIT_DATA')

    def test_rally_is_not_low_position(self):
        self.assertEqual(self.check(q=quote(price=11))['state'],'CROWDED')

    def test_low_but_falling_is_not_eligible_low(self):
        falling=[dict(b,close=10+(64-i)*.03,high=10.5+(64-i)*.03,low=9.5+(64-i)*.03) for i,b in enumerate(bars())]
        self.assertEqual(self.check(history=falling)['state'],'FADING')

    def test_future_stale_and_conflicting_evidence(self):
        ev=evidence('demand'); ev['published_at']=(NOW+dt.timedelta(hours=1)).isoformat()
        self.assertEqual(evidence_status([ev],EVENT['id'],STOCK['ts_code'],NOW)['demand']['status'],'UNKNOWN')
        ev=evidence('flow',now=NOW-dt.timedelta(days=2));ev['valid_until']=(NOW+dt.timedelta(days=1)).isoformat()
        self.assertEqual(evidence_status([ev],EVENT['id'],STOCK['ts_code'],NOW)['flow']['status'],'UNKNOWN')
        self.assertEqual(self.check(ev=[evidence('demand'),evidence('demand','CONTRADICTS')])['state'],'FADING')

    def test_four_confirmations_still_research_only(self):
        r=self.check(q=quote(price=10.1),ev=[evidence(k) for k in ('demand','flow','sector','business')])
        self.assertEqual(r['state'],'EVIDENCE_ALIGNED')
        self.assertFalse(r['execution_eligible'])
        partial=self.check(q=quote(price=10.1),ev=[evidence(k) for k in ('demand','flow','sector')])
        self.assertNotEqual(partial['state'],'EVIDENCE_ALIGNED')

    def test_risk_and_cancellation_override_good_price(self):
        self.assertEqual(self.check(ev=[evidence('risk')])['state'],'BLOCKED')
        self.assertEqual(self.check(event=dict(EVENT,cancelled=True))['state'],'CANCELLED')
        self.assertEqual(self.check(event=dict(EVENT,end='2026-09-06'))['state'],'EXPIRED')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.ops=Operations(self.root);self.radar=EventRadar(self.root,self.ops)
        universe=dict(rows=[STOCK],observed_at=NOW.isoformat())
        self.radar.db.execute("INSERT INTO metadata VALUES ('universe',?)",(json.dumps(universe),));self.radar.db.commit()
        self.prep=type('Prep',(),{'bars':lambda s,*a:bars()})()

    def tearDown(self):
        self.radar.close();self.ops.close();self.tmp.cleanup()

    def run_step(self, now=NOW, quotes=None):
        return self.radar.step(now,lambda *a: (_ for _ in ()).throw(AssertionError('network call')),
                              self.prep,quotes or {STOCK['ts_code']:quote(now)},'20260904')

    def test_originals_and_first_price_are_immutable(self):
        self.run_step();later=NOW+dt.timedelta(minutes=1)
        state=self.run_step(later,{STOCK['ts_code']:quote(later,10.1)})
        self.assertTrue(all(r['first_seen_price']==10 for r in state['candidates']))
        for table in ('event_origin','candidate_origin','price_origin','observation'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.radar.db.execute('UPDATE '+table+' SET '+('price=99' if table=='price_origin' else "payload='{}'"))

    def test_unchanged_state_not_repeated_and_restart_does_not_replay(self):
        self.run_step();n=self.ops.db.execute('SELECT COUNT(*) FROM notice').fetchone()[0]
        self.run_step(NOW+dt.timedelta(minutes=1))
        self.assertEqual(self.ops.db.execute('SELECT COUNT(*) FROM notice').fetchone()[0],n)
        self.radar.close();self.radar=EventRadar(self.root,self.ops)
        self.run_step(NOW+dt.timedelta(minutes=2))
        self.assertEqual(self.ops.db.execute('SELECT COUNT(*) FROM notice').fetchone()[0],n)

    def test_new_module_writes_no_trade_journal(self):
        state=self.run_step()
        self.assertFalse((self.root/'fusion_shadow.sqlite3').exists())
        self.assertEqual(state['status'],'OBSERVING')
        self.assertTrue(self.radar.shortlist())

    def test_corrupt_input_visible_not_exception(self):
        (self.root/'fusion_events.json').write_text('{bad',encoding='utf-8')
        self.assertTrue(self.run_step()['calendar']['errors'])

    def test_shared_cache_reused_without_fetch_and_source_times_preserved(self):
        incoming=dict(records=[dict(STOCK,ts_code='CACHE'+str(i)) for i in range(3000)],
                      observed_at=(NOW+dt.timedelta(minutes=1)).isoformat(),source='Tushare.stock_basic')
        (self.root/'p0_stock_basic_cache.json').write_text(json.dumps(incoming),encoding='utf-8')
        state=self.run_step(NOW+dt.timedelta(minutes=2))
        self.assertEqual(state['universe_observed_at'],incoming['observed_at'])
        self.assertTrue(state['coverage_limited'])

    def test_business_mapping_can_add_duty_free_but_plain_retail_cannot(self):
        retail=dict(STOCK,industry='百货')
        self.radar.db.execute("UPDATE metadata SET payload=? WHERE id='universe'",(json.dumps(dict(rows=[retail],observed_at=NOW.isoformat())),))
        self.radar.db.commit()
        self.assertEqual(self.run_step()['candidates'],[])
        record=evidence('business');record.update(event_id='holiday:2026:national',theme='duty_free')
        (self.root/'fusion_event_evidence.json').write_text(json.dumps([record]),encoding='utf-8')
        state=self.run_step(NOW+dt.timedelta(minutes=1))
        self.assertEqual(len(state['candidates']),1)
        self.assertEqual(state['candidates'][0]['themes'],['duty_free'])

    def test_aviation_manufacturing_is_not_air_travel(self):
        stocks=[dict(STOCK,ts_code='FACTORY',industry='航空'),dict(STOCK,ts_code='AIRLINE',industry='空运')]
        self.radar.db.execute("UPDATE metadata SET payload=? WHERE id='universe'",(json.dumps(dict(rows=stocks,observed_at=NOW.isoformat())),))
        self.radar.db.commit()
        self.assertEqual({r['ts_code'] for r in self.run_step()['candidates']},{'AIRLINE'})

    def test_corrupt_manifest_does_not_falsely_cancel_known_event(self):
        path=self.root/'fusion_events.json';path.write_text(json.dumps([EVENT]),encoding='utf-8')
        self.run_step();path.write_text('{bad',encoding='utf-8')
        state=self.run_step(NOW+dt.timedelta(minutes=1))
        own=[r for r in state['candidates'] if r['event_id']==EVENT['id']]
        self.assertEqual(own[0]['state'],'WAIT_DATA')

    def test_stable_observations_sampled_not_duplicated_every_minute(self):
        self.run_step();n=self.radar.db.execute('SELECT count(*) FROM observation').fetchone()[0]
        self.run_step(NOW+dt.timedelta(minutes=1))
        self.assertEqual(self.radar.db.execute('SELECT count(*) FROM observation').fetchone()[0],n)
        self.run_step(NOW+dt.timedelta(minutes=16))
        self.assertEqual(self.radar.db.execute('SELECT count(*) FROM observation').fetchone()[0],n*2)

    def test_html_escapes_input_and_suppresses_stale_confirmations(self):
        state=self.run_step();state['events'][0]['name']='<script>bad</script>'
        html=render_events(state,NOW)
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('测试公司',render_events(state,NOW+dt.timedelta(hours=1)))

    def test_expired_events_retire_previous_candidates(self):
        self.run_step()
        self.run_step(dt.datetime(2026,10,8,10,tzinfo=TZ))
        self.assertEqual(set(r[0] for r in self.radar.db.execute('SELECT state FROM current')),{'EXPIRED'})


class IntegrationTests(unittest.TestCase):
    def test_module_error_does_not_break_existing_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(Hub(),directory)
            try:
                with patch.object(runtime.event_radar,'step',side_effect=ValueError('fixture')):
                    state=runtime.tick()
                self.assertEqual(state['event_radar']['status'],'EVENT_MODULE_ERROR')
                self.assertFalse(state['execution_eligible'])
            finally:runtime.close()


if __name__=='__main__':unittest.main()
