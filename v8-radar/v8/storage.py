from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .config import CONFIG


SCHEMA = """
CREATE TABLE IF NOT EXISTS v8_signal_decisions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,strategy_version TEXT NOT NULL,
 signal_time TEXT NOT NULL,code TEXT NOT NULL,name TEXT,source TEXT,action TEXT NOT NULL,
 opportunity_score REAL,market_score REAL,market_regime TEXT,sector_score REAL,trend_quality_score REAL,
 fund_quality_score REAL,signal_persistence_score REAL,risk_score REAL,suggested_position_pct REAL,
 executable_price REAL,decision_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS v8_decision_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,evaluation_time TEXT NOT NULL,action TEXT NOT NULL,
 decision_json TEXT NOT NULL,evidence_cutoff TEXT NOT NULL,UNIQUE(event_key,evaluation_time));
CREATE TABLE IF NOT EXISTS v8_counterfactuals(
 id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,group_name TEXT NOT NULL,decision TEXT NOT NULL,
 reason TEXT,feature_json TEXT NOT NULL,outcome_status TEXT NOT NULL DEFAULT 'IMMATURE',
 UNIQUE(event_key,group_name));
CREATE TABLE IF NOT EXISTS v8_persistence_observations(
 id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,checkpoint_minute INTEGER NOT NULL,
 observed_at TEXT NOT NULL,price REAL,vwap REAL,order_imbalance REAL,amount_delta REAL,sector_score REAL,
 blowoff_reversal INTEGER NOT NULL DEFAULT 0,evidence_json TEXT NOT NULL,
 UNIQUE(event_key,checkpoint_minute));
CREATE TABLE IF NOT EXISTS v8_execution_outcomes(
 id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL,track TEXT NOT NULL,horizon TEXT,
 matured INTEGER NOT NULL DEFAULT 0,entry_price REAL,exit_time TEXT,exit_price REAL,exit_reason TEXT,
 gross_return_pct REAL,cost_bps REAL,net_return_pct REAL,mfe_pct REAL,mae_pct REAL,max_drawdown_pct REAL,
 holding_trade_days INTEGER,details_json TEXT NOT NULL DEFAULT '{}',UNIQUE(event_key,track,horizon));
CREATE TABLE IF NOT EXISTS v8_portfolio_risk_snapshots(
 id INTEGER PRIMARY KEY AUTOINCREMENT,observed_at TEXT NOT NULL,total_exposure_pct REAL,sector_exposure_json TEXT,
 daily_pnl_pct REAL,portfolio_drawdown_pct REAL,market_regime TEXT,new_positions_today INTEGER,
 risk_action TEXT,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_push_state(
 dedupe_key TEXT PRIMARY KEY,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,last_attempt_at TEXT,sent_at TEXT,message_hash TEXT);
CREATE TABLE IF NOT EXISTS v8_signal_delivery(
 event_key TEXT NOT NULL,state TEXT NOT NULL,evaluated_at TEXT NOT NULL,first_seen_price REAL,
 evaluated_price REAL,price_move_from_discovery_pct REAL,push_status TEXT,pushed_at TEXT,
 details_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(event_key,state));
CREATE TABLE IF NOT EXISTS v8_positions(
 position_id TEXT PRIMARY KEY,code TEXT NOT NULL,name TEXT,shares REAL NOT NULL,cost_price REAL NOT NULL,
 entry_date TEXT,status TEXT NOT NULL DEFAULT 'HOLDING',manual_stop_price REAL,notes TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS v8_position_trades(
 id INTEGER PRIMARY KEY AUTOINCREMENT,position_id TEXT NOT NULL,trade_time TEXT NOT NULL,side TEXT NOT NULL,
 shares REAL NOT NULL,price REAL NOT NULL,reason TEXT,cost_bps REAL NOT NULL DEFAULT 0,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_api_budget(
 id INTEGER PRIMARY KEY AUTOINCREMENT,called_monotonic REAL NOT NULL,units INTEGER NOT NULL,
 api TEXT NOT NULL DEFAULT 'UNKNOWN',priority TEXT NOT NULL DEFAULT 'NORMAL');
CREATE TABLE IF NOT EXISTS v8_early_watch(
 event_key TEXT PRIMARY KEY,observed_at TEXT NOT NULL,code TEXT NOT NULL,name TEXT,watch_type TEXT NOT NULL,
 watch_score REAL NOT NULL,reason TEXT,feature_json TEXT NOT NULL,action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY');
CREATE TABLE IF NOT EXISTS v8_strategy_factor_shadow(
 event_key TEXT NOT NULL,factor_name TEXT NOT NULL,checkpoint_minute INTEGER NOT NULL,
 observed_at TEXT NOT NULL,code TEXT NOT NULL,name TEXT,industry TEXT,status TEXT NOT NULL,score REAL,
 factor_version TEXT NOT NULL,action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',
 feature_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(event_key,factor_name,checkpoint_minute));
CREATE INDEX IF NOT EXISTS idx_v8_strategy_factor_lookup
 ON v8_strategy_factor_shadow(factor_name,status,observed_at,code);
CREATE TABLE IF NOT EXISTS v8_miss_audit(
 trade_date TEXT NOT NULL,code TEXT NOT NULL,name TEXT,actual_return_pct REAL,max_return_pct REAL,
 discovery_status TEXT NOT NULL,decision_action TEXT,miss_reason TEXT,details_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(trade_date,code));
CREATE TABLE IF NOT EXISTS v8_position_state(
 position_id TEXT PRIMARY KEY,current_state TEXT NOT NULL,entered_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_position_defense(
 position_id TEXT PRIMARY KEY,initial_stop_price REAL,current_trailing_stop REAL,previous_trailing_stop REAL,
 highest_price_since_entry REAL,highest_close_since_entry REAL,current_r_multiple REAL,profit_stage TEXT,
 first_break_time TEXT,break_confirmation_count INTEGER NOT NULL DEFAULT 0,last_break_bar_time TEXT,
 current_action TEXT,action_priority INTEGER NOT NULL DEFAULT 0,suggested_sell_shares REAL NOT NULL DEFAULT 0,
 stop_updated_at TEXT,stop_update_reason TEXT,last_evaluated_at TEXT NOT NULL,data_freshness TEXT,
 rule_version TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_position_defense_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,position_id TEXT NOT NULL,observed_at TEXT NOT NULL,
 old_stop_price REAL,new_stop_price REAL,change_reason TEXT,price REAL,highest_price REAL,r_multiple REAL,
 evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_v8_defense_history_position ON v8_position_defense_history(position_id,observed_at);
CREATE TABLE IF NOT EXISTS v8_event_radar(
 event_id TEXT PRIMARY KEY,source_api TEXT NOT NULL,published_at TEXT,title TEXT NOT NULL,code TEXT,name TEXT,
 topic TEXT,direction TEXT NOT NULL,risk_level TEXT NOT NULL,alert_type TEXT NOT NULL,
 primary_category TEXT,event_tags_json TEXT,related_entities_json TEXT,issuer TEXT,issuer_level TEXT,
 policy_stage TEXT,topic_primary TEXT,topic_secondary TEXT,fetched_at TEXT,raw_body TEXT,
 details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_v8_event_time ON v8_event_radar(published_at,code);
CREATE TABLE IF NOT EXISTS v8_module_health(
 module TEXT PRIMARY KEY,status TEXT NOT NULL,checked_at TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_company_profiles(
 code TEXT PRIMARY KEY,name TEXT,industry TEXT,main_business TEXT,business_scope TEXT,
 updated_date TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_event_hypotheses(
 event_id TEXT PRIMARY KEY,topic TEXT,causal_chain_json TEXT NOT NULL,demand_json TEXT NOT NULL,
 beneficiaries_json TEXT NOT NULL,confidence REAL NOT NULL,impact_horizon TEXT,realization_status TEXT NOT NULL,
 action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS v8_event_forward(
 event_id TEXT NOT NULL,code TEXT NOT NULL,name TEXT,signal_time TEXT NOT NULL,entry_price REAL,
 horizon TEXT NOT NULL,matured INTEGER NOT NULL DEFAULT 0,return_pct REAL,mfe_pct REAL,mae_pct REAL,
 entry_time TEXT,exit_time TEXT,exit_price REAL,cost_bps REAL,net_return_pct REAL,status TEXT NOT NULL DEFAULT 'NOT_MATURED',
 details_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(event_id,code,horizon));
CREATE TABLE IF NOT EXISTS v8_event_evidence(
 event_id TEXT PRIMARY KEY,cluster_key TEXT NOT NULL,source_api TEXT NOT NULL,published_at TEXT,
 observed_at TEXT NOT NULL,title TEXT,cues_json TEXT NOT NULL DEFAULT '[]',direction TEXT,
 details_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_v8_event_evidence_cluster ON v8_event_evidence(cluster_key,observed_at);
CREATE TABLE IF NOT EXISTS v8_event_clusters(
 cluster_key TEXT PRIMARY KEY,topic TEXT,state TEXT NOT NULL,probability REAL NOT NULL,
 source_count INTEGER NOT NULL,evidence_count INTEGER NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
 support_json TEXT NOT NULL DEFAULT '[]',contrary_json TEXT NOT NULL DEFAULT '[]',
 next_checks_json TEXT NOT NULL DEFAULT '[]',details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_event_governance_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT,checked_at TEXT NOT NULL,decision TEXT NOT NULL,reason TEXT,
 config_version INTEGER NOT NULL,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_event_market_shadow(
 event_id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,event_state TEXT NOT NULL,sample_count INTEGER,known_count INTEGER,
 up_ratio_pct REAL,leader_pct REAL,volume_ratio REAL,vwap_support INTEGER,expectation_status TEXT,
 details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_sector_intraday_snapshots(
 observed_at TEXT NOT NULL,trade_date TEXT NOT NULL,minute_key TEXT NOT NULL,industry TEXT NOT NULL,amount REAL,
 up_ratio REAL,median_pct REAL,top3_pct REAL,stock_count INTEGER,above_vwap_ratio REAL,
 details_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(trade_date,minute_key,industry));
CREATE INDEX IF NOT EXISTS idx_v8_sector_snapshot_industry ON v8_sector_intraday_snapshots(industry,trade_date,minute_key);
CREATE TABLE IF NOT EXISTS v8_discovery_runs(
 run_id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,source TEXT NOT NULL,universe_count INTEGER NOT NULL,
 eligible_count INTEGER NOT NULL,candidate_count INTEGER NOT NULL,market_score REAL,market_regime TEXT,
 source_time TEXT,status TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS v8_discovery_candidates(
 event_key TEXT PRIMARY KEY,trade_date TEXT NOT NULL,code TEXT NOT NULL,name TEXT,industry TEXT,channel TEXT NOT NULL,
 first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,source_time TEXT,price REAL,pct_chg REAL,amount REAL,
 discovery_score REAL,opportunity_score REAL,action TEXT NOT NULL DEFAULT 'WATCH',checkpoint_count INTEGER NOT NULL DEFAULT 0,
 pass_count INTEGER NOT NULL DEFAULT 0,last_push_state TEXT,details_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_v8_discovery_date ON v8_discovery_candidates(trade_date,discovery_score DESC);
CREATE TABLE IF NOT EXISTS v8_candidate_recheck_queue(
 event_key TEXT NOT NULL,checkpoint_minute INTEGER NOT NULL,due_at TEXT NOT NULL,
 priority INTEGER NOT NULL DEFAULT 50,status TEXT NOT NULL DEFAULT 'PENDING',attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,allow_initial_push INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(event_key,checkpoint_minute));
CREATE INDEX IF NOT EXISTS idx_v8_recheck_due ON v8_candidate_recheck_queue(status,due_at,priority DESC);
CREATE TABLE IF NOT EXISTS v8_auction_assessments(
 trade_date TEXT NOT NULL,code TEXT NOT NULL,name TEXT,assessed_at TEXT NOT NULL,status TEXT NOT NULL,
 quality_score REAL,gap_pct REAL,final_price REAL,final_amount REAL,amount_ratio_pct REAL,
 peak_fade_pct REAL,last_minute_change_pct REAL,tick_count INTEGER,risk_level TEXT,
 reasons_json TEXT NOT NULL DEFAULT '[]',details_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(trade_date,code));
CREATE TABLE IF NOT EXISTS v8_daily_bars(
 ts_code TEXT NOT NULL,trade_date TEXT NOT NULL,open REAL,high REAL,low REAL,close REAL,pre_close REAL,
 pct_chg REAL,vol_raw REAL,amount_yuan REAL,fetched_at TEXT NOT NULL,source TEXT NOT NULL,
 PRIMARY KEY(ts_code,trade_date));
CREATE INDEX IF NOT EXISTS idx_v8_daily_bars_date ON v8_daily_bars(trade_date,ts_code);
CREATE TABLE IF NOT EXISTS v8_minute_bars(
 ts_code TEXT NOT NULL,bar_time TEXT NOT NULL,freq TEXT NOT NULL,open REAL,high REAL,low REAL,close REAL,
 vol_raw REAL,amount_yuan REAL,fetched_at TEXT NOT NULL,source TEXT NOT NULL,
 PRIMARY KEY(ts_code,bar_time,freq));
CREATE INDEX IF NOT EXISTS idx_v8_minute_bars_time ON v8_minute_bars(bar_time,ts_code);
CREATE TABLE IF NOT EXISTS v8_market_data_provenance(
 dataset TEXT NOT NULL,ts_code TEXT NOT NULL,observed_key TEXT NOT NULL,freq TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL,source_time TEXT,fetched_at TEXT NOT NULL,volume_unit TEXT NOT NULL,
 amount_unit TEXT NOT NULL,adjustment TEXT NOT NULL,fallback_rank INTEGER NOT NULL DEFAULT 0,
 quality_status TEXT NOT NULL,payload_hash TEXT NOT NULL,warnings_json TEXT NOT NULL DEFAULT '[]',
 details_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(dataset,ts_code,observed_key,freq));
CREATE INDEX IF NOT EXISTS idx_v8_provenance_source
 ON v8_market_data_provenance(source,fetched_at,quality_status);
CREATE TABLE IF NOT EXISTS v8_research_validation_runs(
 as_of_date TEXT NOT NULL,group_key TEXT NOT NULL,rule_version TEXT NOT NULL,
 created_at TEXT NOT NULL,sample_n INTEGER NOT NULL,metrics_json TEXT NOT NULL,
 promotion_json TEXT NOT NULL,action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',
 PRIMARY KEY(as_of_date,group_key,rule_version));
CREATE INDEX IF NOT EXISTS idx_v8_research_validation_group
 ON v8_research_validation_runs(group_key,as_of_date);
CREATE TABLE IF NOT EXISTS v8_ai_analyst_reports(
 report_id TEXT PRIMARY KEY,report_type TEXT NOT NULL,subject_key TEXT NOT NULL,
 observed_at TEXT NOT NULL,evidence_cutoff TEXT NOT NULL,analyst_mode TEXT NOT NULL,
 confidence REAL NOT NULL,conclusion TEXT NOT NULL,report_json TEXT NOT NULL,
 action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',push_status TEXT NOT NULL DEFAULT 'NOT_REQUESTED');
CREATE INDEX IF NOT EXISTS idx_v8_ai_analyst_lookup
 ON v8_ai_analyst_reports(report_type,observed_at,subject_key);
CREATE TABLE IF NOT EXISTS v8_probability_shadow(
 event_key TEXT NOT NULL,observed_at TEXT NOT NULL,code TEXT NOT NULL,horizon TEXT NOT NULL,
 model_version TEXT NOT NULL,status TEXT NOT NULL,sample_n INTEGER NOT NULL,probability_pct REAL,
 interval_low_pct REAL,interval_high_pct REAL,expected_net_return_pct REAL,profit_factor REAL,
 confidence_tier TEXT,action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',
 details_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(event_key,horizon));
CREATE INDEX IF NOT EXISTS idx_v8_probability_lookup
 ON v8_probability_shadow(horizon,status,observed_at,code);
CREATE TABLE IF NOT EXISTS v8_candidate_risk_shadow(
 event_key TEXT NOT NULL,risk_type TEXT NOT NULL,observed_at TEXT NOT NULL,code TEXT NOT NULL,
 status TEXT NOT NULL,risk_level TEXT,action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',
 details_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(event_key,risk_type));
CREATE TABLE IF NOT EXISTS v8_factor_research_runs(
 as_of_date TEXT NOT NULL,factor_name TEXT NOT NULL,horizon TEXT NOT NULL,research_version TEXT NOT NULL,
 sample_n INTEGER NOT NULL,mean_ic REAL,icir REAL,quantile_monotonic INTEGER NOT NULL DEFAULT 0,
 action_permission TEXT NOT NULL DEFAULT 'ANNOTATION_ONLY',details_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(as_of_date,factor_name,horizon,research_version));
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db=Path(path or CONFIG.db_path); db.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(db); c.row_factory=sqlite3.Row; return c


def initialize(path: Path | None = None) -> Path:
    c=connect(path); c.executescript(SCHEMA)
    columns={str(row[1]) for row in c.execute("pragma table_info(v8_event_radar)").fetchall()}
    migrations={"primary_category":"TEXT","event_tags_json":"TEXT","related_entities_json":"TEXT",
      "issuer":"TEXT","issuer_level":"TEXT","policy_stage":"TEXT","topic_primary":"TEXT",
      "topic_secondary":"TEXT","fetched_at":"TEXT","raw_body":"TEXT"}
    for name,sql_type in migrations.items():
        if name not in columns:c.execute(f"alter table v8_event_radar add column {name} {sql_type}")
    event_forward_columns={str(row[1]) for row in c.execute("pragma table_info(v8_event_forward)").fetchall()}
    for name,sql_type in {"entry_time":"TEXT","exit_time":"TEXT","exit_price":"REAL","cost_bps":"REAL",
                          "net_return_pct":"REAL","status":"TEXT NOT NULL DEFAULT 'NOT_MATURED'"}.items():
        if name not in event_forward_columns:c.execute(f"alter table v8_event_forward add column {name} {sql_type}")
    budget_columns={str(row[1]) for row in c.execute("pragma table_info(v8_api_budget)").fetchall()}
    for name,sql_type in {"api":"TEXT NOT NULL DEFAULT 'UNKNOWN'",
                          "priority":"TEXT NOT NULL DEFAULT 'NORMAL'"}.items():
        if name not in budget_columns:c.execute(f"alter table v8_api_budget add column {name} {sql_type}")
    discovery_columns={str(row[1]) for row in c.execute("pragma table_info(v8_discovery_candidates)").fetchall()}
    for name,sql_type in {"first_seen_price":"REAL","first_seen_pct_chg":"REAL",
                          "latest_live_pct_chg":"REAL","first_watch_at":"TEXT",
                          "first_watch_price":"REAL","first_watch_pushed_at":"TEXT",
                          "signal_family":"TEXT","confirmed_at":"TEXT",
                          "confirmed_price":"REAL"}.items():
        if name not in discovery_columns:c.execute(f"alter table v8_discovery_candidates add column {name} {sql_type}")
    c.commit(); c.close(); return Path(path or CONFIG.db_path)


def save_decision(event: Mapping[str,Any], decision: Mapping[str,Any], path: Path | None=None) -> None:
    initialize(path); c=connect(path)
    c.execute("""INSERT INTO v8_signal_decisions
      (event_key,strategy_version,signal_time,code,name,source,action,opportunity_score,market_score,market_regime,
       sector_score,trend_quality_score,fund_quality_score,signal_persistence_score,risk_score,suggested_position_pct,
       executable_price,decision_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(event_key) DO UPDATE SET action=excluded.action,opportunity_score=excluded.opportunity_score,
       market_score=excluded.market_score,market_regime=excluded.market_regime,sector_score=excluded.sector_score,
       trend_quality_score=excluded.trend_quality_score,fund_quality_score=excluded.fund_quality_score,
       signal_persistence_score=excluded.signal_persistence_score,risk_score=excluded.risk_score,
       suggested_position_pct=excluded.suggested_position_pct,executable_price=excluded.executable_price,
       decision_json=excluded.decision_json""",(
      event['event_key'],event.get('strategy_version','V8.0-shadow-1'),event['signal_time'],event['code'],event.get('name'),
      event.get('source'),decision['action'],decision['opportunity_score'],decision['market']['score'],decision['market']['label'],
      decision['sector']['score'],decision['trend']['score'],decision['fund']['score'],decision['persistence']['score'],
      decision['risk_score'],decision['position_pct'],event.get('executable_price'),json.dumps(decision,ensure_ascii=False,default=str)))
    evaluation_time=str(event.get('evaluation_time') or event['signal_time'])
    c.execute("""INSERT OR REPLACE INTO v8_decision_history(event_key,evaluation_time,action,decision_json,evidence_cutoff)
      VALUES(?,?,?,?,?)""",(event['event_key'],evaluation_time,decision['action'],json.dumps(decision,ensure_ascii=False,default=str),evaluation_time))
    c.commit(); c.close()


def save_observation(event_key:str,checkpoint_minute:int,observed_at:str,evidence:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path); c=connect(path)
    c.execute("""INSERT INTO v8_persistence_observations
      (event_key,checkpoint_minute,observed_at,price,vwap,order_imbalance,amount_delta,sector_score,blowoff_reversal,evidence_json)
      VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,checkpoint_minute) DO UPDATE SET
      observed_at=excluded.observed_at,price=excluded.price,vwap=excluded.vwap,order_imbalance=excluded.order_imbalance,
      amount_delta=excluded.amount_delta,sector_score=excluded.sector_score,blowoff_reversal=excluded.blowoff_reversal,
      evidence_json=excluded.evidence_json""",(event_key,checkpoint_minute,observed_at,evidence.get('price'),evidence.get('vwap'),
      evidence.get('order_imbalance'),evidence.get('amount_delta'),evidence.get('sector_score'),int(bool(evidence.get('blowoff_reversal'))),
      json.dumps(dict(evidence),ensure_ascii=False,default=str)))
    c.commit(); c.close()


def save_strategy_factor_shadow(event:Mapping[str,Any],factor_name:str,checkpoint_minute:int,
                                features:Mapping[str,Any],path:Path|None=None)->None:
    """Persist research evidence without granting any trading permission."""
    initialize(path);c=connect(path)
    observed_at=str(event.get('evaluation_time') or event.get('signal_time'))
    c.execute("""INSERT INTO v8_strategy_factor_shadow(event_key,factor_name,checkpoint_minute,observed_at,
      code,name,industry,status,score,factor_version,action_permission,feature_json)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,factor_name,checkpoint_minute) DO UPDATE SET
      observed_at=excluded.observed_at,status=excluded.status,score=excluded.score,
      factor_version=excluded.factor_version,action_permission='ANNOTATION_ONLY',feature_json=excluded.feature_json""",(
      event['event_key'],factor_name,int(checkpoint_minute),observed_at,event['code'],event.get('name'),
      event.get('industry'),features.get('status','UNKNOWN'),features.get('score'),
      features.get('factor_version','V8_STRATEGY_SHADOW'),'ANNOTATION_ONLY',
      json.dumps(dict(features),ensure_ascii=False,default=str)))
    c.commit();c.close()


def save_counterfactual(event_key:str,group_name:str,decision:str,reason:str,features:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path); c=connect(path)
    c.execute("""INSERT INTO v8_counterfactuals(event_key,group_name,decision,reason,feature_json)
      VALUES(?,?,?,?,?) ON CONFLICT(event_key,group_name) DO UPDATE SET decision=excluded.decision,reason=excluded.reason,
      feature_json=excluded.feature_json""",(event_key,group_name,decision,reason,json.dumps(dict(features),ensure_ascii=False,default=str)))
    c.commit(); c.close()


def save_execution_outcome(event_key:str,track:str,horizon:str,values:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path); c=connect(path)
    c.execute("""INSERT INTO v8_execution_outcomes(event_key,track,horizon,matured,entry_price,exit_time,exit_price,exit_reason,
      gross_return_pct,cost_bps,net_return_pct,mfe_pct,mae_pct,max_drawdown_pct,holding_trade_days,details_json)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,track,horizon) DO UPDATE SET
      matured=excluded.matured,exit_time=excluded.exit_time,exit_price=excluded.exit_price,exit_reason=excluded.exit_reason,
      gross_return_pct=excluded.gross_return_pct,cost_bps=excluded.cost_bps,net_return_pct=excluded.net_return_pct,
      mfe_pct=excluded.mfe_pct,mae_pct=excluded.mae_pct,max_drawdown_pct=excluded.max_drawdown_pct,
      holding_trade_days=excluded.holding_trade_days,details_json=excluded.details_json""",(event_key,track,horizon,
      int(bool(values.get('matured'))),values.get('entry_price'),values.get('exit_time'),values.get('exit_price'),values.get('exit_reason'),
      values.get('gross_return_pct'),values.get('cost_bps'),values.get('net_return_pct'),values.get('mfe_pct'),values.get('mae_pct'),
      values.get('max_drawdown_pct'),values.get('holding_trade_days'),json.dumps(dict(values),ensure_ascii=False,default=str)))
    c.commit(); c.close()


def save_signal_delivery(event_key:str,state:str,evaluated_at:str,first_seen_price:float|None,
                         evaluated_price:float|None,push_status:str,pushed_at:str|None,
                         details:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path);c=connect(path)
    move=None
    if first_seen_price and evaluated_price:
        move=(float(evaluated_price)/float(first_seen_price)-1)*100
    c.execute("""INSERT INTO v8_signal_delivery(event_key,state,evaluated_at,first_seen_price,evaluated_price,
      price_move_from_discovery_pct,push_status,pushed_at,details_json) VALUES(?,?,?,?,?,?,?,?,?)
      ON CONFLICT(event_key,state) DO UPDATE SET evaluated_at=excluded.evaluated_at,
      evaluated_price=excluded.evaluated_price,price_move_from_discovery_pct=excluded.price_move_from_discovery_pct,
      push_status=excluded.push_status,pushed_at=excluded.pushed_at,details_json=excluded.details_json""",
      (event_key,state,evaluated_at,first_seen_price,evaluated_price,move,push_status,pushed_at,
       json.dumps(dict(details),ensure_ascii=False,default=str)))
    c.commit();c.close()


def save_portfolio_risk(observed_at:str,total_exposure_pct:float,sector_exposure:Mapping[str,Any],daily_pnl_pct:float|None,
                        portfolio_drawdown_pct:float|None,market_regime:str,risk_action:str,details:Mapping[str,Any],
                        *,new_positions_today:int=0,path:Path|None=None)->None:
    initialize(path); c=connect(path)
    c.execute("""INSERT INTO v8_portfolio_risk_snapshots(observed_at,total_exposure_pct,sector_exposure_json,daily_pnl_pct,
      portfolio_drawdown_pct,market_regime,new_positions_today,risk_action,details_json) VALUES(?,?,?,?,?,?,?,?,?)""",
      (observed_at,total_exposure_pct,json.dumps(dict(sector_exposure),ensure_ascii=False),daily_pnl_pct,
       portfolio_drawdown_pct,market_regime,new_positions_today,risk_action,json.dumps(dict(details),ensure_ascii=False,default=str)))
    c.commit(); c.close()


def save_early_watch(event:Mapping[str,Any],annotation:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path);c=connect(path)
    c.execute("""INSERT INTO v8_early_watch(event_key,observed_at,code,name,watch_type,watch_score,reason,feature_json,action_permission)
      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET observed_at=excluded.observed_at,
      watch_type=excluded.watch_type,watch_score=excluded.watch_score,reason=excluded.reason,
      feature_json=excluded.feature_json,action_permission=excluded.action_permission""",(
      event['event_key'],str(event.get('evaluation_time') or event.get('signal_time')),event['code'],event.get('name'),
      annotation['watch_type'],annotation['watch_score'],annotation.get('reason'),
      json.dumps(dict(annotation),ensure_ascii=False,default=str),annotation.get('action_permission','ANNOTATION_ONLY')))
    c.commit();c.close()


def save_miss_audit(row:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path);c=connect(path)
    c.execute("""INSERT INTO v8_miss_audit(trade_date,code,name,actual_return_pct,max_return_pct,discovery_status,
      decision_action,miss_reason,details_json) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,code) DO UPDATE SET
      name=excluded.name,actual_return_pct=excluded.actual_return_pct,max_return_pct=excluded.max_return_pct,
      discovery_status=excluded.discovery_status,decision_action=excluded.decision_action,
      miss_reason=excluded.miss_reason,details_json=excluded.details_json""",(
      row['trade_date'],row['code'],row.get('name'),row.get('actual_return_pct'),row.get('max_return_pct'),
      row['discovery_status'],row.get('decision_action'),row.get('miss_reason'),
      json.dumps(dict(row),ensure_ascii=False,default=str)))
    c.commit();c.close()


def get_position_state(position_id:str,path:Path|None=None)->dict[str,Any]|None:
    initialize(path);c=connect(path)
    row=c.execute("select * from v8_position_state where position_id=?",(position_id,)).fetchone();c.close()
    return dict(row) if row else None


def transition_position_state(position_id:str,state:str,observed_at:str,details:Mapping[str,Any],
                              path:Path|None=None)->tuple[bool,str|None]:
    initialize(path);c=connect(path)
    row=c.execute("select current_state from v8_position_state where position_id=?",(position_id,)).fetchone()
    previous=str(row[0]) if row else None;changed=previous!=state
    if row:
        c.execute("""update v8_position_state set current_state=?,entered_at=case when current_state<>? then ? else entered_at end,
          last_seen_at=?,details_json=? where position_id=?""",
          (state,state,observed_at,observed_at,json.dumps(dict(details),ensure_ascii=False,default=str),position_id))
    else:
        c.execute("insert into v8_position_state values(?,?,?,?,?)",
          (position_id,state,observed_at,observed_at,json.dumps(dict(details),ensure_ascii=False,default=str)))
    c.commit();c.close();return changed,previous


def get_position_defense(position_id:str,path:Path|None=None)->dict[str,Any]|None:
    initialize(path);c=connect(path)
    row=c.execute("select * from v8_position_defense where position_id=?",(position_id,)).fetchone();c.close()
    return dict(row) if row else None


def atomic_update_position_defense(position_id:str,observed_at:str,values:Mapping[str,Any],
                                   path:Path|None=None)->dict[str,Any]:
    """Monotonic SQLite update: peak and trailing stop can never move backwards."""
    initialize(path);c=connect(path)
    c.execute("BEGIN IMMEDIATE")
    old=c.execute("select * from v8_position_defense where position_id=?",(position_id,)).fetchone()
    old_d=dict(old) if old else {}
    old_stop=old_d.get("current_trailing_stop")
    proposed=values.get("current_trailing_stop")
    valid_stops=[float(x) for x in (old_stop,proposed) if x is not None]
    final_stop=max(valid_stops) if valid_stops else None
    old_peak=old_d.get("highest_price_since_entry")
    proposed_peak=values.get("highest_price_since_entry")
    peaks=[float(x) for x in (old_peak,proposed_peak) if x is not None]
    final_peak=max(peaks) if peaks else None
    old_close=old_d.get("highest_close_since_entry")
    proposed_close=values.get("highest_close_since_entry")
    closes=[float(x) for x in (old_close,proposed_close) if x is not None]
    final_close=max(closes) if closes else None
    initial=old_d.get("initial_stop_price") if old_d.get("initial_stop_price") is not None else values.get("initial_stop_price")
    changed=old_stop is None or (final_stop is not None and float(final_stop)>float(old_stop)+1e-9)
    payload=(position_id,initial,final_stop,old_stop,final_peak,final_close,values.get("current_r_multiple"),
      values.get("profit_stage"),values.get("first_break_time"),int(values.get("break_confirmation_count") or 0),
      values.get("last_break_bar_time"),values.get("current_action"),int(values.get("action_priority") or 0),
      float(values.get("suggested_sell_shares") or 0),observed_at if changed else old_d.get("stop_updated_at"),
      values.get("stop_update_reason") if changed else old_d.get("stop_update_reason"),observed_at,
      values.get("data_freshness"),values.get("rule_version") or "V8_DEFENSE_1",
      json.dumps(dict(values),ensure_ascii=False,default=str))
    c.execute("""INSERT INTO v8_position_defense(position_id,initial_stop_price,current_trailing_stop,
      previous_trailing_stop,highest_price_since_entry,highest_close_since_entry,current_r_multiple,profit_stage,
      first_break_time,break_confirmation_count,last_break_bar_time,current_action,action_priority,
      suggested_sell_shares,stop_updated_at,stop_update_reason,last_evaluated_at,data_freshness,rule_version,details_json)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET
      initial_stop_price=excluded.initial_stop_price,current_trailing_stop=excluded.current_trailing_stop,
      previous_trailing_stop=excluded.previous_trailing_stop,highest_price_since_entry=excluded.highest_price_since_entry,
      highest_close_since_entry=excluded.highest_close_since_entry,current_r_multiple=excluded.current_r_multiple,
      profit_stage=excluded.profit_stage,first_break_time=excluded.first_break_time,
      break_confirmation_count=excluded.break_confirmation_count,last_break_bar_time=excluded.last_break_bar_time,
      current_action=excluded.current_action,action_priority=excluded.action_priority,
      suggested_sell_shares=excluded.suggested_sell_shares,stop_updated_at=excluded.stop_updated_at,
      stop_update_reason=excluded.stop_update_reason,last_evaluated_at=excluded.last_evaluated_at,
      data_freshness=excluded.data_freshness,rule_version=excluded.rule_version,details_json=excluded.details_json""",payload)
    if changed:
        c.execute("""insert into v8_position_defense_history(position_id,observed_at,old_stop_price,new_stop_price,
          change_reason,price,highest_price,r_multiple,evidence_json) values(?,?,?,?,?,?,?,?,?)""",(
          position_id,observed_at,old_stop,final_stop,values.get("stop_update_reason"),values.get("price"),final_peak,
          values.get("current_r_multiple"),json.dumps(dict(values),ensure_ascii=False,default=str)))
    c.commit()
    row=dict(c.execute("select * from v8_position_defense where position_id=?",(position_id,)).fetchone());c.close()
    row["stop_changed"]=changed
    return row


def save_event_radar(row:Mapping[str,Any],path:Path|None=None)->bool:
    initialize(path);c=connect(path)
    cur=c.execute("""insert or ignore into v8_event_radar(event_id,source_api,published_at,title,code,name,topic,
      direction,risk_level,alert_type,primary_category,event_tags_json,related_entities_json,issuer,issuer_level,
      policy_stage,topic_primary,topic_secondary,fetched_at,raw_body,details_json)
      values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
      row['event_id'],row['source_api'],row.get('published_at'),row['title'],row.get('code'),row.get('name'),
      row.get('topic'),row['direction'],row['risk_level'],row['alert_type'],row.get('primary_category'),
      json.dumps(row.get('event_tags') or [],ensure_ascii=False),
      json.dumps(row.get('related_entities') or {},ensure_ascii=False,default=str),row.get('issuer'),row.get('issuer_level'),
      row.get('policy_stage'),row.get('topic_primary'),row.get('topic_secondary'),row.get('fetched_at'),row.get('raw_body'),
      json.dumps(dict(row),ensure_ascii=False,default=str)))
    created=cur.rowcount==1;c.commit();c.close();return created


