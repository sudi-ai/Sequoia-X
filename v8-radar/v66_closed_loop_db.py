"""V6.6 Pro 次日选股闭环的独立 SQLite 持久化层。

本模块只负责保存和汇总真实业务数据，不下载行情、不生成候选，也绝不填充
模拟行情。主程序应在以下时点调用：

* 15:05：写入 ``sector_forecasts``、``candidate_pool`` 和
  ``score_components``；
* 19:30：写入 ``evening_revisions``；
* 次日 09:00/09:25：写入 ``preopen_revisions``；
* 次日收盘行情可用后：把行情供应商返回的实际 OHLC 交给
  :func:`record_next_day_ohlc`，随后调用（或让它自动调用）
  :func:`generate_winrate_reports`。

胜负判定口径：

* 最高价触及候选生成时保存的目标位，且未触及防守位：SUCCESS；
* 最低价触及防守位，且未触及目标位：FAILURE；
* 日线同时触及两者且没有分钟级先后证据：AMBIGUOUS；
* 两者都未触及：NO_TRIGGER；
* 缺少判定阈值：UNJUDGED（正常候选写入会阻止这种情况）。

``winrate_reports`` 的胜率分母只包含 SUCCESS + FAILURE。AMBIGUOUS、
NO_TRIGGER 和 UNJUDGED 会单独计数，不会被偷偷算成输赢。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 2
MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = MODULE_DIR / "data_v66" / "v66_closed_loop.sqlite3"

_LOCK = threading.RLock()
_FORBIDDEN_DATA_SOURCE_WORDS = (
    "mock",
    "fake",
    "fixture",
    "sample",
    "synthetic",
    "demo",
    "test",
    "simulated",
    "simulation",
    "test data",
    "测试数据",
    "模拟",
    "样例",
    "合成",
)

_ACTIVE_CANDIDATE_STATUSES = ("CANDIDATE", "WATCH", "FOLLOWED")
_OUTCOMES = ("SUCCESS", "FAILURE", "AMBIGUOUS", "NO_TRIGGER", "UNJUDGED")


class ClosedLoopDataError(ValueError):
    """输入无法作为真实闭环数据落库。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path).expanduser().resolve() if db_path else DEFAULT_DB_PATH


def _iso_date(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ClosedLoopDataError(f"{field} 必须是 YYYY-MM-DD，收到：{value!r}") from exc


def _iso_datetime(value: Any, field: str, *, day: str | None = None) -> str:
    if value is None or str(value).strip() == "":
        return _now_iso()
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="seconds") if value.tzinfo else value.isoformat(timespec="seconds")
    text = str(value).strip()
    if day and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        text = f"{day}T{text}"
    try:
        return datetime.fromisoformat(text.replace(" ", "T", 1)).isoformat(timespec="seconds")
    except ValueError as exc:
        raise ClosedLoopDataError(f"{field} 必须是 ISO 日期时间，收到：{value!r}") from exc


def _stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    raise ClosedLoopDataError(f"股票代码必须包含 6 位数字，收到：{value!r}")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClosedLoopDataError(f"{field} 不能为空")
    return text


def _float(value: Any, field: str, *, required: bool = False) -> float | None:
    if value is None or str(value).strip() == "":
        if required:
            raise ClosedLoopDataError(f"{field} 不能为空")
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ClosedLoopDataError(f"{field} 必须是数字，收到：{value!r}") from exc
    if not math.isfinite(result):
        raise ClosedLoopDataError(f"{field} 必须是有限数字")
    return result


def _score(value: Any, field: str, *, required: bool = True) -> float | None:
    result = _float(value, field, required=required)
    if result is not None and not 0 <= result <= 100:
        raise ClosedLoopDataError(f"{field} 必须在 0~100 之间，收到：{result}")
    return result


def _non_negative(value: Any, field: str, *, default: float = 0.0) -> float:
    result = _float(default if value is None else value, field, required=True)
    assert result is not None
    if result < 0:
        raise ClosedLoopDataError(f"{field} 不能为负数")
    return result


def _json(value: Any, field: str) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ClosedLoopDataError(f"{field} 不是可序列化数据") from exc


def _decode_json_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in tuple(result):
        if key.endswith("_json") and result[key] is not None:
            try:
                result[key[:-5]] = json.loads(result[key])
            except (TypeError, json.JSONDecodeError):
                result[key[:-5]] = result[key]
    return result


def _actual_source(value: Any, field: str = "data_source") -> str:
    source = _required_text(value, field)
    lowered = source.casefold()
    if any(word.casefold() in lowered for word in _FORBIDDEN_DATA_SOURCE_WORDS):
        raise ClosedLoopDataError(f"{field} 标识为模拟/测试来源，禁止写入真实闭环库：{source!r}")
    return source


@contextmanager
def _connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sector_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date TEXT NOT NULL,
    target_trade_date TEXT,
    sector_code TEXT NOT NULL DEFAULT '',
    sector_name TEXT NOT NULL,
    forecast_rank INTEGER,
    lifecycle_stage TEXT,
    today_change_pct REAL,
    turnover_change_pct REAL,
    net_fund_inflow REAL,
    limit_up_count INTEGER,
    leader_stock_code TEXT,
    leader_stock_name TEXT,
    today_change_score REAL CHECK(today_change_score IS NULL OR today_change_score BETWEEN 0 AND 100),
    turnover_change_score REAL CHECK(turnover_change_score IS NULL OR turnover_change_score BETWEEN 0 AND 100),
    fund_flow_score REAL CHECK(fund_flow_score IS NULL OR fund_flow_score BETWEEN 0 AND 100),
    limit_up_score REAL CHECK(limit_up_score IS NULL OR limit_up_score BETWEEN 0 AND 100),
    leader_strength_score REAL CHECK(leader_strength_score IS NULL OR leader_strength_score BETWEEN 0 AND 100),
    breadth_score REAL CHECK(breadth_score IS NULL OR breadth_score BETWEEN 0 AND 100),
    persistence_score REAL CHECK(persistence_score IS NULL OR persistence_score BETWEEN 0 AND 100),
    continuation_score REAL NOT NULL CHECK(continuation_score BETWEEN 0 AND 100),
    weights_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    data_source TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(forecast_date, sector_name)
);

CREATE INDEX IF NOT EXISTS idx_sector_forecasts_day_rank
ON sector_forecasts(forecast_date, forecast_rank, continuation_score DESC);

CREATE TABLE IF NOT EXISTS candidate_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    sector_forecast_id INTEGER,
    selected_at TEXT NOT NULL,
    selection_stage TEXT NOT NULL DEFAULT '15:05_CLOSE',
    selection_reason TEXT NOT NULL,
    base_score REAL,
    comprehensive_score REAL NOT NULL CHECK(comprehensive_score BETWEEN 0 AND 100),
    market_sentiment_score REAL NOT NULL CHECK(market_sentiment_score BETWEEN 0 AND 100),
    sector_score REAL NOT NULL CHECK(sector_score BETWEEN 0 AND 100),
    stock_score REAL NOT NULL CHECK(stock_score BETWEEN 0 AND 100),
    trend_score REAL,
    chip_score REAL,
    risk_score REAL NOT NULL CHECK(risk_score BETWEEN 0 AND 100),
    risk_penalty REAL NOT NULL DEFAULT 0 CHECK(risk_penalty >= 0),
    regulatory_risk_penalty REAL NOT NULL DEFAULT 0 CHECK(regulatory_risk_penalty >= 0),
    high_position_risk_penalty REAL NOT NULL DEFAULT 0 CHECK(high_position_risk_penalty >= 0),
    liquidity_risk_penalty REAL NOT NULL DEFAULT 0 CHECK(liquidity_risk_penalty >= 0),
    negative_news_penalty REAL NOT NULL DEFAULT 0 CHECK(negative_news_penalty >= 0),
    reward_risk_penalty REAL NOT NULL DEFAULT 0 CHECK(reward_risk_penalty >= 0),
    other_risk_penalty REAL NOT NULL DEFAULT 0 CHECK(other_risk_penalty >= 0),
    hard_filter_passed INTEGER NOT NULL DEFAULT 1 CHECK(hard_filter_passed = 1),
    hard_filter_reasons_json TEXT NOT NULL DEFAULT '[]',
    buy_range_low REAL NOT NULL CHECK(buy_range_low > 0),
    buy_range_high REAL NOT NULL CHECK(buy_range_high > 0),
    buy_range_text TEXT NOT NULL,
    reference_price REAL NOT NULL CHECK(reference_price > 0),
    target_price REAL CHECK(target_price > 0),
    stop_price REAL CHECK(stop_price > 0),
    target_return_pct REAL CHECK(target_return_pct > 0),
    risk_limit_pct REAL CHECK(risk_limit_pct > 0),
    candidate_level TEXT NOT NULL DEFAULT 'NORMAL',
    candidate_rank INTEGER,
    market_regime TEXT,
    status TEXT NOT NULL DEFAULT 'CANDIDATE'
        CHECK(status IN ('CANDIDATE','WATCH','CANCELLED','FOLLOWED','EXPIRED')),
    algorithm_version TEXT NOT NULL,
    data_source TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(sector_forecast_id) REFERENCES sector_forecasts(id) ON DELETE SET NULL,
    UNIQUE(candidate_date, stock_code),
    CHECK(buy_range_low <= buy_range_high),
    CHECK(target_price IS NOT NULL OR target_return_pct IS NOT NULL),
    CHECK(stop_price IS NOT NULL OR risk_limit_pct IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_candidate_pool_day_rank
ON candidate_pool(candidate_date, status, candidate_rank, comprehensive_score DESC);
CREATE INDEX IF NOT EXISTS idx_candidate_pool_code_day
ON candidate_pool(stock_code, candidate_date DESC);

CREATE TABLE IF NOT EXISTS score_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT '15:05_CLOSE',
    component_name TEXT NOT NULL,
    component_type TEXT NOT NULL
        CHECK(component_type IN ('WEIGHTED','BONUS','DEDUCTION','FILTER','INFO')),
    raw_value REAL,
    normalized_score REAL,
    weight REAL,
    contribution REAL,
    hard_filter INTEGER NOT NULL DEFAULT 0 CHECK(hard_filter IN (0,1)),
    passed INTEGER CHECK(passed IN (0,1)),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    data_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidate_pool(id) ON DELETE CASCADE,
    UNIQUE(candidate_id, stage, component_name)
);

