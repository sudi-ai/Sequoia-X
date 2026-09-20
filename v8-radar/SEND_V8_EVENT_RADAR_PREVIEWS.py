from __future__ import annotations

import os,time
from datetime import datetime
from v8.env_loader import load_v8_env


def main()->int:
    load_v8_env();os.environ['V8_PUSH_ENABLED']='true'
    from v8.message import build_event_message
    from v8.push import send_once
    stamp=datetime.now().astimezone().isoformat(timespec='seconds')
    samples=[
      ('持仓重大利空','HOLDING_HARD_RISK','NEGATIVE','HARD_BLOCK','英维克收到重大风险公告（模拟）'),
      ('候选催化观察','CANDIDATE_CATALYST','POSITIVE','LOW','英维克中标重大液冷项目（模拟）'),
      ('市场事件观察','MARKET_CATALYST','POSITIVE','LOW','算力行业迎来政策支持（模拟）'),
      ('研报变化观察','REPORT_CHANGE','POSITIVE','LOW','机构首次覆盖并上调盈利预测（模拟）'),
      ('普通事件风险','EVENT_RISK','NEGATIVE','HIGH','公司披露股东拟减持计划（模拟）'),
    ]
    batch=datetime.now().astimezone().strftime('%Y%m%d%H%M%S');failed=[]
    for index,(label,kind,direction,risk,title) in enumerate(samples,1):
        event={'alert_type':kind,'direction':direction,'risk_level':risk,'title':title,'published_at':stamp,
          'source_api':'排版模拟','code':'002837' if kind!='MARKET_CATALYST' else None,
          'name':'英维克' if kind!='MARKET_CATALYST' else None,'topic':'液冷服务器' if kind!='MARKET_CATALYST' else '算力'}
        message=f'【事件雷达排版验收 {index:02d} {label}｜模拟内容】\n'+build_event_message(event)
        ok,detail=send_once(f'V8_EVENT_PREVIEW|{batch}|{index}',message);print(label,ok,detail)
        if not ok:failed.append(label)
        time.sleep(.8)
    return 1 if failed else 0


if __name__=='__main__':raise SystemExit(main())
