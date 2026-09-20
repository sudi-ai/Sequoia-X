# -*- coding: utf-8 -*-
"""发现样本与交易信号样本的统一边界。

全市场 PIT 快照只服务赢家发现、Top20/Top50 召回和全市场排序；
真实 A/B/A+ 或 Primary Signal 及其结果只服务胜率、Meta、CPCV、
Conformal 和交易统计。任何普通市场观察都不得进入交易胜率样本。
"""
from __future__ import annotations

import sqlite3
import copy
import threading
import time
from contextlib import closing
from pathlib import Path

from config import DATA_DIR, META_LABEL

DISCOVERY_SAMPLE_DOMAIN = "FULL_MARKET_PIT"
SIGNAL_SAMPLE_DOMAIN = "TRADE_SIGNAL"
DISCOVERY_CHECKPOINTS = ("09:25", "09:35", "10:00", "11:00", "13:30", "14:30")
TRADE_SIGNAL_POOLS = ("A", "A+", "B", "A级", "A+级", "B级")
PRIMARY_SIGNAL_TYPES = (
    "PRIMARY_SIGNAL",
    "PRIMARY",
    "SHADOW_ENTRY_CONFIRMED",
    "CONFIRMED",
    "EXECUTION_SIGNAL",
    "A",
    "A+",
    "B",
)
DISCOVERY_DB = Path(DATA_DIR) / "p0_intraday_pit.db"
SIGNAL_DB = Path(DATA_DIR) / "live_pit.db"


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _sql_values(values) -> str:
    return ",".join("'" + str(value).replace("'", "''") + "'" for value in values)


def trade_signal_predicate(alias: str = "", columns: set[str] | None = None) -> str:
    """Return a conservative SQL predicate for genuine system trade signals."""
    prefix = f"{alias}." if alias else ""
    available = set(columns or ())
    clauses = []
    if "is_trade_signal" in available:
        clauses.append(f"COALESCE({prefix}is_trade_signal,0)=1")
    if "pool" in available:
        clauses.append(f"TRIM(UPPER(COALESCE({prefix}pool,''))) IN ({_sql_values(TRADE_SIGNAL_POOLS)})")
    if "signal_type" in available:
        clauses.append(f"TRIM(UPPER(COALESCE({prefix}signal_type,''))) IN ({_sql_values(PRIMARY_SIGNAL_TYPES)})")
    return "(" + " OR ".join(clauses) + ")" if clauses else "0=1"


def is_trade_signal_record(record: dict) -> bool:
    if bool(record.get("is_trade_signal")):
        return True
    pool = str(record.get("pool") or "").strip().upper()
    signal_type = str(record.get("signal_type") or "").strip().upper()
    return pool in {value.upper() for value in TRADE_SIGNAL_POOLS} or signal_type in set(PRIMARY_SIGNAL_TYPES)


def require_domain_name(actual: str | None, expected: str, operation: str) -> None:
    if str(actual or "").upper() != expected:
        raise ValueError(f"{operation} 样本域错误：必须显式提供 {expected}，禁止混用全市场样本与交易信号样本")


def _require_frame(frame, expected: str, operation: str):
    if frame is None or not hasattr(frame, "columns") or "sample_domain" not in frame.columns:
        raise ValueError(f"{operation} 缺少 sample_domain，拒绝使用来源不明的数据")
    domains = {str(value or "").upper() for value in frame["sample_domain"].tolist()}
    if domains != {expected}:
        raise ValueError(f"{operation} 检测到混合样本域：{sorted(domains)}，要求仅为 {expected}")
    if "is_pit_safe" not in frame.columns or not bool(frame["is_pit_safe"].fillna(False).astype(bool).all()):
        raise ValueError(f"{operation} 仅允许 PIT 安全样本")
    return frame


def require_discovery_training_frame(frame, operation: str = "赢家发现/全市场排序训练"):
    return _require_frame(frame, DISCOVERY_SAMPLE_DOMAIN, operation)


def require_trade_signal_training_frame(frame, operation: str = "交易胜率/Meta训练"):
    checked = _require_frame(frame, SIGNAL_SAMPLE_DOMAIN, operation)
    if "is_trade_signal" not in checked.columns or not bool(checked["is_trade_signal"].fillna(False).astype(bool).all()):
        raise ValueError(f"{operation} 包含非 A/B/A+/Primary Signal，已阻止训练")
    if not ({"signal_snapshot_id", "snapshot_id"} & set(checked.columns)):
        raise ValueError(f"{operation} 缺少真实信号快照标识，无法追溯")
    return checked


def _coalesce(alias: str, columns: set[str], names: tuple[str, ...]) -> str:
    found = [f"{alias}.{name}" for name in names if name in columns]
    if not found:
        return "NULL"
    return found[0] if len(found) == 1 else "COALESCE(" + ",".join(found) + ")"


