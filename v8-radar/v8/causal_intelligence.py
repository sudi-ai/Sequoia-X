from __future__ import annotations

import json
from datetime import datetime
from typing import Any,Mapping

from .paid_data import PAID_DATA
from .runtime_log import log_exception
from .storage import connect,initialize,register_event_forward,save_event_hypothesis
from .event_taxonomy import TOPIC_ALIASES


CHAINS={
 "液冷服务器":{"triggers":("液冷","冷板","浸没式散热","数据中心散热"),"demand":["液冷系统","冷板/管路","温控设备"],
  "chain":["算力密度提升","风冷效率下降","液冷渗透率提升","温控与零部件需求增长"],"keywords":("液冷","温控","冷板","数据中心散热"),"horizon":"中短期"},
 "算力":{"triggers":("算力","数据中心","智算中心","人工智能基础设施"),"demand":["服务器","光模块","交换机","供配电与温控"],
  "chain":["计算需求增长","数据中心投资增加","设备与配套需求扩张"],"keywords":("服务器","光模块","数据中心","算力","交换机"),"horizon":"中期"},
 "半导体":{"triggers":("半导体","芯片","晶圆","先进封装","国产替代"),"demand":["设备","材料","设计与封测"],
  "chain":["终端/政策需求变化","晶圆与封装扩产","设备材料订单变化"],"keywords":("半导体","集成电路","晶圆","封装","光刻"),"horizon":"中期"},
 "机器人":{"triggers":("机器人","人形机器人","减速器","伺服系统"),"demand":["减速器","伺服电机","传感器","执行器"],
  "chain":["应用与订单增长","整机排产提升","核心零部件需求增长"],"keywords":("机器人","减速器","伺服","执行器","传感器"),"horizon":"中期"},
 "低空经济":{"triggers":("低空经济","eVTOL","无人机","通航"),"demand":["整机","动力系统","空管与通信","复合材料"],
  "chain":["政策/适航推进","示范应用增加","整机及配套采购增长"],"keywords":("无人机","航空器","通航","空管","复合材料"),"horizon":"中长期"},
 "固态电池":{"triggers":("固态电池","固体电解质","硫化物电解质"),"demand":["固态电解质","锂电设备","关键材料"],
  "chain":["技术验证推进","中试与扩产","设备材料需求提升"],"keywords":("固态电池","固体电解质","锂电设备"),"horizon":"中长期"},
 "创新药":{"triggers":("创新药","临床试验","新药获批","授权交易"),"demand":["创新药研发","临床服务","商业化渠道"],
  "chain":["临床/审批/授权进展","成功概率或现金流预期变化","研发与商业化价值重估"],"keywords":("创新药","药物研发","临床试验","生物医药"),"horizon":"中长期"},
 "黄金":{"triggers":("黄金","金价","贵金属"),"demand":["黄金采选","冶炼","资源储量"],
  "chain":["避险/利率预期变化","金价变化","矿企收入与利润弹性变化"],"keywords":("黄金采选","黄金矿","贵金属"),"horizon":"短中期"},
 "稀土":{"triggers":("稀土","稀土永磁","镨钕"),"demand":["稀土资源","磁材","回收"],
  "chain":["供给/出口/需求变化","稀土价格变化","资源与磁材利润变化"],"keywords":("稀土","永磁","磁性材料","镨钕"),"horizon":"短中期"},
 "光伏":{"triggers":("光伏","硅料","组件","逆变器"),"demand":["硅料硅片","电池组件","逆变器与储能"],
  "chain":["装机/政策/价格变化","产业链排产变化","设备材料与组件盈利变化"],"keywords":("光伏","太阳能","逆变器","硅片","组件"),"horizon":"中期"},
}

# The taxonomy recognizes many more industries than the original ten hand-
# written chains.  Build a conservative fallback for every recognized topic so
# an event can reach sector/company mapping without inventing a stock.
for _topic,_aliases in TOPIC_ALIASES.items():
    CHAINS.setdefault(_topic,{"triggers":tuple(_aliases),"demand":[f"{_topic}相关产品与服务"],
      "chain":["事件改变行业预期","订单/成本/供需可能变化",f"{_topic}产业链盈利预期变化"],
      "keywords":tuple(dict.fromkeys((_topic,*_aliases))),"horizon":"待Forward验证"})