def save_module_health(module:str,status:str,checked_at:str,details:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path);c=connect(path)
    c.execute("""insert into v8_module_health(module,status,checked_at,details_json) values(?,?,?,?)
      on conflict(module) do update set status=excluded.status,checked_at=excluded.checked_at,
      details_json=excluded.details_json""",(module,status,checked_at,json.dumps(dict(details),ensure_ascii=False,default=str)))
    c.commit();c.close()


def save_research_validation(as_of_date:str,group_key:str,rule_version:str,created_at:str,
                             metrics:Mapping[str,Any],promotion:Mapping[str,Any],
                             path:Path|None=None)->None:
    """Persist research evidence without granting any scoring permission."""
    initialize(path);c=connect(path)
    action_permission=str(promotion.get('action_permission') or 'ANNOTATION_ONLY')
    if action_permission not in {'ANNOTATION_ONLY','REVIEW_ONLY'}:
        action_permission='ANNOTATION_ONLY'
    c.execute("""insert into v8_research_validation_runs(as_of_date,group_key,rule_version,
      created_at,sample_n,metrics_json,promotion_json,action_permission) values(?,?,?,?,?,?,?,?)
      on conflict(as_of_date,group_key,rule_version) do update set created_at=excluded.created_at,
      sample_n=excluded.sample_n,metrics_json=excluded.metrics_json,
      promotion_json=excluded.promotion_json,action_permission=excluded.action_permission""",(
      as_of_date,group_key,rule_version,created_at,int(metrics.get('mature_n') or 0),
      json.dumps(dict(metrics),ensure_ascii=False,default=str),
      json.dumps(dict(promotion),ensure_ascii=False,default=str),action_permission))
    c.commit();c.close()