CREATE INDEX IF NOT EXISTS idx_score_components_candidate
ON score_components(candidate_id, stage);

CREATE TABLE IF NOT EXISTS evening_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    revision_date TEXT NOT NULL,
    revised_at TEXT NOT NULL,
    old_rank INTEGER,
    new_rank INTEGER,
    old_score REAL,
    new_score REAL,
    old_status TEXT,
    new_status TEXT,
    score_delta REAL,
    rank_delta INTEGER,
    revision_reason TEXT NOT NULL,
    announcements_json TEXT NOT NULL DEFAULT '[]',
    industry_news_json TEXT NOT NULL DEFAULT '[]',
    policy_news_json TEXT NOT NULL DEFAULT '[]',
    overseas_market_json TEXT NOT NULL DEFAULT '{}',
    data_complete INTEGER NOT NULL CHECK(data_complete IN (0,1)),
    data_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidate_pool(id) ON DELETE CASCADE,
    UNIQUE(candidate_id, revision_date)
);

CREATE INDEX IF NOT EXISTS idx_evening_revision_day
ON evening_revisions(revision_date, new_rank);

CREATE TABLE IF NOT EXISTS preopen_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    strategy_date TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('09:00','09:25')),
    revised_at TEXT NOT NULL,
    old_rank INTEGER,
    new_rank INTEGER,
    old_score REAL,
    new_score REAL,
    old_status TEXT,
    new_status TEXT,
    auction_price REAL,
    auction_change_pct REAL,
    auction_amount REAL,
    sector_auction_score REAL,
    yesterday_feedback_score REAL,
    risk_message TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    data_complete INTEGER NOT NULL CHECK(data_complete IN (0,1)),
    data_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidate_pool(id) ON DELETE CASCADE,
    UNIQUE(candidate_id, strategy_date, stage)
);

CREATE INDEX IF NOT EXISTS idx_preopen_revision_day_stage
ON preopen_revisions(strategy_date, stage, new_rank);

CREATE TABLE IF NOT EXISTS next_day_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    quote_timestamp TEXT NOT NULL,
    open_price REAL NOT NULL CHECK(open_price > 0),
    high_price REAL NOT NULL CHECK(high_price > 0),
    low_price REAL NOT NULL CHECK(low_price > 0),
    close_price REAL NOT NULL CHECK(close_price > 0),
    prior_close REAL NOT NULL CHECK(prior_close > 0),
    pct_change REAL NOT NULL,
    open_gap_pct REAL NOT NULL,
    max_up_pct REAL NOT NULL,
    max_drawdown_pct REAL NOT NULL CHECK(max_drawdown_pct >= 0),
    min_return_pct REAL NOT NULL,
    buy_zone_touched INTEGER NOT NULL CHECK(buy_zone_touched IN (0,1)),
    target_price REAL,
    stop_price REAL,
    target_hit INTEGER NOT NULL CHECK(target_hit IN (0,1)),
    stop_hit INTEGER NOT NULL CHECK(stop_hit IN (0,1)),
    first_hit TEXT CHECK(first_hit IN ('TARGET','STOP') OR first_hit IS NULL),
    target_hit_at TEXT,
    stop_hit_at TEXT,
    outcome TEXT NOT NULL
        CHECK(outcome IN ('SUCCESS','FAILURE','AMBIGUOUS','NO_TRIGGER','UNJUDGED')),
    outcome_reason TEXT NOT NULL,
    market_data_source TEXT NOT NULL,
    actual_data INTEGER NOT NULL DEFAULT 1 CHECK(actual_data = 1),
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidate_pool(id) ON DELETE CASCADE,
    UNIQUE(candidate_id, trade_date),
    CHECK(low_price <= open_price),
    CHECK(low_price <= close_price),
    CHECK(low_price <= high_price),
    CHECK(high_price >= open_price),
    CHECK(high_price >= close_price)
);

CREATE INDEX IF NOT EXISTS idx_next_tracking_trade_outcome
ON next_day_tracking(trade_date, outcome);

CREATE TABLE IF NOT EXISTS winrate_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_as_of TEXT NOT NULL,
    dimension TEXT NOT NULL,
    group_key TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    decisive_count INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    ambiguous_count INTEGER NOT NULL,
    no_trigger_count INTEGER NOT NULL,
    unjudged_count INTEGER NOT NULL,
    win_rate_pct REAL,
    target_hit_rate_pct REAL,
    stop_hit_rate_pct REAL,
    average_close_return_pct REAL,
    average_max_up_pct REAL,
    average_max_drawdown_pct REAL,
    data_start_date TEXT,
    data_end_date TEXT,
    generated_at TEXT NOT NULL,
    source_table TEXT NOT NULL DEFAULT 'next_day_tracking(actual_data=1)',
    UNIQUE(report_as_of, dimension, group_key)
);