NEGATIVE_IMPACTS={
 "原油":["航空运输","高耗能化工","公路物流"],"天然气":["燃气下游","高耗能制造"],
 "黄金":["高风险偏好资产"],"航运港口":["依赖进口原料的制造业","出口交付链"],
 "半导体":["受限制的海外供应链","旧制程替代路线"],"固态电池":["传统液态电解液","传统隔膜"],
 "新能源汽车":["传统燃油车产业链"],"房地产":["高杠杆及流动性弱主体"],
 "军工":["航空旅游","风险偏好敏感行业"],"应急安全":["当地生产运输与消费服务"],
}

CUE_SECTOR_EFFECTS={
 "SECURITY_ESCALATION":{"benefit":["军工","黄金","原油","应急安全"],"negative":["旅游酒店","航运港口"]},
 "SUPPLY_DISRUPTION":{"benefit":["原油","天然气","煤炭","航运港口"],"negative":["化工","汽车消费"]},
 "EXTREME_WEATHER":{"benefit":["应急安全","水利","农业种业"],"negative":["旅游酒店","航运港口"]},
 "PUBLIC_HEALTH":{"benefit":["医疗器械","创新药","中药"],"negative":["旅游酒店"]},
 "POLICY_TIGHTEN":{"benefit":["国产软件","半导体"],"negative":[]},
 "DEESCALATION":{"benefit":["旅游酒店","航运港口"],"negative":["黄金","军工"]},
}


def detect_topics(text:str)->list[tuple[str,dict[str,Any],list[str]]]:
    scored=[]
    for topic,rule in CHAINS.items():
        hits=[word for word in rule['triggers'] if word.lower() in text.lower()]
        if hits:scored.append((len(hits),topic,rule,hits))
    scored.sort(key=lambda x:(x[0],sum(len(word) for word in x[3])),reverse=True)
    return [(topic,rule,hits) for _,topic,rule,hits in scored]


def detect_topic(text:str)->tuple[str|None,dict[str,Any]|None,list[str]]:
    topics=detect_topics(text)
    return topics[0] if topics else (None,None,[])


def refresh_company_profiles(now:datetime)->dict[str,dict[str,Any]]:
    initialize();c=connect();day=now.date().isoformat()
    count=c.execute("select count(*) from v8_company_profiles where updated_date=?",(day,)).fetchone()[0]
    if count<1000:
        basics={}
        try:
            raw=PAID_DATA.call('stock_basic',cache_key=f'v8:causal:basic:{day}',ttl_seconds=86400,
              exchange='',list_status='L',fields='ts_code,name,industry')
            if hasattr(raw,'to_dict'):
                basics={str(x.get('ts_code')):x for x in raw.to_dict(orient='records')}
        except Exception as exc:log_exception('causal_stock_basic',exc)
        for exchange in ('SSE','SZSE','BSE'):
            try:
                raw=PAID_DATA.call('stock_company',cache_key=f'v8:causal:company:{exchange}:{day}',ttl_seconds=86400,
                  exchange=exchange,fields='ts_code,main_business,business_scope')
                rows=raw.to_dict(orient='records') if hasattr(raw,'to_dict') else []
                for row in rows:
                    ts=str(row.get('ts_code') or '');code=ts.split('.')[0];base=basics.get(ts,{})
                    c.execute("""insert into v8_company_profiles(code,name,industry,main_business,business_scope,updated_date,details_json)
                      values(?,?,?,?,?,?,?) on conflict(code) do update set name=excluded.name,industry=excluded.industry,
                      main_business=excluded.main_business,business_scope=excluded.business_scope,updated_date=excluded.updated_date""",
                      (code,base.get('name'),base.get('industry'),row.get('main_business'),row.get('business_scope'),day,'{}'))
                c.commit()
            except Exception as exc:log_exception('causal_stock_company',exc,exchange=exchange)
    rows=c.execute("select * from v8_company_profiles").fetchall();c.close();return {str(x['code']):dict(x) for x in rows}


def _business_beneficiaries(topic:str,rule:Mapping[str,Any],profiles:Mapping[str,Mapping[str,Any]],direct_code:str|None)->list[dict[str,Any]]:
    results=[]
    for code,row in profiles.items():
        text=' '.join(str(row.get(k) or '') for k in ('industry','main_business','business_scope'))
        hits=[word for word in rule['keywords'] if word in text]
        if not hits and code!=direct_code:continue
        direct=code==direct_code;industry=str(row.get('industry') or '')
        industry_match=bool(topic and topic in industry)
        score=min(95,(88 if direct else 44)+10*len(hits)+(12 if industry_match else 0))
        tier="A" if direct else ("B" if score>=64 else ("C" if score>=50 else "D"))
        results.append({'code':code,'name':row.get('name') or '', 'relevance_score':score,
          'tier':tier,'mapped_sector':topic,
          'relation_type':'事件直接点名' if direct else ('所属行业+主营匹配' if industry_match else '主营业务文本匹配'),
          'evidence':([f'行业:{industry}'] if industry_match else [])+hits[:4], 'price_realization':'UNKNOWN'})
    results.sort(key=lambda x:(x['relevance_score'],x['tier']=='A'),reverse=True);return results[:8]


