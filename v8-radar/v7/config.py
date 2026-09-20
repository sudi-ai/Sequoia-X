from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


@dataclass(frozen=True)
class V7Config:
    # Legacy switches retained for backward compatibility.
    shadow_enabled: bool = _bool("V7_SHADOW_ENABLED", _bool("V71_ENABLE", False))
    tushare_enabled: bool = _bool("V7_TUSHARE_ENABLED", True)
    paid_provider_enabled: bool = _bool("V7_PAID_PROVIDER_ENABLED", True)
    api_limit_per_minute: int = max(1, min(240, _int("V7_API_LIMIT_PER_MINUTE", 220)))
    realtime_cache_seconds: int = max(3, _int("V7_REALTIME_CACHE_SECONDS", 10))
    news_cache_seconds: int = max(60, _int("V71_NEWS_CACHE_MINUTES", 10) * 60)
    notice_cache_seconds: int = max(60, _int("V71_ANN_CACHE_MINUTES", 10) * 60)
    request_timeout_seconds: int = max(2, _int("V7_REQUEST_TIMEOUT_SECONDS", 8))
    max_retries: int = max(0, min(2, _int("V71_MAX_API_RETRIES", 1)))
    retry_backoff_seconds: int = max(0, _int("V71_API_RETRY_BACKOFF_SECONDS", 1))
    top_candidates_for_enrichment: int = max(1, min(20, _int("V7_TOP_CANDIDATES_FOR_ENRICHMENT", 12)))
    enrich_minute_cooldown_seconds: int = max(30, _int("V7_MINUTE_COOLDOWN_SECONDS", 60))

    # V7.1 Shadow controls. Defaults are deliberately conservative.
    push_enabled: bool = _bool("V71_PUSH_ENABLED", False)
    # No true 09:15-09:25 realtime auction feed is configured yet.
    # Keep False until a verified realtime-auction API is added.
    realtime_auction_enabled: bool = _bool("V71_REALTIME_AUCTION_ENABLED", False)
    push_max_attempts: int = max(1, min(5, _int("V71_PUSH_MAX_ATTEMPTS", 3)))
    push_retry_backoff_seconds: int = max(0, _int("V71_PUSH_RETRY_BACKOFF_SECONDS", 1))
    max_push_per_round: int = max(1, min(5, _int("V71_MAX_PUSH_PER_ROUND", 5)))
    auction_max_gap_pct: float = _float("V71_AUCTION_MAX_GAP_PCT", 6.0)
    auction_min_amount: float = _float("V71_AUCTION_MIN_AMOUNT", 2_000_000.0)
    min_amount: float = _float("V71_MIN_AMOUNT", 50_000_000.0)
    max_spread_pct: float = _float("V71_MAX_SPREAD_PCT", 1.0)
    watch_top_n: int = max(1, min(20, _int("V71_WATCH_TOP_N", 10)))
    fail_open: bool = _bool("V71_FAIL_OPEN", True)
    confirm_start_time: str = os.getenv("V71_CONFIRM_START_TIME", "09:35")
    confirm_recheck_time: str = os.getenv("V71_CONFIRM_RECHECK_TIME", "10:00")
    confirm_end_time: str = os.getenv("V71_CONFIRM_END_TIME", "10:10")
    unified_mode: bool = _bool("V71_UNIFIED_MODE", True)
    import_v66_intraday: bool = _bool("V71_IMPORT_V66_INTRADAY", True)
    v66_original_push_enabled: bool = _bool("V66_ORIGINAL_PUSH_ENABLED", True)
    route_v66_messages: bool = _bool("V71_ROUTE_V66_MESSAGES", True)
    intraday_confirm_rounds: int = max(2, min(4, _int("V71_INTRADAY_CONFIRM_ROUNDS", 2)))
    intraday_max_pct: float = _float("V71_INTRADAY_MAX_PCT", 6.5)
    intraday_morning_start: str = os.getenv("V71_INTRADAY_MORNING_START", "09:35")
    intraday_morning_end: str = os.getenv("V71_INTRADAY_MORNING_END", "11:20")
    intraday_afternoon_start: str = os.getenv("V71_INTRADAY_AFTERNOON_START", "13:05")
    intraday_afternoon_end: str = os.getenv("V71_INTRADAY_AFTERNOON_END", "14:20")


