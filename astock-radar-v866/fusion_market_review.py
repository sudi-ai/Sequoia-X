"""Read-only factual market digests and research accounting."""
import sqlite3
from contextlib import closing
from pathlib import Path
from fusion_engine import timestamp, number
from fusion_journal_v2 import VERSION


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)


def market_snapshot(now, close_review=False, database=None):
    if database is None:
        from p0_realtime import DB_PATH
        database=DB_PATH
    try:
        with closing(readonly(database)) as db:
            db.row_factory=sqlite3.Row
            day=now.strftime('%Y%m%d')
            row=db.execute('SELECT * FROM snapshot_manifest WHERE trade_date=? ORDER BY observed_at DESC LIMIT 1',(day,)).fetchone()
            if not row:return {'status':'NO_TODAY_SNAPSHOT'}
            s=dict(row);source=str(s.get('source') or '')
            if not source or any(x in source.upper() for x in ('DEMO','MOCK','SIM','LEGACY')):
                return {'status':'SOURCE_REJECTED'}
            observed=timestamp(s.get('observed_at'))
            if observed is None or observed.date()!=now.date():return {'status':'TIMESTAMP_INVALID'}
            age=(now-observed).total_seconds()
            if age < -30 or (not close_review and age>180):return {'status':'STALE_OR_FUTURE'}
            def counts(sid):
                row=db.execute('SELECT COUNT(*) total,COUNT(pct_chg) valid,SUM(CASE WHEN pct_chg>0 THEN 1 ELSE 0 END) up,SUM(CASE WHEN pct_chg<0 THEN 1 ELSE 0 END) down FROM intraday_quote WHERE snapshot_id=?',(sid,)).fetchone()
                return {k:int(row[k] or 0) for k in row.keys()}
            b=counts(s['snapshot_id'])
            if not b['valid']:return {'status':'NO_VALID_QUOTES'}
            ratio=100*b['up']/b['valid'];delta=None
            previous=db.execute('SELECT snapshot_id FROM snapshot_manifest WHERE trade_date=? AND observed_at<? ORDER BY observed_at DESC LIMIT 1',(day,s['observed_at'])).fetchone()
            if previous:
                old=counts(previous[0])
                if old['valid']:delta=ratio-100*old['up']/old['valid']
            leaders=[dict(x) for x in db.execute('SELECT ts_code,name,pct_chg,industry FROM intraday_quote WHERE snapshot_id=? AND pct_chg IS NOT NULL ORDER BY pct_chg DESC,ts_code LIMIT 3',(s['snapshot_id'],))]
            sectors=[]
            try:sectors=[dict(x) for x in db.execute('SELECT sector,median_pct,up_ratio FROM sector_realtime_snapshot WHERE snapshot_id=? ORDER BY diffusion_rank LIMIT 3',(s['snapshot_id'],))]
            except sqlite3.Error:pass
            return dict(status='AVAILABLE',observed_at=s['observed_at'],source=source,breadth=b,
                        up_pct=ratio,up_change_pp=delta,leaders=leaders,sectors=sectors,close_verified=False)
    except (OSError,sqlite3.Error,ValueError,TypeError):
        return {'status':'MARKET_DATA_UNAVAILABLE'}


def research_review(directory, now):
    try:
        with closing(readonly(Path(directory)/'fusion_shadow.sqlite3')) as db:
            day=now.strftime('%Y%m%d');date=now.date().isoformat()
            total=db.execute('SELECT COUNT(*) FROM origin WHERE version=?',(VERSION,)).fetchone()[0]
            added=db.execute('SELECT COUNT(*),COUNT(DISTINCT code) FROM origin WHERE version=? AND trade_date=?',(VERSION,day)).fetchone()
            states=dict(db.execute('SELECT b.state,COUNT(DISTINCT b.signal_id) FROM observation b JOIN origin o USING(signal_id) WHERE o.version=? AND substr(b.evaluated_at,1,10)=? GROUP BY b.state',(VERSION,date)))
            outcomes=[]
            for h in (1,3,5):
                n,stocks,mean=db.execute('SELECT COUNT(*),COUNT(DISTINCT o.code),AVG(r.assumed_net_pct) FROM research_outcome r JOIN origin o USING(signal_id) WHERE o.version=? AND r.horizon=?',(VERSION,h)).fetchone()
                outcomes.append(dict(horizon=h,samples=n,stocks=stocks,mean_net_pct=mean,unsettled=total-n))
            return dict(status='AVAILABLE',new_signals=added[0],new_stocks=added[1],states=states,outcomes=outcomes)
    except (OSError,sqlite3.Error,ValueError,TypeError):
        return {'status':'RESEARCH_RECORDS_UNAVAILABLE'}