def _merge_companies(groups:list[list[dict[str,Any]]],limit:int=10)->list[dict[str,Any]]:
    merged:dict[str,dict[str,Any]]={}
    for group in groups:
        for item in group:
            code=str(item.get('code') or '')
            old=merged.get(code)
            if old is None or float(item.get('relevance_score') or 0)>float(old.get('relevance_score') or 0):merged[code]=item
    rows=sorted(merged.values(),key=lambda x:(str(x.get('tier'))=='A',float(x.get('relevance_score') or 0)),reverse=True)
    # Preserve sector diversity so one broad industry (for example coal) does
    # not crowd out every other transmission path in the WeChat mapping.
    selected=[];sector_counts:dict[str,int]={}
    for item in rows:
        sector=str(item.get('mapped_sector') or 'UNKNOWN')
        if sector_counts.get(sector,0)>=2:continue
        selected.append(item);sector_counts[sector]=sector_counts.get(sector,0)+1
        if len(selected)>=limit:return selected
    for item in rows:
        if item not in selected:selected.append(item)
        if len(selected)>=limit:break
    return selected


def _price_realization(beneficiary:dict[str,Any],now:datetime)->None:
    code=str(beneficiary['code']);ts=f"{code}.{'SH' if code.startswith('6') else ('BJ' if code.startswith(('4','8','92')) else 'SZ')}"
    try:
        raw=PAID_DATA.call('rt_k',cache_key=f'v8:causal:rtk:{code}:{now:%Y%m%d%H%M}',ttl_seconds=300,ts_code=ts)
        rows=raw.to_dict(orient='records') if hasattr(raw,'to_dict') else []
        row=rows[0] if rows else {};price=float(row.get('close') or 0);pre=float(row.get('pre_close') or 0)
        pct=(price/pre-1)*100 if price and pre else None
        status='UNKNOWN' if pct is None else ('HIGH' if pct>=7 else ('PARTIAL' if pct>=3 else 'LOW'))
        beneficiary.update({'price':price or None,'day_pct':round(pct,2) if pct is not None else None,'price_realization':status})
    except Exception as exc:log_exception('causal_price_realization',exc,code=code)


def _direct_fundamentals(code:str,now:datetime)->dict[str,Any]:
    ts=f"{code}.{'SH' if code.startswith('6') else ('BJ' if code.startswith(('4','8','92')) else 'SZ')}";result={}
    try:
        raw=PAID_DATA.call('index_member_all',cache_key=f'v8:causal:industry:{code}:{now.date()}',ttl_seconds=86400,
          ts_code=ts,is_new='Y')
        rows=raw.to_dict(orient='records') if hasattr(raw,'to_dict') else []
        if rows:
            row=rows[0];result['industry_path']=[row.get('l1_name'),row.get('l2_name'),row.get('l3_name')]
    except Exception as exc:log_exception('causal_industry_member',exc,code=code)
    try:
        raw=PAID_DATA.call('fina_mainbz',cache_key=f'v8:causal:mainbz:{code}:{now.date()}',ttl_seconds=86400,
          ts_code=ts,type='P')
        rows=raw.to_dict(orient='records') if hasattr(raw,'to_dict') else []
        rows.sort(key=lambda x:(str(x.get('end_date') or ''),float(x.get('bz_sales') or 0)),reverse=True)
        result['main_products']=[str(x.get('bz_item')) for x in rows[:5] if x.get('bz_item')]
    except Exception as exc:log_exception('causal_main_business_breakdown',exc,code=code)
    return result