CONFIG = V7Config()

@dataclass(frozen=True)
class V72ResearchConfig:
    research_enable: bool = _bool("V72_RESEARCH_ENABLE", True)
    portfolio_enable: bool = _bool("V72_PORTFOLIO_ENABLE", True)
    portfolio_push: bool = _bool("V72_PORTFOLIO_PUSH", False)
    research_push: bool = _bool("V72_RESEARCH_PUSH", False)
    holding_scan_seconds: int = max(10, _int("V72_HOLDING_SCAN_SECONDS", 20))
    research_top_n: int = max(1, min(50, _int("V72_RESEARCH_TOP_N", 20)))
    episode_dedupe_days: int = max(1, _int("V72_EPISODE_DEDUPE_DAYS", 20))
    forward_enable: bool = _bool("V72_FORWARD_ENABLE", True)
    second_strength_enable: bool = _bool("V72_SECOND_STRENGTH_ENABLE", True)
    state_confirm_rounds: int = max(2, _int("V72_STATE_CONFIRM_ROUNDS", 2))
    second_strength_window_days: int = max(5, _int("V72_SECOND_STRENGTH_WINDOW_DAYS", 30))
    commission_bps: float = max(0.0, _float("V72_COMMISSION_BPS", 2.5))
    stamp_duty_bps: float = max(0.0, _float("V72_STAMP_DUTY_BPS", 5.0))
    entry_slippage_bps: float = max(0.0, _float("V72_ENTRY_SLIPPAGE_BPS", 5.0))
    exit_slippage_bps: float = max(0.0, _float("V72_EXIT_SLIPPAGE_BPS", 5.0))
    other_cost_bps: float = max(0.0, _float("V72_OTHER_COST_BPS", 0.0))
    api_failure_threshold: int = max(1, _int("V72_API_FAILURE_THRESHOLD", 3))
    api_circuit_cooldown_seconds: int = max(30, _int("V72_API_CIRCUIT_COOLDOWN_SECONDS", 120))
    api_default_per_minute: int = max(1, _int("V72_API_DEFAULT_PER_MINUTE", 180))
    api_default_per_day: int = max(100, _int("V72_API_DEFAULT_PER_DAY", 20000))
    portfolio_single_stock_push_limit: int = max(1, _int("V72_PORTFOLIO_SINGLE_STOCK_PUSH_LIMIT", 4))
    portfolio_state_cooldown_minutes: int = max(1, _int("V72_PORTFOLIO_STATE_COOLDOWN_MINUTES", 30))
    portfolio_global_cooldown_seconds: int = max(0, _int("V72_PORTFOLIO_GLOBAL_COOLDOWN_SECONDS", 5))
    forward_cost_config_version: str = os.getenv("V72_FORWARD_COST_CONFIG_VERSION", "V72-costs-1")
    minute_archive_retention_days: int = max(30, _int("V72_MINUTE_ARCHIVE_RETENTION_DAYS", 730))
    checkpoint_max_retries: int = max(1, min(10, _int("V72_CHECKPOINT_MAX_RETRIES", 3)))
    checkpoint_retry_seconds: int = max(5, _int("V72_CHECKPOINT_RETRY_SECONDS", 60))
    health_retry_minutes: int = max(1, _int("V72_HEALTH_RETRY_MINUTES", 10))
    health_unavailable_after: int = max(2, _int("V72_HEALTH_UNAVAILABLE_AFTER", 3))
    archive_candidate_top_n: int = max(1, min(100, _int("V72_ARCHIVE_CANDIDATE_TOP_N", 20)))
    catalyst_enable: bool = _bool("V72_CATALYST_ENABLE", True)
    catalyst_push: bool = _bool("V72_CATALYST_PUSH", True)
    catalyst_target_top_n: int = max(1, min(30, _int("V72_CATALYST_TARGET_TOP_N", 12)))
    catalyst_push_score: float = max(50.0, min(95.0, _float("V72_CATALYST_PUSH_SCORE", 70.0)))
    catalyst_max_push_per_day: int = max(1, min(20, _int("V72_CATALYST_MAX_PUSH_PER_DAY", 6)))

V72_CONFIG = V72ResearchConfig()
