# -*- coding: utf-8 -*-
from datetime import datetime

def dashboard_snapshot():
    return {
      'market':{'advance_rate':58.4,'limit_up':98,'limit_down':9,'broken_rate':25.8,'advance_height':6},
      'indices':[
        {'name':'上证指数','value':4108.14,'pct':-1.32},
        {'name':'深证成指','value':16015.11,'pct':-2.18},
        {'name':'创业板指','value':4235.14,'pct':1.02},
        {'name':'沪深300','value':4936.17,'pct':0.34}],
      'themes':[
        {'name':'CPO/光模块','rise':5.12,'strength':92,'flow':32.45},
        {'name':'中报预增','rise':3.85,'strength':88,'flow':18.62},
        {'name':'AI应用','rise':4.21,'strength':85,'flow':26.31},
        {'name':'半导体','rise':2.96,'strength':82,'flow':21.07},
        {'name':'存储芯片','rise':3.15,'strength':78,'flow':12.34}],
      'candidates':[
        {'name':'中际旭创','ts_code':'300308.SZ','daily_pct':3.2,'market_temp':68,'sector_strength':92,'auction_quality':81,'main_flow_score':72,'chip_lock_score':76,'close_strength':74,'event_risk':8,'distribution_risk':12,'trend_score':90,'buy_timing_score':88,'rr':2.4,'market_phase':'主升','sector':'CPO/光模块','entry_price':138.8,'action':'回踩确认后1成试仓','defense':'135.20','target':'147.50'},
        {'name':'新易盛','ts_code':'300502.SZ','daily_pct':7.8,'market_temp':68,'sector_strength':90,'auction_quality':78,'main_flow_score':64,'chip_lock_score':69,'close_strength':70,'event_risk':10,'distribution_risk':18,'trend_score':88,'buy_timing_score':76,'rr':2.0,'market_phase':'主升','sector':'CPO/光模块','entry_price':118.5,'action':'等待回踩，不追高','defense':'114.80','target':'126.00'},
        {'name':'万润科技','ts_code':'002654.SZ','daily_pct':2.1,'market_temp':68,'sector_strength':78,'auction_quality':61,'main_flow_score':42,'chip_lock_score':63,'close_strength':58,'event_risk':12,'distribution_risk':18,'trend_score':73,'buy_timing_score':66,'rr':1.7,'market_phase':'主升','sector':'存储芯片','entry_price':16.2,'action':'观察','defense':'15.72','target':'17.10'}],
      'time':datetime.now().strftime('%H:%M:%S')
    }
