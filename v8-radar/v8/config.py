from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class V8Config:
    enabled: bool = _bool("V8_ENABLE", True)
    shadow_only: bool = _bool("V8_SHADOW_ONLY", True)
    push_enabled: bool = _bool("V8_PUSH_ENABLED", False)
    auto_order_enabled: bool = False  # hard-disabled by design
    db_path: Path = ROOT / os.getenv("V8_DB_PATH", "data_v8/signal_lab_v8.sqlite3")
    webhook_file: Path = ROOT / os.getenv("V8_WEBHOOK_FILE", "wework_webhook_v8_shadow.txt")
    # Six monitored holdings need roughly 5-7 independent enriched calls on the
    # first uncached pass.  Thirty caused deterministic starvation of the final
    # positions, so keep a bounded but usable default.
    max_paid_calls_per_minute: int = max(1, _int("V8_MAX_PAID_CALLS_PER_MINUTE", 60))
    # Keep part of the shared minute budget available for positions and due
    # 3/6/10-minute checks.  Lower-priority news/research jobs may use only the
    # remainder, so a burst cannot starve the trading-time safety path.
    paid_reserve_critical: int = max(0, _int("V8_PAID_RESERVE_CRITICAL", 10))
    paid_reserve_checkpoint: int = max(0, _int("V8_PAID_RESERVE_CHECKPOINT", 10))
    paid_reserve_normal: int = max(0, _int("V8_PAID_RESERVE_NORMAL", 8))
    max_enriched_candidates: int = max(1, _int("V8_MAX_ENRICHED_CANDIDATES", 18))
    holding_scan_seconds: int = max(20, _int("V8_HOLDING_SCAN_SECONDS", 60))
    min_persistence_rounds: int = max(2, _int("V8_MIN_PERSISTENCE_ROUNDS", 2))
    max_new_positions_per_day: int = max(0, _int("V8_MAX_NEW_POSITIONS_PER_DAY", 2))
    max_single_position_pct: float = min(20, max(1, _float("V8_MAX_SINGLE_POSITION_PCT", 10)))
    max_sector_exposure_pct: float = min(50, max(5, _float("V8_MAX_SECTOR_EXPOSURE_PCT", 25)))
    risk_per_trade_pct: float = min(2, max(0.1, _float("V8_RISK_PER_TRADE_PCT", .6)))
    max_total_attack_pct: float = min(100, max(0, _float("V8_MAX_TOTAL_EXPOSURE_ATTACK_PCT", 60)))
    max_total_normal_pct: float = min(100, max(0, _float("V8_MAX_TOTAL_EXPOSURE_NORMAL_PCT", 40)))
    max_total_defense_pct: float = min(100, max(0, _float("V8_MAX_TOTAL_EXPOSURE_DEFENSE_PCT", 15)))
    daily_loss_limit_pct: float = max(0, _float("V8_DAILY_LOSS_LIMIT_PCT", 2))
    portfolio_drawdown_limit_pct: float = max(0, _float("V8_PORTFOLIO_DRAWDOWN_LIMIT_PCT", 8))
    forward_cost_bps: float = max(0, _float("V8_FORWARD_COST_BPS", 20))
    independent_scan_seconds: int = max(60, _int("V8_INDEPENDENT_SCAN_SECONDS", 60))
    independent_watch_top_n: int = max(30, _int("V8_INDEPENDENT_WATCH_TOP_N", 60))
    independent_watch_min_n: int = max(10, _int("V8_INDEPENDENT_WATCH_MIN_N", 30))
    independent_enrich_top_n: int = max(8, _int("V8_INDEPENDENT_ENRICH_TOP_N", 12))
    independent_enrich_min_n: int = max(3, _int("V8_INDEPENDENT_ENRICH_MIN_N", 8))
    paid_target_utilization_pct: float = min(95, max(60, _float("V8_PAID_TARGET_UTILIZATION_PCT", 85)))
    independent_push_top_n: int = max(1, _int("V8_INDEPENDENT_PUSH_TOP_N", 3))
    # Event APIs can return several hundred rows around the open/close.  Keep a
    # bounded, configurable window and process newest rows first.
    event_max_rows_per_source: int = max(300, min(2000, _int("V8_EVENT_MAX_ROWS_PER_SOURCE", 800)))
    event_max_pushes_per_scan: int = max(1, min(20, _int("V8_EVENT_MAX_PUSHES_PER_SCAN", 8)))
    independent_min_amount: float = max(1_000_000, _float("V8_INDEPENDENT_MIN_AMOUNT", 8_000_000))
    candidate_retention_minutes: int = max(15, _int("V8_CANDIDATE_RETENTION_MINUTES", 30))
    recheck_poll_seconds: int = max(5, _int("V8_RECHECK_POLL_SECONDS", 10))
    recheck_max_jobs_per_cycle: int = max(1, _int("V8_RECHECK_MAX_JOBS_PER_CYCLE", 10))
    early_watch_max_pct_chg: float = max(0.5, _float("V8_EARLY_WATCH_MAX_PCT_CHG", 2.5))
    early_watch_min_score: float = min(100, max(0, _float("V8_EARLY_WATCH_MIN_SCORE", 64)))
    # Entry confirmation is deliberately stricter than discovery.  These are
    # configurable guard rails, not claims of profitability; their results are
    # persisted for out-of-sample review before any later tuning.
    entry_min_market_score: float = min(100, max(0, _float("V8_ENTRY_MIN_MARKET_SCORE", 48)))
    entry_min_sector_score: float = min(100, max(0, _float("V8_ENTRY_MIN_SECTOR_SCORE", 62)))
    entry_min_sector_up_ratio: float = min(1, max(0, _float("V8_ENTRY_MIN_SECTOR_UP_RATIO", .52)))
    entry_max_pct_chg: float = max(0, _float("V8_ENTRY_MAX_PCT_CHG", 4.0))
    entry_max_vwap_distance_pct: float = max(.1, _float("V8_ENTRY_MAX_VWAP_DISTANCE_PCT", 1.5))
    entry_pullback_min_drop_pct: float = max(.05, _float("V8_ENTRY_PULLBACK_MIN_DROP_PCT", .35))
    entry_zone_floor_pct: float = max(0, _float("V8_ENTRY_ZONE_FLOOR_PCT", .5))
    # External-strategy ideas are reimplemented as isolated research tags.
    # They may be recorded and settled, but never alter the live entry score.
    strategy_shadow_enabled: bool = _bool("V8_STRATEGY_SHADOW_ENABLED", True)
    # V8.4 predictive research never changes signal quantity or entry
    # permission automatically.  It records calibrated evidence until the
    # out-of-sample promotion gate is satisfied and manually reviewed.
    predictive_shadow_enabled: bool = _bool("V8_PREDICTIVE_SHADOW_ENABLED", True)
    predictive_horizon: str = os.getenv("V8_PREDICTIVE_HORIZON", "D3").strip().upper() or "D3"
    predictive_min_samples: int = max(30, _int("V8_PREDICTIVE_MIN_SAMPLES", 60))
    predictive_neighbor_limit: int = max(60, min(500, _int("V8_PREDICTIVE_NEIGHBOR_LIMIT", 200)))
    portfolio_correlation_lookback: int = max(40, min(250, _int("V8_PORTFOLIO_CORRELATION_LOOKBACK", 60)))
    portfolio_correlation_warning: float = min(.99, max(.5, _float("V8_PORTFOLIO_CORRELATION_WARNING", .85)))
    # AI analyst is an asynchronous evidence-synthesis layer.  It can create
    # Shadow reports and messages, but it cannot modify discovery scores,
    # thresholds, position sizes or orders.
    ai_analyst_enabled: bool = _bool("V8_AI_ANALYST_ENABLED", True)
    ai_analyst_push_enabled: bool = _bool("V8_AI_ANALYST_PUSH_ENABLED", True)
    ai_analyst_candidate_limit: int = max(3, min(20, _int("V8_AI_ANALYST_CANDIDATE_LIMIT", 8)))
    ai_analyst_event_lead_days: int = max(15, min(120, _int("V8_AI_ANALYST_EVENT_LEAD_DAYS", 60)))
    paid_archive_enable: bool = _bool("V8_PAID_ARCHIVE_ENABLE", True)
    paid_archive_db_path: Path = Path(os.getenv(
        "V8_PAID_ARCHIVE_DB_PATH", str(ROOT / "data_v8/paid_archive_v8.sqlite3")
    ))
    paid_archive_worker_seconds: int = max(5, _int("V8_PAID_ARCHIVE_WORKER_SECONDS", 8))
    paid_archive_target_n: int = max(5, min(80, _int("V8_PAID_ARCHIVE_TARGET_N", 30)))
    paid_archive_chip_target_n: int = max(3, min(40, _int("V8_PAID_ARCHIVE_CHIP_TARGET_N", 12)))
    paid_archive_daily_days: int = max(365, _int("V8_PAID_ARCHIVE_DAILY_DAYS", 1825))
    paid_archive_basic_days: int = max(180, _int("V8_PAID_ARCHIVE_BASIC_DAYS", 730))
    paid_archive_moneyflow_days: int = max(90, _int("V8_PAID_ARCHIVE_MONEYFLOW_DAYS", 365))
    paid_archive_min_free_gb: float = max(5, _float("V8_PAID_ARCHIVE_MIN_FREE_GB", 20))
    paid_archive_max_db_gb: float = max(2, _float("V8_PAID_ARCHIVE_MAX_DB_GB", 25))


CONFIG = V8Config()
