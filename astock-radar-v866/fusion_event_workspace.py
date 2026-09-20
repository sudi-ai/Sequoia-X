"""Server-rendered event observations; all external strings escaped."""
from html import escape
from fusion_engine import timestamp
from fusion_event_radar import LABELS


def render_events(state, now):
    e=lambda v:escape(str(v if v is not None else '未核验'),quote=True)
    generated=timestamp(state.get('generated_at'))
    fresh=bool(generated and -30 <= (now-generated).total_seconds() <= 180)
    out=['<section id="fusion-event-radar"><style>#fusion-event-radar{margin:24px 0;padding:20px;border:1px solid #577a94;border-radius:12px}#fusion-event-radar p{overflow-wrap:anywhere}#fusion-event-radar .event-calendar{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:12px}#fusion-event-radar .event-calendar article{background:#ffffff08;padding:12px;border-radius:8px}#fusion-event-radar .event-stocks{max-height:620px;overflow:auto;padding:8px}</style><h3>事件与情绪提前观察</h3>',
         '<p>提前发现事件 → 关联板块与公司 → 检查相对价格位置 → 跟踪证据和失效。</p>',
         '<p>相对低位不代表低估；行业关联不等于公司受益。所有候选仅研究观察。</p>']
    if not fresh:
        return ''.join(out)+ '<p>事件模块未运行或快照过期，以下旧状态不作为当前确认。</p></section>'
    label={'OBSERVING':'事件观察中','WAIT_INDUSTRY_DATA':'等待行业数据','EVENT_MODULE_ERROR':'模块异常，等待检查'}
    out.append('<p>更新：'+e(state.get('generated_at'))+'｜'+e(label.get(state.get('status'),'状态待核验'))+'</p>')
    cal=state.get('calendar',{})
    out.append('<p>'+e(cal.get('coverage','日历覆盖未知'))+'</p>')
    if cal.get('missing_calendar_years'):
        out.append('<p>尚缺官方年度日历：'+e(cal['missing_calendar_years'])+'，不外推农历节日。</p>')
    for error in cal.get('errors',[]):out.append('<p>'+e(error)+'</p>')
    events=state.get('events',[])
    if not events:out.append('<p>维护范围内暂无未来60天事件，不代表没有其他催化因素。</p>')
    themes=state.get('themes',{})
    out.append('<div class="event-calendar">')
    for event in events:
        out.append('<article><h4>'+e(event['name'])+' · '+e(event['start'])+'—'+e(event['end'])+'</h4>')
        days=event.get('days_to_start',0)
        out.append('<p>'+('还有'+e(days)+'天' if days>0 else '进行中')+'｜来源：'+e(event.get('source'))+'</p>')
        for key in event.get('themes',[]):
            theme=themes.get(key,{})
            out.append('<p>'+e(theme.get('name',key))+'：'+e(theme.get('hypothesis'))+'</p>')
        out.append('</article>')
    out.append('</div>')
    out.append('<p>'+e(state.get('evidence_coverage'))+'</p>')
    out.append('<p>行业列表获取时间：'+e(state.get('universe_observed_at'))+
               '；每个事件最多评估'+e(state.get('candidate_limit_per_event'))+'只，非全市场排名。</p>')
    if state.get('coverage_limited'):out.append('<p>关联股票超过评估上限，当前候选覆盖不完整。</p>')
    candidates=state.get('candidates',[])
    out.append('<details open><summary>关联股票观察池（'+str(len({r['ts_code'] for r in candidates}))+'只股票 / '+str(len(candidates))+'条事件关联，展示前30条）</summary><div class="event-stocks">')
    if not candidates:out.append('<p>等待真实行业分类与公司映射，不填充示例股票。</p>')
    for r in candidates[:30]:
        out.append('<article style="border-top:1px solid #ffffff26;padding:10px 0"><b>'+e(r['name'])+' '+e(r['ts_code'])+'</b> · '+e(r['event_name']))
        out.append('<p>'+e(LABELS.get(r['state'],r['state']))+'｜'+e(r.get('mapping'))+'</p>')
        out.append('<p>首次关联：'+e(r.get('first_seen_at'))+'；首次有效行情价：'+e(r.get('first_seen_price'))+
                   '（行情时间 '+e(r.get('first_price_at'))+'）</p>')
        s=r.get('structure',{})
        pos=s.get('position60')
        out.append('<p>60日区间位置：'+(e(round(pos*100,1))+'%' if pos is not None else '未核验')+
                   '；20日变化：'+(e(round(s['return20_pct'],2))+'%' if s.get('return20_pct') is not None else '未核验')+'；当前价：'+e(r.get('price'))+'</p>')
        names={'business':'主营关联','demand':'需求/订单','flow':'资金','sector':'板块','risk':'重大风险'}
        states={'SUPPORTS':'证据支持','CONTRADICTS':'证据反对','CONFLICT':'证据冲突','UNKNOWN':'未确认'}
        out.append('<p>'+'；'.join(e(names[k])+': '+e(states.get(v.get('status'),'未确认')) for k,v in r.get('evidence',{}).items())+'</p>')
        out.append('<p>下一步：'+e(r.get('next_condition'))+'<br>失效：'+e(r.get('invalidation'))+'</p></article>')
    out.append('</div></details><p>'+e(state.get('validation'))+'</p></section>')
    return ''.join(out)
