"""V6.6 Pro 晚间与盘前真实证据采集。

本模块只负责采集、筛选和保留可审计证据，不负责给新闻下结论，也不负责
写入生产数据库。所有外部接口均在函数被调用后才访问；导入本模块不会联网。

公开接口可能改字段或临时失效，因此返回值始终带 ``data_complete`` 与
``source_status``。调用方不得把 ``data_complete=False`` 当成“没有利空”。
"""

from __future__ import annotations

import importlib
import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MODULE_VERSION = "V6.6_PRO_DATA_SOURCES_1.0"

MAX_ANNOUNCEMENTS_PER_STOCK = 8
MAX_INDUSTRY_NEWS_PER_STOCK = 6
MAX_POLICY_NEWS_PER_STOCK = 6
MAX_GLOBAL_MARKETS = 12
MAX_AUDIT_TEXT = 600

# 这些词只用于标注“标题中出现了什么”，不代表理解公告或新闻全文。
POSITIVE_TITLE_KEYWORDS: Tuple[str, ...] = (
    "预增", "扭亏", "增持", "回购", "中标", "获批", "重大合同", "签订合同",
    "超预期", "上调", "分红", "突破", "扩产", "订单增长", "利好", "政策支持",
    "降准", "降息", "补贴", "免税", "减税", "创新高",
)
NEGATIVE_TITLE_KEYWORDS: Tuple[str, ...] = (
    "预亏", "亏损", "减持", "立案", "调查", "处罚", "退市", "终止", "暂停",
    "诉讼", "违约", "风险提示", "监管", "问询", "下修", "下调", "爆雷",
    "被执行", "冻结", "质押风险", "重大损失", "业绩下降", "业绩下滑",
)
POLICY_TITLE_KEYWORDS: Tuple[str, ...] = (
    "国务院", "中央", "发改委", "财政部", "央行", "人民银行", "证监会", "工信部",
    "商务部", "住建部", "政策", "规划", "指导意见", "实施方案", "条例", "办法",
    "通知", "会议", "降准", "降息", "专项债", "财政", "货币政策", "资本市场",
)

GENERIC_SECTOR_WORDS = {
    "概念", "行业", "板块", "其他", "未知", "暂无", "综合", "沪深", "股票",
}

NOTICE_FIELDS = {
    "code": ("代码", "股票代码", "证券代码", "code", "symbol"),
    "name": ("名称", "股票名称", "证券简称", "name"),
    "title": ("公告标题", "标题", "title"),
    "notice_type": ("公告类型", "类型", "notice_type", "type"),
    "publish_date": ("公告日期", "发布日期", "日期", "publish_date", "date"),
    "url": ("网址", "公告链接", "链接", "url"),
}
NEWS_FIELDS = {
    "title": ("标题", "新闻标题", "title"),
    "content": ("内容", "正文", "摘要", "content", "summary"),
    "publish_date": ("发布日期", "日期", "publish_date", "date"),
    "publish_time": ("发布时间", "时间", "publish_time", "time"),
    "url": ("链接", "网址", "url"),
}
GLOBAL_FIELDS = {
    "name": ("名称", "指数名称", "name", "symbol"),
    "latest": ("最新价", "最新", "现价", "latest", "price"),
    "change_pct": ("涨跌幅", "涨幅", "change_pct", "pct", "percent"),
    "change": ("涨跌额", "change", "amount"),
    "open": ("开盘价", "今开", "open"),
    "previous_close": ("昨日收盘价", "昨收", "previous_close", "prev_close"),
    "quote_time": ("行情时间", "更新时间", "quote_time", "time"),
    "high": ("最高价", "最高", "high"),
    "low": ("最低价", "最低", "low"),
    "source": ("数据来源", "source"),
    "source_symbol": ("源代码", "source_symbol"),
}

TENCENT_GLOBAL_SYMBOLS: Tuple[str, ...] = (
    "usDJI", "usIXIC", "usINX", "r_hkHSI", "jpN225",
)
TENCENT_GLOBAL_URL = "https://qt.gtimg.cn/q=" + ",".join(TENCENT_GLOBAL_SYMBOLS)


