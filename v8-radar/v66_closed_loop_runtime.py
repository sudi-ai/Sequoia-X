"""V6.6 Pro 次日选股闭环的运行时编排层。

本模块只负责把 :mod:`v66_nextday_engine` 的纯评分结果和
:mod:`v66_closed_loop_db` 的真实持久化接口串起来。它有三条明确边界：

* 不主动访问网络；行情、公告、新闻、竞价等数据只能由主程序显式传入，
  或通过 ``track_untracked_candidates`` 的回调提供。
* 不生成模拟候选、模拟价格或缺失风险证据。价格区间、目标位和防守位
  必须来自收盘截面中的 V6.4 ``deep.trade``，不完整时直接排除候选。
* 数据库提交状态与微信群推送状态分别记录。数据库成功不代表消息已发送，
  主程序应在 ``push_message`` 返回后另行调用 ``mark_stage_push``。

典型调用顺序::

    initialize(BASE_DIR)
    capture_market_snapshot(sector_rows, radar, sentiment, regime)
    close_result = run_close_selection()
    # ok = push_message(build_close_message(close_result))
    # mark_stage_push("15:05_CLOSE", close_result["candidate_date"], ok)

次日收盘后，主程序传入一个返回不复权日线的回调::

    track_untracked_candidates(fetch_raw_daily)

回调推荐签名为 ``callback(stock_code, candidate_date, as_of_date)``，返回值为
``{"rows": [...], "data_source": "腾讯不复权日线"}``。为便于接入现有代码，
单参数 ``callback(stock_code)`` 也受支持。
"""

from __future__ import annotations

import inspect
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
from copy import deepcopy
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import v66_closed_loop_db as closed_db
import v66_nextday_engine as nextday_engine


RUNTIME_VERSION = "V6.6-Pro-ClosedLoop-Runtime-1.0"
DEFAULT_DATA_DIR_NAME = "data_v66"
DEFAULT_DB_NAME = "v66_closed_loop.sqlite3"
SNAPSHOT_FILE_NAME = "v66_latest_market_snapshot.json"
JOB_STATE_FILE_NAME = "v66_closed_loop_job_state.json"

STAGE_CLOSE = "15:05_CLOSE"
STAGE_EVENING = "19:30_EVENING"
STAGE_PREOPEN_0900 = "PREOPEN_09:00"
STAGE_PREOPEN_0925 = "PREOPEN_09:25"
STAGE_TRACKING = "NEXT_DAY_TRACKING"

_FORBIDDEN_SOURCE_WORDS = ("mock", "fake", "simulat", "测试", "模拟", "虚构")
_LOCK = threading.RLock()
_BASE_DIR = Path(__file__).resolve().parent
_DATA_DIR = _BASE_DIR / DEFAULT_DATA_DIR_NAME
_DB_PATH = _DATA_DIR / DEFAULT_DB_NAME
_SNAPSHOT_PATH = _DATA_DIR / SNAPSHOT_FILE_NAME
_JOB_STATE_PATH = _DATA_DIR / JOB_STATE_FILE_NAME


class ClosedLoopRuntimeError(RuntimeError):
    """运行时编排失败，且没有把不完整结果伪装成成功。"""


def _now() -> datetime:
    # 主程序使用 datetime.now()（本地 naive）；运行时统一成同一口径，避免
    # Windows 下 aware/naive 混合比较或序列化后日期漂移。
    return datetime.now()


