import unittest,tempfile,sqlite3,json,datetime as dt
from pathlib import Path
from fusion_event_research import valid_event,recent,flow_history,render,TZ,Article,market_context,shortlist
class Checks(unittest.TestCase):
 def setUp(self):self.now=dt.datetime(2026,9,8,17,tzinfo=TZ)
 def test_quote_and_industry(self):
  body='正式发布清洁能源设备采购补贴政策，下月开始执行。'
  r=dict(relevant=True,summary='发布补贴',mechanism='可能增加采购',risk='执行进度',invalidation='取消补贴',quote=body,benefits=['电气设备'],harms=[])
  self.assertEqual(valid_event(r,body,['电气设备']),r)
  with self.assertRaises(ValueError):valid_event(dict(r,benefits=['虚构行业']),body,['电气设备'])
  with self.assertRaises(ValueError):valid_event(dict(r,quote='这条证据没有出现在原始新闻正文里面'),body,['电气设备'])
 def test_future_and_stale(self):
  self.assertFalse(recent((self.now+dt.timedelta(seconds=1)).isoformat(),self.now,100))
  self.assertFalse(recent((self.now-dt.timedelta(seconds=101)).isoformat(),self.now,100))
 def test_article_scope(self):
  p=Article();p.feed('<nav>广告</nav><div class="left_zw"><p>政策正文</p><script>假证据</script></div><footer>其他股票</footer>')
  self.assertEqual(' '.join(p.parts),'政策正文')
 def make_db(self,p):
  c=sqlite3.connect(p);c.execute('CREATE TABLE moneyflow(id INTEGER PRIMARY KEY,ts_code TEXT,source TEXT,trade_date TEXT,observed_at TEXT,effective_at TEXT,data_quality REAL,payload_json TEXT)')
  for i in range(5):
   day=(self.now-dt.timedelta(days=i+1)).strftime('%Y%m%d');at=(self.now-dt.timedelta(days=i+1)).isoformat()
   c.execute('INSERT INTO moneyflow VALUES (?,?,?,?,?,?,?,?)',(i,'000001.SZ','tushare.moneyflow_ths@dailyfetch',day,at,at,1,json.dumps(dict(ts_code='000001.SZ',trade_date=day,net_amount=10))))
  c.commit();return c
 def test_flow_continuity(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.db';c=self.make_db(p)
   self.assertEqual(flow_history(p,'000001.SZ',self.now)['positive_days'],5)
   c.execute("UPDATE moneyflow SET payload_json='{}' WHERE id=0");c.commit()
   self.assertIsNone(flow_history(p,'000001.SZ',self.now));c.close()
 def test_no_mixed_provider(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.db';c=self.make_db(p);c.execute("UPDATE moneyflow SET source='tushare.moneyflow@dailyfetch' WHERE id=0");c.commit()
   self.assertIsNone(flow_history(p,'000001.SZ',self.now));c.close()
 def test_stale_intraday_not_confirmation(self):
  s=dict(code='000001.SZ',industry='银行',stage='值得跟踪')
  result=market_context([s],[dict(ts_code='000001.SZ',industry='银行')],dict(generated_at=self.now.isoformat(),event_radar=dict(candidates=[dict(ts_code='000001.SZ',quote_fresh=True,evaluated_at=self.now.isoformat(),source_trade_time='2026-09-08 09:30:00',pct_chg=5)])),self.now)
  self.assertEqual(result[0]['intraday'],'当日价格异动未确认')
  self.assertIn('覆盖不足',result[0]['sentiment'])
 def test_end_to_end_shortlist(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.db';c=self.make_db(p)
   c.execute('CREATE TABLE stock_daily(id INTEGER PRIMARY KEY,ts_code TEXT,source TEXT,trade_date TEXT,observed_at TEXT,effective_at TEXT,data_quality REAL,payload_json TEXT)')
   for i in range(20):
    day=(self.now-dt.timedelta(days=i+1)).strftime('%Y%m%d');at=(self.now-dt.timedelta(days=i+1)).isoformat()
    payload=dict(ts_code='000001.SZ',trade_date=day,close=10,low=9,high=11,vol=100)
    c.execute('INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?,?)',(i,'000001.SZ','tushare.daily@dailyfetch',day,at,at,1,json.dumps(payload)))
   c.commit();c.close()
   found,coverage=shortlist([dict(ts_code='000001.SZ',name='测试银行',industry='银行')],['银行'],p,self.now)
   self.assertEqual(len(found),1);self.assertEqual(found[0]['flow']['positive_days'],5)
   self.assertIn('未核验',found[0]['business']);self.assertEqual(coverage['qualified'],1)
   self.assertEqual(shortlist([dict(ts_code='000001.SZ',name='测试银行',industry='银行')],['设备'],p,self.now)[0],[])
 def test_no_candidate_invention(self):
  r=dict(summary='事件',mechanism='条件推演',benefits=['设备'],harms=[],risk='执行不及预期',quote='正文证据',invalidation='事件取消')
  out=render(r,dict(url='https://www.chinanews.com.cn/x'),[],dict(industry_universe=20,daily_coverage=2),self.now)
  self.assertIn('不强行列标的',out);self.assertIn('覆盖',out);self.assertIn('不自动交易',out)
if __name__=='__main__':unittest.main()