CREATE INDEX IF NOT EXISTS idx_winrate_report_asof
ON winrate_reports(report_as_of, dimension, win_rate_pct DESC);
"""


_SECTOR_FACTOR_SCORE_COLUMNS = (
    "today_change_score",
    "turnover_change_score",
    "fund_flow_score",
    "limit_up_score",
    "leader_strength_score",
    "breadth_score",
    "persistence_score",
)


def _migrate_sector_forecasts_to_v2(conn: sqlite3.Connection) -> bool:
    """把 v1 的七个 NOT NULL 因子列安全迁移为可空列。

    SQLite 不能直接删除列级 NOT NULL，因此使用官方建议的“新表复制、旧表
    替换”方式。复制时显式保留主键 ID，candidate_pool 的外键关系不变。
    返回值表示本次是否实际执行了迁移。
    """
    columns = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(sector_forecasts)").fetchall()
    }
    if not columns:
        return False
    if not any(
        name in columns and int(columns[name]["notnull"]) == 1
        for name in _SECTOR_FACTOR_SCORE_COLUMNS
    ):
        return False

    copy_columns = (
        "id", "forecast_date", "target_trade_date", "sector_code", "sector_name",
        "forecast_rank", "lifecycle_stage", "today_change_pct",
        "turnover_change_pct", "net_fund_inflow", "limit_up_count",
        "leader_stock_code", "leader_stock_name", "today_change_score",
        "turnover_change_score", "fund_flow_score", "limit_up_score",
        "leader_strength_score", "breadth_score", "persistence_score",
        "continuation_score", "weights_json", "evidence_json", "data_source",
        "algorithm_version", "created_at", "updated_at",
    )
    missing_columns = [name for name in copy_columns if name not in columns]
    if missing_columns:
        raise RuntimeError(
            "sector_forecasts 旧表缺少迁移所需字段：" + ", ".join(missing_columns)
        )

    # PRAGMA foreign_keys 只能在事务外切换；executescript(_SCHEMA) 已确保此处
    # 没有未提交事务。表替换期间临时关闭，提交后立即恢复并做完整性检查。
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS sector_forecasts_v2_migration")
        conn.execute(
            """CREATE TABLE sector_forecasts_v2_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_date TEXT NOT NULL,
                target_trade_date TEXT,
                sector_code TEXT NOT NULL DEFAULT '',
                sector_name TEXT NOT NULL,
                forecast_rank INTEGER,
                lifecycle_stage TEXT,
                today_change_pct REAL,
                turnover_change_pct REAL,
                net_fund_inflow REAL,
                limit_up_count INTEGER,
                leader_stock_code TEXT,
                leader_stock_name TEXT,
                today_change_score REAL CHECK(today_change_score IS NULL OR today_change_score BETWEEN 0 AND 100),
                turnover_change_score REAL CHECK(turnover_change_score IS NULL OR turnover_change_score BETWEEN 0 AND 100),
                fund_flow_score REAL CHECK(fund_flow_score IS NULL OR fund_flow_score BETWEEN 0 AND 100),
                limit_up_score REAL CHECK(limit_up_score IS NULL OR limit_up_score BETWEEN 0 AND 100),
                leader_strength_score REAL CHECK(leader_strength_score IS NULL OR leader_strength_score BETWEEN 0 AND 100),
                breadth_score REAL CHECK(breadth_score IS NULL OR breadth_score BETWEEN 0 AND 100),
                persistence_score REAL CHECK(persistence_score IS NULL OR persistence_score BETWEEN 0 AND 100),
                continuation_score REAL NOT NULL CHECK(continuation_score BETWEEN 0 AND 100),
                weights_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                data_source TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(forecast_date, sector_name)
            )"""
        )
        names = ", ".join(copy_columns)
        conn.execute(
            f"""INSERT INTO sector_forecasts_v2_migration ({names})
                SELECT {names} FROM sector_forecasts"""
        )
        old_count = int(conn.execute("SELECT COUNT(*) FROM sector_forecasts").fetchone()[0])
        new_count = int(
            conn.execute("SELECT COUNT(*) FROM sector_forecasts_v2_migration").fetchone()[0]
        )
        if old_count != new_count:
            raise RuntimeError(
                f"sector_forecasts 迁移行数不一致：旧表 {old_count}，新表 {new_count}"
            )
        conn.execute("DROP TABLE sector_forecasts")
        conn.execute(
            "ALTER TABLE sector_forecasts_v2_migration RENAME TO sector_forecasts"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"v2 迁移后外键检查失败：{[tuple(row) for row in violations]}")
    return True


def initialize_database(db_path: str | Path | None = None) -> Path:
    """创建/升级独立闭环数据库并返回绝对路径；不会写入业务样本。"""
    path = _db_path(db_path)
    with _LOCK, _connection(path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        # 第一次执行负责创建新库的 v2 表，也会补齐旧库中的其他表。
        conn.executescript(_SCHEMA)
        _migrate_sector_forecasts_to_v2(conn)
        # 旧表替换会移除其索引；再次执行幂等 schema 以恢复全部索引。
        conn.executescript(_SCHEMA)
        with conn:
            conn.execute(
                """INSERT INTO schema_metadata(key, value, updated_at)
                   VALUES('schema_version', ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                                  updated_at=excluded.updated_at""",
                (str(SCHEMA_VERSION), _now_iso()),
            )
    return path


def _ensure(db_path: str | Path | None) -> Path:
    return initialize_database(db_path)


def _sector_forecast_values(forecast: Mapping[str, Any]) -> dict[str, Any]:
    """校验一个真实收盘截面的板块预测并转换为数据库字段。

    七个 ``*_score`` 因子允许真实缺失并保存为 NULL；最终
    ``continuation_score`` 必须显式传入。本函数不会填 0 或编造缺失分数。
    """
    day = _iso_date(forecast.get("forecast_date", forecast.get("date")), "forecast_date")
    target_day_value = forecast.get("target_trade_date")
    target_day = _iso_date(target_day_value, "target_trade_date") if target_day_value else None
    sector_name = _required_text(forecast.get("sector_name", forecast.get("sector")), "sector_name")
    source = _actual_source(forecast.get("data_source"))
    algorithm = _required_text(forecast.get("algorithm_version"), "algorithm_version")
    raw_evidence = forecast.get("evidence", {})
    if isinstance(raw_evidence, Mapping):
        evidence = dict(raw_evidence)
    else:
        evidence = {"detail": raw_evidence}
    # 兼容预测层把缺失项和置信度放在顶层；统一原样进入 evidence_json，
    # 不把缺失因子换算为 0，也不在持久化层臆造置信度。
    for key in ("missing", "missing_factors", "confidence", "data_confidence"):
        if key in forecast:
            evidence[key] = forecast[key]
    now = _now_iso()
    values = {
        "forecast_date": day,
        "target_trade_date": target_day,
        "sector_code": str(forecast.get("sector_code") or "").strip(),
        "sector_name": sector_name,
        "forecast_rank": forecast.get("forecast_rank", forecast.get("rank")),
        "lifecycle_stage": str(forecast.get("lifecycle_stage") or "").strip() or None,
        "today_change_pct": _float(forecast.get("today_change_pct"), "today_change_pct"),
        "turnover_change_pct": _float(forecast.get("turnover_change_pct"), "turnover_change_pct"),
        "net_fund_inflow": _float(forecast.get("net_fund_inflow"), "net_fund_inflow"),
        "limit_up_count": forecast.get("limit_up_count"),
        "leader_stock_code": _stock_code(forecast["leader_stock_code"]) if forecast.get("leader_stock_code") else None,
        "leader_stock_name": str(forecast.get("leader_stock_name") or "").strip() or None,
        "today_change_score": _score(forecast.get("today_change_score"), "today_change_score", required=False),
        "turnover_change_score": _score(forecast.get("turnover_change_score"), "turnover_change_score", required=False),
        "fund_flow_score": _score(forecast.get("fund_flow_score"), "fund_flow_score", required=False),
        "limit_up_score": _score(forecast.get("limit_up_score"), "limit_up_score", required=False),
        "leader_strength_score": _score(forecast.get("leader_strength_score"), "leader_strength_score", required=False),
        "breadth_score": _score(forecast.get("breadth_score"), "breadth_score", required=False),
        "persistence_score": _score(forecast.get("persistence_score"), "persistence_score", required=False),
        "continuation_score": _score(forecast.get("continuation_score", forecast.get("sector_score")), "continuation_score"),
        "weights_json": _json(forecast.get("weights", {}), "weights"),
        "evidence_json": _json(evidence, "evidence"),
        "data_source": source,
        "algorithm_version": algorithm,
        "created_at": now,
        "updated_at": now,
    }
    if values["forecast_rank"] is not None:
        values["forecast_rank"] = int(values["forecast_rank"])
    if values["limit_up_count"] is not None:
        values["limit_up_count"] = int(values["limit_up_count"])
        if values["limit_up_count"] < 0:
            raise ClosedLoopDataError("limit_up_count 不能为负数")

    return values


def _upsert_sector_forecast_conn(
    conn: sqlite3.Connection, forecast: Mapping[str, Any]
) -> int:
    values = _sector_forecast_values(forecast)
    day = values["forecast_date"]
    sector_name = values["sector_name"]

    columns = tuple(values)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column not in {"created_at"})
    sql = (
        f"INSERT INTO sector_forecasts ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(forecast_date, sector_name) DO UPDATE SET {updates}"
    )
    conn.execute(sql, tuple(values[c] for c in columns))
    row = conn.execute(
        "SELECT id FROM sector_forecasts WHERE forecast_date=? AND sector_name=?",
        (day, sector_name),
    ).fetchone()
    return int(row["id"])


def upsert_sector_forecast(
    forecast: Mapping[str, Any], db_path: str | Path | None = None
) -> int:
    """保存一个真实收盘截面的板块预测，返回记录 ID。

    七个 ``*_score`` 因子允许真实缺失并保存为 NULL；最终
    ``continuation_score`` 必须显式传入。本函数不会填 0 或编造缺失分数。
    """
    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        forecast_id = _upsert_sector_forecast_conn(conn, forecast)
    return forecast_id


def get_sector_forecasts(
    forecast_date: Any, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    day = _iso_date(forecast_date, "forecast_date")
    path = _ensure(db_path)
    with _connection(path) as conn:
        rows = conn.execute(
            """SELECT * FROM sector_forecasts WHERE forecast_date=?
               ORDER BY COALESCE(forecast_rank, 999999), continuation_score DESC, sector_name""",
            (day,),
        ).fetchall()
    return [_decode_json_fields(row) for row in rows]


def _pick(data: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def _candidate_values(candidate: Mapping[str, Any]) -> dict[str, Any]:
    day = _iso_date(_pick(candidate, "candidate_date", "date"), "candidate_date")
    code = _stock_code(_pick(candidate, "stock_code", "code"))
    name = _required_text(_pick(candidate, "stock_name", "name"), "stock_name")
    sector = _required_text(_pick(candidate, "sector_name", "sector"), "sector_name")

    buy_low = _pick(candidate, "buy_range_low", "buy_low")
    buy_high = _pick(candidate, "buy_range_high", "buy_high")
    buy_range = candidate.get("buy_range")
    if isinstance(buy_range, Sequence) and not isinstance(buy_range, (str, bytes)) and len(buy_range) >= 2:
        buy_low = buy_low if buy_low is not None else buy_range[0]
        buy_high = buy_high if buy_high is not None else buy_range[1]
    buy_low_f = _float(buy_low, "buy_range_low", required=True)
    buy_high_f = _float(buy_high, "buy_range_high", required=True)
    assert buy_low_f is not None and buy_high_f is not None
    if buy_low_f <= 0 or buy_high_f <= 0 or buy_low_f > buy_high_f:
        raise ClosedLoopDataError("买入区间必须为正数，且 buy_range_low <= buy_range_high")

    reference = _float(
        _pick(candidate, "reference_price", "recommendation_price", "price"),
        "reference_price",
        required=True,
    )
    target = _float(_pick(candidate, "target_price", "target1"), "target_price")
    stop = _float(_pick(candidate, "stop_price", "defense"), "stop_price")
    target_pct = _float(candidate.get("target_return_pct"), "target_return_pct")
    risk_pct = _float(candidate.get("risk_limit_pct"), "risk_limit_pct")
    assert reference is not None
    if reference <= 0:
        raise ClosedLoopDataError("reference_price 必须大于 0")
    if target is None and target_pct is None:
        raise ClosedLoopDataError("必须提供 target_price 或 target_return_pct，才能真实判定次日成功")
    if stop is None and risk_pct is None:
        raise ClosedLoopDataError("必须提供 stop_price 或 risk_limit_pct，才能真实判定次日失败")
    if target is not None and target <= reference:
        raise ClosedLoopDataError("target_price 必须高于 reference_price")
    if stop is not None and stop >= reference:
        raise ClosedLoopDataError("stop_price 必须低于 reference_price")
    if target_pct is not None and target_pct <= 0:
        raise ClosedLoopDataError("target_return_pct 必须大于 0")
    if risk_pct is not None and risk_pct <= 0:
        raise ClosedLoopDataError("risk_limit_pct 必须大于 0")

    status = str(candidate.get("status") or "CANDIDATE").strip().upper()
    if status not in {"CANDIDATE", "WATCH", "CANCELLED", "FOLLOWED", "EXPIRED"}:
        raise ClosedLoopDataError(f"无效候选状态：{status}")
    hard_filter = bool(candidate.get("hard_filter_passed", True))
    if not hard_filter:
        raise ClosedLoopDataError("触发硬性风险过滤的股票不能写入 candidate_pool")

    selected_at = _iso_datetime(
        _pick(candidate, "selected_at", "selection_time"), "selected_at", day=day
    )
    now = _now_iso()
    risk_penalty = _non_negative(_pick(candidate, "risk_penalty", "risk_penalty_total", default=0), "risk_penalty")
    return {
        "candidate_date": day,
        "stock_code": code,
        "stock_name": name,
        "sector_name": sector,
        "sector_forecast_id": candidate.get("sector_forecast_id"),
        "selected_at": selected_at,
        "selection_stage": str(candidate.get("selection_stage") or "15:05_CLOSE").strip(),
        "selection_reason": _required_text(_pick(candidate, "selection_reason", "reason"), "selection_reason"),
        "base_score": _float(candidate.get("base_score"), "base_score"),
        "comprehensive_score": _score(_pick(candidate, "comprehensive_score", "final_score", "score"), "comprehensive_score"),
        "market_sentiment_score": _score(_pick(candidate, "market_sentiment_score", "sentiment_score"), "market_sentiment_score"),
        "sector_score": _score(candidate.get("sector_score"), "sector_score"),
        "stock_score": _score(_pick(candidate, "stock_score", "individual_score", "buy_score"), "stock_score"),
        "trend_score": _score(candidate.get("trend_score"), "trend_score", required=False),
        "chip_score": _score(candidate.get("chip_score"), "chip_score", required=False),
        "risk_score": _score(candidate.get("risk_score"), "risk_score"),
        "risk_penalty": risk_penalty,
        "regulatory_risk_penalty": _non_negative(candidate.get("regulatory_risk_penalty"), "regulatory_risk_penalty"),
        "high_position_risk_penalty": _non_negative(candidate.get("high_position_risk_penalty"), "high_position_risk_penalty"),
        "liquidity_risk_penalty": _non_negative(candidate.get("liquidity_risk_penalty"), "liquidity_risk_penalty"),
        "negative_news_penalty": _non_negative(candidate.get("negative_news_penalty"), "negative_news_penalty"),
        "reward_risk_penalty": _non_negative(candidate.get("reward_risk_penalty"), "reward_risk_penalty"),
        "other_risk_penalty": _non_negative(candidate.get("other_risk_penalty"), "other_risk_penalty"),
        "hard_filter_passed": 1,
        "hard_filter_reasons_json": _json(candidate.get("hard_filter_reasons", []), "hard_filter_reasons"),
        "buy_range_low": buy_low_f,
        "buy_range_high": buy_high_f,
        "buy_range_text": str(candidate.get("buy_range_text") or f"{buy_low_f:.2f}-{buy_high_f:.2f}"),
        "reference_price": reference,
        "target_price": target,
        "stop_price": stop,
        "target_return_pct": target_pct,
        "risk_limit_pct": risk_pct,
        "candidate_level": str(candidate.get("candidate_level") or "NORMAL").strip().upper(),
        "candidate_rank": int(candidate["candidate_rank"]) if candidate.get("candidate_rank") is not None else None,
        "market_regime": str(candidate.get("market_regime") or "").strip() or None,
        "status": status,
        "algorithm_version": _required_text(candidate.get("algorithm_version"), "algorithm_version"),
        "data_source": _actual_source(candidate.get("data_source")),
        "extra_json": _json(candidate.get("extra", {}), "extra"),
        "created_at": now,
        "updated_at": now,
    }


def _upsert_candidate_conn(conn: sqlite3.Connection, candidate: Mapping[str, Any]) -> int:
    values = _candidate_values(candidate)
    if values["sector_forecast_id"] is None:
        forecast = conn.execute(
            "SELECT id FROM sector_forecasts WHERE forecast_date=? AND sector_name=?",
            (values["candidate_date"], values["sector_name"]),
        ).fetchone()
        values["sector_forecast_id"] = int(forecast["id"]) if forecast else None
    elif not conn.execute("SELECT 1 FROM sector_forecasts WHERE id=?", (values["sector_forecast_id"],)).fetchone():
        raise ClosedLoopDataError("sector_forecast_id 不存在")

    columns = tuple(values)
    immutable = {"created_at"}
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column not in immutable)
    sql = (
        f"INSERT INTO candidate_pool ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(candidate_date, stock_code) DO UPDATE SET {updates}"
    )
    conn.execute(sql, tuple(values[column] for column in columns))
    row = conn.execute(
        "SELECT id FROM candidate_pool WHERE candidate_date=? AND stock_code=?",
        (values["candidate_date"], values["stock_code"]),
    ).fetchone()
    return int(row["id"])


def upsert_candidate(
    candidate: Mapping[str, Any],
    db_path: str | Path | None = None,
    *,
    score_components: Iterable[Mapping[str, Any]] | None = None,
    replace_score_components: bool = False,
) -> int:
    """真实保存/更新一只推荐股票及完整评分，返回 candidate_id。"""
    if score_components is None and candidate.get("score_components") is not None:
        score_components = candidate.get("score_components")
    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        candidate_id = _upsert_candidate_conn(conn, candidate)
        if score_components is not None:
            _upsert_score_components_conn(
                conn, candidate_id, score_components, replace=replace_score_components
            )
    return candidate_id


def upsert_candidates(
    candidates: Iterable[Mapping[str, Any]], db_path: str | Path | None = None
) -> list[int]:
    """在单个事务中批量保存每日真实候选；任意一条失败则整批回滚。"""
    path = _ensure(db_path)
    ids: list[int] = []
    with _LOCK, _connection(path) as conn, conn:
        for candidate in candidates:
            candidate_id = _upsert_candidate_conn(conn, candidate)
            components = candidate.get("score_components")
            if components is not None:
                _upsert_score_components_conn(conn, candidate_id, components, replace=True)
            ids.append(candidate_id)
    return ids


def save_close_snapshot(
    sector_forecasts: Iterable[Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """用一个事务原子保存 15:05 板块预测、候选及评分明细。

    候选必须能关联到本批次同日同名的板块预测。任何 forecast、candidate 或
    score component 校验失败时整个收盘批次回滚，杜绝“候选写了、板块没写”
    的半完成状态。
    """
    forecasts = list(sector_forecasts)
    candidate_rows = list(candidates)
    if not forecasts:
        raise ClosedLoopDataError("save_close_snapshot 至少需要一条 sector_forecast")
    if not candidate_rows:
        raise ClosedLoopDataError("save_close_snapshot 至少需要一条 candidate")

    path = _ensure(db_path)
    forecast_ids: list[int] = []
    candidate_ids: list[int] = []
    with _LOCK, _connection(path) as conn, conn:
        for forecast in forecasts:
            forecast_ids.append(_upsert_sector_forecast_conn(conn, forecast))
        for candidate in candidate_rows:
            candidate_id = _upsert_candidate_conn(conn, candidate)
            linked = conn.execute(
                """SELECT c.candidate_date, c.sector_name, c.sector_forecast_id,
                          f.forecast_date AS linked_forecast_date
                   FROM candidate_pool c
                   LEFT JOIN sector_forecasts f ON f.id=c.sector_forecast_id
                   WHERE c.id=?""",
                (candidate_id,),
            ).fetchone()
            if (
                linked["sector_forecast_id"] is None
                or linked["linked_forecast_date"] != linked["candidate_date"]
            ):
                raise ClosedLoopDataError(
                    f"候选 {linked['sector_name']} 无法关联同日 sector_forecast"
                )
            components = candidate.get("score_components")
            if components is None:
                raise ClosedLoopDataError(
                    f"候选 {candidate.get('stock_code', candidate.get('code'))} 缺少 score_components"
                )
            _upsert_score_components_conn(
                conn, candidate_id, components, replace=True
            )
            candidate_ids.append(candidate_id)

        snapshot_days = {
            str(row[0])
            for row in conn.execute(
                f"""SELECT forecast_date FROM sector_forecasts
                     WHERE id IN ({','.join('?' for _ in forecast_ids)})""",
                forecast_ids,
            ).fetchall()
        }
        snapshot_days.update(
            str(row[0])
            for row in conn.execute(
                f"""SELECT candidate_date FROM candidate_pool
                     WHERE id IN ({','.join('?' for _ in candidate_ids)})""",
                candidate_ids,
            ).fetchall()
        )
        if len(snapshot_days) != 1:
            raise ClosedLoopDataError(
                f"一个收盘快照只能包含同一日期，收到：{sorted(snapshot_days)}"
            )
    return {
        "candidate_date": next(iter(snapshot_days)),
        "sector_forecast_ids": forecast_ids,
        "candidate_ids": candidate_ids,
        "database": str(path),
    }


def _resolve_candidate_id(
    conn: sqlite3.Connection,
    candidate_id: int | None = None,
    *,
    candidate_date: Any = None,
    stock_code: Any = None,
) -> int:
    if candidate_id is not None:
        row = conn.execute("SELECT id FROM candidate_pool WHERE id=?", (int(candidate_id),)).fetchone()
    else:
        day = _iso_date(candidate_date, "candidate_date")
        code = _stock_code(stock_code)
        row = conn.execute(
            "SELECT id FROM candidate_pool WHERE candidate_date=? AND stock_code=?",
            (day, code),
        ).fetchone()
    if not row:
        raise ClosedLoopDataError("找不到对应的 candidate_pool 记录")
    return int(row["id"])


def _upsert_score_components_conn(
    conn: sqlite3.Connection,
    candidate_id: int,
    components: Iterable[Mapping[str, Any]],
    *,
    replace: bool,
) -> None:
    rows = list(components)
    for component in rows:
        if bool(component.get("hard_filter")) and component.get("passed") is False:
            raise ClosedLoopDataError(
                f"硬过滤项 {component.get('component_name', component.get('name'))!r} 未通过，不能保存为候选"
            )
    if replace:
        conn.execute("DELETE FROM score_components WHERE candidate_id=?", (candidate_id,))
    now = _now_iso()
    for component in rows:
        name = _required_text(_pick(component, "component_name", "name"), "component_name")
        stage = str(component.get("stage") or "15:05_CLOSE").strip()
        component_type = str(component.get("component_type") or "INFO").strip().upper()
        if component_type not in {"WEIGHTED", "BONUS", "DEDUCTION", "FILTER", "INFO"}:
            raise ClosedLoopDataError(f"无效 component_type：{component_type}")
        passed = component.get("passed")
        values = (
            candidate_id,
            stage,
            name,
            component_type,
            _float(component.get("raw_value"), "raw_value"),
            _score(component.get("normalized_score"), "normalized_score", required=False),
            _float(component.get("weight"), "weight"),
            _float(component.get("contribution"), "contribution"),
            int(bool(component.get("hard_filter"))),
            None if passed is None else int(bool(passed)),
            _json(component.get("evidence", {}), "evidence"),
            _actual_source(component.get("data_source")),
            now,
            now,
        )
        conn.execute(
            """INSERT INTO score_components(
                   candidate_id, stage, component_name, component_type, raw_value,
                   normalized_score, weight, contribution, hard_filter, passed,
                   evidence_json, data_source, created_at, updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_id, stage, component_name) DO UPDATE SET
                   component_type=excluded.component_type,
                   raw_value=excluded.raw_value,
                   normalized_score=excluded.normalized_score,
                   weight=excluded.weight,
                   contribution=excluded.contribution,
                   hard_filter=excluded.hard_filter,
                   passed=excluded.passed,
                   evidence_json=excluded.evidence_json,
                   data_source=excluded.data_source,
                   updated_at=excluded.updated_at""",
            values,
        )


def upsert_score_components(
    candidate_id: int,
    components: Iterable[Mapping[str, Any]],
    db_path: str | Path | None = None,
    *,
    replace: bool = False,
) -> None:
    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        cid = _resolve_candidate_id(conn, candidate_id)
        _upsert_score_components_conn(conn, cid, components, replace=replace)


def get_candidates(
    candidate_date: Any,
    db_path: str | Path | None = None,
    *,
    include_cancelled: bool = True,
) -> list[dict[str, Any]]:
    """读取某日实际保存的候选和评分；默认也返回后续被取消的候选。"""
    day = _iso_date(candidate_date, "candidate_date")
    path = _ensure(db_path)
    sql = "SELECT * FROM candidate_pool WHERE candidate_date=?"
    params: list[Any] = [day]
    if not include_cancelled:
        sql += " AND status IN ('CANDIDATE','WATCH','FOLLOWED')"
    sql += " ORDER BY COALESCE(candidate_rank, 999999), comprehensive_score DESC, stock_code"
    with _connection(path) as conn:
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = _decode_json_fields(row)
            components = conn.execute(
                """SELECT * FROM score_components WHERE candidate_id=?
                   ORDER BY stage, id""",
                (row["id"],),
            ).fetchall()
            item["score_components"] = [_decode_json_fields(c) for c in components]
            result.append(item)
    return result


def get_previous_trading_day_untracked_candidates(
    as_of_date: Any = None,
    db_path: str | Path | None = None,
    *,
    include_cancelled: bool = True,
) -> list[dict[str, Any]]:
    """返回最近候选交易日、尚无次日实际行情的 15:05 初版候选。

    此处不猜测节假日日历，而是以 ``candidate_pool`` 中早于 ``as_of_date`` 的
    最近真实候选日期为“前一交易日”。交易日历判断应由行情层负责。

    ``candidate_pool`` 的每一行代表 15:05 初版真实入选，而 ``status`` 是后续
    晚间/盘前最终状态。为避免幸存者偏差，默认 ``include_cancelled=True``，
    后来变为 CANCELLED/EXPIRED 的股票仍获取实际行情。设为 False 时只返回
    CANDIDATE/WATCH/FOLLOWED，与旧的“当前有效候选”口径一致。
    """
    today = _iso_date(as_of_date or date.today(), "as_of_date")
    path = _ensure(db_path)
    with _connection(path) as conn:
        day_row = conn.execute(
            """SELECT MAX(candidate_date) AS candidate_date
               FROM candidate_pool
               WHERE candidate_date < ?""",
            (today,),
        ).fetchone()
        previous_day = day_row["candidate_date"] if day_row else None
        if not previous_day:
            return []
        status_filter = (
            ""
            if include_cancelled
            else "AND c.status IN ('CANDIDATE','WATCH','FOLLOWED')"
        )
        rows = conn.execute(
            f"""SELECT c.* FROM candidate_pool c
               WHERE c.candidate_date=?
                 {status_filter}
                 AND NOT EXISTS (
                     SELECT 1 FROM next_day_tracking t WHERE t.candidate_id=c.id
                 )
               ORDER BY COALESCE(c.candidate_rank, 999999),
                        c.comprehensive_score DESC, c.stock_code""",
            (previous_day,),
        ).fetchall()
    return [_decode_json_fields(row) for row in rows]


def get_all_untracked_candidates(
    as_of_date: Any = None,
    db_path: str | Path | None = None,
    *,
    include_cancelled: bool = True,
) -> list[dict[str, Any]]:
    """返回所有尚未跟踪的 15:05 初版候选，默认包含后续取消/过期票。

    ``candidate_pool`` 只登记 15:05 初版真实入选；后续 CANCELLED/EXPIRED 是
    策略修订结果，不应删除其实际行情。默认包含这些股票以消除幸存者偏差；
    ``include_cancelled=False`` 时恢复旧的当前有效状态过滤。
    """
    today = _iso_date(as_of_date or date.today(), "as_of_date")
    path = _ensure(db_path)
    with _connection(path) as conn:
        status_filter = (
            ""
            if include_cancelled
            else "AND c.status IN ('CANDIDATE','WATCH','FOLLOWED')"
        )
        rows = conn.execute(
            f"""SELECT c.* FROM candidate_pool c
               WHERE c.candidate_date < ?
                 {status_filter}
                 AND NOT EXISTS (
                     SELECT 1 FROM next_day_tracking t WHERE t.candidate_id=c.id
                 )
               ORDER BY c.candidate_date, COALESCE(c.candidate_rank, 999999), c.stock_code""",
            (today,),
        ).fetchall()
    return [_decode_json_fields(row) for row in rows]


def record_evening_revision(
    revision: Mapping[str, Any], db_path: str | Path | None = None
) -> int:
    """保存 19:30 修订，并同步候选最终分、排名和状态。

    当 ``data_complete=False`` 时强制保留旧分数/旧排名/旧状态，防止把接口
    失败误当成“没有利空”。
    """
    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        cid = _resolve_candidate_id(
            conn,
            revision.get("candidate_id"),
            candidate_date=revision.get("candidate_date"),
            stock_code=revision.get("stock_code"),
        )
        candidate = conn.execute("SELECT * FROM candidate_pool WHERE id=?", (cid,)).fetchone()
        revision_day = _iso_date(revision.get("revision_date", candidate["candidate_date"]), "revision_date")
        complete = bool(revision.get("data_complete", True))
        old_rank, old_score, old_status = candidate["candidate_rank"], candidate["comprehensive_score"], candidate["status"]
        if complete:
            new_rank = int(revision["new_rank"]) if revision.get("new_rank") is not None else old_rank
            new_score = _score(revision.get("new_score", old_score), "new_score")
            new_status = str(revision.get("new_status") or old_status).upper()
        else:
            new_rank, new_score, new_status = old_rank, old_score, old_status
        if new_status not in {"CANDIDATE", "WATCH", "CANCELLED", "FOLLOWED", "EXPIRED"}:
            raise ClosedLoopDataError(f"无效 new_status：{new_status}")
        reason = _required_text(revision.get("revision_reason"), "revision_reason")
        now = _now_iso()
        values = (
            cid, revision_day, _iso_datetime(revision.get("revised_at"), "revised_at", day=revision_day),
            old_rank, new_rank, old_score, new_score, old_status, new_status,
            round(float(new_score) - float(old_score), 6),
            (old_rank - new_rank) if old_rank is not None and new_rank is not None else None,
            reason, _json(revision.get("announcements", []), "announcements"),
            _json(revision.get("industry_news", []), "industry_news"),
            _json(revision.get("policy_news", []), "policy_news"),
            _json(revision.get("overseas_market", {}), "overseas_market"),
            int(complete), _actual_source(revision.get("data_source")), now,
        )
        conn.execute(
            """INSERT INTO evening_revisions(
                   candidate_id, revision_date, revised_at, old_rank, new_rank,
                   old_score, new_score, old_status, new_status, score_delta,
                   rank_delta, revision_reason, announcements_json, industry_news_json,
                   policy_news_json, overseas_market_json, data_complete, data_source, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_id, revision_date) DO UPDATE SET
                   revised_at=excluded.revised_at, old_rank=excluded.old_rank,
                   new_rank=excluded.new_rank, old_score=excluded.old_score,
                   new_score=excluded.new_score, old_status=excluded.old_status,
                   new_status=excluded.new_status, score_delta=excluded.score_delta,
                   rank_delta=excluded.rank_delta, revision_reason=excluded.revision_reason,
                   announcements_json=excluded.announcements_json,
                   industry_news_json=excluded.industry_news_json,
                   policy_news_json=excluded.policy_news_json,
                   overseas_market_json=excluded.overseas_market_json,
                   data_complete=excluded.data_complete, data_source=excluded.data_source""",
            values,
        )
        conn.execute(
            """UPDATE candidate_pool SET candidate_rank=?, comprehensive_score=?,
                   status=?, updated_at=? WHERE id=?""",
            (new_rank, new_score, new_status, now, cid),
        )
        row = conn.execute(
            "SELECT id FROM evening_revisions WHERE candidate_id=? AND revision_date=?",
            (cid, revision_day),
        ).fetchone()
    return int(row["id"])


def record_preopen_revision(
    revision: Mapping[str, Any], db_path: str | Path | None = None
) -> int:
    """保存次日 09:00 或 09:25 修订并同步候选排序/状态。"""
    stage = str(revision.get("stage") or "").strip()
    if stage not in {"09:00", "09:25"}:
        raise ClosedLoopDataError("preopen stage 只能是 09:00 或 09:25")
    strategy_day = _iso_date(revision.get("strategy_date"), "strategy_date")
    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        cid = _resolve_candidate_id(
            conn,
            revision.get("candidate_id"),
            candidate_date=revision.get("candidate_date"),
            stock_code=revision.get("stock_code"),
        )
        candidate = conn.execute("SELECT * FROM candidate_pool WHERE id=?", (cid,)).fetchone()
        complete = bool(revision.get("data_complete", True))
        old_rank, old_score, old_status = candidate["candidate_rank"], candidate["comprehensive_score"], candidate["status"]
        if complete:
            new_rank = int(revision["new_rank"]) if revision.get("new_rank") is not None else old_rank
            new_score = _score(revision.get("new_score", old_score), "new_score")
            new_status = str(revision.get("new_status") or old_status).upper()
        else:
            new_rank, new_score, new_status = old_rank, old_score, old_status
        if new_status not in {"CANDIDATE", "WATCH", "CANCELLED", "FOLLOWED", "EXPIRED"}:
            raise ClosedLoopDataError(f"无效 new_status：{new_status}")
        now = _now_iso()
        values = (
            cid, strategy_day, stage,
            _iso_datetime(revision.get("revised_at"), "revised_at", day=strategy_day),
            old_rank, new_rank, old_score, new_score, old_status, new_status,
            _float(revision.get("auction_price"), "auction_price"),
            _float(revision.get("auction_change_pct"), "auction_change_pct"),
            _float(revision.get("auction_amount"), "auction_amount"),
            _score(revision.get("sector_auction_score"), "sector_auction_score", required=False),
            _score(revision.get("yesterday_feedback_score"), "yesterday_feedback_score", required=False),
            str(revision.get("risk_message") or "").strip() or None,
            _json(revision.get("evidence", {}), "evidence"), int(complete),
            _actual_source(revision.get("data_source")), now,
        )
        conn.execute(
            """INSERT INTO preopen_revisions(
                   candidate_id, strategy_date, stage, revised_at, old_rank, new_rank,
                   old_score, new_score, old_status, new_status, auction_price,
                   auction_change_pct, auction_amount, sector_auction_score,
                   yesterday_feedback_score, risk_message, evidence_json,
                   data_complete, data_source, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_id, strategy_date, stage) DO UPDATE SET
                   revised_at=excluded.revised_at, old_rank=excluded.old_rank,
                   new_rank=excluded.new_rank, old_score=excluded.old_score,
                   new_score=excluded.new_score, old_status=excluded.old_status,
                   new_status=excluded.new_status, auction_price=excluded.auction_price,
                   auction_change_pct=excluded.auction_change_pct,
                   auction_amount=excluded.auction_amount,
                   sector_auction_score=excluded.sector_auction_score,
                   yesterday_feedback_score=excluded.yesterday_feedback_score,
                   risk_message=excluded.risk_message, evidence_json=excluded.evidence_json,
                   data_complete=excluded.data_complete, data_source=excluded.data_source""",
            values,
        )
        conn.execute(
            """UPDATE candidate_pool SET candidate_rank=?, comprehensive_score=?,
                   status=?, updated_at=? WHERE id=?""",
            (new_rank, new_score, new_status, now, cid),
        )
        row = conn.execute(
            """SELECT id FROM preopen_revisions
               WHERE candidate_id=? AND strategy_date=? AND stage=?""",
            (cid, strategy_day, stage),
        ).fetchone()
    return int(row["id"])


def _tracking_outcome(
    high: float,
    low: float,
    target: float | None,
    stop: float | None,
    first_hit: str | None,
) -> tuple[bool, bool, str, str]:
    target_hit = target is not None and high >= target
    stop_hit = stop is not None and low <= stop
    if target is None or stop is None:
        return target_hit, stop_hit, "UNJUDGED", "候选缺少目标位或防守位，无法完整判定"
    if target_hit and stop_hit:
        if first_hit == "TARGET":
            return True, True, "SUCCESS", "目标位与防守位均触及；分钟级证据显示目标先触及"
        if first_hit == "STOP":
            return True, True, "FAILURE", "目标位与防守位均触及；分钟级证据显示防守位先触及"
        return True, True, "AMBIGUOUS", "日线同时触及目标位和防守位，缺少分钟级先后证据"
    if target_hit:
        return True, False, "SUCCESS", "次日最高价达到候选目标位"
    if stop_hit:
        return False, True, "FAILURE", "次日最低价跌破候选防守位"
    return False, False, "NO_TRIGGER", "次日未触及目标位或防守位"


def record_next_day_ohlc(
    *,
    candidate_date: Any,
    stock_code: Any,
    trade_date: Any,
    open_price: Any,
    high_price: Any,
    low_price: Any,
    close_price: Any,
    data_source: Any,
    prior_close: Any = None,
    quote_timestamp: Any = None,
    first_hit: str | None = None,
    target_hit_at: str | None = None,
    stop_hit_at: str | None = None,
    raw_payload: Any = None,
    db_path: str | Path | None = None,
    refresh_reports: bool = True,
) -> dict[str, Any]:
    """写入行情供应商返回的次日实际 OHLC，并自动计算结果和真实胜率。

    ``first_hit`` 只能在有分钟级证据时传 ``TARGET`` 或 ``STOP``。仅有日线
    数据且目标/止损都触及时必须留空，系统会诚实标记 ``AMBIGUOUS``。
    """
    candidate_day = _iso_date(candidate_date, "candidate_date")
    tracking_day = _iso_date(trade_date, "trade_date")
    if tracking_day <= candidate_day:
        raise ClosedLoopDataError("trade_date 必须晚于 candidate_date")
    code = _stock_code(stock_code)
    source = _actual_source(data_source, "market_data_source")
    o = _float(open_price, "open_price", required=True)
    h = _float(high_price, "high_price", required=True)
    low = _float(low_price, "low_price", required=True)
    close = _float(close_price, "close_price", required=True)
    assert o is not None and h is not None and low is not None and close is not None
    if min(o, h, low, close) <= 0 or low > min(o, close, h) or h < max(o, close, low):
        raise ClosedLoopDataError("OHLC 不合法：必须 low <= open/close <= high，且价格均大于 0")
    hit = str(first_hit).strip().upper() if first_hit else None
    if hit not in {None, "TARGET", "STOP"}:
        raise ClosedLoopDataError("first_hit 只能是 TARGET、STOP 或留空")

    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        cid = _resolve_candidate_id(
            conn, candidate_date=candidate_day, stock_code=code
        )
        candidate = conn.execute(
            """SELECT c.*, f.target_trade_date
               FROM candidate_pool c
               LEFT JOIN sector_forecasts f ON f.id=c.sector_forecast_id
               WHERE c.id=?""",
            (cid,),
        ).fetchone()
        if candidate["target_trade_date"] and candidate["target_trade_date"] != tracking_day:
            raise ClosedLoopDataError(
                "trade_date 与收盘预测保存的 target_trade_date 不一致："
                f"期望 {candidate['target_trade_date']}，收到 {tracking_day}"
            )
        reference = float(candidate["reference_price"])
        previous_close = _float(prior_close, "prior_close")
        previous_close = previous_close if previous_close is not None else reference
        if previous_close <= 0:
            raise ClosedLoopDataError("prior_close 必须大于 0")
        target = candidate["target_price"]
        if target is None and candidate["target_return_pct"] is not None:
            target = reference * (1 + float(candidate["target_return_pct"]) / 100)
        stop = candidate["stop_price"]
        if stop is None and candidate["risk_limit_pct"] is not None:
            stop = reference * (1 - float(candidate["risk_limit_pct"]) / 100)
        target = float(target) if target is not None else None
        stop = float(stop) if stop is not None else None
        target_hit, stop_hit, outcome, reason = _tracking_outcome(h, low, target, stop, hit)
        if hit and not (target_hit and stop_hit):
            raise ClosedLoopDataError("first_hit 仅能在目标位与防守位均实际触及时提供")

        pct_change = round((close / previous_close - 1) * 100, 6)
        open_gap_pct = round((o / previous_close - 1) * 100, 6)
        max_up_pct = round((h / reference - 1) * 100, 6)
        min_return_pct = round((low / reference - 1) * 100, 6)
        max_drawdown_pct = round(max(0.0, (reference - low) / reference * 100), 6)
        buy_touched = int(h >= float(candidate["buy_range_low"]) and low <= float(candidate["buy_range_high"]))
        now = _now_iso()
        quote_at = _iso_datetime(
            quote_timestamp or f"{tracking_day}T15:00:00",
            "quote_timestamp",
            day=tracking_day,
        )
        values = (
            cid, tracking_day, quote_at, o, h, low, close, previous_close,
            pct_change, open_gap_pct, max_up_pct, max_drawdown_pct, min_return_pct,
            buy_touched, target, stop, int(target_hit), int(stop_hit), hit,
            target_hit_at, stop_hit_at, outcome, reason, source, 1,
            _json(raw_payload or {}, "raw_payload"), now, now,
        )
        conn.execute(
            """INSERT INTO next_day_tracking(
                   candidate_id, trade_date, quote_timestamp, open_price, high_price,
                   low_price, close_price, prior_close, pct_change, open_gap_pct,
                   max_up_pct, max_drawdown_pct, min_return_pct, buy_zone_touched,
                   target_price, stop_price, target_hit, stop_hit, first_hit,
                   target_hit_at, stop_hit_at, outcome, outcome_reason,
                   market_data_source, actual_data, raw_payload_json, created_at, updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_id, trade_date) DO UPDATE SET
                   quote_timestamp=excluded.quote_timestamp,
                   open_price=excluded.open_price, high_price=excluded.high_price,
                   low_price=excluded.low_price, close_price=excluded.close_price,
                   prior_close=excluded.prior_close, pct_change=excluded.pct_change,
                   open_gap_pct=excluded.open_gap_pct, max_up_pct=excluded.max_up_pct,
                   max_drawdown_pct=excluded.max_drawdown_pct,
                   min_return_pct=excluded.min_return_pct,
                   buy_zone_touched=excluded.buy_zone_touched,
                   target_price=excluded.target_price, stop_price=excluded.stop_price,
                   target_hit=excluded.target_hit, stop_hit=excluded.stop_hit,
                   first_hit=excluded.first_hit, target_hit_at=excluded.target_hit_at,
                   stop_hit_at=excluded.stop_hit_at, outcome=excluded.outcome,
                   outcome_reason=excluded.outcome_reason,
                   market_data_source=excluded.market_data_source, actual_data=1,
                   raw_payload_json=excluded.raw_payload_json,
                   updated_at=excluded.updated_at""",
            values,
        )
        row = conn.execute(
            """SELECT t.*, c.candidate_date, c.stock_code, c.stock_name,
                      c.sector_name, c.comprehensive_score
               FROM next_day_tracking t JOIN candidate_pool c ON c.id=t.candidate_id
               WHERE t.candidate_id=? AND t.trade_date=?""",
            (cid, tracking_day),
        ).fetchone()
    if refresh_reports:
        generate_winrate_reports(tracking_day, path)
    return _decode_json_fields(row)


# 语义清晰的兼容别名，主程序可以任选其一。
record_next_day_tracking = record_next_day_ohlc


def get_tracking_records(
    *,
    candidate_date: Any = None,
    trade_date: Any = None,
    stock_code: Any = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    path = _ensure(db_path)
    clauses = ["t.actual_data=1"]
    params: list[Any] = []
    if candidate_date is not None:
        clauses.append("c.candidate_date=?")
        params.append(_iso_date(candidate_date, "candidate_date"))
    if trade_date is not None:
        clauses.append("t.trade_date=?")
        params.append(_iso_date(trade_date, "trade_date"))
    if stock_code is not None:
        clauses.append("c.stock_code=?")
        params.append(_stock_code(stock_code))
    sql = f"""SELECT t.*, c.candidate_date, c.stock_code, c.stock_name,
                     c.sector_name, c.candidate_level, c.comprehensive_score,
                     c.market_regime, c.algorithm_version
              FROM next_day_tracking t JOIN candidate_pool c ON c.id=t.candidate_id
              WHERE {' AND '.join(clauses)}
              ORDER BY t.trade_date, c.stock_code"""
    with _connection(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_decode_json_fields(row) for row in rows]


def _score_band(value: float) -> str:
    if value >= 80:
        return "80+"
    if value >= 72:
        return "72-79.99"
    if value >= 65:
        return "65-71.99"
    return "<65"


def _report_row(
    report_as_of: str,
    dimension: str,
    group_key: str,
    rows: Sequence[Mapping[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    outcomes = [str(row["outcome"]) for row in rows]
    success = outcomes.count("SUCCESS")
    failure = outcomes.count("FAILURE")
    decisive = success + failure
    sample = len(rows)

    def average(field: str) -> float | None:
        values = [float(row[field]) for row in rows if row[field] is not None]
        return round(sum(values) / len(values), 6) if values else None

    return {
        "report_as_of": report_as_of,
        "dimension": dimension,
        "group_key": group_key,
        "sample_count": sample,
        "decisive_count": decisive,
        "success_count": success,
        "failure_count": failure,
        "ambiguous_count": outcomes.count("AMBIGUOUS"),
        "no_trigger_count": outcomes.count("NO_TRIGGER"),
        "unjudged_count": outcomes.count("UNJUDGED"),
        "win_rate_pct": round(success / decisive * 100, 4) if decisive else None,
        "target_hit_rate_pct": round(sum(int(row["target_hit"]) for row in rows) / sample * 100, 4) if sample else None,
        "stop_hit_rate_pct": round(sum(int(row["stop_hit"]) for row in rows) / sample * 100, 4) if sample else None,
        "average_close_return_pct": average("pct_change"),
        "average_max_up_pct": average("max_up_pct"),
        "average_max_drawdown_pct": average("max_drawdown_pct"),
        "data_start_date": min(str(row["trade_date"]) for row in rows),
        "data_end_date": max(str(row["trade_date"]) for row in rows),
        "generated_at": generated_at,
    }


def generate_winrate_reports(
    report_as_of: Any = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """仅基于 ``next_day_tracking.actual_data=1`` 生成并持久化真实胜率。

    没有真实跟踪记录时返回空列表，且不会制造 0 样本“报告”。分组包含总体、
    板块、候选等级、最终评分区间、最终状态、市场环境和算法版本。最终状态
    分组明确保留后续 CANCELLED/EXPIRED 的初版候选，避免幸存者偏差。
    """
    as_of = _iso_date(report_as_of or date.today(), "report_as_of")
    path = _ensure(db_path)
    with _LOCK, _connection(path) as conn, conn:
        source_rows = conn.execute(
            """SELECT t.trade_date, t.outcome, t.target_hit, t.stop_hit,
                      t.pct_change, t.max_up_pct, t.max_drawdown_pct,
                      c.sector_name, c.candidate_level, c.comprehensive_score,
                      COALESCE(c.market_regime, 'UNKNOWN') AS market_regime,
                      c.algorithm_version, c.status
               FROM next_day_tracking t
               JOIN candidate_pool c ON c.id=t.candidate_id
               WHERE t.actual_data=1 AND t.trade_date <= ?
               ORDER BY t.trade_date, t.id""",
            (as_of,),
        ).fetchall()
        # 重算同一截止日，确保报告永远与真实明细一致。
        conn.execute("DELETE FROM winrate_reports WHERE report_as_of=?", (as_of,))
        if not source_rows:
            return []
        groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in source_rows:
            groups[("OVERALL", "ALL")].append(row)
            groups[("SECTOR", str(row["sector_name"]))].append(row)
            groups[("CANDIDATE_LEVEL", str(row["candidate_level"]))].append(row)
            groups[("SCORE_BAND", _score_band(float(row["comprehensive_score"])))].append(row)
            groups[("FINAL_STATUS", str(row["status"]))].append(row)
            groups[("MARKET_REGIME", str(row["market_regime"]))].append(row)
            groups[("ALGORITHM_VERSION", str(row["algorithm_version"]))].append(row)

        generated_at = _now_iso()
        reports = [
            _report_row(as_of, dimension, group_key, rows, generated_at)
            for (dimension, group_key), rows in sorted(groups.items())
        ]
        columns = tuple(reports[0])
        conn.executemany(
            f"INSERT INTO winrate_reports ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [tuple(report[column] for column in columns) for report in reports],
        )
    return reports


def get_winrate_reports(
    report_as_of: Any = None,
    db_path: str | Path | None = None,
    *,
    dimension: str | None = None,
) -> list[dict[str, Any]]:
    """读取已生成的真实胜率报告；不隐式生成或补造数据。"""
    path = _ensure(db_path)
    with _connection(path) as conn:
        if report_as_of is None:
            row = conn.execute("SELECT MAX(report_as_of) AS day FROM winrate_reports").fetchone()
            as_of = row["day"] if row else None
            if not as_of:
                return []
        else:
            as_of = _iso_date(report_as_of, "report_as_of")
        sql = "SELECT * FROM winrate_reports WHERE report_as_of=?"
        params: list[Any] = [as_of]
        if dimension:
            sql += " AND dimension=?"
            params.append(str(dimension).upper())
        sql += " ORDER BY dimension, group_key"
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def database_status(db_path: str | Path | None = None) -> dict[str, Any]:
    """返回各闭环表真实行数，用于启动自检和运维监控。"""
    path = _ensure(db_path)
    tables = (
        "sector_forecasts",
        "candidate_pool",
        "score_components",
        "evening_revisions",
        "preopen_revisions",
        "next_day_tracking",
        "winrate_reports",
    )
    with _connection(path) as conn:
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "database": str(path),
        "schema_version": SCHEMA_VERSION,
        "integrity_check": integrity,
        "row_counts": counts,
    }


__all__ = [
    "ClosedLoopDataError",
    "DEFAULT_DB_PATH",
    "SCHEMA_VERSION",
    "initialize_database",
    "database_status",
    "upsert_sector_forecast",
    "get_sector_forecasts",
    "upsert_candidate",
    "upsert_candidates",
    "save_close_snapshot",
    "upsert_score_components",
    "get_candidates",
    "get_previous_trading_day_untracked_candidates",
    "get_all_untracked_candidates",
    "record_evening_revision",
    "record_preopen_revision",
    "record_next_day_ohlc",
    "record_next_day_tracking",
    "get_tracking_records",
    "generate_winrate_reports",
    "get_winrate_reports",
]