def save_ai_analyst_report(report:Mapping[str,Any],path:Path|None=None)->None:
    """Persist analyst evidence while enforcing the permanent Shadow boundary."""
    initialize(path);c=connect(path)
    permission=str(report.get('action_permission') or 'ANNOTATION_ONLY')
    if permission!='ANNOTATION_ONLY':permission='ANNOTATION_ONLY'
    c.execute("""insert into v8_ai_analyst_reports(report_id,report_type,subject_key,
      observed_at,evidence_cutoff,analyst_mode,confidence,conclusion,report_json,
      action_permission,push_status) values(?,?,?,?,?,?,?,?,?,?,?)
      on conflict(report_id) do update set confidence=excluded.confidence,
      conclusion=excluded.conclusion,report_json=excluded.report_json,
      action_permission='ANNOTATION_ONLY',push_status=excluded.push_status""",(
      report['report_id'],report['report_type'],report['subject_key'],report['observed_at'],
      report['evidence_cutoff'],report.get('analyst_mode','LOCAL_EVIDENCE_SHADOW'),
      float(report.get('confidence') or 0),str(report.get('conclusion') or ''),
      json.dumps(dict(report),ensure_ascii=False,default=str),permission,
      str(report.get('push_status') or 'NOT_REQUESTED')))
    c.commit();c.close()