def _clean_text(value: Any, limit: int = MAX_AUDIT_TEXT) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and math.isnan(value):
            return ""
    except Exception:
        pass
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit]


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    text = _clean_text(value, 80).replace(",", "").replace("%", "")
    if not text or text.lower() in {"nan", "none", "null", "--", "-"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalise_date(value: Any, default: Optional[date] = None) -> date:
    if value is None:
        if default is not None:
            return default
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean_text(value, 40)
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10] if "%Y-%m-%d" == fmt else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法识别日期: {text!r}")


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = _clean_text(value, 40)
    match = re.search(r"(20\d{2})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return text
    return text


def _canonical_code(value: Any) -> str:
    text = _clean_text(value, 40)
    matches = re.findall(r"\d{6}", text)
    if matches:
        return matches[-1]
    digits = re.sub(r"\D", "", text)
    return digits.zfill(6) if digits and len(digits) <= 6 else digits[-6:]


def _candidate_value(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _normalise_candidates(candidates: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if candidates is None:
        source_rows: List[Any] = []
    elif isinstance(candidates, Mapping):
        source_rows = list(candidates.values()) if not any(
            key in candidates for key in ("code", "stock_code", "股票代码", "证券代码")
        ) else [candidates]
    else:
        source_rows = list(candidates)

    valid: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(source_rows):
        if not isinstance(raw, Mapping):
            invalid.append({"index": index, "reason": "candidate_not_mapping"})
            continue
        code = _canonical_code(_candidate_value(
            raw, ("code", "stock_code", "股票代码", "证券代码", "symbol")
        ))
        name = _clean_text(_candidate_value(
            raw, ("name", "stock_name", "股票名称", "证券简称", "名称")
        ), 80)
        sector = _clean_text(_candidate_value(
            raw, ("sector", "sector_name", "所属板块", "板块", "industry")
        ), 120)
        candidate_date = _date_text(
            _candidate_value(raw, ("candidate_date", "recommend_date", "日期", "入选日期", "date"))
        )
        if not re.fullmatch(r"\d{6}", code) or not name:
            invalid.append({
                "index": index,
                "code": code,
                "name": name,
                "reason": "missing_or_invalid_code_name",
            })
            continue
        if code in seen:
            continue
        seen.add(code)
        valid.append({
            "code": code,
            "name": name,
            "sector": sector,
            "candidate_date": candidate_date,
        })
    return valid, invalid


def _records(table: Any) -> List[Dict[str, Any]]:
    if table is None:
        return []
    if isinstance(table, list):
        return [dict(row) for row in table if isinstance(row, Mapping)]
    if isinstance(table, tuple):
        return [dict(row) for row in table if isinstance(row, Mapping)]
    if isinstance(table, Mapping):
        return [dict(table)]
    to_dict = getattr(table, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict(orient="records")
        except TypeError:
            rows = to_dict("records")
        return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _columns(table: Any, rows: Sequence[Mapping[str, Any]]) -> List[str]:
    columns = getattr(table, "columns", None)
    if columns is not None:
        return [_clean_text(column, 100) for column in list(columns)]
    result: List[str] = []
    for row in rows:
        for key in row:
            text = _clean_text(key, 100)
            if text not in result:
                result.append(text)
    return result


def _field_map(columns: Sequence[str], aliases: Mapping[str, Sequence[str]]) -> Dict[str, str]:
    direct = {str(column): str(column) for column in columns}
    mapped: Dict[str, str] = {}
    for canonical, choices in aliases.items():
        for choice in choices:
            if choice in direct:
                mapped[canonical] = direct[choice]
                break
    return mapped


def _value(row: Mapping[str, Any], fields: Mapping[str, str], canonical: str) -> Any:
    key = fields.get(canonical)
    return row.get(key) if key else None


def _title_keyword_evidence(title: str) -> Dict[str, Any]:
    positive = [word for word in POSITIVE_TITLE_KEYWORDS if word in title]
    negative = [word for word in NEGATIVE_TITLE_KEYWORDS if word in title]
    policy = [word for word in POLICY_TITLE_KEYWORDS if word in title]
    if positive and negative:
        label = "mixed_title_keywords"
    elif negative:
        label = "negative_title_keyword"
    elif positive:
        label = "positive_title_keyword"
    else:
        label = "no_directional_title_keyword"
    return {
        "classification_basis": "title_keyword_only",
        "label": label,
        "positive_keywords": positive,
        "negative_keywords": negative,
        "policy_keywords": policy,
        "full_text_interpreted": False,
    }


def _source_status(
    function: str,
    complete: bool,
    rows_received: int = 0,
    requested: str = "",
    fields: Optional[Mapping[str, str]] = None,
    missing_fields: Optional[Sequence[str]] = None,
    error: str = "",
    attempts: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "state": "complete" if complete else "incomplete",
        "complete": bool(complete),
        "function": function,
        "requested": requested,
        "rows_received": int(rows_received),
        "fields": dict(fields or {}),
        "missing_fields": list(missing_fields or []),
        "error": _clean_text(error, 300),
        "attempts": [dict(item) for item in (attempts or [])],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def _load_akshare(ak_module: Any) -> Tuple[Any, str]:
    if ak_module is not None:
        return ak_module, "injected_akshare_compatible_module"
    try:
        return importlib.import_module("akshare"), "akshare"
    except Exception as exc:
        raise RuntimeError(f"akshare导入失败: {type(exc).__name__}: {exc}") from exc


def _call_with_fallbacks(function: Callable[..., Any], variants: Sequence[Tuple[str, Dict[str, Any]]]) -> Tuple[Any, str, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    last_error = ""
    for label, kwargs in variants:
        try:
            value = function(**kwargs)
            attempts.append({"request": label, "ok": True})
            return value, label, attempts
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append({"request": label, "ok": False, "error": _clean_text(last_error, 240)})
    raise RuntimeError(last_error or "所有接口调用方式均失败")


def _fetch_notices(ak: Any, analysis_day: date) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    function_name = "akshare.stock_notice_report"
    function = getattr(ak, "stock_notice_report", None)
    if not callable(function):
        return [], _source_status(function_name, False, error="接口不存在", missing_fields=["function"])
    yyyymmdd = analysis_day.strftime("%Y%m%d")
    attempts: List[Dict[str, Any]] = []
    table = None
    requested = ""
    last_error = ""
    for label, kwargs in (
        (f"symbol=全部,date={yyyymmdd}", {"symbol": "全部", "date": yyyymmdd}),
        (f"date={yyyymmdd}", {"date": yyyymmdd}),
    ):
        try:
            table = function(**kwargs)
            requested = label
            attempts.append({"request": label, "ok": True})
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append({"request": label, "ok": False, "error": _clean_text(last_error, 240)})
    if table is None:
        return [], _source_status(
            function_name, False, requested=f"date={yyyymmdd}",
            error=last_error or "公告接口无响应", attempts=attempts,
        )
    rows = _records(table)
    fields = _field_map(_columns(table, rows), NOTICE_FIELDS)
    missing = [name for name in ("title", "publish_date", "url") if name not in fields]
    if "code" not in fields and "name" not in fields:
        missing.append("code_or_name")
    complete = not missing
    status = _source_status(
        function_name, complete, len(rows), requested=requested, fields=fields,
        missing_fields=missing, error="" if complete else "公告字段不完整", attempts=attempts,
    )
    return rows, status


def _fetch_cls_news(ak: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    function_name = "akshare.stock_info_global_cls"
    function = getattr(ak, "stock_info_global_cls", None)
    if not callable(function):
        return [], _source_status(function_name, False, error="接口不存在", missing_fields=["function"])
    attempts: List[Dict[str, Any]] = []
    table = None
    requested = ""
    last_error = ""
    selected_fields: Dict[str, str] = {}
    selected_missing: List[str] = []
    # “重点”为空、报错或字段不完整时再取“全部”，避免把异常空响应当成无新闻。
    for symbol in ("重点", "全部"):
        try:
            current = function(symbol=symbol)
            current_rows = _records(current)
            current_fields = _field_map(_columns(current, current_rows), NEWS_FIELDS)
            current_missing = [
                name for name in ("title", "content", "publish_date", "publish_time")
                if name not in current_fields
            ]
            attempts.append({
                "request": f"symbol={symbol}", "ok": True, "rows": len(current_rows),
                "missing_fields": current_missing,
            })
            table, requested = current, f"symbol={symbol}"
            selected_fields, selected_missing = current_fields, current_missing
            if current_rows and not current_missing:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append({"request": f"symbol={symbol}", "ok": False, "error": _clean_text(last_error, 240)})
    if table is None:
        return [], _source_status(function_name, False, error=last_error or "重点和全部均无响应", attempts=attempts)
    rows = _records(table)
    fields = selected_fields or _field_map(_columns(table, rows), NEWS_FIELDS)
    missing = selected_missing or [
        name for name in ("title", "content", "publish_date", "publish_time")
        if name not in fields
    ]
    complete = not missing
    status = _source_status(
        function_name, complete, len(rows), requested=requested, fields=fields,
        missing_fields=missing, error="" if complete else "新闻字段不完整", attempts=attempts,
    )
    return rows, status


def _parse_tencent_global_quote_text(text: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """解析腾讯全球指数 ``~`` 协议；只按公开字段位置取值，不补造数据。"""

    raw_text = text.decode("gb18030", errors="replace") if isinstance(text, bytes) else str(text or "")
    matches = re.findall(r'v_([^=\s]+)="([^"]*)"', raw_text)
    rows: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    received_symbols: List[str] = []
    for source_symbol, payload in matches:
        if source_symbol not in TENCENT_GLOBAL_SYMBOLS:
            continue
        received_symbols.append(source_symbol)
        values = payload.split("~")
        if len(values) <= 34:
            invalid.append({
                "source_symbol": source_symbol,
                "reason": "protocol_field_count_too_short",
                "field_count": len(values),
            })
            continue
        row = {
            "name": _clean_text(values[1], 100),
            "latest": _to_float(values[3]),
            "previous_close": _to_float(values[4]),
            "open": _to_float(values[5]),
            "quote_time": _clean_text(values[30], 80),
            "change": _to_float(values[31]),
            "change_pct": _to_float(values[32]),
            "high": _to_float(values[33]),
            "low": _to_float(values[34]),
            "source": "Tencent qt.gtimg.cn",
            "source_symbol": source_symbol,
        }
        missing_fields: List[str] = []
        if not row["name"]:
            missing_fields.append("name[1]")
        for field_name, position in (
            ("latest", 3), ("previous_close", 4), ("open", 5),
            ("change", 31), ("change_pct", 32), ("high", 33), ("low", 34),
        ):
            value = row[field_name]
            if value is None or (field_name in {"latest", "previous_close", "open", "high", "low"} and value <= 0):
                missing_fields.append(f"{field_name}[{position}]")
        if not row["quote_time"]:
            missing_fields.append("quote_time[30]")
        if missing_fields:
            invalid.append({
                "source_symbol": source_symbol,
                "reason": "missing_or_invalid_key_fields",
                "missing_fields": missing_fields,
            })
            continue
        rows.append(row)

    missing_symbols = [symbol for symbol in TENCENT_GLOBAL_SYMBOLS if symbol not in received_symbols]
    valid_symbols = [row["source_symbol"] for row in rows]
    return rows, {
        "requested_symbols": list(TENCENT_GLOBAL_SYMBOLS),
        "received_symbols": received_symbols,
        "valid_symbols": valid_symbols,
        "missing_symbols": missing_symbols,
        "invalid_rows": invalid,
        "complete": not missing_symbols and not invalid and len(rows) == len(TENCENT_GLOBAL_SYMBOLS),
    }


def _fetch_tencent_global_markets(requests_module: Any = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """函数调用时才加载 requests 并访问腾讯备用源。"""

    function_name = "Tencent qt.gtimg.cn"
    if requests_module is None:
        try:
            requests_module = importlib.import_module("requests")
        except Exception as exc:
            return [], _source_status(
                function_name, False, requested=TENCENT_GLOBAL_URL,
                error=f"requests导入失败: {type(exc).__name__}: {exc}",
                missing_fields=["requests_module"],
                attempts=[{"source": function_name, "request": TENCENT_GLOBAL_URL, "ok": False}],
            )
    try:
        response = requests_module.get(
            TENCENT_GLOBAL_URL,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        content = getattr(response, "content", None)
        if isinstance(content, bytes) and content:
            protocol_text: Any = content
        else:
            protocol_text = getattr(response, "text", "")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        return [], _source_status(
            function_name, False, requested=TENCENT_GLOBAL_URL, error=error,
            attempts=[{
                "source": function_name, "request": TENCENT_GLOBAL_URL,
                "ok": False, "error": _clean_text(error, 240),
            }],
        )

    rows, diagnostics = _parse_tencent_global_quote_text(protocol_text)
    missing_fields = [f"missing_symbol:{symbol}" for symbol in diagnostics["missing_symbols"]]
    for item in diagnostics["invalid_rows"]:
        missing_fields.append(f"invalid_symbol:{item.get('source_symbol', 'unknown')}")
    complete = bool(diagnostics["complete"])
    status = _source_status(
        function_name, complete, len(rows), requested=TENCENT_GLOBAL_URL,
        fields=_field_map(_columns(rows, rows), GLOBAL_FIELDS),
        missing_fields=missing_fields,
        error="" if complete else "腾讯全球指数响应缺行或关键字段无效",
        attempts=[{
            "source": function_name,
            "request": TENCENT_GLOBAL_URL,
            "ok": complete,
            "rows": len(rows),
            "missing_symbols": diagnostics["missing_symbols"],
            "invalid_rows": diagnostics["invalid_rows"],
        }],
    )
    status["selected_source"] = function_name if complete else ""
    status["protocol_diagnostics"] = diagnostics
    return rows, status


def _fetch_global_markets(ak: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """优先东方财富；失败或字段不完整时切换腾讯真实全球指数。"""

    combined_name = "akshare.index_global_spot_em | Tencent qt.gtimg.cn"
    eastmoney_attempt: Dict[str, Any]
    function = getattr(ak, "index_global_spot_em", None) if ak is not None else None
    if not callable(function):
        eastmoney_attempt = {
            "source": "akshare.index_global_spot_em",
            "request": "index_global_spot_em()",
            "ok": False,
            "error": "接口不存在或akshare不可用",
        }
    else:
        try:
            table = function()
            rows = _records(table)
            fields = _field_map(_columns(table, rows), GLOBAL_FIELDS)
            missing = [name for name in ("name", "latest", "change_pct") if name not in fields]
            if not rows:
                missing.append("rows")
            invalid_key_rows: List[Dict[str, Any]] = []
            if not missing:
                for index, row in enumerate(rows):
                    bad: List[str] = []
                    if not _clean_text(_value(row, fields, "name"), 100):
                        bad.append("name")
                    latest = _to_float(_value(row, fields, "latest"))
                    if latest is None or latest <= 0:
                        bad.append("latest")
                    if _to_float(_value(row, fields, "change_pct")) is None:
                        bad.append("change_pct")
                    if bad:
                        invalid_key_rows.append({"row_index": index, "invalid_fields": bad})
                if invalid_key_rows:
                    missing.append(f"invalid_key_rows:{len(invalid_key_rows)}")
            if not missing:
                status = _source_status(
                    combined_name, True, len(rows), requested="all_available_global_indices",
                    fields=fields, attempts=[{
                        "source": "akshare.index_global_spot_em",
                        "request": "index_global_spot_em()", "ok": True, "rows": len(rows),
                    }],
                )
                status["selected_source"] = "akshare.index_global_spot_em"
                status["fallback_used"] = False
                return rows, status
            eastmoney_attempt = {
                "source": "akshare.index_global_spot_em",
                "request": "index_global_spot_em()",
                "ok": False,
                "rows": len(rows),
                "missing_fields": missing,
                "invalid_key_rows": invalid_key_rows[:20],
                "error": "东方财富外围市场字段不完整",
            }
        except Exception as exc:
            eastmoney_attempt = {
                "source": "akshare.index_global_spot_em",
                "request": "index_global_spot_em()",
                "ok": False,
                "error": _clean_text(f"{type(exc).__name__}: {exc}", 240),
            }

    # 测试可通过兼容 ak 对象注入假的 requests；生产路径在此处才导入 requests。
    injected_requests = getattr(ak, "_v66_requests_module", None) if ak is not None else None
    fallback_rows, fallback_status = _fetch_tencent_global_markets(injected_requests)
    merged_attempts = [eastmoney_attempt] + list(fallback_status.get("attempts") or [])
    complete = bool(fallback_status.get("complete"))
    status = _source_status(
        combined_name,
        complete,
        len(fallback_rows),
        requested=fallback_status.get("requested", TENCENT_GLOBAL_URL),
        fields=fallback_status.get("fields") or {},
        missing_fields=fallback_status.get("missing_fields") or [],
        error="" if complete else (
            "东方财富源不可用，且腾讯备用源不完整：" + _clean_text(fallback_status.get("error"), 220)
        ),
        attempts=merged_attempts,
    )
    status["selected_source"] = "Tencent qt.gtimg.cn" if complete else ""
    status["fallback_used"] = True
    status["protocol_diagnostics"] = fallback_status.get("protocol_diagnostics", {})
    return fallback_rows, status


def _sector_tokens(candidate: Mapping[str, Any]) -> List[str]:
    values = [candidate.get("name", ""), candidate.get("code", "")]
    sector = _clean_text(candidate.get("sector"), 120)
    if sector:
        values.append(sector)
        values.extend(re.split(r"[\s,，、/|;+＋·\-]+", sector))
    result: List[str] = []
    for value in values:
        token = _clean_text(value, 80)
        if len(token) >= 2 and token not in GENERIC_SECTOR_WORDS and token not in result:
            result.append(token)
    return result


def _make_notice_item(row: Mapping[str, Any], fields: Mapping[str, str], matched_on: Sequence[str]) -> Dict[str, Any]:
    title = _clean_text(_value(row, fields, "title"), 300)
    return {
        "source": "akshare.stock_notice_report",
        "category": "announcement",
        "raw_title": title,
        "raw_content_excerpt": "",
        "notice_type": _clean_text(_value(row, fields, "notice_type"), 80),
        "published_date": _date_text(_value(row, fields, "publish_date")),
        "published_time": "",
        "url": _clean_text(_value(row, fields, "url"), 400),
        "matched_on": list(matched_on),
        "title_keyword_evidence": _title_keyword_evidence(title),
    }


def _make_news_item(row: Mapping[str, Any], fields: Mapping[str, str], category: str, matched_on: Sequence[str]) -> Dict[str, Any]:
    title = _clean_text(_value(row, fields, "title"), 300)
    return {
        "source": "akshare.stock_info_global_cls",
        "category": category,
        "raw_title": title,
        "raw_content_excerpt": _clean_text(_value(row, fields, "content"), MAX_AUDIT_TEXT),
        "published_date": _date_text(_value(row, fields, "publish_date")),
        "published_time": _clean_text(_value(row, fields, "publish_time"), 40),
        "url": _clean_text(_value(row, fields, "url"), 400),
        "matched_on": list(matched_on),
        "title_keyword_evidence": _title_keyword_evidence(title),
    }


def _evidence_fingerprint(item: Mapping[str, Any]) -> str:
    return "|".join((
        _clean_text(item.get("source"), 80),
        _clean_text(item.get("raw_title"), 300),
        _clean_text(item.get("published_date"), 20),
        _clean_text(item.get("published_time"), 20),
    ))


def _direction_counts(groups: Iterable[Sequence[Mapping[str, Any]]]) -> Tuple[int, int]:
    positive = 0
    negative = 0
    seen = set()
    for group in groups:
        for item in group:
            fingerprint = _evidence_fingerprint(item)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            keywords = item.get("title_keyword_evidence") or {}
            if keywords.get("positive_keywords"):
                positive += 1
            if keywords.get("negative_keywords"):
                negative += 1
    return positive, negative


def _filter_notices(
    candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], fields: Mapping[str, str]
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    code = candidate["code"]
    name = candidate["name"]
    for row in rows:
        row_code = _canonical_code(_value(row, fields, "code"))
        row_name = _clean_text(_value(row, fields, "name"), 80)
        title = _clean_text(_value(row, fields, "title"), 300)
        matched_on: List[str] = []
        if row_code and row_code == code:
            matched_on.append("stock_code")
        if name and (row_name == name or name in title):
            matched_on.append("stock_name")
        if not matched_on:
            continue
        result.append(_make_notice_item(row, fields, matched_on))
        if len(result) >= MAX_ANNOUNCEMENTS_PER_STOCK:
            break
    return result


def _filter_candidate_news(
    candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], fields: Mapping[str, str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    industry: List[Dict[str, Any]] = []
    policy: List[Dict[str, Any]] = []
    tokens = _sector_tokens(candidate)
    for row in rows:
        title = _clean_text(_value(row, fields, "title"), 300)
        if not title:
            continue
        matched_tokens = [token for token in tokens if token in title]
        keyword_evidence = _title_keyword_evidence(title)
        if matched_tokens and len(industry) < MAX_INDUSTRY_NEWS_PER_STOCK:
            industry.append(_make_news_item(row, fields, "industry_or_company_news", matched_tokens))
        if keyword_evidence["policy_keywords"] and len(policy) < MAX_POLICY_NEWS_PER_STOCK:
            matched = matched_tokens or ["global_policy_title_keyword"]
            policy.append(_make_news_item(row, fields, "policy_news", matched))
        if len(industry) >= MAX_INDUSTRY_NEWS_PER_STOCK and len(policy) >= MAX_POLICY_NEWS_PER_STOCK:
            break
    return industry, policy


def _global_market_items(rows: Sequence[Mapping[str, Any]], fields: Mapping[str, str]) -> List[Dict[str, Any]]:
    priority_words = (
        "纳斯达克", "标普", "道琼斯", "恒生", "日经", "富时", "德国", "法国",
        "韩国", "印度", "黄金", "原油", "NASDAQ", "Dow", "S&P", "Nikkei", "Hang Seng",
    )
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        name = _clean_text(_value(row, fields, "name"), 100)
        if not name:
            continue
        parsed.append({
            "source": _clean_text(_value(row, fields, "source"), 100) or "akshare.index_global_spot_em",
            "source_symbol": _clean_text(_value(row, fields, "source_symbol"), 40),
            "name": name,
            "latest": _to_float(_value(row, fields, "latest")),
            "change_pct": _to_float(_value(row, fields, "change_pct")),
            "change": _to_float(_value(row, fields, "change")),
            "open": _to_float(_value(row, fields, "open")),
            "previous_close": _to_float(_value(row, fields, "previous_close")),
            "quote_time": _clean_text(_value(row, fields, "quote_time"), 80),
            "high": _to_float(_value(row, fields, "high")),
            "low": _to_float(_value(row, fields, "low")),
            "raw_name": name,
            "classification_basis": "numeric_market_snapshot_only",
        })
    priority = [item for item in parsed if any(word in item["name"] for word in priority_words)]
    remainder = [item for item in parsed if item not in priority]
    return (priority + remainder)[:MAX_GLOBAL_MARKETS]


def _safe_external_fetch(ak_module: Any, include_notices: bool, analysis_day: date) -> Dict[str, Any]:
    try:
        ak, provider_label = _load_akshare(ak_module)
    except Exception as exc:
        status = _source_status("akshare", False, error=str(exc), missing_fields=["module"])
        global_rows, global_status = _fetch_global_markets(None)
        result = {
            "provider_label": "akshare_unavailable_with_tencent_global_fallback",
            "notice_rows": [], "notice_status": status,
            "news_rows": [], "news_status": status,
            "global_rows": global_rows, "global_status": global_status,
        }
        return result

    if include_notices:
        notice_rows, notice_status = _fetch_notices(ak, analysis_day)
    else:
        notice_rows = []
        notice_status = {
            "state": "not_requested", "complete": True,
            "function": "akshare.stock_notice_report", "requested": "", "rows_received": 0,
            "fields": {}, "missing_fields": [], "error": "", "attempts": [],
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    news_rows, news_status = _fetch_cls_news(ak)
    global_rows, global_status = _fetch_global_markets(ak)
    return {
        "provider_label": provider_label,
        "notice_rows": notice_rows, "notice_status": notice_status,
        "news_rows": news_rows, "news_status": news_status,
        "global_rows": global_rows, "global_status": global_status,
    }


def _build_candidate_evidence(
    candidates: Sequence[Mapping[str, Any]],
    notice_rows: Sequence[Mapping[str, Any]],
    notice_status: Mapping[str, Any],
    news_rows: Sequence[Mapping[str, Any]],
    news_status: Mapping[str, Any],
    global_items: Sequence[Mapping[str, Any]],
    source_complete: bool,
    include_notices: bool,
) -> Dict[str, Dict[str, Any]]:
    notice_fields = notice_status.get("fields") or {}
    news_fields = news_status.get("fields") or {}
    result: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        notices = _filter_notices(candidate, notice_rows, notice_fields) if include_notices and notice_fields else []
        industry, policy = _filter_candidate_news(candidate, news_rows, news_fields) if news_fields else ([], [])
        positive_count, negative_count = _direction_counts((notices, industry, policy))
        result[candidate["code"]] = {
            "code": candidate["code"],
            "name": candidate["name"],
            "sector": candidate.get("sector", ""),
            "candidate_date": candidate.get("candidate_date", ""),
            "announcements": notices,
            "industry_news": industry,
            "policy_news": policy,
            "external_market_snapshot": [dict(item) for item in global_items],
            "positive_count": positive_count,
            "negative_count": negative_count,
            "count_basis": "unique_titles_with_directional_keywords; mixed titles count in both",
            "data_complete": bool(source_complete),
            "missing_sources": [],
            "interpretation_warning": "仅保留标题关键词和数值快照证据，未理解或核验全文。",
        }
        missing = []
        if include_notices and not notice_status.get("complete"):
            missing.append("announcements")
        if not news_status.get("complete"):
            missing.append("industry_policy_news")
        # 外围状态由调用方统一追加，避免这里引入额外参数。
        result[candidate["code"]]["missing_sources"] = missing
    return result


def fetch_evening_evidence(
    candidates: Any,
    analysis_date: Any = None,
    ak_module: Any = None,
) -> Dict[str, Any]:
    """采集 19:30 修正所需的公告、行业/政策消息与外围市场证据。

    ``ak_module`` 可注入兼容对象用于离线测试；省略时在函数内部延迟导入
    :mod:`akshare`。返回结果不含交易建议。
    """

    analysis_day = _normalise_date(analysis_date)
    normalised, invalid = _normalise_candidates(candidates)
    fetched = _safe_external_fetch(ak_module, include_notices=True, analysis_day=analysis_day)
    statuses = {
        "announcements": fetched["notice_status"],
        "industry_policy_news": fetched["news_status"],
        "external_market": fetched["global_status"],
    }
    sources_complete = all(bool(status.get("complete")) for status in statuses.values())
    scope_complete = bool(normalised) and not invalid
    global_fields = fetched["global_status"].get("fields") or {}
    global_items = _global_market_items(fetched["global_rows"], global_fields) if global_fields else []
    evidence_by_code = _build_candidate_evidence(
        normalised, fetched["notice_rows"], fetched["notice_status"],
        fetched["news_rows"], fetched["news_status"], global_items,
        source_complete=sources_complete and scope_complete, include_notices=True,
    )
    if not fetched["global_status"].get("complete"):
        for evidence in evidence_by_code.values():
            evidence["missing_sources"].append("external_market")
            evidence["data_complete"] = False

    return {
        "module_version": MODULE_VERSION,
        "stage": "19:30_evening_revision",
        "analysis_date": analysis_day.isoformat(),
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "evidence_by_code": evidence_by_code,
        "data_complete": bool(sources_complete and scope_complete),
        "source_status": statuses,
        "data_source": [
            "akshare.stock_notice_report",
            "akshare.stock_info_global_cls",
            fetched["global_status"].get("selected_source") or "external_market_source_incomplete",
        ],
        "candidate_scope": {
            "received_count": len(normalised) + len(invalid),
            "valid_count": len(normalised),
            "invalid_candidates": invalid,
            "complete": scope_complete,
        },
        "provider_label": fetched["provider_label"],
        "interpretation_warning": "标题关键词仅作为证据，不代表已理解公告或新闻全文；接口不完整时不得推断为无风险。",
    }


def _recent_news_rows(
    rows: Sequence[Mapping[str, Any]], fields: Mapping[str, str], strategy_day: date
) -> Tuple[List[Mapping[str, Any]], bool, List[str]]:
    if not rows:
        return [], True, []
    date_field = fields.get("publish_date")
    if not date_field:
        return list(rows), False, ["publish_date"]
    # 周一盘前覆盖周五收盘后的周末消息；其他交易日也保留最多三个自然日。
    earliest = strategy_day - timedelta(days=3)
    selected: List[Mapping[str, Any]] = []
    unparseable = 0
    for row in rows:
        text = _date_text(row.get(date_field))
        try:
            row_day = _normalise_date(text)
        except Exception:
            unparseable += 1
            continue
        if earliest <= row_day <= strategy_day:
            selected.append(row)
    missing = [f"unparseable_publish_date_rows={unparseable}"] if unparseable else []
    return selected, not unparseable, missing


def _select_previous_candidate_cohort(
    candidates: Sequence[Mapping[str, Any]], strategy_day: date
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    dated: List[Tuple[date, Dict[str, Any]]] = []
    undated: List[Dict[str, Any]] = []
    invalid_dates: List[Dict[str, Any]] = []
    for candidate in candidates:
        text = candidate.get("candidate_date", "")
        if not text:
            undated.append(dict(candidate))
            continue
        try:
            candidate_day = _normalise_date(text)
        except Exception:
            invalid_dates.append(dict(candidate))
            continue
        if candidate_day < strategy_day:
            dated.append((candidate_day, dict(candidate)))
    if not dated:
        return [], {
            "complete": False,
            "reason": "no_verifiable_previous_candidate_date",
            "selected_candidate_date": "",
            "excluded_undated_codes": [item["code"] for item in undated],
            "excluded_invalid_date_codes": [item["code"] for item in invalid_dates],
            "excluded_non_previous_count": len(candidates) - len(undated) - len(invalid_dates),
        }
    latest_day = max(item[0] for item in dated)
    selected = [item[1] for item in dated if item[0] == latest_day]
    return selected, {
        "complete": not undated and not invalid_dates,
        "reason": "latest_candidate_date_before_strategy_date",
        "selected_candidate_date": latest_day.isoformat(),
        "excluded_undated_codes": [item["code"] for item in undated],
        "excluded_invalid_date_codes": [item["code"] for item in invalid_dates],
        "excluded_non_previous_count": len(candidates) - len(selected) - len(undated) - len(invalid_dates),
    }


def _quote_value(quote: Mapping[str, Any], names: Sequence[str]) -> Tuple[Any, str]:
    for name in names:
        if name in quote and quote[name] not in (None, ""):
            return quote[name], name
    return None, ""


def _parse_quote_datetime(value: Any) -> Optional[datetime]:
    text = _clean_text(value, 80)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 14:
        try:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("/", "-").replace(" ", "T", 1))
    except ValueError:
        return None


def _parse_candidate_quote(
    code: str, quote: Any, expected_strategy_day: Optional[date] = None
) -> Tuple[Dict[str, Any], List[str]]:
    if not isinstance(quote, Mapping):
        return {"available": False, "code": code, "raw_type": type(quote).__name__}, ["quote_mapping"]

    price_raw, price_field = _quote_value(quote, ("price", "current", "最新价", "现价"))
    previous_raw, previous_field = _quote_value(
        quote, ("previous_close", "prev_close", "pre_close", "昨收", "昨日收盘价")
    )
    pct_raw, pct_field = _quote_value(quote, ("pct", "change_pct", "涨跌幅", "percent"))
    open_raw, open_field = _quote_value(quote, ("open", "open_price", "今开", "开盘价"))
    amount_raw, amount_field = _quote_value(
        quote, ("amount_raw", "成交额原始值", "raw_amount")
    )
    time_raw, time_field = _quote_value(quote, ("quote_time", "time", "datetime", "更新时间"))
    name_raw, name_field = _quote_value(quote, ("name", "股票名称", "名称"))
    quote_code = _canonical_code(_quote_value(quote, ("code", "股票代码", "证券代码"))[0]) or code

    price = _to_float(price_raw)
    previous_close = _to_float(previous_raw)
    pct = _to_float(pct_raw)
    open_price = _to_float(open_raw)
    derived_previous = False
    if previous_close is None and price is not None and pct is not None and abs(100.0 + pct) > 1e-9:
        previous_close = price / (1.0 + pct / 100.0)
        derived_previous = True
    if pct is None and price is not None and previous_close and previous_close > 0:
        pct = (price / previous_close - 1.0) * 100.0

    auction_price = open_price if open_price and open_price > 0 else price
    auction_pct = None
    if auction_price is not None and previous_close and previous_close > 0:
        auction_pct = (auction_price / previous_close - 1.0) * 100.0
    elif pct is not None:
        auction_pct = pct

    missing: List[str] = []
    if quote_code != code:
        missing.append("returned_code_mismatch")
    if price is None or price <= 0:
        missing.append("price")
    if pct is None:
        missing.append("change_pct_or_previous_close")
    quote_datetime = _parse_quote_datetime(time_raw)
    if not _clean_text(time_raw, 80):
        missing.append("quote_time")
    elif quote_datetime is None:
        missing.append("quote_time_unparseable")
    elif expected_strategy_day is not None:
        if quote_datetime.date() != expected_strategy_day:
            missing.append("stale_quote_date")
        elif (quote_datetime.hour, quote_datetime.minute) < (9, 25):
            missing.append("quote_before_09_25")
    result = {
        "available": not missing,
        "code": code,
        "returned_code": quote_code,
        "name": _clean_text(name_raw, 80),
        "price": price,
        "previous_close": previous_close,
        "previous_close_derived_from_price_and_pct": derived_previous,
        "open_price": open_price,
        "auction_amount_raw": _to_float(amount_raw),
        "auction_amount_unit": "upstream_unspecified_do_not_infer",
        "auction_or_snapshot_price": auction_price,
        "snapshot_change_pct": round(pct, 4) if pct is not None else None,
        "auction_or_snapshot_change_pct": round(auction_pct, 4) if auction_pct is not None else None,
        "quote_time": _clean_text(time_raw, 80),
        "quote_time_verified": quote_datetime.isoformat(timespec="seconds") if quote_datetime else None,
        "quote_kind": "candidate_only_09_25_quote_snapshot",
        "calculation_basis": "open/previous_close when available; otherwise current snapshot pct",
        "source_fields": {
            "price": price_field, "previous_close": previous_field, "pct": pct_field,
            "open": open_field, "amount_raw": amount_field,
            "quote_time": time_field, "name": name_field,
        },
        "raw_scalar_fields": {
            _clean_text(key, 80): value
            for key, value in list(quote.items())[:40]
            if isinstance(value, (str, int, float, bool)) or value is None
        },
        "missing_fields": missing,
    }
    return result, missing


def fetch_preopen_evidence(
    candidates: Any,
    strategy_date: Any,
    stage: str,
    quote_fetcher: Optional[Callable[[str], Any]] = None,
    ak_module: Any = None,
) -> Dict[str, Any]:
    """采集 09:00 或 09:25 的盘前真实证据。

    * ``09:00``：只取隔夜新闻/政策及外围市场，明确没有集合竞价数据；
      即使传入 ``quote_fetcher`` 也不会调用。
    * ``09:25``：只对“候选日期早于策略日且最近一批”的股票调用
      ``quote_fetcher(code)``，不扫描全市场。个股缺报价会单独标记不完整。
    """

    stage = _clean_text(stage, 20)
    if stage not in {"09:00", "09:25"}:
        raise ValueError("stage 只能是 '09:00' 或 '09:25'")
    strategy_day = _normalise_date(strategy_date)
    normalised, invalid = _normalise_candidates(candidates)
    cohort, cohort_status = _select_previous_candidate_cohort(normalised, strategy_day)

    fetched = _safe_external_fetch(ak_module, include_notices=False, analysis_day=strategy_day)
    news_fields = fetched["news_status"].get("fields") or {}
    recent_news, recent_complete, recent_missing = _recent_news_rows(
        fetched["news_rows"], news_fields, strategy_day
    ) if news_fields else ([], False, ["news_field_map"])
    news_status = dict(fetched["news_status"])
    if not recent_complete:
        news_status["complete"] = False
        news_status["state"] = "incomplete"
        news_status["missing_fields"] = list(news_status.get("missing_fields") or []) + recent_missing
        news_status["error"] = _clean_text(
            (news_status.get("error", "") + "; 部分新闻发布日期无法校验").strip("; "), 300
        )
    global_fields = fetched["global_status"].get("fields") or {}
    global_items = _global_market_items(fetched["global_rows"], global_fields) if global_fields else []

    source_complete = bool(news_status.get("complete") and fetched["global_status"].get("complete"))
    scope_complete = bool(cohort) and cohort_status.get("complete", False) and not invalid
    evidence_by_code = _build_candidate_evidence(
        cohort, [], {"fields": {}, "complete": True}, recent_news, news_status,
        global_items, source_complete=source_complete and scope_complete,
        include_notices=False,
    )
    if not fetched["global_status"].get("complete"):
        for evidence in evidence_by_code.values():
            evidence["missing_sources"].append("external_market")
            evidence["data_complete"] = False

    quote_status: Dict[str, Any]
    if stage == "09:00":
        for evidence in evidence_by_code.values():
            evidence["auction_available"] = False
            evidence["auction_or_quote_snapshot"] = None
            evidence["auction_note"] = "09:00 集合竞价尚未开始，本阶段不调用任何股票报价接口。"
        quote_status = {
            "state": "not_applicable_at_09_00",
            "complete": True,
            "function": "quote_fetcher",
            "requested": "none",
            "rows_received": 0,
            "fields": {}, "missing_fields": [], "error": "", "attempts": [],
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "note": "09:00无集合竞价数据，未调用quote_fetcher。",
        }
    else:
        attempts: List[Dict[str, Any]] = []
        missing_codes: List[str] = []
        if not callable(quote_fetcher):
            missing_codes = [candidate["code"] for candidate in cohort]
            for code in missing_codes:
                evidence_by_code[code]["auction_available"] = False
                evidence_by_code[code]["auction_or_quote_snapshot"] = None
                evidence_by_code[code]["data_complete"] = False
                evidence_by_code[code]["missing_sources"].append("candidate_quote_snapshot")
            quote_status = _source_status(
                "quote_fetcher", False, requested="candidate_codes_only",
                missing_fields=["quote_fetcher"], error="未提供候选股报价函数",
            )
        else:
            for candidate in cohort:
                code = candidate["code"]
                try:
                    raw_quote = quote_fetcher(code)
                    parsed, missing = _parse_candidate_quote(code, raw_quote, strategy_day)
                    attempts.append({"code": code, "ok": not missing, "missing_fields": missing})
                except Exception as exc:
                    parsed = {"available": False, "code": code, "error": f"{type(exc).__name__}: {exc}"}
                    missing = ["quote_fetch_failed"]
                    attempts.append({"code": code, "ok": False, "error": _clean_text(str(exc), 240)})
                evidence_by_code[code]["auction_available"] = bool(parsed.get("available"))
                evidence_by_code[code]["auction_or_quote_snapshot"] = parsed
                if missing:
                    missing_codes.append(code)
                    evidence_by_code[code]["data_complete"] = False
                    evidence_by_code[code]["missing_sources"].append("candidate_quote_snapshot")
            quote_status = _source_status(
                "quote_fetcher", bool(cohort) and not missing_codes, len(cohort) - len(missing_codes),
                requested="candidate_codes_only", fields={"return": "mapping"},
                missing_fields=[f"missing_quote:{code}" for code in missing_codes],
                error="" if not missing_codes else "一个或多个昨日候选缺少有效报价",
                attempts=attempts,
            )

    statuses = {
        "overnight_industry_policy_news": news_status,
        "external_market": fetched["global_status"],
        "candidate_quote_snapshot": quote_status,
    }
    all_complete = bool(
        source_complete and scope_complete and quote_status.get("complete")
    )
    if not all_complete:
        for evidence in evidence_by_code.values():
            if stage == "09:25" and not evidence.get("auction_available"):
                evidence["data_complete"] = False
            elif not (source_complete and scope_complete):
                evidence["data_complete"] = False

    return {
        "module_version": MODULE_VERSION,
        "stage": f"{stage}_preopen_revision",
        "strategy_date": strategy_day.isoformat(),
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "evidence_by_code": evidence_by_code,
        "data_complete": all_complete,
        "source_status": statuses,
        "data_source": [
            "akshare.stock_info_global_cls",
            fetched["global_status"].get("selected_source") or "external_market_source_incomplete",
            "injected_candidate_quote_fetcher" if quote_fetcher is not None else "candidate_quote_fetcher_missing",
        ],
        "candidate_scope": {
            "received_count": len(normalised) + len(invalid),
            "valid_count": len(normalised),
            "selected_count": len(cohort),
            "invalid_candidates": invalid,
            **cohort_status,
        },
        "auction_available": stage == "09:25" and bool(cohort) and quote_status.get("complete", False),
        "stage_note": (
            "09:00无集合竞价；本结果只含截至采集时可得的隔夜新闻与外围快照。"
            if stage == "09:00" else
            "09:25只请求昨日最近候选池，不做全市场扫描；报价是当时快照，不声称等同最终可成交价。"
        ),
        "provider_label": fetched["provider_label"],
        "interpretation_warning": "标题关键词仅作为证据，不代表已理解或核验全文；数据不完整时不得推断为无风险。",
    }


__all__ = [
    "MODULE_VERSION",
    "fetch_evening_evidence",
    "fetch_preopen_evidence",
]