def discovery_sample_status(path: Path = DISCOVERY_DB, budget_seconds=None) -> dict:
    result = {
        "domain": DISCOVERY_SAMPLE_DOMAIN,
        "purpose": "赢家发现、Top20/Top50召回、全市场强度排序",
        "snapshot_count": 0,
        "pit_snapshot_count": 0,
        "market_rows": 0,
        "pit_market_rows": 0,
        "trade_days": 0,
        "complete_trade_days": 0,
        "discovery_observations": 0,
        "recall_labeled_days": 0,
        "latest_trade_date": None,
        "checkpoints": {label: {"days": 0, "rows": 0} for label in DISCOVERY_CHECKPOINTS},
        "status": "尚未采集真实盘中PIT",
        "ready_for_alpha_rank": False,
    }
    if not Path(path).exists():
        return result
    try:
        with closing(sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True,
                                    timeout=0.1 if budget_seconds is not None else 5)) as conn:
            if budget_seconds is not None:
                deadline = time.monotonic() + max(0, budget_seconds)
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"snapshot_manifest", "intraday_quote", "discovery_observation"}.issubset(tables):
                return result
            result["snapshot_count"] = int(conn.execute("SELECT COUNT(*) FROM snapshot_manifest").fetchone()[0])
            result["pit_snapshot_count"] = int(conn.execute("SELECT COUNT(*) FROM snapshot_manifest WHERE is_pit_safe=1 AND status LIKE 'OK%'").fetchone()[0])
            result["market_rows"] = int(conn.execute("SELECT COUNT(*) FROM intraday_quote").fetchone()[0])
            result["pit_market_rows"] = int(conn.execute("SELECT COUNT(*) FROM intraday_quote WHERE is_pit_safe=1").fetchone()[0])
            result["trade_days"] = int(conn.execute("SELECT COUNT(DISTINCT trade_date) FROM snapshot_manifest WHERE is_pit_safe=1 AND status LIKE 'OK%'").fetchone()[0])
            result["latest_trade_date"] = conn.execute("SELECT MAX(trade_date) FROM snapshot_manifest WHERE is_pit_safe=1 AND status LIKE 'OK%'").fetchone()[0]
            result["complete_trade_days"] = int(conn.execute(
                "SELECT COUNT(*) FROM (SELECT trade_date FROM snapshot_manifest WHERE is_pit_safe=1 AND status LIKE 'OK%' "
                "AND mandatory_label IN ('09:25','09:35','10:00','11:00','13:30','14:30') GROUP BY trade_date "
                "HAVING COUNT(DISTINCT mandatory_label)=6)"
            ).fetchone()[0])
            result["discovery_observations"] = int(conn.execute("SELECT COUNT(*) FROM discovery_observation WHERE is_pit_safe=1").fetchone()[0])
            if "winner_recall_daily" in tables:
                result["recall_labeled_days"] = int(conn.execute(
                    "SELECT COUNT(*) FROM winner_recall_daily WHERE actual_top20=20 AND actual_top50=50 "
                    "AND (recall_0925 IS NOT NULL OR recall_0935 IS NOT NULL OR recall_1000 IS NOT NULL OR recall_1100 IS NOT NULL OR recall_1330 IS NOT NULL OR recall_1430 IS NOT NULL)"
                ).fetchone()[0])
            for label in DISCOVERY_CHECKPOINTS:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT trade_date),COALESCE(SUM(row_count),0) FROM snapshot_manifest "
                    "WHERE mandatory_label=? AND is_pit_safe=1 AND status LIKE 'OK%'",
                    (label,),
                ).fetchone()
                result["checkpoints"][label] = {"days": int(row[0] or 0), "rows": int(row[1] or 0)}
    except (sqlite3.Error, OSError) as exc:
        result["status"] = f"读取失败：{type(exc).__name__}"
        result["status_complete"] = False
        return result
    result["status_complete"] = True
    result["ready_for_alpha_rank"] = result["complete_trade_days"] > 0 and result["recall_labeled_days"] > 0
    if result["ready_for_alpha_rank"]:
        result["status"] = "已有完整交易日样本，可开始离线发现层验证"
    elif result["pit_market_rows"]:
        result["status"] = "正在积累，尚缺完整交易日或收盘赢家标签"
    elif result["market_rows"]:
        result["status"] = "仅有盘外测试数据，不计入真实PIT样本"
    return result