def save_probability_shadow(event:Mapping[str,Any],result:Mapping[str,Any],path:Path|None=None)->None:
    """Persist probability evidence without granting entry permission."""
    initialize(path);c=connect(path)
    c.execute("""insert into v8_probability_shadow(event_key,observed_at,code,horizon,
      model_version,status,sample_n,probability_pct,interval_low_pct,interval_high_pct,
      expected_net_return_pct,profit_factor,confidence_tier,action_permission,details_json)
      values(?,?,?,?,?,?,?,?,?,?,?,?,?,'ANNOTATION_ONLY',?)
      on conflict(event_key,horizon) do update set observed_at=excluded.observed_at,
      model_version=excluded.model_version,status=excluded.status,sample_n=excluded.sample_n,
      probability_pct=excluded.probability_pct,interval_low_pct=excluded.interval_low_pct,
      interval_high_pct=excluded.interval_high_pct,expected_net_return_pct=excluded.expected_net_return_pct,
      profit_factor=excluded.profit_factor,confidence_tier=excluded.confidence_tier,
      action_permission='ANNOTATION_ONLY',details_json=excluded.details_json""",(
      event['event_key'],str(event.get('evaluation_time') or event.get('signal_time')),event['code'],
      str(result.get('horizon') or 'D3'),str(result.get('model_version') or 'V8_PREDICTIVE_SHADOW'),
      str(result.get('status') or 'UNKNOWN'),int(result.get('sample_n') or 0),result.get('probability_pct'),
      result.get('interval_low_pct'),result.get('interval_high_pct'),result.get('expected_net_return_pct'),
      result.get('profit_factor'),result.get('confidence_tier'),
      json.dumps(dict(result),ensure_ascii=False,default=str)))
    c.commit();c.close()