def analyze_event(event:Mapping[str,Any],now:datetime|None=None)->dict[str,Any]:
    now=now or datetime.now().astimezone();text=f"{event.get('title','')} {event.get('summary','')} {event.get('reason','')}"
    topics=detect_topics(text)
    preferred=str(event.get('topic') or '')
    chosen=next((item for item in topics if item[0]==preferred),topics[0] if topics else None)
    topic,rule,hits=chosen if chosen else (None,None,[])
    cue_names=set(str(x) for x in (event.get('cue_names') or []))
    cue_priority=("SECURITY_ESCALATION","SUPPLY_DISRUPTION","POLICY_SUPPORT","POLICY_TIGHTEN",
      "DEMAND_SURGE","PRICE_SHOCK","EXTREME_WEATHER","PUBLIC_HEALTH","DEESCALATION")
    effect_benefit=[];effect_negative=[]
    for cue in sorted(cue_names,key=lambda x:cue_priority.index(x) if x in cue_priority else len(cue_priority)):
        effect=CUE_SECTOR_EFFECTS.get(cue,{})
        effect_benefit.extend(effect.get('benefit') or []);effect_negative.extend(effect.get('negative') or [])
    if not topic and effect_benefit:
        topic=effect_benefit[0];rule=CHAINS.get(topic)
    if not rule and not effect_benefit and not effect_negative:
        generic=event.get('generic_chain') if isinstance(event.get('generic_chain'),list) else []
        result={**dict(event),'causal_chain':generic,'demand':[],'beneficiaries':[],
          'confidence':event.get('probability',25),
          'impact_horizon':'UNKNOWN','realization_status':'UNKNOWN','action_permission':'ANNOTATION_ONLY'}
        save_event_hypothesis(result);return result
    profiles=refresh_company_profiles(now);direct_code=str(event.get('code') or '') or None
    direction=str(event.get('direction') or 'NEUTRAL')
    primary_topics=[topic] if topic else []
    benefit_topics=list(dict.fromkeys((primary_topics if direction!='NEGATIVE' else [])+effect_benefit))
    negative_topics=list(dict.fromkeys((primary_topics if direction=='NEGATIVE' else [])+
      (NEGATIVE_IMPACTS.get(topic,[]) if topic else [])+effect_negative))
    benefit_groups=[_business_beneficiaries(t,CHAINS[t],profiles,direct_code if direction!='NEGATIVE' and t==topic else None)
      for t in benefit_topics if t in CHAINS]
    negative_groups=[_business_beneficiaries(t,CHAINS[t],profiles,direct_code if direction=='NEGATIVE' and t==topic else None)
      for t in negative_topics if t in CHAINS]
    beneficiaries=_merge_companies(benefit_groups)
    affected_companies=_merge_companies(negative_groups)
    if direct_code and not any(x['code']==direct_code for x in beneficiaries):
        target=affected_companies if direction=='NEGATIVE' else beneficiaries
        if not any(x['code']==direct_code for x in target):target.insert(0,{'code':direct_code,'name':event.get('name') or '',
          'relevance_score':95,'tier':'A','mapped_sector':topic,'relation_type':'事件直接点名',
          'evidence':['事件直接点名'],'price_realization':'UNKNOWN'})
    if direct_code:
        direct=next((x for x in beneficiaries+affected_companies if x['code']==direct_code),None)
        if direct:direct.update(_direct_fundamentals(direct_code,now))
    high_value=(direct_code or str(event.get('primary_category') or '')=='NATIONAL_POLICY' or
      str(event.get('alert_type') or '') in {'CANDIDATE_CATALYST','HOLDING_HARD_RISK','EVENT_RISK','REPORT_CHANGE'})
    if high_value:
        for item in (beneficiaries[:3]+affected_companies[:3]):_price_realization(item,now)
    source_weight={'anns_d':85,'major_news':75,'news':65,'report_rc':55}.get(str(event.get('source_api')),50)
    confidence=min(95,source_weight+min(10,len(hits)*4)+(8 if event.get('code') else 0))
    known=[x['price_realization'] for x in (beneficiaries[:4]+affected_companies[:4]) if x.get('price_realization')!='UNKNOWN']
    realization='HIGH' if 'HIGH' in known else ('PARTIAL' if 'PARTIAL' in known else ('LOW' if known else 'UNKNOWN'))
    result={**dict(event),'topic':topic,'related_topics':[item[0] for item in topics if item[0]!=topic][:3],
      'causal_chain':rule['chain'] if rule else list(event.get('generic_chain') or []),
      'demand':rule['demand'] if rule else [],'direct_benefit_sectors':benefit_topics,
      'beneficiaries':beneficiaries,'negative_sectors':negative_topics,'affected_companies':affected_companies,
      'confidence':confidence,'impact_horizon':rule['horizon'],
      'realization_status':realization,'action_permission':'ANNOTATION_ONLY'}
    save_event_hypothesis(result)
    for item in (beneficiaries[:4]+affected_companies[:4]):
        register_event_forward(str(event['event_id']),str(item['code']),str(item.get('name') or ''),
          str(event.get('published_at') or now.isoformat(timespec='seconds')),item.get('price'))
    return result