def trade_signal_sample_status(path: Path = SIGNAL_DB, budget_seconds=None) -> dict:
    minimum = int(META_LABEL.get("min_train_rows", 600))
    result = {
        "domain": SIGNAL_SAMPLE_DOMAIN,
        "purpose": "真实胜率、Meta、A+、CPCV、Conformal和交易统计",
        "all_records": 0,
        "trade_signals": 0,
        "excluded_non_signals": 0,
        "pit_safe_signals": 0,
        "paired": 0,
        "t1_labeled": 0,
        "t3_labeled": 0,
        "t5_labeled": 0,
        "triple_barrier_labeled": 0,
        "meta_eligible": 0,
        "min_meta_samples": minimum,
        "status": "真实交易信号样本不足",
        "statistics_ready": False,
    }
    if not Path(path).exists():
        return result
    try:
        with closing(sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True,
                                    timeout=0.1 if budget_seconds is not None else 5)) as conn:
            if budget_seconds is not None:
                deadline = time.monotonic() + max(0, budget_seconds)
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "signal_snapshot" not in tables:
                return result
            signal_columns = table_columns(conn, "signal_snapshot")
            predicate = trade_signal_predicate("s", signal_columns)
            result["all_records"] = int(conn.execute("SELECT COUNT(*) FROM signal_snapshot").fetchone()[0])
            result["trade_signals"] = int(conn.execute(f"SELECT COUNT(*) FROM signal_snapshot s WHERE {predicate}").fetchone()[0])
            result["excluded_non_signals"] = max(0, result["all_records"] - result["trade_signals"])
            result["pit_safe_signals"] = int(conn.execute(f"SELECT COUNT(*) FROM signal_snapshot s WHERE {predicate} AND COALESCE(s.is_pit_safe,0)=1").fetchone()[0])
            if "signal_outcome" in tables:
                outcome_columns = table_columns(conn, "signal_outcome")
                t1 = _coalesce("o", outcome_columns, ("t1_return", "t1"))
                t3 = _coalesce("o", outcome_columns, ("t3_return", "t3"))
                t5 = _coalesce("o", outcome_columns, ("t5_return", "t5"))
                triple = _coalesce("o", outcome_columns, ("triple_barrier_label",))
                join = "FROM signal_snapshot s JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id"
                result["paired"] = int(conn.execute(f"SELECT COUNT(DISTINCT s.snapshot_id) {join} WHERE {predicate}").fetchone()[0])
                for key, expression in (("t1_labeled", t1), ("t3_labeled", t3), ("t5_labeled", t5), ("triple_barrier_labeled", triple)):
                    result[key] = int(conn.execute(f"SELECT COUNT(DISTINCT s.snapshot_id) {join} WHERE {predicate} AND {expression} IS NOT NULL").fetchone()[0])
                result["meta_eligible"] = int(conn.execute(
                    f"SELECT COUNT(DISTINCT s.snapshot_id) {join} WHERE {predicate} AND COALESCE(s.is_pit_safe,0)=1 "
                    f"AND s.score IS NOT NULL AND {t1} IS NOT NULL"
                ).fetchone()[0])
    except (sqlite3.Error, OSError) as exc:
        result["status"] = f"读取失败：{type(exc).__name__}"
        result["status_complete"] = False
        return result
    result["status_complete"] = True
    result["statistics_ready"] = result["meta_eligible"] >= minimum
    if result["statistics_ready"]:
        result["status"] = "交易信号样本达到最低训练门槛，仍需样本外验收"
    return result


_status_lock = threading.Lock()
_status_cache = None
_status_expires = 0.0


def sample_domain_status(refresh=False) -> dict:
    """Bounded UI status. Full audit callers can use the individual functions."""
    global _status_cache, _status_expires
    if not refresh and _status_cache is not None and time.monotonic() < _status_expires:
        return copy.deepcopy(_status_cache)
    if not _status_lock.acquire(blocking=False):
        if _status_cache is not None:
            cached = copy.deepcopy(_status_cache)
            cached['discovery'].update(ready_for_alpha_rank=False, status='统计刷新中，暂不判断就绪状态')
            cached['trade_signal'].update(statistics_ready=False, status='统计刷新中，暂不判断就绪状态')
            return cached
        return {'discovery': {'ready_for_alpha_rank': False, 'status': '统计刷新中'},
                'trade_signal': {'meta_eligible': 0, 'paired': 0, 't1_labeled': 0,
                                 'statistics_ready': False, 'status': '统计刷新中'}}
    try:
        result = {
            'discovery': discovery_sample_status(budget_seconds=0.35),
            'trade_signal': trade_signal_sample_status(budget_seconds=0.35),
            'separation_rule': '全市场普通股票只能进入发现层；只有真实 A/B/A+/Primary Signal 才能进入胜率层',
        }
        complete = all(result[k].get('status_complete', True) for k in ('discovery','trade_signal'))
        result['status_complete'] = complete
        if not complete:
            for key in ('discovery','trade_signal'):
                if not result[key].get('status_complete', True):
                    result[key]['status'] = '统计暂不可用：查询超时、数据库忙或读取失败；部分计数不可作验收依据'
        _status_cache = copy.deepcopy(result)
        _status_expires = time.monotonic() + (60 if complete else 5)
        return result
    finally:
        _status_lock.release()