def save_candidate_risk_shadow(event:Mapping[str,Any],risk_type:str,result:Mapping[str,Any],
                               path:Path|None=None)->None:
    initialize(path);c=connect(path)
    c.execute("""insert into v8_candidate_risk_shadow(event_key,risk_type,observed_at,code,status,
      risk_level,action_permission,details_json) values(?,?,?,?,?,?,'ANNOTATION_ONLY',?)
      on conflict(event_key,risk_type) do update set observed_at=excluded.observed_at,
      status=excluded.status,risk_level=excluded.risk_level,action_permission='ANNOTATION_ONLY',
      details_json=excluded.details_json""",(
      event['event_key'],str(risk_type),str(event.get('evaluation_time') or event.get('signal_time')),
      event['code'],str(result.get('status') or 'UNKNOWN'),str(result.get('risk') or ''),
      json.dumps(dict(result),ensure_ascii=False,default=str)))
    c.commit();c.close()


def save_factor_research(as_of_date:str,factor_name:str,horizon:str,result:Mapping[str,Any],
                         path:Path|None=None)->None:
    initialize(path);c=connect(path)
    c.execute("""insert into v8_factor_research_runs(as_of_date,factor_name,horizon,research_version,
      sample_n,mean_ic,icir,quantile_monotonic,action_permission,details_json)
      values(?,?,?,?,?,?,?,?,'ANNOTATION_ONLY',?)
      on conflict(as_of_date,factor_name,horizon,research_version) do update set
      sample_n=excluded.sample_n,mean_ic=excluded.mean_ic,icir=excluded.icir,
      quantile_monotonic=excluded.quantile_monotonic,action_permission='ANNOTATION_ONLY',
      details_json=excluded.details_json""",(
      as_of_date,factor_name,horizon,str(result.get('research_version') or 'V8_FACTOR_LAB'),
      int(result.get('sample_n') or 0),result.get('mean_ic'),result.get('icir'),
      int(bool(result.get('quantile_monotonic'))),json.dumps(dict(result),ensure_ascii=False,default=str)))
    c.commit();c.close()