def _local_naive(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone().replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _parse_datetime(value: Any, field: str, *, default_now: bool = False) -> datetime:
    if value is None and default_now:
        return _now()
    if isinstance(value, datetime):
        return _local_naive(value)
    text = str(value or "").strip()
    if not text:
        raise ClosedLoopRuntimeError(f"{field} 不能为空")
    try:
        return _local_naive(datetime.fromisoformat(text.replace(" ", "T", 1)))
    except ValueError as exc:
        raise ClosedLoopRuntimeError(f"{field} 必须是 ISO 日期时间：{value!r}") from exc


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return _local_naive(value).date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError as exc:
        raise ClosedLoopRuntimeError(f"{field} 必须是 YYYY-MM-DD：{value!r}") from exc


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_number(source: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key in source:
            value = _number(source.get(key))
            if value is not None:
                return value
    return None


def _code(value: Any) -> str:
    text = str(value or "").strip()
    found = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if found:
        return found.group(1)
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return ""


def _json_safe(value: Any) -> Any:
    """递归转换常见 numpy/pandas 值，同时拒绝非有限浮点数。"""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    text = str(value)
    return None if text in {"nan", "NaN", "NaT", "<NA>"} else text


def _rows(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict") and not isinstance(value, Mapping):
        try:
            value = value.to_dict("records")
        except TypeError:
            value = value.to_dict()
    if isinstance(value, Mapping):
        if all(isinstance(item, Mapping) for item in value.values()):
            value = list(value.values())
        else:
            value = [value]
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ClosedLoopRuntimeError(f"{field} 必须是字典行列表")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ClosedLoopRuntimeError(f"{field} 中存在非字典行")
        result.append(dict(_json_safe(item)))
    return result


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_json_safe(value), ensure_ascii=False, indent=2, sort_keys=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return deepcopy(default)


def _actual_source(value: Any, fallback: Optional[str] = None) -> str:
    raw = value or fallback or ""
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        source = ", ".join(str(item).strip() for item in raw if str(item).strip())
    else:
        source = str(raw).strip()
    if not source:
        raise ClosedLoopRuntimeError("真实 data_source 不能为空")
    lowered = source.casefold()
    if any(word.casefold() in lowered for word in _FORBIDDEN_SOURCE_WORDS):
        raise ClosedLoopRuntimeError(f"禁止把模拟/测试来源写入真实闭环：{source}")
    return source


def initialize(base_dir: Any = None) -> dict[str, Any]:
    """初始化运行目录、独立 SQLite 库与空 job state。

    本函数只创建目录/表/状态文件，不写任何候选、行情或胜率样本。传入
    ``base_dir`` 时，数据库位于 ``base_dir/data_v66``，便于正式版、临时
    自测和未来升级彼此隔离。
    """
    global _BASE_DIR, _DATA_DIR, _DB_PATH, _SNAPSHOT_PATH, _JOB_STATE_PATH
    with _LOCK:
        _BASE_DIR = Path(base_dir).expanduser().resolve() if base_dir else Path(__file__).resolve().parent
        _DATA_DIR = _BASE_DIR / DEFAULT_DATA_DIR_NAME
        _DB_PATH = _DATA_DIR / DEFAULT_DB_NAME
        _SNAPSHOT_PATH = _DATA_DIR / SNAPSHOT_FILE_NAME
        _JOB_STATE_PATH = _DATA_DIR / JOB_STATE_FILE_NAME
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        closed_db.initialize_database(_DB_PATH)
        if not _JOB_STATE_PATH.exists():
            _atomic_write_json(
                _JOB_STATE_PATH,
                {"runtime_version": RUNTIME_VERSION, "updated_at": _now().isoformat(timespec="seconds"), "jobs": {}},
            )
        status = closed_db.database_status(_DB_PATH)
        return {
            "base_dir": str(_BASE_DIR),
            "data_dir": str(_DATA_DIR),
            "database": str(_DB_PATH),
            "snapshot_file": str(_SNAPSHOT_PATH),
            "job_state_file": str(_JOB_STATE_PATH),
            "database_status": status,
        }


def _job_key(stage: str, business_date: Any) -> str:
    day = _parse_date(business_date, "business_date").isoformat()
    normalized = str(stage or "").strip().upper()
    if not normalized:
        raise ClosedLoopRuntimeError("stage 不能为空")
    return f"{day}|{normalized}"


def get_job_state(stage: Any = None, business_date: Any = None) -> dict[str, Any]:
    """读取持久 job state；同时传 stage/date 时只返回该阶段状态。"""
    with _LOCK:
        state = _read_json(_JOB_STATE_PATH, {"runtime_version": RUNTIME_VERSION, "jobs": {}})
    if stage is None and business_date is None:
        return state
    if stage is None or business_date is None:
        raise ClosedLoopRuntimeError("查询单个任务时 stage 和 business_date 必须同时提供")
    return dict(state.get("jobs", {}).get(_job_key(str(stage), business_date), {}))


def _update_job(stage: str, business_date: Any, fields: Mapping[str, Any]) -> dict[str, Any]:
    key = _job_key(stage, business_date)
    with _LOCK:
        state = _read_json(_JOB_STATE_PATH, {"runtime_version": RUNTIME_VERSION, "jobs": {}})
        state.setdefault("jobs", {})
        entry = dict(state["jobs"].get(key, {}))
        entry.update(_json_safe(fields))
        entry["stage"] = str(stage)
        entry["business_date"] = _parse_date(business_date, "business_date").isoformat()
        entry["updated_at"] = _now().isoformat(timespec="seconds")
        state["jobs"][key] = entry
        state["runtime_version"] = RUNTIME_VERSION
        state["updated_at"] = entry["updated_at"]
        _atomic_write_json(_JOB_STATE_PATH, state)
    return entry


def is_stage_db_committed(stage: str, business_date: Any) -> bool:
    """数据库阶段是否已成功提交；与消息是否推送无关。"""
    return bool(get_job_state(stage, business_date).get("db_committed"))


def mark_stage_db_committed(
    stage: str, business_date: Any, committed: bool = True, details: Any = None
) -> dict[str, Any]:
    """记录数据库提交结果，不修改 ``push_sent``。"""
    return _update_job(
        stage,
        business_date,
        {
            "db_committed": bool(committed),
            "db_committed_at": _now().isoformat(timespec="seconds") if committed else None,
            "db_details": _json_safe(details or {}),
        },
    )


def mark_stage_push(
    stage: str, business_date: Any, sent: bool, details: Any = None
) -> dict[str, Any]:
    """单独记录微信群推送结果；绝不反向修改数据库提交状态。"""
    current = get_job_state(stage, business_date)
    attempts = int(current.get("push_attempts", 0)) + 1
    return _update_job(
        stage,
        business_date,
        {
            "push_sent": bool(sent),
            "push_attempts": attempts,
            "push_last_at": _now().isoformat(timespec="seconds"),
            "push_details": _json_safe(details or {}),
        },
    )


def capture_market_snapshot(
    sector_rows: Any,
    radar: Any,
    market_sentiment: Any,
    market_regime: Any,
    captured_at: Any = None,
) -> dict[str, Any]:
    """原子保存当天最新真实盘中截面，不运行选股也不写业务数据库。

    ``radar`` 中每个板块的 ``_v66_raw_candidates`` 会原样保留；其中每只
    股票的 ``_v64`` 深度数据（含 ``trade``）也会完整保留。若主程序没有
    提供 ``_v66_raw_candidates``，本函数不会把已过滤的 ``candidates``
    偷偷冒充原始候选，而会将该板块标记为 ``raw_candidates_missing``。
    """
    stamp = _parse_datetime(captured_at, "captured_at", default_now=True)
    sectors = _rows(sector_rows, "sector_rows")
    radar_rows = _rows(radar, "radar")
    normalized_radar: list[dict[str, Any]] = []
    for item in radar_rows:
        copied = deepcopy(item)
        raw = copied.get("_v66_raw_candidates")
        if raw is None:
            copied["_v66_raw_candidates"] = []
            copied["raw_candidates_missing"] = True
        elif not isinstance(raw, list):
            copied["_v66_raw_candidates"] = _rows(raw, "_v66_raw_candidates")
        else:
            copied["_v66_raw_candidates"] = _rows(raw, "_v66_raw_candidates")
        for candidate in copied["_v66_raw_candidates"]:
            if "_v64" not in candidate:
                candidate["_v64_missing"] = True
        normalized_radar.append(copied)
    snapshot = {
        "schema": "v66_market_snapshot/1",
        "runtime_version": RUNTIME_VERSION,
        "captured_at": stamp.isoformat(timespec="seconds"),
        "capture_date": stamp.date().isoformat(),
        "market_regime": str(market_regime or "").strip(),
        "market_sentiment": _json_safe(market_sentiment),
        "sector_rows": sectors,
        "radar": normalized_radar,
        "uses_network": False,
        "uses_simulated_data": False,
    }
    with _LOCK:
        _atomic_write_json(_SNAPSHOT_PATH, snapshot)
    return {
        "snapshot_file": str(_SNAPSHOT_PATH),
        "captured_at": snapshot["captured_at"],
        "capture_date": snapshot["capture_date"],
        "sector_count": len(sectors),
        "radar_sector_count": len(normalized_radar),
        "raw_candidate_count": sum(len(item["_v66_raw_candidates"]) for item in normalized_radar),
    }


def _sector_name(row: Mapping[str, Any]) -> str:
    return str(
        row.get("sector") or row.get("板块") or row.get("板块名称") or row.get("行业") or ""
    ).strip()


def _extract_candidates(snapshot: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    deep_by_code_and_sector: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("radar", []):
        if not isinstance(item, Mapping):
            continue
        sector = _sector_name(item)
        for raw in item.get("_v66_raw_candidates", []):
            if not isinstance(raw, Mapping):
                continue
            candidate = dict(raw)
            candidate["sector"] = sector or _sector_name(candidate)
            code = _code(candidate.get("code") or candidate.get("股票代码"))
            if not code:
                continue
            candidate["code"] = code
            candidates.append(candidate)
            deep = candidate.get("_v64")
            if isinstance(deep, Mapping):
                # Engine 当前按 code 查 deep。重复 code 的最终选择在评分后进行；
                # 先为每个 code+sector 保留，以便逐行评分时不串板块。
                deep_by_code_and_sector[f"{code}|{candidate['sector']}"] = dict(deep)
    return candidates, deep_by_code_and_sector


def _run_engine_per_candidate(
    sector_rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    deep_lookup: Mapping[str, Mapping[str, Any]],
    market_sentiment: Any,
    historical_sector_records: Any = None,
) -> dict[str, Any]:
    forecasts = nextday_engine.forecast_sectors(sector_rows, historical_sector_records=historical_sector_records)
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        code = _code(candidate.get("code"))
        sector = _sector_name(candidate)
        # 优先使用候选自身截面里的 deep，避免同代码同板块重复行互相覆盖。
        own_deep = candidate.get("_v64")
        deep = dict(own_deep) if isinstance(own_deep, Mapping) else dict(deep_lookup.get(f"{code}|{sector}", {}))
        tech = deep.get("tech") if isinstance(deep.get("tech"), Mapping) else {}
        for field in (
            "change_5d", "change_10d", "change_20d", "deviation_ma20",
            "recent_limit_up_count", "profit_ratio", "volume_ratio",
        ):
            if field not in deep and tech.get(field) is not None:
                deep[field] = tech.get(field)
        force = deep.get("force") if isinstance(deep.get("force"), Mapping) else {}
        if deep.get("force_score") is None and force.get("score") is not None:
            deep["force_score"] = force.get("score")
        trade = deep.get("trade") if isinstance(deep.get("trade"), Mapping) else {}
        if deep.get("rr") is None and trade.get("rr") is not None:
            deep["rr"] = trade.get("rr")
        risk_value = candidate.get("_v66_risk") or candidate.get("risk_context") or candidate.get("regulatory_risk")
        risk_context = dict(risk_value) if isinstance(risk_value, Mapping) else {}
        forecast = next((item for item in forecasts if _sector_name(item) == sector), None)
        scored.append(
            nextday_engine.score_candidate(
                candidate,
                deep_metrics=deep,
                sector_forecast=forecast,
                market_sentiment=market_sentiment,
                risk_context=risk_context,
            )
        )
    scored.sort(
        key=lambda item: (
            not bool(item.get("hard_veto")), bool(item.get("eligible")),
            float(item.get("final_score", -1)), float(item.get("confidence", -1)),
        ),
        reverse=True,
    )
    return {
        "engine_version": nextday_engine.ENGINE_VERSION,
        "sector_forecasts": forecasts,
        "candidate_scores": scored,
        "formula_weights": dict(nextday_engine.CANDIDATE_COMPONENT_WEIGHTS),
        "sector_factor_weights": dict(nextday_engine.SECTOR_FACTOR_WEIGHTS),
    }


def _load_real_sector_history(before_day: str) -> list[dict[str, Any]]:
    """从闭环库读取此前真实板块收盘强弱，供历史持续性因子使用。

    数据库目前没有板块指数次日 OHLC，因此这里只提供真实的历史每日涨跌幅；
    引擎会据此计算连续活跃度，但不会冒充校准后的“上涨概率”。
    """
    if not _DB_PATH.exists():
        return []
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT forecast_date AS date, sector_name AS sector,
                      today_change_pct AS pct,
                      continuation_score AS forecast_score,
                      evidence_json
               FROM sector_forecasts
               WHERE forecast_date < ? AND today_change_pct IS NOT NULL
               ORDER BY forecast_date, sector_name""",
            (before_day,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_evidence = item.pop("evidence_json", None)
            try:
                evidence = json.loads(raw_evidence) if raw_evidence else {}
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            actual = evidence.get("source_row_actual") if isinstance(evidence, Mapping) else {}
            if isinstance(actual, Mapping):
                for key in ("amount", "up", "down", "breadth", "leader_pct", "limit_up_count"):
                    if actual.get(key) is not None:
                        item[key] = actual.get(key)
            result.append(item)
        return result
    except sqlite3.Error:
        # 历史读取失败属于缺失证据；保持 None/低置信，不注入替代值。
        return []
    finally:
        if conn is not None:
            conn.close()


def _source_sector_row(sector_rows: Sequence[Mapping[str, Any]], sector: str) -> dict[str, Any]:
    return next((dict(row) for row in sector_rows if _sector_name(row) == sector), {})


def _map_sector_forecast(
    forecast: Mapping[str, Any], source_row: Mapping[str, Any], forecast_day: str
) -> dict[str, Any]:
    components = dict(forecast.get("components") or {})

    def score(name: str) -> Optional[float]:
        item = components.get(name) or {}
        value = _number(item.get("score")) if isinstance(item, Mapping) else None
        # 缺失必须保持 NULL；不能用 0 或中性分冒充已有证据。
        return value

    amount_change = _first_number(
        source_row, "amount_change_pct", "turnover_amount_change_pct", "成交金额变化率", "成交额变化率"
    )
    if amount_change is None:
        amount_component = components.get("amount_change") or {}
        if isinstance(amount_component, Mapping):
            amount_change = _number(amount_component.get("value"))

    evidence = {
        "confidence": forecast.get("confidence"),
        "status": forecast.get("status"),
        "components": components,
        "missing_fields": forecast.get("missing_fields", []),
        "available_factor_weight": forecast.get("available_factor_weight"),
        "score_label": forecast.get("score_label"),
        "calibrated_probability": forecast.get("calibrated_probability"),
        "calibration_samples": forecast.get("calibration_samples"),
        # 保存当天真实收盘原始量，供下一交易日与昨日比较；不使用同日
        # 盘中快照冒充昨日成交额。
        "source_row_actual": {
            "amount": _first_number(source_row, "amount", "总成交额", "turnover_amount", "成交金额"),
            "up": _first_number(source_row, "up", "上涨家数", "up_count"),
            "down": _first_number(source_row, "down", "下跌家数", "down_count"),
            "breadth": _first_number(source_row, "breadth", "上涨下跌比", "up_down_ratio"),
            "leader_pct": _first_number(source_row, "leader_pct", "领涨股-涨跌幅", "龙头股涨跌幅"),
            "limit_up_count": _first_number(source_row, "limit_up_count", "涨停家数", "涨停数量"),
        },
    }
    return {
        "forecast_date": forecast_day,
        "target_trade_date": None,
        "sector_code": str(source_row.get("sector_code") or source_row.get("板块代码") or "").strip(),
        "sector_name": _sector_name(forecast),
        "forecast_rank": forecast.get("rank"),
        "lifecycle_stage": forecast.get("lifecycle"),
        "today_change_pct": _first_number(source_row, "pct", "涨跌幅", "today_change_pct"),
        "turnover_change_pct": amount_change,
        "net_fund_inflow": _first_number(source_row, "net", "净流入", "net_inflow", "capital_flow"),
        "limit_up_count": _first_number(source_row, "limit_up_count", "涨停家数", "涨停数量"),
        "leader_stock_code": _code(source_row.get("leader_code") or source_row.get("领涨股代码")) or None,
        "leader_stock_name": str(source_row.get("leader") or source_row.get("领涨股") or "").strip() or None,
        "today_change_score": score("today_change"),
        "turnover_change_score": score("amount_change"),
        "fund_flow_score": score("capital_flow"),
        "limit_up_score": score("limit_up_count"),
        "leader_strength_score": score("leader_strength"),
        "breadth_score": score("diffusion"),
        "persistence_score": score("historical_persistence"),
        "continuation_score": forecast.get("score"),
        "weights": dict(nextday_engine.SECTOR_FACTOR_WEIGHTS),
        "evidence": evidence,
        "data_source": "主程序传入的同花顺行业板块真实收盘截面",
        "algorithm_version": nextday_engine.ENGINE_VERSION,
    }


def _parse_buy_zone(value: Any) -> Optional[tuple[float, float, str]]:
    text = str(value or "").strip()
    numbers = [_number(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    valid = [item for item in numbers if item is not None and item > 0]
    if len(valid) < 2:
        return None
    low, high = float(valid[0]), float(valid[1])
    if low > high:
        low, high = high, low
    return low, high, text


def _score_components(scored: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, component in (scored.get("score_components") or {}).items():
        if not isinstance(component, Mapping):
            continue
        rows.append({
            "stage": STAGE_CLOSE,
            "component_name": str(name),
            "component_type": "WEIGHTED",
            "raw_value": component.get("score"),
            "normalized_score": component.get("score"),
            "weight": component.get("weight"),
            "contribution": component.get("contribution"),
            "evidence": {
                "missing": component.get("missing"),
                "source": component.get("source"),
                "reliability": component.get("reliability"),
                "detail": component.get("evidence"),
            },
            "data_source": "V6.6次日引擎对真实收盘截面的规则计算",
        })
    for name, penalty in (scored.get("risk_components") or {}).items():
        value = _number(penalty) or 0.0
        rows.append({
            "stage": STAGE_CLOSE,
            "component_name": f"risk_{name}",
            "component_type": "DEDUCTION",
            "raw_value": value,
            "contribution": -value,
            "evidence": (scored.get("risk_details") or {}).get(name, {}),
            "data_source": "V6.6风险模块对真实收盘证据的规则计算",
        })
    rows.append({
        "stage": STAGE_CLOSE,
        "component_name": "hard_filter",
        "component_type": "FILTER",
        "hard_filter": True,
        "passed": not bool(scored.get("hard_veto")),
        "evidence": {"reasons": scored.get("hard_veto_reasons", [])},
        "data_source": "V6.6硬风险过滤规则",
    })
    return rows


def _candidate_to_db(
    scored: Mapping[str, Any], candidate_day: str, selected_at: str, market_regime: str, rank: int
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    code = _code(scored.get("code"))
    name = str(scored.get("name") or "").strip()
    sector = _sector_name(scored)
    deep = scored.get("_v64") if isinstance(scored.get("_v64"), Mapping) else {}
    trade = deep.get("trade") if isinstance(deep.get("trade"), Mapping) else {}
    reference = _number(scored.get("price"))
    zone = _parse_buy_zone(trade.get("zone"))
    target = _number(trade.get("target1"))
    stop = _number(trade.get("defense"))
    if not code or not name or not sector:
        return None, "代码、名称或板块不完整"
    if reference is None or reference <= 0:
        return None, "真实收盘参考价缺失或无效"
    if zone is None:
        return None, "deep.trade 买入区间缺失或无法解析"
    if target is None or target <= reference:
        return None, "deep.trade 目标价缺失或不高于参考价"
    if stop is None or stop >= reference or stop <= 0:
        return None, "deep.trade 防守位缺失或不低于参考价"
    low, high, zone_text = zone
    if high <= 0 or low <= 0:
        return None, "deep.trade 买入区间价格无效"
    score_components = scored.get("score_components") or {}
    individual = _number((score_components.get("individual") or {}).get("score"))
    trend = _number((score_components.get("trend") or {}).get("score"))
    chip = _number((score_components.get("chip_proxy") or {}).get("score"))
    sector_score = _number((score_components.get("sector_forecast") or {}).get("score"))
    sentiment = _number((score_components.get("market_sentiment") or {}).get("score"))
    if None in {individual, trend, chip, sector_score, sentiment}:
        return None, "最终评分必需的个股/趋势/筹码/板块/情绪分不完整"
    risks = dict(scored.get("risk_components") or {})
    grade = str(scored.get("grade") or "")
    level = {"重点候选": "KEY", "普通候选": "NORMAL", "观察池": "WATCH"}.get(grade, "NORMAL")
    status = "WATCH" if level == "WATCH" else "CANDIDATE"
    reason_parts = [
        f"板块{sector}次日续强分{float(sector_score):.1f}",
        f"综合分{float(scored.get('final_score', 0)):.1f}",
        f"置信度{float(scored.get('confidence', 0)):.1f}",
    ]
    if deep.get("confirmed") is True:
        reason_parts.append("V6.5深度确认通过")
    return {
        "candidate_date": candidate_day,
        "stock_code": code,
        "stock_name": name,
        "sector_name": sector,
        "selected_at": selected_at,
        "selection_stage": STAGE_CLOSE,
        "selection_reason": "；".join(reason_parts),
        "base_score": scored.get("base_score"),
        "comprehensive_score": scored.get("final_score"),
        "market_sentiment_score": sentiment,
        "sector_score": sector_score,
        "stock_score": individual,
        "trend_score": trend,
        "chip_score": chip,
        "risk_score": scored.get("risk_score"),
        "risk_penalty": scored.get("risk_penalty", 0),
        "regulatory_risk_penalty": risks.get("regulatory", 0),
        "high_position_risk_penalty": risks.get("high_position", 0),
        "liquidity_risk_penalty": risks.get("liquidity", 0),
        "negative_news_penalty": float(risks.get("negative_news", 0) or 0) + float(risks.get("announcement", 0) or 0),
        "reward_risk_penalty": risks.get("risk_reward", 0),
        "other_risk_penalty": 0,
        "hard_filter_passed": True,
        "hard_filter_reasons": [],
        "buy_range_low": low,
        "buy_range_high": high,
        "buy_range_text": zone_text,
        "reference_price": reference,
        "target_price": target,
        "stop_price": stop,
        "candidate_level": level,
        "candidate_rank": rank,
        "market_regime": market_regime,
        "status": status,
        "algorithm_version": nextday_engine.ENGINE_VERSION,
        "data_source": "同花顺行业与量价齐升、腾讯实际行情、V6.4深度指标",
        "extra": {
            "confidence": scored.get("confidence"),
            "grade": grade,
            "formula": scored.get("formula"),
            "risk_reasons": scored.get("risk_reasons", []),
            "missing_score_components": scored.get("missing_score_components", []),
            "trade": trade,
        },
        "score_components": _score_components(scored),
    }, None


def _existing_close_result(candidate_day: str) -> dict[str, Any]:
    """从数据库重建不可变的 15:05 初版结果，供微信失败后可靠补推。

    19:30 和盘前修订会更新 ``candidate_pool`` 当前分数、排名和状态。初版
    分数仍可由不可变的 ``base_score - risk_penalty`` 恢复；初版状态则由
    ``candidate_level`` 恢复。这样补推不会误把晚间结果包装成 15:05 初版。
    """
    forecasts = closed_db.get_sector_forecasts(candidate_day, _DB_PATH)
    current_candidates = closed_db.get_candidates(candidate_day, _DB_PATH)
    state = get_job_state(STAGE_CLOSE, candidate_day)
    expected = state.get("db_details") if isinstance(state.get("db_details"), Mapping) else {}
    expected_forecasts = expected.get("forecast_count")
    expected_candidates = expected.get("candidate_count")
    if not forecasts:
        raise ClosedLoopRuntimeError("15:05状态显示已提交，但数据库没有板块预测；拒绝空结果补推")
    if expected_forecasts is not None and len(forecasts) != int(expected_forecasts):
        raise ClosedLoopRuntimeError(
            f"15:05板块预测数量与提交状态不一致：数据库{len(forecasts)}，状态{expected_forecasts}"
        )
    if expected_candidates is not None and len(current_candidates) != int(expected_candidates):
        raise ClosedLoopRuntimeError(
            f"15:05候选数量与提交状态不一致：数据库{len(current_candidates)}，状态{expected_candidates}"
        )
    candidates: list[dict[str, Any]] = []
    for row in current_candidates:
        item = dict(row)
        base_score = _number(item.get("base_score"))
        risk_penalty = _number(item.get("risk_penalty"))
        if base_score is not None and risk_penalty is not None:
            item["comprehensive_score"] = round(
                max(0.0, min(100.0, base_score - risk_penalty)), 1
            )
        level = str(item.get("candidate_level") or "NORMAL").strip().upper()
        item["status"] = "WATCH" if level == "WATCH" else "CANDIDATE"
        extra = item.get("extra") if isinstance(item.get("extra"), Mapping) else {}
        item["_initial_confidence"] = _number(extra.get("confidence")) or 0.0
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            float(item.get("comprehensive_score") or 0.0),
            float(item.get("_initial_confidence") or 0.0),
            -int(item.get("id") or 0),
        ),
        reverse=True,
    )
    for rank, item in enumerate(candidates, 1):
        item["candidate_rank"] = rank
        item.pop("_initial_confidence", None)
    return {
        "ok": True,
        "idempotent": True,
        "candidate_date": candidate_day,
        "sector_forecasts": forecasts,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "message": "该日期收盘闭环已提交数据库，未重复计算。",
    }


def run_close_selection(now: Any = None) -> dict[str, Any]:
    """读取 14:55 后真实截面，执行次日板块预测和最终候选入库。

    同一股票跨多个板块出现时，先按 ``final_score``、``confidence`` 选出
    唯一记录，再校验 deep.trade；不会依赖 SQLite upsert 静默覆盖。价格不
    完整的记录进入返回值 ``excluded_candidates``，绝不写入候选池。
    """
    stamp = _parse_datetime(now, "now", default_now=True)
    # 数据库已提交时，补推不再依赖可能已经丢失/损坏的行情截面。
    # 首次计算仍会在下方严格校验 14:55 后真实截面。
    requested_day = stamp.date().isoformat()
    if is_stage_db_committed(STAGE_CLOSE, requested_day):
        return _existing_close_result(requested_day)
    snapshot = _read_json(_SNAPSHOT_PATH, None)
    if not isinstance(snapshot, Mapping):
        raise ClosedLoopRuntimeError("尚无可用的真实市场截面")
    captured = _parse_datetime(snapshot.get("captured_at"), "snapshot.captured_at")
    if captured.date() != stamp.date():
        raise ClosedLoopRuntimeError(
            f"截面不是当日数据：截面 {captured.date()}，当前 {stamp.date()}"
        )
    if captured.time() < clock_time(14, 55):
        raise ClosedLoopRuntimeError(f"只接受当日14:55后截面，当前截面时间 {captured:%H:%M:%S}")
    candidate_day = captured.date().isoformat()
    sector_rows = _rows(snapshot.get("sector_rows", []), "snapshot.sector_rows")
    if not sector_rows:
        raise ClosedLoopRuntimeError("收盘截面没有真实板块数据，不能运行次日预测")
    raw_candidates, deep_lookup = _extract_candidates(snapshot)
    engine_result = _run_engine_per_candidate(
        sector_rows,
        raw_candidates,
        deep_lookup,
        snapshot.get("market_sentiment"),
        historical_sector_records=_load_real_sector_history(candidate_day),
    )
    db_forecasts = [
        _map_sector_forecast(forecast, _source_sector_row(sector_rows, _sector_name(forecast)), candidate_day)
        for forecast in engine_result["sector_forecasts"]
    ]

    # 先评分后按股票代码显式去重。相同代码只保留分数/置信度更高的一条。
    best_by_code: dict[str, dict[str, Any]] = {}
    duplicate_drops: list[dict[str, Any]] = []
    for scored in engine_result["candidate_scores"]:
        code = _code(scored.get("code"))
        if not code:
            continue
        current = best_by_code.get(code)
        key = (float(scored.get("final_score", -1)), float(scored.get("confidence", -1)))
        current_key = (
            float(current.get("final_score", -1)), float(current.get("confidence", -1))
        ) if current else (-math.inf, -math.inf)
        if current is None or key > current_key:
            if current is not None:
                duplicate_drops.append({"code": code, "sector": _sector_name(current), "reason": "同代码较低分/置信度记录"})
            best_by_code[code] = dict(scored)
        else:
            duplicate_drops.append({"code": code, "sector": _sector_name(scored), "reason": "同代码较低分/置信度记录"})

    unique_scored = sorted(
        best_by_code.values(),
        key=lambda item: (float(item.get("final_score", -1)), float(item.get("confidence", -1))),
        reverse=True,
    )
    db_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for scored in unique_scored:
        code = _code(scored.get("code"))
        if not bool(scored.get("eligible")):
            excluded.append({
                "code": code, "name": scored.get("name"), "sector": _sector_name(scored),
                "reason": "；".join(scored.get("hard_veto_reasons") or [str(scored.get("grade") or "不入池")]),
                "final_score": scored.get("final_score"), "confidence": scored.get("confidence"),
            })
            continue
        mapped, reason = _candidate_to_db(
            scored, candidate_day, captured.isoformat(timespec="seconds"),
            str(snapshot.get("market_regime") or ""), len(db_candidates) + 1,
        )
        if mapped is None:
            excluded.append({
                "code": code, "name": scored.get("name"), "sector": _sector_name(scored),
                "reason": reason, "final_score": scored.get("final_score"),
                "confidence": scored.get("confidence"),
            })
        else:
            db_candidates.append(mapped)

    # 严格重排连续名次；价格校验排除后不会留下名次空洞。
    for rank, candidate in enumerate(db_candidates, 1):
        candidate["candidate_rank"] = rank

    if db_candidates:
        saved = closed_db.save_close_snapshot(db_forecasts, db_candidates, _DB_PATH)
    else:
        # 没有合格候选时绝不造票；板块预测仍可逐条保存，便于积累真实历史。
        forecast_ids = [closed_db.upsert_sector_forecast(row, _DB_PATH) for row in db_forecasts]
        saved = {"candidate_date": candidate_day, "sector_forecast_ids": forecast_ids, "candidate_ids": [], "database": str(_DB_PATH)}
    mark_stage_db_committed(
        STAGE_CLOSE,
        candidate_day,
        True,
        {"forecast_count": len(db_forecasts), "candidate_count": len(db_candidates), "snapshot_at": snapshot.get("captured_at")},
    )
    return {
        "ok": True,
        "idempotent": False,
        "candidate_date": candidate_day,
        "captured_at": snapshot.get("captured_at"),
        "engine_version": engine_result["engine_version"],
        "formula_weights": engine_result["formula_weights"],
        "sector_factor_weights": engine_result["sector_factor_weights"],
        "sector_forecasts": db_forecasts,
        "candidates": db_candidates,
        "candidate_count": len(db_candidates),
        "excluded_candidates": excluded,
        "duplicate_drops": duplicate_drops,
        "database_commit": saved,
    }


def _callback_result(fetch_raw_daily: Callable[..., Any], code: str, candidate_day: str, as_of_day: str) -> Any:
    try:
        signature = inspect.signature(fetch_raw_daily)
        positional = [
            p for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and p.default is p.empty
        ]
        if any(p.kind == p.VAR_POSITIONAL for p in signature.parameters.values()) or len(positional) >= 3:
            return fetch_raw_daily(code, candidate_day, as_of_day)
        if len(positional) == 2:
            return fetch_raw_daily(code, candidate_day)
    except (TypeError, ValueError):
        pass
    return fetch_raw_daily(code)


def _bar_date(row: Mapping[str, Any]) -> Optional[date]:
    value = row.get("date") or row.get("trade_date") or row.get("日期") or row.get("时间")
    try:
        return _parse_date(value, "bar.date")
    except ClosedLoopRuntimeError:
        return None


def _normalize_daily_payload(payload: Any) -> tuple[list[dict[str, Any]], str, Optional[str]]:
    source: Any = None
    quote_timestamp: Optional[str] = None
    rows_value = payload
    if isinstance(payload, Mapping):
        rows_value = payload.get("rows", payload.get("data", payload.get("klines", [])))
        source = payload.get("data_source") or payload.get("source")
        quote_timestamp = payload.get("quote_timestamp")
    rows = _rows(rows_value, "daily_rows")
    if not source:
        for row in rows:
            source = row.get("data_source") or row.get("source")
            if source:
                break
    return rows, _actual_source(source), quote_timestamp


def track_untracked_candidates(
    fetch_raw_daily: Callable[..., Any], as_of: Any = None
) -> dict[str, Any]:
    """抓取并保存每个候选日期后的第一实际交易日不复权 OHLC。

    为防止“取到任意后续一天”冒充次日，本函数要求回调结果中同时存在
    ``candidate_date`` 当日或更早的锚点日线；随后按日期排序，严格选择
    ``candidate_date`` 之后第一条实际日线。当天 15:00 前的未收盘日线不会
    入库。仅有日线且目标/止损同日触发时不猜顺序，数据库会标为
    ``AMBIGUOUS``。
    """
    if not callable(fetch_raw_daily):
        raise ClosedLoopRuntimeError("fetch_raw_daily 必须是可调用对象")
    stamp = _parse_datetime(as_of, "as_of", default_now=True)
    as_of_day = stamp.date().isoformat()
    # 包括晚间/盘前后来被取消的初版候选，避免只追踪幸存者造成胜率偏高。
    pending = closed_db.get_all_untracked_candidates(
        as_of_day, _DB_PATH, include_cancelled=True
    )
    tracked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in pending:
        code = _code(candidate.get("stock_code"))
        candidate_day = _parse_date(candidate.get("candidate_date"), "candidate_date")
        try:
            payload = _callback_result(fetch_raw_daily, code, candidate_day.isoformat(), as_of_day)
            rows, source, payload_timestamp = _normalize_daily_payload(payload)
            if "不复权" not in source and "raw" not in source.casefold() and "unadjusted" not in source.casefold():
                raise ClosedLoopRuntimeError("data_source 必须明确标注为不复权/RAW 日线")
            dated = sorted(
                ((day, row) for row in rows if (day := _bar_date(row)) is not None),
                key=lambda item: item[0],
            )
            anchors = [(day, row) for day, row in dated if day <= candidate_day]
            after = [(day, row) for day, row in dated if candidate_day < day <= stamp.date()]
            if not any(day == candidate_day for day, _ in anchors):
                raise ClosedLoopRuntimeError("回调缺少候选日当日不复权日线，无法严格验证第一实际交易日")
            if not after:
                skipped.append({"code": code, "candidate_date": candidate_day.isoformat(), "reason": "尚无下一交易日日线"})
                continue
            trade_day, bar = after[0]
            if trade_day == stamp.date() and stamp.time() < clock_time(15, 0):
                skipped.append({"code": code, "candidate_date": candidate_day.isoformat(), "reason": "当日日线尚未收盘"})
                continue
            previous_candidates = [(day, row) for day, row in dated if day < trade_day]
            prior_row = previous_candidates[-1][1] if previous_candidates else None
            prior_close = _first_number(bar, "prior_close", "pre_close", "昨收")
            if prior_close is None and prior_row is not None:
                prior_close = _first_number(prior_row, "close", "收盘", "收盘价")
            if prior_close is None or prior_close <= 0:
                raise ClosedLoopRuntimeError("缺少实际上一交易日收盘价")
            o = _first_number(bar, "open", "开盘", "开盘价")
            h = _first_number(bar, "high", "最高", "最高价")
            low = _first_number(bar, "low", "最低", "最低价")
            close = _first_number(bar, "close", "收盘", "收盘价")
            if None in {o, h, low, close}:
                raise ClosedLoopRuntimeError("实际日线 OHLC 不完整")
            first_hit = None
            if bar.get("first_hit") and (bar.get("first_hit_evidence") or bar.get("intraday_evidence")):
                first_hit = str(bar.get("first_hit")).upper()
            record = closed_db.record_next_day_ohlc(
                candidate_date=candidate_day.isoformat(),
                stock_code=code,
                trade_date=trade_day.isoformat(),
                open_price=o,
                high_price=h,
                low_price=low,
                close_price=close,
                prior_close=prior_close,
                quote_timestamp=bar.get("quote_timestamp") or payload_timestamp or f"{trade_day.isoformat()}T15:00:00",
                first_hit=first_hit,
                target_hit_at=bar.get("target_hit_at") if first_hit else None,
                stop_hit_at=bar.get("stop_hit_at") if first_hit else None,
                data_source=source,
                raw_payload={"bar": bar, "first_actual_trade_day_verified": True},
                db_path=_DB_PATH,
                refresh_reports=False,
            )
            tracked.append(record)
        except Exception as exc:
            skipped.append({"code": code, "candidate_date": candidate_day.isoformat(), "reason": str(exc)})
    reports: list[dict[str, Any]] = []
    if tracked:
        reports = closed_db.generate_winrate_reports(as_of_day, _DB_PATH)
    complete = not skipped
    mark_stage_db_committed(
        STAGE_TRACKING,
        as_of_day,
        complete,
        {"pending_count": len(pending), "tracked_count": len(tracked), "skipped_count": len(skipped)},
    )
    return {
        "ok": complete,
        "as_of": stamp.isoformat(timespec="seconds"),
        "pending_count": len(pending),
        "tracked": tracked,
        "tracked_count": len(tracked),
        "skipped": skipped,
        "winrate_reports": reports,
        "report_basis": "next_day_tracking.actual_data=1",
    }


def _evidence_map(evidence_by_code: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not isinstance(evidence_by_code, Mapping):
        raise ClosedLoopRuntimeError("evidence_by_code 必须是 code -> evidence 字典")
    global_evidence = dict(evidence_by_code.get("__global__") or evidence_by_code.get("_global") or {})
    result: dict[str, dict[str, Any]] = {}
    for key, value in evidence_by_code.items():
        if str(key) in {"__global__", "_global"}:
            continue
        code = _code(key)
        if code and isinstance(value, Mapping):
            result[code] = dict(_json_safe(value))
    return result, global_evidence


def _normalize_revision_evidence(evidence: Mapping[str, Any], stage: str) -> dict[str, Any]:
    """适配 ``v66_data_sources`` 的审计结构，不推断缺失字段或金额单位。"""
    normalized = dict(evidence)
    # 只有与候选代码/名称匹配的正式公告，才允许触发监管类硬过滤；行业新闻
    # 的普通标题关键词不会被提升为个股监管事实。
    announcement_keywords: set[str] = set()
    for item in normalized.get("announcements") or []:
        if not isinstance(item, Mapping):
            continue
        keyword_evidence = item.get("title_keyword_evidence") or {}
        if isinstance(keyword_evidence, Mapping):
            announcement_keywords.update(
                str(word) for word in keyword_evidence.get("negative_keywords") or []
            )
    if "退市" in announcement_keywords:
        normalized["delisting_risk"] = True
    if "立案" in announcement_keywords or (
        "监管" in announcement_keywords and "调查" in announcement_keywords
    ):
        normalized["regulatory_investigation"] = True
    elif announcement_keywords.intersection({"处罚", "监管", "问询"}):
        normalized.setdefault("regulatory_risk_score", 70.0)

    external = normalized.get("external_market_snapshot")
    if isinstance(external, Sequence) and not isinstance(external, (str, bytes)) and external:
        changes: list[float] = []
        complete_numeric = True
        for item in external:
            if not isinstance(item, Mapping):
                complete_numeric = False
                break
            value = _number(item.get("change_pct"))
            if value is None:
                complete_numeric = False
                break
            changes.append(value)
        if complete_numeric and changes:
            normalized["external_market_average_pct"] = round(sum(changes) / len(changes), 4)
            normalized["external_market_complete_numeric"] = True

    if stage == "09:25":
        quote = normalized.get("auction_or_quote_snapshot")
        if isinstance(quote, Mapping):
            price = _first_number(
                quote, "auction_or_snapshot_price", "auction_price", "price"
            )
            pct = _first_number(
                quote, "auction_or_snapshot_change_pct", "auction_change_pct", "snapshot_change_pct", "pct"
            )
            if normalized.get("auction_price") is None and price is not None:
                normalized["auction_price"] = price
            if normalized.get("auction_change_pct") is None and pct is not None:
                normalized["auction_change_pct"] = pct
            # data_sources 明确标记上游金额单位未知时，只留在原始 evidence，
            # 不写 auction_amount，也不让它进入任何评分。
            amount_unit = str(quote.get("auction_amount_unit") or "").strip().casefold()
            amount = _first_number(quote, "auction_amount", "amount")
            unit_known = bool(amount_unit) and not any(
                word in amount_unit for word in ("unspecified", "unknown", "do_not_infer", "未知")
            )
            if normalized.get("auction_amount") is None and amount is not None and unit_known:
                normalized["auction_amount"] = amount
    return normalized


def _risk_penalty(evidence: Mapping[str, Any], stage: str) -> tuple[float, float, list[str], bool]:
    """把显式真实风险证据转换为规则扣分；返回 penalty/bonus/reasons/veto。"""
    reasons: list[str] = []
    veto_flags = (
        "hard_veto", "regulatory_investigation", "major_illegal", "major_negative_announcement",
        "suspended", "cannot_buy", "delisting_risk",
    )
    veto = any(evidence.get(flag) is True for flag in veto_flags)
    if stage == "09:25" and evidence.get("limit_up_locked") is True:
        veto = True
        reasons.append("竞价封死无法按计划成交")
    explicit = _number(evidence.get("score_deduction"))
    if explicit is not None:
        penalty = min(max(explicit, 0.0), 50.0)
        reasons.append(f"调用方显式风险扣分{penalty:.1f}") if penalty else None
    else:
        penalty = 0.0
        for key, maximum, label in (
            ("regulatory_risk_score", 15.0, "监管"),
            ("announcement_risk_score", 15.0, "公告"),
            ("negative_news_risk_score", 15.0, "负面消息"),
            ("industry_risk_score", 8.0, "行业消息"),
            ("policy_risk_score", 8.0, "政策"),
            ("overseas_risk_score", 8.0, "外围市场"),
        ):
            value = _number(evidence.get(key))
            if value is not None:
                item = min(max(value, 0.0), 100.0) / 100.0 * maximum
                penalty += item
                if item:
                    reasons.append(f"{label}风险-{item:.1f}")
        if evidence.get("negative_news") is True:
            penalty = max(penalty, 8.0)
            reasons.append("存在真实负面消息")
        if evidence.get("negative_announcement") is True:
            penalty = max(penalty, 8.0)
            reasons.append("存在真实负面公告")
    bonus = max(-10.0, min(10.0, _number(evidence.get("score_bonus")) or 0.0))
    negative_count = _number(evidence.get("negative_count"))
    positive_count = _number(evidence.get("positive_count"))
    if negative_count is not None and negative_count > 0 and _number(evidence.get("negative_news_risk_score")) is None:
        title_penalty = min(8.0, max(0.0, negative_count) * 2.0)
        penalty += title_penalty
        reasons.append(f"负向标题{int(negative_count)}条-{title_penalty:.1f}")
    if positive_count is not None and positive_count > 0:
        title_bonus = min(3.0, max(0.0, positive_count) * 0.75)
        bonus += title_bonus
        reasons.append(f"正向标题{int(positive_count)}条+{title_bonus:.1f}")
    if evidence.get("external_market_complete_numeric") is True:
        external_avg = _number(evidence.get("external_market_average_pct"))
        if external_avg is not None:
            if external_avg < 0:
                external_penalty = min(2.0, abs(external_avg) * 0.8)
                penalty += external_penalty
                if external_penalty:
                    reasons.append(f"外围指数均值{external_avg:+.2f}%-{external_penalty:.1f}")
            elif external_avg > 0:
                external_bonus = min(1.5, external_avg * 0.5)
                bonus += external_bonus
                if external_bonus:
                    reasons.append(f"外围指数均值{external_avg:+.2f}%+{external_bonus:.1f}")
    if stage in {"09:00", "09:25"}:
        feedback = _number(evidence.get("yesterday_feedback_score"))
        if feedback is not None:
            bonus += max(-4.0, min(4.0, (feedback - 50.0) * 0.08))
        sector_auction = _number(evidence.get("sector_auction_score"))
        if sector_auction is not None:
            bonus += max(-5.0, min(5.0, (sector_auction - 50.0) * 0.10))
        auction_change = _number(evidence.get("auction_change_pct"))
        if auction_change is not None:
            if auction_change > 7.0:
                penalty += min(8.0, auction_change - 3.0)
                reasons.append("竞价涨幅过高，追高风险")
            elif auction_change < -5.0:
                veto = True
                reasons.append("竞价明显低开，取消关注")
            elif 0.0 <= auction_change <= 3.0:
                bonus += 1.5
    if veto:
        reasons.append("触发硬性取消条件")
    return round(min(penalty, 60.0), 2), round(max(-10.0, min(10.0, bonus)), 2), list(dict.fromkeys(reasons)), veto


def _merge_evidence(global_evidence: Mapping[str, Any], local: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(global_evidence)
    merged.update(local)
    return merged


def _rank_revisions(
    candidates: Sequence[Mapping[str, Any]], evidence_map: Mapping[str, Mapping[str, Any]],
    global_evidence: Mapping[str, Any], data_complete: bool, stage: str,
) -> list[dict[str, Any]]:
    proposed: list[dict[str, Any]] = []
    for candidate in candidates:
        code = _code(candidate.get("stock_code"))
        if data_complete and code not in evidence_map:
            raise ClosedLoopRuntimeError(f"data_complete=True 但 {code} 缺少逐股证据，不能把缺数当无风险")
        local_evidence = evidence_map.get(code, {})
        if data_complete and local_evidence.get("data_complete") is False:
            raise ClosedLoopRuntimeError(f"data_complete=True 但 {code} 的逐股证据标记为不完整")
        evidence = _normalize_revision_evidence(
            _merge_evidence(global_evidence, local_evidence), stage
        )
        if data_complete and stage == "09:25" and (
            _number(evidence.get("auction_price")) is None
            or _number(evidence.get("auction_change_pct")) is None
        ):
            raise ClosedLoopRuntimeError(f"data_complete=True 但 {code} 缺少可核验的09:25价格/涨跌幅")
        old_score = float(candidate.get("comprehensive_score") or 0)
        old_status = str(candidate.get("status") or "CANDIDATE")
        if not data_complete:
            new_score, new_status, penalty, bonus, reasons = old_score, old_status, 0.0, 0.0, ["数据不完整，强制保留原评分"]
        else:
            penalty, bonus, reasons, veto = _risk_penalty(evidence, stage)
            explicit_adjustment = _number(evidence.get("score_adjustment"))
            adjustment = max(-50.0, min(15.0, explicit_adjustment)) if explicit_adjustment is not None else bonus - penalty
            new_score = round(max(0.0, min(100.0, old_score + adjustment)), 1)
            if old_status == "CANCELLED" and evidence.get("restore") is not True:
                new_status = "CANCELLED"
            elif veto or new_score < 65:
                new_status = "CANCELLED"
            elif new_score < 72:
                new_status = "WATCH"
            else:
                new_status = "CANDIDATE"
        proposed.append({
            "candidate": dict(candidate), "code": code, "evidence": evidence,
            "old_score": old_score, "new_score": new_score, "old_status": old_status,
            "new_status": new_status, "penalty": penalty, "bonus": bonus,
            "reasons": reasons or ["真实修正数据未触发调分"],
        })
    if not data_complete:
        proposed.sort(key=lambda row: int(row["candidate"].get("candidate_rank") or 999999))
        for row in proposed:
            row["new_rank"] = row["candidate"].get("candidate_rank")
    else:
        proposed.sort(
            key=lambda row: (row["new_status"] != "CANCELLED", row["new_score"], -int(row["candidate"].get("candidate_rank") or 999999)),
            reverse=True,
        )
        for rank, row in enumerate(proposed, 1):
            row["new_rank"] = rank
    return proposed


def apply_evening_revision(
    candidate_date: Any,
    evidence_by_code: Any,
    data_complete: bool,
    revised_at: Any = None,
) -> dict[str, Any]:
    """应用 19:30 公告/行业/政策/外围证据并真实扣分重排。

    ``data_complete=True`` 时要求每只候选都有逐股证据记录；否则直接报错，
    防止把接口漏数理解成“没有利空”。``False`` 时仍写修订审计记录，但 DB
    会强制保持原分、原排名和原状态。
    """
    day = _parse_date(candidate_date, "candidate_date").isoformat()
    stamp = _parse_datetime(revised_at, "revised_at", default_now=True)
    if is_stage_db_committed(STAGE_EVENING, day):
        return {
            "ok": True,
            "idempotent": True,
            "candidate_date": day,
            "data_complete": True,
            "revisions": [],
            "candidates": closed_db.get_candidates(day, _DB_PATH),
        }
    candidates = closed_db.get_candidates(day, _DB_PATH)
    mapped, global_evidence = _evidence_map(evidence_by_code)
    proposed = _rank_revisions(candidates, mapped, global_evidence, bool(data_complete), "19:30")
    results: list[dict[str, Any]] = []
    for row in proposed:
        evidence = row["evidence"]
        source = _actual_source(
            evidence.get("data_source") or global_evidence.get("data_source"),
            "主程序传入的19:30公告/行业/政策/外围接口状态",
        )
        revision_id = closed_db.record_evening_revision({
            "candidate_id": row["candidate"]["id"],
            "candidate_date": day,
            "revision_date": day,
            "revised_at": stamp.isoformat(timespec="seconds"),
            "new_rank": row["new_rank"],
            "new_score": row["new_score"],
            "new_status": row["new_status"],
            "revision_reason": "；".join(row["reasons"]),
            "announcements": evidence.get("announcements", []),
            "industry_news": evidence.get("industry_news", []),
            "policy_news": evidence.get("policy_news", []),
            "overseas_market": (
                evidence.get("overseas_market")
                or evidence.get("external_market_snapshot")
                or global_evidence.get("overseas_market")
                or global_evidence.get("external_market_snapshot")
                or {}
            ),
            "data_complete": bool(data_complete),
            "data_source": source,
        }, _DB_PATH)
        results.append({**{key: value for key, value in row.items() if key != "candidate"}, "revision_id": revision_id, "name": row["candidate"].get("stock_name")})
    # 缺数修订已留审计，但不标记阶段完成，以便数据源恢复后安全重试。
    mark_stage_db_committed(STAGE_EVENING, day, bool(data_complete), {"candidate_count": len(results), "data_complete": bool(data_complete)})
    return {"ok": True, "idempotent": False, "candidate_date": day, "data_complete": bool(data_complete), "revisions": results, "candidates": closed_db.get_candidates(day, _DB_PATH)}


def apply_preopen_revision(
    candidate_date: Any,
    strategy_date: Any,
    stage: str,
    evidence_by_code: Any,
    data_complete: bool,
    revised_at: Any = None,
) -> dict[str, Any]:
    """应用次日 09:00 或 09:25 实际证据，输出关注/取消/风险重排。"""
    candidate_day = _parse_date(candidate_date, "candidate_date").isoformat()
    strategy_day = _parse_date(strategy_date, "strategy_date").isoformat()
    if strategy_day <= candidate_day:
        raise ClosedLoopRuntimeError("strategy_date 必须晚于 candidate_date")
    normalized_stage = str(stage or "").strip()
    if normalized_stage not in {"09:00", "09:25"}:
        raise ClosedLoopRuntimeError("stage 只能是 09:00 或 09:25")
    state_stage = STAGE_PREOPEN_0900 if normalized_stage == "09:00" else STAGE_PREOPEN_0925
    stamp = _parse_datetime(revised_at, "revised_at", default_now=True)
    if is_stage_db_committed(state_stage, strategy_day):
        return {
            "ok": True,
            "idempotent": True,
            "candidate_date": candidate_day,
            "strategy_date": strategy_day,
            "stage": normalized_stage,
            "data_complete": True,
            "revisions": [],
            "candidates": closed_db.get_candidates(candidate_day, _DB_PATH),
        }
    candidates = closed_db.get_candidates(candidate_day, _DB_PATH)
    mapped, global_evidence = _evidence_map(evidence_by_code)
    proposed = _rank_revisions(candidates, mapped, global_evidence, bool(data_complete), normalized_stage)
    results: list[dict[str, Any]] = []
    for row in proposed:
        evidence = row["evidence"]
        source = _actual_source(
            evidence.get("data_source") or global_evidence.get("data_source"),
            f"主程序传入的{normalized_stage}盘前接口状态",
        )
        revision_id = closed_db.record_preopen_revision({
            "candidate_id": row["candidate"]["id"],
            "candidate_date": candidate_day,
            "strategy_date": strategy_day,
            "stage": normalized_stage,
            "revised_at": stamp.isoformat(timespec="seconds"),
            "new_rank": row["new_rank"],
            "new_score": row["new_score"],
            "new_status": row["new_status"],
            "auction_price": evidence.get("auction_price"),
            "auction_change_pct": evidence.get("auction_change_pct"),
            "auction_amount": evidence.get("auction_amount"),
            "sector_auction_score": evidence.get("sector_auction_score"),
            "yesterday_feedback_score": evidence.get("yesterday_feedback_score"),
            "risk_message": "；".join(row["reasons"]),
            "evidence": evidence,
            "data_complete": bool(data_complete),
            "data_source": source,
        }, _DB_PATH)
        results.append({**{key: value for key, value in row.items() if key != "candidate"}, "revision_id": revision_id, "name": row["candidate"].get("stock_name")})
    # 缺数时保留旧分并允许稍后重试，不把本阶段误标为完整完成。
    mark_stage_db_committed(state_stage, strategy_day, bool(data_complete), {"candidate_date": candidate_day, "candidate_count": len(results), "data_complete": bool(data_complete)})
    return {"ok": True, "idempotent": False, "candidate_date": candidate_day, "strategy_date": strategy_day, "stage": normalized_stage, "data_complete": bool(data_complete), "revisions": results, "candidates": closed_db.get_candidates(candidate_day, _DB_PATH)}


def build_close_message(result: Mapping[str, Any], max_candidates: int = 5) -> str:
    """生成精简的 15:05 次日候选池微信群文本。"""
    forecasts = list(result.get("sector_forecasts") or [])[:3]
    candidates = list(result.get("candidates") or [])[:max(0, int(max_candidates))]
    lines = [f"🌙 A股机会雷达 V6.6 Pro｜15:05次日初选", f"日期：{result.get('candidate_date', '-')}｜候选{len(result.get('candidates') or [])}只", "", "🔥 明日板块TOP"]
    for item in forecasts:
        name = item.get("sector_name") or item.get("sector") or "-"
        score = item.get("continuation_score", item.get("score"))
        confidence = ((item.get("evidence") or {}).get("confidence") if isinstance(item.get("evidence"), Mapping) else item.get("confidence"))
        lines.append(f"{item.get('forecast_rank', item.get('rank', '-'))}. {name}｜续强分{float(score):.1f}｜置信{float(confidence or 0):.0f}")
    lines.extend(["", "📋 次日候选"])
    if not candidates:
        lines.append("今日没有通过完整评分与价格校验的候选，不造票。")
    for item in candidates:
        lines.append(
            f"{item.get('candidate_rank', '-')}. {item.get('stock_name', item.get('name', '-'))} {item.get('stock_code', item.get('code', ''))}｜{float(item.get('comprehensive_score', item.get('final_score', 0))):.1f}分｜买入观察{item.get('buy_range_text', '-')}"
        )
        lines.append(f"   目标{item.get('target_price', '-')}｜防守{item.get('stop_price', '-')}｜{item.get('status', '-')}")
    lines.append("⚠️ 仅作次日观察池，19:30及次日盘前还会复核；不构成投资建议。")
    return "\n".join(lines)


def build_evening_message(result: Mapping[str, Any], max_candidates: int = 5) -> str:
    """生成精简的 19:30 消息修正微信群文本。"""
    candidates = list(result.get("candidates") or [])
    active = [item for item in candidates if item.get("status") != "CANCELLED"][:max_candidates]
    cancelled = [item for item in candidates if item.get("status") == "CANCELLED"]
    lines = ["🌃 A股机会雷达 V6.6 Pro｜19:30复核", f"日期：{result.get('candidate_date', '-')}｜数据{'完整' if result.get('data_complete') else '不完整，评分保持不变'}", "", "✅ 保留关注"]
    lines.extend(
        f"{item.get('candidate_rank', '-')}. {item.get('stock_name', '-')} {item.get('stock_code', '')}｜{float(item.get('comprehensive_score', 0)):.1f}分"
        for item in active
    )
    if not active:
        lines.append("无")
    lines.append("❌ 取消关注：" + ("、".join(f"{item.get('stock_name')} {item.get('stock_code')}" for item in cancelled) or "无"))
    lines.append("⚠️ 次日09:00/09:25仍需盘前复核。")
    return "\n".join(lines)


def build_preopen_message(result: Mapping[str, Any], max_candidates: int = 5) -> str:
    """生成精简的 09:00/09:25 盘前策略微信群文本。"""
    candidates = list(result.get("candidates") or [])
    active = [item for item in candidates if item.get("status") in {"CANDIDATE", "WATCH", "FOLLOWED"}][:max_candidates]
    cancelled = [item for item in candidates if item.get("status") == "CANCELLED"]
    lines = [f"🌅 A股机会雷达 V6.6 Pro｜{result.get('stage', '-')}盘前策略", f"策略日：{result.get('strategy_date', '-')}｜数据{'完整' if result.get('data_complete') else '不完整，沿用原排序'}", "", "👀 今日关注"]
    lines.extend(
        f"{item.get('candidate_rank', '-')}. {item.get('stock_name', '-')} {item.get('stock_code', '')}｜{float(item.get('comprehensive_score', 0)):.1f}分｜{item.get('status')}"
        for item in active
    )
    if not active:
        lines.append("无")
    lines.append("⛔ 取消关注：" + ("、".join(f"{item.get('stock_name')} {item.get('stock_code')}" for item in cancelled) or "无"))
    lines.append("⚠️ 集合竞价只用于修正关注顺序，仍需人工核对盘口；不构成投资建议。")
    return "\n".join(lines)


__all__ = [
    "ClosedLoopRuntimeError",
    "RUNTIME_VERSION",
    "STAGE_CLOSE",
    "STAGE_EVENING",
    "STAGE_PREOPEN_0900",
    "STAGE_PREOPEN_0925",
    "STAGE_TRACKING",
    "initialize",
    "capture_market_snapshot",
    "run_close_selection",
    "track_untracked_candidates",
    "apply_evening_revision",
    "apply_preopen_revision",
    "build_close_message",
    "build_evening_message",
    "build_preopen_message",
    "get_job_state",
    "is_stage_db_committed",
    "mark_stage_db_committed",
    "mark_stage_push",
]