def num(v, suffix=''):
    x=number(v)
    return format(x,'.2f')+suffix if x is not None else '未核验'


def format_brief(kind,now,market,research,worker_status):
    close=kind=='CLOSE_REVIEW'
    lines=['📊 新版｜收盘复盘' if close else '🌐 新版｜市场变化摘要',
           '日期：'+now.date().isoformat()+'｜生成：'+now.strftime('%H:%M:%S'),'【市场事实】']
    if market.get('status')=='AVAILABLE':
        b=market['breadth']
        lines+=['快照接收：'+str(market['observed_at']),
                '覆盖 '+str(b['total'])+'｜有效涨跌幅 '+str(b['valid'])+'｜上涨 '+str(b['up'])+'｜下跌 '+str(b['down']),
                '上涨占比 '+num(market['up_pct'],'%')+'｜较前快照 '+num(market.get('up_change_pp'),'个百分点')]
        if market.get('sectors'):
            lines.append('板块：'+'；'.join(str(x['sector'])[:12]+' '+num(x.get('median_pct'),'%') for x in market['sectors']))
        lines.append('涨幅榜观察（不是提前发现或买点）：')
        lines += [str(x.get('name') or x['ts_code'])[:16]+' '+str(x['ts_code'])+' '+num(x['pct_chg'],'%') for x in market.get('leaders',[])[:3]]
        lines.append('源时间未核验；'+('当日最后已存快照，不保证最终收盘价。' if close else '仅描述接收到的行情。'))
    else:lines.append('数据不可用：'+str(market.get('status'))+'；不使用昨日数据替代。')
    if close:
        lines+=['','【真实研究记录】']
        if research.get('status')=='AVAILABLE':
            s=research['states']
            lines+=['今日新增 '+str(research['new_signals'])+'条／'+str(research['new_stocks'])+'只',
                    '出现失效 '+str(s.get('EXPIRED',0))+'｜数据暂停 '+str(s.get('WAIT_DATA',0))+'｜风险否决 '+str(s.get('BLOCKED',0)),
                    '按信号去重，状态可重叠；零记录不等于全市场扫描完成。',
                    '【累计成熟样本，不是今日收益】']
            for x in research['outcomes']:
                lines.append('T+'+str(x['horizon'])+'：成熟 '+str(x['samples'])+'／'+str(x['stocks'])+'只｜均值 '+num(x['mean_net_pct'],'%')+'｜未结算 '+str(x['unsettled']))
            lines.append('未结算包含未到期与缺数据，不混称未成熟。')
        else:lines.append('研究账本不可用，不填零值冒充结果。')
    lines+=['','👉 先复核数据与原观察条件，再核对实际持仓；不按涨幅榜追买。',
            '运行：'+str(worker_status)+'｜执行资格未开放。',
            '📌 结算为发现价至固定收盘的复权收益，假设总成本20bp；非成交收益、非样本外胜率，同股和同期样本可能相关。']
    text='\n'.join(lines)
    if len(text.encode('utf-8'))>1800:
        lines=[x for x in lines if not any(str(y['ts_code']) in x for y in market.get('leaders',[])) and not x.startswith('板块：')]
        text='\n'.join(lines)
    return text


def build_brief(kind,now,directory,worker_status):
    m=market_snapshot(now,kind=='CLOSE_REVIEW')
    review=research_review(directory,now) if kind=='CLOSE_REVIEW' else {}
    return format_brief(kind,now,m,review,worker_status),{'market':m.get('status'),'research':review.get('status')}
