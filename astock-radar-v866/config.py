# -*- coding: utf-8 -*-
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA_DIR=ROOT/"data";DATA_DIR.mkdir(exist_ok=True)
DB_FILE=DATA_DIR/"radar_v82.db"
CAPABILITY_FILE=ROOT/"capabilities.json"
WEWORK_WEBHOOK_ENV="WEWORK_WEBHOOK_URL"

A_GATE={"min_score":82,"min_t1_probability":0.72,"min_rr":1.8,"max_daily_pct":8.5,"max_overnight_risk":38}
B_GATE={"min_score":70,"min_t1_probability":0.58}
QUALITY_TARGET={"a_win_rate":0.80,"min_samples":50,"max_drawdown_pct":12,"require_positive_expectancy":True}
PROVIDER_GUARD={"fail_threshold":3,"cooldown_seconds":45}


# ===== V8.3 潜伏启动雷达 =====
LATENT_GATE = {
    "watch": 68.0,          # 进入潜伏池
    "ready": 78.0,          # 接近启动，重点监控
    "start": 86.0,          # 启动候选，不等于买点
    "max_daily_pct": 4.5,   # 潜伏阶段避免追已经大涨
    "max_breakout_gap_pct": 5.0,
    "min_data_quality": 0.75,
}
MISSED_REVIEW = {
    "top_n": 50,
    "ret_5d_threshold": 12.0,
    "ret_10d_threshold": 20.0,
    "lookback_days": 5,
    "min_samples_for_tuning": 30,
}

# ===== V8.4 连板生态 / 晋级模型 =====
LIMITUP_ECOLOGY = {
    "min_board_potential": 72.0,
    "strong_board_potential": 82.0,
    "max_board_for_new_entry": 3,
    "max_broken_rate": 38.0,
    "min_sector_limitups": 2,
    "leader_high_board_risk": 4,
    "leader_extreme_board_risk": 6,
    "min_promotion_rate": 0.35,
    "strong_promotion_rate": 0.55,
}
FIRST_BOARD_TRAINING = {
    "pre_days": 5,
    "future_board_success": 2,
    "min_positive_samples": 30,
    "min_negative_samples": 30,
}

# ===== V8.5 统计学习增强 =====
FACTOR_LAB = {
    "quantiles": 5,
    "corr_threshold": 0.82,
    "min_cross_section": 20,
    "min_dates": 20,
}
ML_RANKER = {
    "min_train_rows": 800,
    "min_train_dates": 40,
    "top_rank_pct": 0.02,
    "resonance_rank_pct": 0.08,
    "random_state": 42,
}
BOOTSTRAP = {
    "iterations": 3000,
    "confidence": 0.95,
    "seed": 42,
}
EXECUTION = {
    "commission_bps": 2.5,
    "stamp_tax_bps_sell": 5.0,
    "slippage_bps": 5.0,
    "max_participation": 0.10,
}
MONTE_CARLO = {
    "paths": 5000,
    "trades_per_path": 100,
    "drawdown_threshold_pct": 20.0,
}
DRIFT = {
    "psi_warn": 0.10,
    "psi_alert": 0.25,
    "performance_window": 50,
}

# ===== V8.6 Precision Engine =====
TRIPLE_BARRIER={"profit_take_pct":4.0,"stop_loss_pct":2.0,"max_holding_bars":3}
META_LABEL={"min_train_rows":600,"min_train_dates":35,"accept_prob":0.78,"a_plus_prob":0.86,"random_state":42}
SELECTIVE={"min_prob":0.78,"a_plus_prob":0.86,"min_conformal_lower":0.80,"max_uncertainty_width":0.18}
CPCV={"n_groups":6,"n_test_groups":2,"embargo_bars":2}
SAMPLE_UNIQUENESS={"use_weights":True,"min_weight":0.20}
BACKTEST_OVERFIT={"min_trials_for_deflation":5,"min_dsr":0.60}