def save_event_hypothesis(row:Mapping[str,Any],path:Path|None=None)->None:
    initialize(path);c=connect(path)
    c.execute("""insert into v8_event_hypotheses(event_id,topic,causal_chain_json,demand_json,beneficiaries_json,
      confidence,impact_horizon,realization_status,action_permission) values(?,?,?,?,?,?,?,?,?)
      on conflict(event_id) do update set topic=excluded.topic,causal_chain_json=excluded.causal_chain_json,
      demand_json=excluded.demand_json,beneficiaries_json=excluded.beneficiaries_json,confidence=excluded.confidence,
      impact_horizon=excluded.impact_horizon,realization_status=excluded.realization_status""",(
      row['event_id'],row.get('topic'),json.dumps(row.get('causal_chain') or [],ensure_ascii=False),
      json.dumps(row.get('demand') or [],ensure_ascii=False),json.dumps(row.get('beneficiaries') or [],ensure_ascii=False),
      row.get('confidence',0),row.get('impact_horizon'),row.get('realization_status','UNKNOWN'),
      row.get('action_permission','ANNOTATION_ONLY')))
    c.commit();c.close()


def register_event_forward(event_id:str,code:str,name:str,signal_time:str,entry_price:float|None,
                           path:Path|None=None)->None:
    initialize(path);c=connect(path)
    executable=entry_price;status='NOT_MATURED';entry_time=signal_time
    try:
        stamp=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).astimezone();minute=stamp.hour*60+stamp.minute
        if stamp.weekday()>=5 or not (565<=minute<=690 or 780<=minute<=900):
            executable=None;entry_time=None;status='PENDING_EXECUTABLE'
    except Exception:status='PENDING_EXECUTABLE';executable=None;entry_time=None
    for horizon in ('M3','M10','M30','M60','CLOSE','D1','D3','D5','D10'):
        c.execute("""insert or ignore into v8_event_forward(event_id,code,name,signal_time,entry_price,horizon,entry_time,status)
          values(?,?,?,?,?,?,?,?)""",(event_id,code,name,signal_time,executable,horizon,entry_time,status))
    c.commit();c.close()
