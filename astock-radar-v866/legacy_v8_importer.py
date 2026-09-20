from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


SOURCE_NAME = "V8_LEGACY"
SUPPORTED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".json", ".jsonl", ".csv", ".txt", ".log"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "outputs", "patch_tmp", "node_modules"}
SKIP_FILES = {
    "live_pit.db",
    "v8_legacy_data_inventory.json",
    "v8_legacy_data_inventory.txt",
    "v8_import_audit.json",
    "v8_import_audit.txt",
}
DATE_FIELDS = (
    "decision_time",
    "observed_at",
    "effective_at",
    "trade_date",
    "signal_time",
    "outcome_time",
    "updated_at",
    "review_date",
    "runup_start",
    "ts",
    "date",
)
CODE_FIELDS = ("ts_code", "code", "symbol")
SIGNAL_HINTS = ("signal", "tracking", "watch", "position", "pool")
OUTCOME_HINTS = ("outcome", "t1", "review")

SIGNAL_COLUMNS = (
    "trade_date",
    "decision_time",
    "observed_at",
    "ts_code",
    "name",
    "price",
    "score",
    "pool",
    "signal_type",
    "market_phase",
    "market_temperature",
    "sector",
    "sector_strength",
    "sector_resonance",
    "main_flow_score",
    "main_flow_amount",
    "auction_quality",
    "chip_lock_score",
    "chip_cost",
    "chip_concentration",
    "trend_score",
    "buy_timing_score",
    "support",
    "strong_support",
    "resistance",
    "strong_resistance",
    "wash_score",
    "true_drop_score",
    "rr",
    "event_risk",
    "distribution_risk",
    "overnight_risk",
    "data_quality",
)

NUMERIC_SIGNAL_COLUMNS = {
    "price",
    "score",
    "market_temperature",
    "sector_strength",
    "sector_resonance",
    "main_flow_score",
    "main_flow_amount",
    "auction_quality",
    "chip_lock_score",
    "chip_cost",
    "chip_concentration",
    "trend_score",
    "buy_timing_score",
    "support",
    "strong_support",
    "resistance",
    "strong_resistance",
    "wash_score",
    "true_drop_score",
    "rr",
    "event_risk",
    "distribution_risk",
    "overnight_risk",
    "data_quality",
}

SIGNAL_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": ("trade_date", "date"),
    "decision_time": ("decision_time", "signal_time", "confirm_time", "discovery_time", "entry_time", "ts"),
    "observed_at": ("observed_at", "_evidence_cutoff", "evidence_cutoff", "updated"),
    "ts_code": ("ts_code", "code", "symbol"),
    "name": ("name", "stock_name"),
    "price": ("price", "executable_price", "entry_price", "discovery_price", "confirm_price", "close"),
    "score": ("score", "opportunity_score", "individual_score", "primary_rule_score"),
    "pool": ("pool", "source", "channel", "stage", "stages"),
    "signal_type": ("signal_type", "action", "decision"),
    "market_phase": ("market_phase", "market_regime"),
    "market_temperature": ("market_temperature",),
    "sector": ("sector", "industry"),
    "sector_strength": ("sector_strength", "sector_score"),
    "sector_resonance": ("sector_resonance",),
    "main_flow_score": ("main_flow_score", "fund_quality_score"),
    "main_flow_amount": ("main_flow_amount",),
    "auction_quality": ("auction_quality",),
    "chip_lock_score": ("chip_lock_score",),
    "chip_cost": ("chip_cost",),
    "chip_concentration": ("chip_concentration",),
    "trend_score": ("trend_score", "trend_quality_score"),
    "buy_timing_score": ("buy_timing_score", "buy_score"),
    "support": ("support",),
    "strong_support": ("strong_support",),
    "resistance": ("resistance",),
    "strong_resistance": ("strong_resistance",),
    "wash_score": ("wash_score",),
    "true_drop_score": ("true_drop_score",),
    "rr": ("rr",),
    "event_risk": ("event_risk",),
    "distribution_risk": ("distribution_risk",),
    "overnight_risk": ("overnight_risk",),
    "data_quality": ("data_quality",),
}

TARGET_SIGNAL_SCHEMA = """
CREATE TABLE signal_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    trade_date TEXT,
    decision_time TEXT,
    ts_code TEXT NOT NULL,
    name TEXT,
    price REAL,
    score REAL,
    pool TEXT,
    market_phase TEXT,
    sector TEXT,
    auction_quality REAL,
    main_flow_score REAL,
    chip_lock_score REAL,
    trend_score REAL,
    buy_timing_score REAL,
    rr REAL,
    event_risk REAL,
    distribution_risk REAL,
    data_quality REAL,
    source TEXT NOT NULL,
    observed_at TEXT,
    effective_at TEXT,
    is_pit_safe INTEGER NOT NULL DEFAULT 0,
    features_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    signal_type TEXT,
    market_temperature REAL,
    sector_strength REAL,
    sector_resonance REAL,
    main_flow_amount REAL,
    chip_cost REAL,
    chip_concentration REAL,
    support REAL,
    strong_support REAL,
    resistance REAL,
    strong_resistance REAL,
    wash_score REAL,
    true_drop_score REAL,
    overnight_risk REAL,
    primary_rule_score REAL,
    legacy_source_file TEXT,
    legacy_row_id TEXT,
    legacy_version TEXT,
    imported_at TEXT,
    legacy_push_status TEXT,
    legacy_pushed_at TEXT
)
"""

TARGET_OUTCOME_SCHEMA = """
CREATE TABLE signal_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_snapshot_id TEXT NOT NULL,
    outcome_time TEXT,
    t1 REAL,
    t3 REAL,
    t5 REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    triple_barrier_label INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    signal_id TEXT,
    ts_code TEXT,
    decision_time TEXT,
    t1_return REAL,
    t3_return REAL,
    t5_return REAL,
    t7_return REAL,
    t1_status TEXT,
    t7_status TEXT,
    stop_hit INTEGER,
    profit_hit INTEGER,
    wash_result TEXT,
    risk_result TEXT,
    legacy_source_file TEXT,
    legacy_row_id TEXT,
    legacy_version TEXT,
    imported_at TEXT,
    FOREIGN KEY(signal_snapshot_id) REFERENCES signal_snapshot(snapshot_id)
)
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def safe_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def normalize_ts_code(value: Any) -> str | None:
    if value is None:
        return None
    code = str(value).strip().upper()
    if not code:
        return None
    code = code.replace("XSHG", "SH").replace("XSHE", "SZ")
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        return code
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", code):
        return f"{code[2:]}.{code[:2]}"
    if re.fullmatch(r"\d{6}", code):
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        if code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
    return code


def parse_datetime(value: Any) -> tuple[str | None, datetime | None, bool]:
    if value is None:
        return None, None, False
    text = str(value).strip()
    if not text:
        return None, None, False
    candidate = text.replace("Z", "+00:00")
    formats = (
        ("%Y-%m-%d %H:%M:%S.%f", True),
        ("%Y-%m-%d %H:%M:%S", True),
        ("%Y-%m-%d %H:%M", True),
        ("%Y%m%d%H%M%S", True),
        ("%Y-%m-%d", False),
        ("%Y%m%d", False),
    )
    try:
        parsed = datetime.fromisoformat(candidate)
        has_time = "T" in text or " " in text
        normalized = parsed.isoformat(sep=" ", timespec="seconds") if has_time else parsed.date().isoformat()
        return normalized, parsed, has_time
    except ValueError:
        pass
    for fmt, has_time in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            normalized = parsed.isoformat(sep=" ", timespec="seconds") if has_time else parsed.date().isoformat()
            return normalized, parsed, has_time
        except ValueError:
            continue
    return text, None, False


def normalize_trade_date(value: Any) -> str | None:
    normalized, parsed, _ = parse_datetime(value)
    if parsed is not None:
        return parsed.date().isoformat()
    return normalized


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def scalar_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return " | ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return None
    return str(value)


def combine_explicit_date_time(trade_date: str | None, value: Any) -> Any:
    if value is None or not trade_date:
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?", text):
        return f"{trade_date} {text}"
    return value


def first_present(record: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for key in aliases:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def merge_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    for payload_key in ("payload", "features", "pre_features"):
        payload = parse_json_object(record.get(payload_key))
        for key, value in payload.items():
            merged.setdefault(str(key), value)
    return merged


def legacy_version(path: Path, source_root: Path) -> str:
    haystack = " ".join((path.name, path.parent.name, source_root.name))
    patterns = (
        r"(?i)v(?:ersion)?[_\-. ]?(\d+)[_\-. ]?(\d+)[_\-. ]?(\d+)",
        r"(?i)v(?:ersion)?[_\-. ]?(\d+)[_\-. ]?(\d+)",
        r"(?i)v(\d)(\d)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, haystack)
        if match:
            return "V" + ".".join(match.groups())
    return "V8_LEGACY"


def relative_source(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def sqlite_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def sqlite_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [str(row[0]) for row in rows]


def sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({quote_ident(table)})")]


def table_time_range(connection: sqlite3.Connection, table: str, columns: Sequence[str]) -> dict[str, Any]:
    ranges: list[dict[str, Any]] = []
    for column in DATE_FIELDS:
        if column not in columns:
            continue
        quoted_table = quote_ident(table)
        quoted_column = quote_ident(column)
        row = connection.execute(
            f"SELECT MIN({quoted_column}), MAX({quoted_column}) FROM {quoted_table} "
            f"WHERE {quoted_column} IS NOT NULL AND TRIM(CAST({quoted_column} AS TEXT)) <> ''"
        ).fetchone()
        if row and (row[0] is not None or row[1] is not None):
            ranges.append({"field": column, "start": row[0], "end": row[1]})
    starts = [str(item["start"]) for item in ranges if item["start"] is not None]
    ends = [str(item["end"]) for item in ranges if item["end"] is not None]
    return {
        "start": min(starts) if starts else None,
        "end": max(ends) if ends else None,
        "by_field": ranges,
    }


def table_stock_count(connection: sqlite3.Connection, table: str, columns: Sequence[str]) -> int:
    for column in CODE_FIELDS:
        if column in columns:
            quoted_column = quote_ident(column)
            quoted_table = quote_ident(table)
            return int(
                connection.execute(
                    f"SELECT COUNT(DISTINCT {quoted_column}) FROM {quoted_table} "
                    f"WHERE {quoted_column} IS NOT NULL AND TRIM(CAST({quoted_column} AS TEXT)) <> ''"
                ).fetchone()[0]
            )
    return 0


def embedded_fields(connection: sqlite3.Connection, table: str, columns: Sequence[str]) -> list[str]:
    payload_columns = [column for column in ("payload", "features", "pre_features", "source_map") if column in columns]
    found: set[str] = set()
    for column in payload_columns:
        query = (
            f"SELECT {quote_ident(column)} FROM {quote_ident(table)} "
            f"WHERE {quote_ident(column)} IS NOT NULL LIMIT 100"
        )
        for row in connection.execute(query):
            found.update(parse_json_object(row[0]).keys())
    return sorted(str(item) for item in found)


def classify_structure(name: str, fields: Sequence[str], row_count: int) -> tuple[list[str], str, bool]:
    lower_name = name.lower()
    field_set = {item.lower() for item in fields}
    kinds: list[str] = []
    basis = "No recognized migration schema"
    pit_evidence = {"observed_at", "decision_time"}.issubset(field_set)

    if lower_name in {"signals", "signal_snapshot", "latent_signal", "v8_signal_decisions"}:
        kinds.append("signal_snapshot")
        basis = f"Recognized table name: {lower_name}"
    elif (
        ("ts_code" in field_set or "code" in field_set)
        and ("trade_date" in field_set or "date" in field_set)
        and bool({"entry_time", "discovery_time", "decision_time", "signal_time"} & field_set)
        and bool(
            {
                "score",
                "opportunity_score",
                "individual_score",
                "selected",
                "qualified",
                "decision",
            }
            & field_set
        )
    ):
        kinds.append("signal_snapshot")
        basis = "Explicit code/date/time plus score or decision-state fields"
    elif "ts_code" in field_set and ("score" in field_set or "primary_rule_score" in field_set):
        if any(hint in lower_name for hint in SIGNAL_HINTS):
            kinds.append("signal_snapshot")
            basis = "Signal-like filename/table plus explicit ts_code and score fields"

    if lower_name in {"outcomes", "signal_outcome", "v8_execution_outcomes"}:
        kinds.append("signal_outcome")
        basis = f"Recognized table name: {lower_name}"
    elif ("t1_return" in field_set or "t1" in field_set) and any(hint in lower_name for hint in OUTCOME_HINTS):
        kinds.append("signal_outcome")
        basis = "Outcome-like filename/table plus explicit return field"

    if lower_name == "feature_snapshot":
        kinds.append("pit_feature_reference")
        basis = "Recognized PIT feature snapshot table"
    if lower_name == "v8_decision_history":
        kinds.append("pit_evidence_reference")
        basis = "Decision history contains explicit evaluation_time and evidence_cutoff"
    if lower_name == "v8_discovery_candidates":
        kinds.append("signal_enrichment_reference")
        basis = "Discovery candidates enrich decisions but are not duplicated as Primary Signals"
    if lower_name in {"v8_push_state", "v8_signal_delivery"}:
        kinds.append("push_reference")
        basis = "Recognized V8 push/delivery state table"
    if lower_name in {
        "v63_prelaunch_state",
        "v63_daily_review_push_state",
        "v64_intraday_alert_state",
        "wework_message_archive",
        "wework_push_log",
        "wework_pending",
    }:
        kinds.append("push_or_tracking_reference")
        basis = "Recognized legacy push/tracking state filename"
    if lower_name in {"limitup_day", "ecology_daily", "first_board_sample"}:
        kinds.append("research_reference_only")
        basis = "Recognized market/research table; not treated as a trading signal"
    if lower_name == "missed_case":
        kinds.append("review_reference_only")
        basis = "Recognized missed-case review table; no fabricated signal link"

    return sorted(set(kinds)), basis, bool(row_count and pit_evidence)


def inspect_sqlite(path: Path, root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file_name": path.name,
        "relative_path": relative_source(path, root),
        "type": "sqlite",
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "row_count": 0,
        "line_count": None,
        "time_range": {"start": None, "end": None},
        "stock_count": 0,
        "fields": [],
        "migratable_data_types": [],
        "possible_pit_history": False,
        "tables": [],
        "error": None,
    }
    try:
        with closing(sqlite_ro(path)) as connection:
            all_fields: set[str] = set()
            all_kinds: set[str] = set()
            starts: list[str] = []
            ends: list[str] = []
            stocks: set[str] = set()
            for table in sqlite_tables(connection):
                columns = sqlite_columns(connection, table)
                count = int(connection.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0])
                time_range = table_time_range(connection, table, columns)
                stock_count = table_stock_count(connection, table, columns)
                payload_fields = embedded_fields(connection, table, columns)
                kinds, basis, possible_pit = classify_structure(table, columns + payload_fields, count)
                result["tables"].append(
                    {
                        "name": table,
                        "row_count": count,
                        "time_range": time_range,
                        "stock_count": stock_count,
                        "fields": columns,
                        "embedded_payload_fields": payload_fields,
                        "migratable_data_types": kinds,
                        "classification_basis": basis,
                        "possible_pit_history": possible_pit,
                    }
                )
                result["row_count"] += count
                result["stock_count"] += stock_count
                all_fields.update(columns)
                all_kinds.update(kinds)
                if time_range["start"] is not None:
                    starts.append(str(time_range["start"]))
                if time_range["end"] is not None:
                    ends.append(str(time_range["end"]))
                if "ts_code" in columns and count:
                    for row in connection.execute(
                        f"SELECT DISTINCT ts_code FROM {quote_ident(table)} WHERE ts_code IS NOT NULL"
                    ):
                        code = normalize_ts_code(row[0])
                        if code:
                            stocks.add(code)
                result["possible_pit_history"] = result["possible_pit_history"] or possible_pit
            result["fields"] = sorted(all_fields)
            result["migratable_data_types"] = sorted(all_kinds)
            result["time_range"] = {
                "start": min(starts) if starts else None,
                "end": max(ends) if ends else None,
            }
            result["stock_count"] = len(stocks) if stocks else result["stock_count"]
    except (OSError, sqlite3.Error) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def structured_records(path: Path) -> tuple[list[dict[str, Any]], list[str], str | None]:
    try:
        if path.suffix.lower() == ".csv":
            text = read_text(path)
            reader = csv.DictReader(text.splitlines())
            records = [dict(row) for row in reader]
            return records, list(reader.fieldnames or []), None
        if path.suffix.lower() == ".jsonl":
            records: list[dict[str, Any]] = []
            for line_number, line in enumerate(read_text(path).splitlines(), start=1):
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, Mapping):
                    record = dict(parsed)
                    record.setdefault("_legacy_line_number", line_number)
                    records.append(record)
            fields = sorted({str(key) for item in records for key in item})
            return records, fields, None
        payload = json.loads(read_text(path))
        if isinstance(payload, list):
            records = [dict(item) for item in payload if isinstance(item, Mapping)]
            fields = sorted({str(key) for item in records for key in item})
            return records, fields, None
        if isinstance(payload, Mapping):
            for key in ("signals", "signal_snapshot", "rows", "data", "items", "records"):
                value = payload.get(key)
                if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
                    records = [dict(item) for item in value]
                    fields = sorted({str(field) for item in records for field in item})
                    return records, fields, None
            if payload and all(isinstance(value, Mapping) for value in payload.values()):
                records = []
                for mapping_key, value in payload.items():
                    record = dict(value)
                    record.setdefault("_legacy_mapping_key", str(mapping_key))
                    records.append(record)
                fields = sorted({str(field) for item in records for field in item})
                return records, fields, None
            return [dict(payload)], sorted(str(key) for key in payload), None
        return [], [], "Top-level JSON is not an object or list of objects"
    except (OSError, csv.Error, ValueError, json.JSONDecodeError) as exc:
        return [], [], f"{type(exc).__name__}: {exc}"


def inspect_structured(path: Path, root: Path) -> dict[str, Any]:
    records, fields, error = structured_records(path)
    kinds, basis, possible_pit = classify_structure(path.stem, fields, len(records))
    dates: list[str] = []
    stocks: set[str] = set()
    for record in records[:100000]:
        for key in DATE_FIELDS:
            if record.get(key) not in (None, ""):
                dates.append(str(record[key]))
        for key in CODE_FIELDS:
            code = normalize_ts_code(record.get(key))
            if code:
                stocks.add(code)
                break
    return {
        "file_name": path.name,
        "relative_path": relative_source(path, root),
        "type": path.suffix.lower().lstrip("."),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "row_count": len(records),
        "line_count": sum(1 for _ in path.open("rb")),
        "time_range": {"start": min(dates) if dates else None, "end": max(dates) if dates else None},
        "stock_count": len(stocks),
        "fields": fields,
        "migratable_data_types": kinds,
        "classification_basis": basis,
        "possible_pit_history": possible_pit,
        "tables": [],
        "error": error,
    }


def inspect_text(path: Path, root: Path) -> dict[str, Any]:
    error = None
    try:
        line_count = sum(1 for _ in path.open("rb"))
    except OSError as exc:
        line_count = 0
        error = f"{type(exc).__name__}: {exc}"
    kinds, basis, possible_pit = classify_structure(path.stem, [], line_count)
    return {
        "file_name": path.name,
        "relative_path": relative_source(path, root),
        "type": path.suffix.lower().lstrip("."),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "row_count": line_count,
        "line_count": line_count,
        "time_range": {"start": None, "end": None},
        "stock_count": 0,
        "fields": [],
        "migratable_data_types": kinds,
        "classification_basis": (
            basis if kinds else "Unstructured text retained in inventory; no fields inferred"
        ),
        "possible_pit_history": possible_pit,
        "tables": [],
        "error": error,
    }


def discover_files(root: Path, target_db: Path | None = None) -> list[Path]:
    files: list[Path] = []
    target_resolved = target_db.resolve() if target_db else None
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part.lower() in SKIP_DIRS for part in relative_parts[:-1]):
            continue
        if path.name.lower() in SKIP_FILES:
            continue
        if target_resolved is not None and path.resolve() == target_resolved:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix().lower())


def build_inventory(root: Path, target_db: Path | None = None) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in discover_files(root, target_db):
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            item = inspect_sqlite(path, root)
        elif path.suffix.lower() in {".json", ".jsonl", ".csv"}:
            item = inspect_structured(path, root)
        else:
            item = inspect_text(path, root)
        files.append(item)

    def dated_values(field: str) -> list[str]:
        normalized: list[str] = []
        for item in files:
            value = item["time_range"].get(field)
            if not value:
                continue
            parsed_text, parsed, _ = parse_datetime(value)
            if parsed is not None and parsed.year >= 1990 and parsed_text:
                normalized.append(parsed_text)
        return normalized

    starts = dated_values("start")
    ends = dated_values("end")
    signal_rows = 0
    research_rows = 0
    for item in files:
        if item["type"] == "sqlite":
            for table in item["tables"]:
                kinds = set(table["migratable_data_types"])
                if "signal_snapshot" in kinds:
                    signal_rows += int(table["row_count"])
                if "research_reference_only" in kinds or "review_reference_only" in kinds:
                    research_rows += int(table["row_count"])
        elif "signal_snapshot" in item["migratable_data_types"]:
            signal_rows += int(item["row_count"])

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "source_root": str(root.resolve()),
        "read_only_scan": True,
        "summary": {
            "discovered_file_count": len(files),
            "migratable_file_count": sum(bool(item["migratable_data_types"]) for item in files),
            "signal_candidate_rows": signal_rows,
            "research_reference_rows_not_signals": research_rows,
            "earliest_value": min(starts) if starts else None,
            "latest_value": max(ends) if ends else None,
            "files_with_errors": sum(bool(item["error"]) for item in files),
        },
        "files": files,
        "rules": {
            "no_field_guessing": True,
            "unstructured_text_policy": "Inventory only; never imported without an explicit schema",
            "market_research_policy": "limitup/ecology/review rows are not treated as trading signals",
            "pit_policy": "PIT is safe only when explicit observed_at and decision_time are parseable with time precision and observed_at <= decision_time",
        },
    }


def inventory_text(inventory: Mapping[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "V8 LEGACY DATA INVENTORY",
        "=" * 72,
        f"Generated at: {inventory['generated_at']}",
        f"Source root: {inventory['source_root']}",
        "Scan mode: READ ONLY",
        "",
        f"Files found: {summary['discovered_file_count']}",
        f"Files with recognizable migration/reference schemas: {summary['migratable_file_count']}",
        f"Signal candidate rows: {summary['signal_candidate_rows']}",
        f"Research/reference rows not counted as signals: {summary['research_reference_rows_not_signals']}",
        f"Observed value range: {summary['earliest_value']} -> {summary['latest_value']}",
        f"Files with scan errors: {summary['files_with_errors']}",
        "",
        "FILES",
        "-" * 72,
    ]
    for item in inventory["files"]:
        lines.extend(
            [
                f"File: {item['relative_path']}",
                f"  Type: {item['type']}; rows/lines: {item['row_count']}; bytes: {item['bytes']}",
                f"  Time range: {item['time_range']['start']} -> {item['time_range']['end']}",
                f"  Stocks: {item['stock_count']}",
                f"  Fields: {', '.join(item['fields']) if item['fields'] else 'NONE/UNSTRUCTURED'}",
                f"  Migratable/reference types: {', '.join(item['migratable_data_types']) if item['migratable_data_types'] else 'NONE'}",
                f"  Possible PIT history: {item['possible_pit_history']}",
                f"  SHA256: {item['sha256']}",
            ]
        )
        if item.get("classification_basis"):
            lines.append(f"  Classification: {item['classification_basis']}")
        if item.get("error"):
            lines.append(f"  Error: {item['error']}")
        for table in item.get("tables", []):
            lines.extend(
                [
                    f"  Table: {table['name']} ({table['row_count']} rows)",
                    f"    Time: {table['time_range']['start']} -> {table['time_range']['end']}; stocks: {table['stock_count']}",
                    f"    Fields: {', '.join(table['fields'])}",
                    f"    Embedded payload fields: {', '.join(table['embedded_payload_fields']) if table['embedded_payload_fields'] else 'NONE'}",
                    f"    Migration/reference: {', '.join(table['migratable_data_types']) if table['migratable_data_types'] else 'NONE'}",
                    f"    PIT possible: {table['possible_pit_history']}; basis: {table['classification_basis']}",
                ]
            )
        lines.append("")
    lines.extend(
        [
            "POLICY",
            "-" * 72,
            "Missing legacy fields remain NULL. Current market data is never used to backfill history.",
            "Rows without provable timestamps remain visible but are excluded from Meta/Factor/Alpha/CPCV eligibility.",
            "Limit-up ecology and review records are retained as references, not fabricated into Primary Signals.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(safe_json(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_reports(inventory: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "V8_LEGACY_DATA_INVENTORY.json", inventory)
    (output_dir / "V8_LEGACY_DATA_INVENTORY.txt").write_text(inventory_text(inventory), encoding="utf-8")


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def rebuild_table(
    connection: sqlite3.Connection,
    table: str,
    schema: str,
    nullable_columns: set[str],
    required_columns: set[str],
) -> None:
    if not table_exists(connection, table):
        connection.execute(schema)
        return
    info = list(connection.execute(f"PRAGMA table_info({quote_ident(table)})"))
    existing = {str(row[1]) for row in info}
    not_null = {str(row[1]) for row in info if int(row[3]) == 1}
    if required_columns.issubset(existing) and not (nullable_columns & not_null):
        return
    old_table = f"{table}__before_v8_legacy_import"
    connection.execute(f"DROP TABLE IF EXISTS {quote_ident(old_table)}")
    connection.execute(f"ALTER TABLE {quote_ident(table)} RENAME TO {quote_ident(old_table)}")
    connection.execute(schema)
    new_columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quote_ident(table)})")}
    common = sorted(existing & new_columns)
    if common:
        column_sql = ", ".join(quote_ident(column) for column in common)
        connection.execute(
            f"INSERT INTO {quote_ident(table)} ({column_sql}) SELECT {column_sql} FROM {quote_ident(old_table)}"
        )
    connection.execute(f"DROP TABLE {quote_ident(old_table)}")


def ensure_target_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if table_exists(connection, "signal_outcome"):
            connection.execute("DROP TABLE IF EXISTS signal_outcome__before_v8_legacy_import")
            connection.execute("ALTER TABLE signal_outcome RENAME TO signal_outcome__before_v8_legacy_import")
        rebuild_table(
            connection,
            "signal_snapshot",
            TARGET_SIGNAL_SCHEMA,
            {"trade_date", "decision_time", "observed_at", "data_quality"},
            {
                "legacy_source_file",
                "legacy_row_id",
                "legacy_version",
                "primary_rule_score",
                "signal_type",
                "legacy_push_status",
                "legacy_pushed_at",
            },
        )
        if table_exists(connection, "signal_outcome__before_v8_legacy_import"):
            connection.execute(TARGET_OUTCOME_SCHEMA)
            old_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(signal_outcome__before_v8_legacy_import)")
            }
            new_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(signal_outcome)")}
            common = sorted(old_columns & new_columns)
            if common:
                column_sql = ", ".join(quote_ident(column) for column in common)
                connection.execute(
                    f"INSERT INTO signal_outcome ({column_sql}) "
                    f"SELECT {column_sql} FROM signal_outcome__before_v8_legacy_import"
                )
            connection.execute("DROP TABLE signal_outcome__before_v8_legacy_import")
        elif not table_exists(connection, "signal_outcome"):
            connection.execute(TARGET_OUTCOME_SCHEMA)

        connection.execute("CREATE INDEX IF NOT EXISTS ix_signal_date_code ON signal_snapshot(trade_date, ts_code)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_signal_source_time ON signal_snapshot(source, decision_time)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_outcome_signal ON signal_outcome(signal_snapshot_id)")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_legacy_source "
            "ON signal_snapshot(legacy_version, legacy_source_file, legacy_row_id) "
            "WHERE legacy_row_id IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_outcome_legacy_source "
            "ON signal_outcome(legacy_version, legacy_source_file, legacy_row_id) "
            "WHERE legacy_row_id IS NOT NULL"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


def deterministic_snapshot_id(version: str, source_file: str, row_id: str) -> str:
    raw = f"{version}|{source_file}|{row_id}".encode("utf-8")
    return "legacy-" + hashlib.sha256(raw).hexdigest()[:32]


def normalized_signal(
    record: Mapping[str, Any],
    source_file: str,
    row_id: str,
    version: str,
    table: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    merged = merge_payload(record)
    output: dict[str, Any] = {}
    for target, aliases in SIGNAL_ALIASES.items():
        value = first_present(merged, aliases)
        output[target] = as_float(value) if target in NUMERIC_SIGNAL_COLUMNS else value

    output["ts_code"] = normalize_ts_code(output.get("ts_code"))
    if not output["ts_code"]:
        return None, ["missing ts_code"]
    output["trade_date"] = normalize_trade_date(output.get("trade_date"))
    decision_value = combine_explicit_date_time(output["trade_date"], output.get("decision_time"))
    observed_value = combine_explicit_date_time(output["trade_date"], output.get("observed_at"))
    decision_text, decision_dt, decision_has_time = parse_datetime(decision_value)
    observed_text, observed_dt, observed_has_time = parse_datetime(observed_value)
    output["decision_time"] = decision_text
    output["observed_at"] = observed_text
    if output["trade_date"] is None and decision_dt is not None:
        output["trade_date"] = decision_dt.date().isoformat()

    if bool(record.get("confirmed")) and record.get("confirm_price") not in (None, ""):
        output["price"] = as_float(record.get("confirm_price"))

    for text_field in ("name", "pool", "signal_type", "market_phase", "sector"):
        output[text_field] = scalar_text(output.get(text_field))

    if not output.get("signal_type"):
        if table == "signals":
            output["signal_type"] = "PRIMARY"
        elif table == "latent_signal":
            output["signal_type"] = "LATENT"

    try:
        timestamp_order_safe = bool(observed_dt is not None and decision_dt is not None and observed_dt <= decision_dt)
    except TypeError:
        timestamp_order_safe = False
    pit_safe = bool(decision_has_time and observed_has_time and timestamp_order_safe)
    warnings: list[str] = []
    if observed_dt is None or decision_dt is None:
        warnings.append("PIT timestamp missing or unparseable")
    elif not observed_has_time or not decision_has_time:
        warnings.append("PIT timestamp lacks time precision")
    else:
        try:
            if observed_dt > decision_dt:
                warnings.append("future-function risk: observed_at > decision_time")
        except TypeError:
            warnings.append("PIT timestamp timezone mismatch")

    future_keys = sorted(
        key
        for key in merged
        if re.search(r"(?i)(future|t\+?[1357]|outcome|forward|label|max_favorable|max_adverse)", str(key))
    )
    if future_keys:
        warnings.append("signal row contains outcome/future-like fields: " + ", ".join(future_keys))
        pit_safe = False
    if record.get("_force_pit_unsafe"):
        warnings.append("mutable aggregate export is not a provable decision-time snapshot")
        pit_safe = False

    imported_at = now_iso()
    output.update(
        {
            "snapshot_id": deterministic_snapshot_id(version, source_file, row_id),
            "source": SOURCE_NAME,
            "effective_at": first_present(merged, ("effective_at", "_evidence_cutoff", "evidence_cutoff")),
            "is_pit_safe": int(pit_safe),
            "features_json": json.dumps(safe_json(dict(record)), ensure_ascii=False, separators=(",", ":")),
            "created_at": imported_at,
            "primary_rule_score": output.get("score"),
            "legacy_source_file": source_file,
            "legacy_row_id": row_id,
            "legacy_version": version,
            "imported_at": imported_at,
            "legacy_push_status": (
                first_present(record, ("_legacy_push_status",))
                or ("SENT" if record.get("pushed") is True else "NOT_SENT" if record.get("pushed") is False else None)
            ),
            "legacy_pushed_at": first_present(record, ("_legacy_pushed_at", "pushed_at")),
        }
    )
    return output, warnings


def insert_signal(connection: sqlite3.Connection, signal: Mapping[str, Any]) -> tuple[str, str]:
    provenance = connection.execute(
        "SELECT snapshot_id FROM signal_snapshot "
        "WHERE legacy_version=? AND legacy_source_file=? AND legacy_row_id=?",
        (signal["legacy_version"], signal["legacy_source_file"], signal["legacy_row_id"]),
    ).fetchone()
    if provenance:
        return "duplicate", str(provenance[0])
    if signal.get("decision_time"):
        semantic = connection.execute(
            "SELECT snapshot_id FROM signal_snapshot WHERE source=? AND ts_code=? AND decision_time=? "
            "AND COALESCE(signal_type, '')=COALESCE(?, '') LIMIT 1",
            (SOURCE_NAME, signal["ts_code"], signal["decision_time"], signal.get("signal_type")),
        ).fetchone()
        if semantic:
            return "duplicate", str(semantic[0])

    columns = [
        "snapshot_id",
        "trade_date",
        "decision_time",
        "ts_code",
        "name",
        "price",
        "score",
        "pool",
        "market_phase",
        "sector",
        "auction_quality",
        "main_flow_score",
        "chip_lock_score",
        "trend_score",
        "buy_timing_score",
        "rr",
        "event_risk",
        "distribution_risk",
        "data_quality",
        "source",
        "observed_at",
        "effective_at",
        "is_pit_safe",
        "features_json",
        "created_at",
        "signal_type",
        "market_temperature",
        "sector_strength",
        "sector_resonance",
        "main_flow_amount",
        "chip_cost",
        "chip_concentration",
        "support",
        "strong_support",
        "resistance",
        "strong_resistance",
        "wash_score",
        "true_drop_score",
        "overnight_risk",
        "primary_rule_score",
        "legacy_source_file",
        "legacy_row_id",
        "legacy_version",
        "imported_at",
        "legacy_push_status",
        "legacy_pushed_at",
    ]
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO signal_snapshot ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(signal.get(column) for column in columns),
    )
    return "inserted", str(signal["snapshot_id"])


def iter_sqlite_signal_rows(path: Path) -> Iterator[tuple[str, str, dict[str, Any]]]:
    with closing(sqlite_ro(path)) as connection:
        tables = set(sqlite_tables(connection))
        if "v8_signal_decisions" in tables:
            evidence: dict[tuple[str, str], str] = {}
            if "v8_decision_history" in tables:
                for history_row in connection.execute(
                    "SELECT event_key,evaluation_time,evidence_cutoff FROM v8_decision_history "
                    "WHERE evidence_cutoff IS NOT NULL"
                ):
                    evidence[(str(history_row[0]), str(history_row[1]))] = str(history_row[2])
            sent_rows: list[tuple[str, str | None]] = []
            if "v8_push_state" in tables:
                sent_rows = [
                    (str(push_row[0]), push_row[1])
                    for push_row in connection.execute(
                        "SELECT dedupe_key,sent_at FROM v8_push_state WHERE status='SENT'"
                    )
                ]
            for row in connection.execute("SELECT * FROM v8_signal_decisions ORDER BY id"):
                record = dict(row)
                event_key = str(record.get("event_key") or record.get("id"))
                signal_time = str(record.get("signal_time") or "")
                record["_evidence_cutoff"] = evidence.get((event_key, signal_time))
                date_part = signal_time[:10]
                code = str(record.get("code") or "")
                action = str(record.get("action") or "")
                direct_prefix = f"{date_part}|{code}|{action}"
                matching_pushes = [
                    sent_at
                    for dedupe_key, sent_at in sent_rows
                    if event_key in dedupe_key or dedupe_key.startswith(direct_prefix)
                ]
                if matching_pushes:
                    record["_legacy_push_status"] = "SENT"
                    record["_legacy_pushed_at"] = max(str(value) for value in matching_pushes if value)
                yield "v8_signal_decisions", f"v8_signal_decisions:{event_key}", record
        for table in ("signals", "signal_snapshot", "latent_signal"):
            if table not in tables:
                continue
            cursor = connection.execute(f"SELECT * FROM {quote_ident(table)}")
            for index, row in enumerate(cursor, start=1):
                record = dict(row)
                raw_id = record.get("id") or record.get("snapshot_id") or index
                yield table, f"{table}:{raw_id}", record


def iter_structured_signal_rows(path: Path) -> Iterator[tuple[str, str, dict[str, Any]]]:
    records, fields, error = structured_records(path)
    if error:
        return
    kinds, _, _ = classify_structure(path.stem, fields, len(records))
    if "signal_snapshot" not in kinds:
        return
    for index, record in enumerate(records, start=1):
        raw_id = (
            record.get("id")
            or record.get("snapshot_id")
            or record.get("key")
            or record.get("event_key")
            or record.get("_legacy_mapping_key")
            or index
        )
        yield path.stem, f"records:{raw_id}", record


def normalized_outcome(
    record: Mapping[str, Any],
    source_file: str,
    row_id: str,
    version: str,
    snapshot_id: str,
    signal: Mapping[str, Any] | None,
) -> dict[str, Any]:
    imported_at = now_iso()
    t1 = as_float(first_present(record, ("t1_return", "t1_return_pct", "t1")))
    t3 = as_float(first_present(record, ("t3_return", "t3_return_pct", "t3")))
    t5 = as_float(first_present(record, ("t5_return", "t5_return_pct", "t5")))
    return {
        "signal_snapshot_id": snapshot_id,
        "outcome_time": first_present(record, ("outcome_time", "updated_at", "updated", "exit_time")),
        "t1": t1,
        "t3": t3,
        "t5": t5,
        "max_favorable_excursion": as_float(
            first_present(record, ("max_favorable_excursion", "max_favorable", "max_return_pct", "mfe_pct"))
        ),
        "max_adverse_excursion": as_float(
            first_present(record, ("max_adverse_excursion", "max_adverse", "min_return_pct", "mae_pct"))
        ),
        "triple_barrier_label": as_int(first_present(record, ("triple_barrier_label", "label"))),
        "payload_json": json.dumps(safe_json(dict(record)), ensure_ascii=False, separators=(",", ":")),
        "created_at": imported_at,
        "signal_id": str(record.get("signal_id")) if record.get("signal_id") is not None else None,
        "ts_code": normalize_ts_code(first_present(record, ("ts_code",))) or (signal or {}).get("ts_code"),
        "decision_time": first_present(record, ("decision_time",)) or (signal or {}).get("decision_time"),
        "t1_return": t1,
        "t3_return": t3,
        "t5_return": t5,
        "t7_return": as_float(first_present(record, ("t7_return", "t7_return_pct", "t7"))),
        "t1_status": first_present(record, ("t1_status", "track_status", "exit_reason")),
        "t7_status": first_present(record, ("t7_status",)),
        "stop_hit": as_int(first_present(record, ("stop_hit",))),
        "profit_hit": as_int(first_present(record, ("profit_hit",))),
        "wash_result": first_present(record, ("wash_result",)),
        "risk_result": first_present(record, ("risk_result",)),
        "legacy_source_file": source_file,
        "legacy_row_id": row_id,
        "legacy_version": version,
        "imported_at": imported_at,
    }


def structured_outcome_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    outcome_fields = (
        "t1_return",
        "t1_return_pct",
        "t3_return",
        "t3_return_pct",
        "t5_return",
        "t5_return_pct",
        "t7_return",
        "t7_return_pct",
        "return_5m",
        "return_15m",
        "return_30m",
        "return_60m",
        "close_return_pct",
        "max_return_pct",
        "min_return_pct",
    )
    if not any(record.get(field) not in (None, "") for field in outcome_fields):
        return None
    result = dict(record)
    result["_return_unit"] = "percent_points_as_stored_by_legacy"
    result["_intraday_returns"] = {
        field: as_float(record.get(field))
        for field in ("return_5m", "return_15m", "return_30m", "return_60m", "close_return_pct")
        if record.get(field) not in (None, "")
    }
    return result


def iter_v8_matured_outcomes(connection: sqlite3.Connection) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    if "v8_execution_outcomes" not in set(sqlite_tables(connection)):
        return
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(
        "SELECT * FROM v8_execution_outcomes WHERE matured=1 ORDER BY event_key,track,horizon,id"
    ):
        record = dict(row)
        grouped.setdefault(str(record.get("event_key") or ""), []).append(record)
    for event_key, records in grouped.items():
        if event_key:
            yield event_key, records


def normalized_v8_outcome_group(
    records: Sequence[Mapping[str, Any]],
    source_file: str,
    version: str,
    snapshot_id: str,
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    fixed = {
        str(record.get("horizon")): record
        for record in records
        if str(record.get("track")) == "FIXED" and int(record.get("matured") or 0) == 1
    }
    result: dict[str, Any] = {
        "signal_id": records[0].get("event_key"),
        "ts_code": signal.get("ts_code"),
        "decision_time": signal.get("decision_time"),
        "outcome_time": max(
            (str(record.get("exit_time")) for record in records if record.get("exit_time")),
            default=None,
        ),
        "t1_return": as_float((fixed.get("D1") or {}).get("net_return_pct")),
        "t3_return": as_float((fixed.get("D3") or {}).get("net_return_pct")),
        "t5_return": as_float((fixed.get("D5") or {}).get("net_return_pct")),
        "t7_return": as_float((fixed.get("D7") or {}).get("net_return_pct")),
        "t1_status": (fixed.get("D1") or {}).get("exit_reason"),
        "t7_status": (fixed.get("D7") or {}).get("exit_reason"),
        "max_favorable_excursion": max(
            (float(record["mfe_pct"]) for record in records if record.get("mfe_pct") is not None),
            default=None,
        ),
        "max_adverse_excursion": min(
            (float(record["mae_pct"]) for record in records if record.get("mae_pct") is not None),
            default=None,
        ),
        "_return_unit": "percent_points_net_of_cost_as_stored_by_legacy",
        "_legacy_horizons": [dict(record) for record in records],
    }
    outcome = normalized_outcome(
        result,
        source_file,
        f"v8_execution_outcomes:{result['signal_id']}:matured",
        version,
        snapshot_id,
        signal,
    )
    outcome["payload_json"] = json.dumps(safe_json(result), ensure_ascii=False, separators=(",", ":"))
    return outcome


def insert_outcome(connection: sqlite3.Connection, outcome: Mapping[str, Any]) -> str:
    existing = connection.execute(
        "SELECT 1 FROM signal_outcome WHERE legacy_version=? AND legacy_source_file=? AND legacy_row_id=?",
        (outcome["legacy_version"], outcome["legacy_source_file"], outcome["legacy_row_id"]),
    ).fetchone()
    if existing:
        return "duplicate"
    semantic = connection.execute(
        "SELECT 1 FROM signal_outcome WHERE signal_snapshot_id=? LIMIT 1",
        (outcome["signal_snapshot_id"],),
    ).fetchone()
    if semantic:
        return "duplicate"
    columns = list(outcome)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO signal_outcome ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(outcome[column] for column in columns),
    )
    return "inserted"


def source_hashes(inventory: Mapping[str, Any]) -> dict[str, str]:
    return {str(item["relative_path"]): str(item["sha256"]) for item in inventory["files"]}


def structured_semantic_key(record: Mapping[str, Any]) -> str:
    explicit = first_present(record, ("key", "event_key", "_legacy_mapping_key"))
    if explicit:
        return str(explicit)
    return "|".join(
        str(first_present(record, aliases) or "")
        for aliases in (("date", "trade_date"), ("source", "pool", "channel"), ("code", "ts_code"))
    )


def consolidate_structured_signals(
    paths: Sequence[Path], root: Path
) -> dict[Path, list[tuple[str, str, dict[str, Any]]]]:
    groups: dict[str, list[tuple[Path, str, str, dict[str, Any]]]] = {}
    for path in paths:
        for table, row_id, record in iter_structured_signal_rows(path):
            key = structured_semantic_key(record)
            groups.setdefault(key, []).append((path, table, row_id, record))

    result: dict[Path, list[tuple[str, str, dict[str, Any]]]] = {}
    for key, candidates in groups.items():
        def richness(item: tuple[Path, str, str, dict[str, Any]]) -> tuple[int, int, int]:
            path, _, _, record = item
            nonempty = sum(value not in (None, "", [], {}) for value in record.values())
            has_outcome = sum(
                record.get(field) not in (None, "")
                for field in ("return_5m", "return_60m", "close_return_pct", "t1_return_pct")
            )
            return nonempty, has_outcome, int(path.suffix.lower() == ".json")

        canonical_path, table, _, canonical = max(candidates, key=richness)
        merged = dict(canonical)
        related_sources: list[str] = []
        for path, _, _, record in sorted(candidates, key=richness, reverse=True):
            related_sources.append(relative_source(path, root))
            for field, value in record.items():
                if merged.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                    merged[field] = value
        merged["_legacy_related_sources"] = sorted(set(related_sources))
        merged["_legacy_semantic_key"] = key
        merged["_force_pit_unsafe"] = True
        row_id = f"records:{key}"
        result.setdefault(canonical_path, []).append((table, row_id, merged))
    return result


@dataclass
class ImportCounters:
    signal_candidates: int = 0
    signal_inserted: int = 0
    signal_duplicates: int = 0
    signal_failed: int = 0
    outcome_candidates: int = 0
    outcome_inserted: int = 0
    outcome_duplicates: int = 0
    outcome_failed: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)


def import_history(root: Path, target_db: Path, inventory: Mapping[str, Any]) -> dict[str, Any]:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(target_db)
    target.row_factory = sqlite3.Row
    ensure_target_schema(target)
    counters = ImportCounters()
    signal_map: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    import_files: list[Path] = []
    for item in inventory["files"]:
        has_signal = "signal_snapshot" in item["migratable_data_types"] or any(
            "signal_snapshot" in table["migratable_data_types"] for table in item.get("tables", [])
        )
        has_outcome = any(
            "signal_outcome" in table["migratable_data_types"] for table in item.get("tables", [])
        )
        if not (has_signal or has_outcome):
            continue
        relative = str(item["relative_path"]).lower().replace("\\", "/")
        table_names = {str(table["name"]).lower() for table in item.get("tables", [])}
        if "v8_signal_decisions" in table_names and relative != "data_v8/signal_lab_v8.sqlite3":
            continue
        import_files.append(root / item["relative_path"])
    inventory_by_path = {str(item["relative_path"]): item for item in inventory["files"]}

    def import_priority(path: Path) -> tuple[int, int, str]:
        relative = relative_source(path, root)
        lower = relative.lower().replace("\\", "/")
        item = inventory_by_path.get(relative, {})
        field_count = len(item.get("fields", []))
        if lower == "data_v8/signal_lab_v8.sqlite3":
            return (0, 0, lower)
        if path.suffix.lower() in {".json", ".jsonl", ".csv"}:
            return (1, -field_count, lower)
        if "backup" in lower or "before_" in lower:
            return (3, 0, lower)
        return (2, 0, lower)

    import_files.sort(key=import_priority)
    structured_paths = [
        path for path in import_files if path.suffix.lower() in {".json", ".jsonl", ".csv"}
    ]
    consolidated_structured = consolidate_structured_signals(structured_paths, root)
    import_files = [
        path
        for path in import_files
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} or path in consolidated_structured
    ]
    try:
        target.execute("BEGIN IMMEDIATE")
        for path in import_files:
            source_file = relative_source(path, root)
            version = legacy_version(path, root)
            rows: Iterable[tuple[str, str, dict[str, Any]]]
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                rows = iter_sqlite_signal_rows(path)
            else:
                rows = consolidated_structured.get(path, [])
            for table, row_id, record in rows:
                counters.signal_candidates += 1
                try:
                    signal, warnings = normalized_signal(record, source_file, row_id, version, table)
                    if signal is None:
                        counters.signal_failed += 1
                        counters.failures.append({"file": source_file, "row_id": row_id, "reasons": warnings})
                        continue
                    status, snapshot_id = insert_signal(target, signal)
                    if status == "inserted":
                        counters.signal_inserted += 1
                    else:
                        counters.signal_duplicates += 1
                    signal_map[(source_file, row_id)] = (snapshot_id, signal)
                    if warnings and status == "inserted":
                        counters.warnings.append({"file": source_file, "row_id": row_id, "warnings": warnings})

                    if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
                        outcome_record = structured_outcome_record(record)
                        if outcome_record is not None:
                            counters.outcome_candidates += 1
                            outcome = normalized_outcome(
                                outcome_record,
                                source_file,
                                f"{row_id}:outcome",
                                version,
                                snapshot_id,
                                signal,
                            )
                            outcome_status = insert_outcome(target, outcome)
                            if outcome_status == "inserted":
                                counters.outcome_inserted += 1
                            else:
                                counters.outcome_duplicates += 1
                except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
                    counters.signal_failed += 1
                    counters.failures.append(
                        {"file": source_file, "row_id": row_id, "reasons": [f"{type(exc).__name__}: {exc}"]}
                    )

            if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
                continue
            with closing(sqlite_ro(path)) as source:
                source_tables = set(sqlite_tables(source))
                if "outcomes" in source_tables:
                    for index, row in enumerate(source.execute("SELECT * FROM outcomes"), start=1):
                        record = dict(row)
                        raw_signal_id = record.get("signal_id")
                        row_id = f"outcomes:{raw_signal_id if raw_signal_id is not None else index}"
                        counters.outcome_candidates += 1
                        signal_key = (source_file, f"signals:{raw_signal_id}")
                        linked = signal_map.get(signal_key)
                        if linked is None:
                            counters.outcome_failed += 1
                            counters.failures.append(
                                {"file": source_file, "row_id": row_id, "reasons": ["no matching legacy signal"]}
                            )
                            continue
                        snapshot_id, signal = linked
                        try:
                            outcome = normalized_outcome(record, source_file, row_id, version, snapshot_id, signal)
                            status = insert_outcome(target, outcome)
                            if status == "inserted":
                                counters.outcome_inserted += 1
                            else:
                                counters.outcome_duplicates += 1
                        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
                            counters.outcome_failed += 1
                            counters.failures.append(
                                {
                                    "file": source_file,
                                    "row_id": row_id,
                                    "reasons": [f"{type(exc).__name__}: {exc}"],
                                }
                            )

                lower_source = source_file.lower().replace("\\", "/")
                if "v8_execution_outcomes" in source_tables and "backup" not in lower_source and "before_" not in lower_source:
                    for event_key, records in iter_v8_matured_outcomes(source):
                        counters.outcome_candidates += 1
                        linked = signal_map.get((source_file, f"v8_signal_decisions:{event_key}"))
                        if linked is None:
                            counters.outcome_failed += 1
                            counters.failures.append(
                                {
                                    "file": source_file,
                                    "row_id": f"v8_execution_outcomes:{event_key}:matured",
                                    "reasons": ["matured outcome has no matching v8_signal_decisions row"],
                                }
                            )
                            continue
                        snapshot_id, signal = linked
                        try:
                            outcome = normalized_v8_outcome_group(
                                records, source_file, version, snapshot_id, signal
                            )
                            status = insert_outcome(target, outcome)
                            if status == "inserted":
                                counters.outcome_inserted += 1
                            else:
                                counters.outcome_duplicates += 1
                        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
                            counters.outcome_failed += 1
                            counters.failures.append(
                                {
                                    "file": source_file,
                                    "row_id": f"v8_execution_outcomes:{event_key}:matured",
                                    "reasons": [f"{type(exc).__name__}: {exc}"],
                                }
                            )
        target.commit()
    except Exception:
        target.rollback()
        raise

    legacy_filter = "WHERE source=?"
    signal_stats = target.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ts_code), MIN(trade_date), MAX(trade_date), "
        "SUM(CASE WHEN is_pit_safe=1 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN is_pit_safe=0 THEN 1 ELSE 0 END) "
        f"FROM signal_snapshot {legacy_filter}",
        (SOURCE_NAME,),
    ).fetchone()
    outcome_stats = target.execute(
        "SELECT "
        "SUM(CASE WHEN COALESCE(t1_return,t1) IS NOT NULL THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN COALESCE(t3_return,t3) IS NOT NULL THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN t7_return IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM signal_outcome o JOIN signal_snapshot s ON s.snapshot_id=o.signal_snapshot_id WHERE s.source=?",
        (SOURCE_NAME,),
    ).fetchone()
    meta_eligible = int(
        target.execute(
            "SELECT COUNT(DISTINCT s.snapshot_id) FROM signal_snapshot s "
            "JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id "
            "WHERE s.source=? AND s.is_pit_safe=1 AND s.decision_time IS NOT NULL "
            "AND s.score IS NOT NULL AND COALESCE(o.t1_return,o.t1,o.t3_return,o.t3,o.t5_return,o.t5) IS NOT NULL",
            (SOURCE_NAME,),
        ).fetchone()[0]
    )
    primary_meta_eligible = int(
        target.execute(
            "SELECT COUNT(DISTINCT s.snapshot_id) FROM signal_snapshot s "
            "JOIN signal_outcome o ON o.signal_snapshot_id=s.snapshot_id "
            "WHERE s.source=? AND s.is_pit_safe=1 AND s.decision_time IS NOT NULL "
            "AND s.score IS NOT NULL AND s.signal_type IN ('SHADOW_ENTRY_CONFIRMED','CONFIRMED','A','A+') "
            "AND COALESCE(o.t1_return,o.t1,o.t3_return,o.t3,o.t5_return,o.t5) IS NOT NULL",
            (SOURCE_NAME,),
        ).fetchone()[0]
    )
    pushed_signals = int(
        target.execute(
            "SELECT COUNT(*) FROM signal_snapshot WHERE source=? AND legacy_push_status='SENT'",
            (SOURCE_NAME,),
        ).fetchone()[0]
    )
    missing_fields: dict[str, int] = {}
    legacy_count = int(signal_stats[0] or 0)
    for column in SIGNAL_COLUMNS:
        if column in {"ts_code"}:
            continue
        count = int(
            target.execute(
                f"SELECT COUNT(*) FROM signal_snapshot WHERE source=? AND {quote_ident(column)} IS NULL",
                (SOURCE_NAME,),
            ).fetchone()[0]
        )
        if count:
            missing_fields[column] = count

    fk_errors = [list(row) for row in target.execute("PRAGMA foreign_key_check")]
    integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
    duplicate_provenance = int(
        target.execute(
            "SELECT COUNT(*) FROM (SELECT legacy_version,legacy_source_file,legacy_row_id,COUNT(*) n "
            "FROM signal_snapshot WHERE source=? AND legacy_row_id IS NOT NULL "
            "GROUP BY legacy_version,legacy_source_file,legacy_row_id HAVING n>1)",
            (SOURCE_NAME,),
        ).fetchone()[0]
    )
    target.close()

    after_hashes = {relative_source(path, root): file_sha256(path) for path in discover_files(root, target_db)}
    before_hashes = source_hashes(inventory)
    changed_sources = sorted(
        key for key, value in before_hashes.items() if after_hashes.get(key) is not None and after_hashes[key] != value
    )

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "source_root": str(root.resolve()),
        "target_database": str(target_db.resolve()),
        "mode": "READ_ONLY_LEGACY_IMPORT",
        "status": "PASS" if not changed_sources and integrity == "ok" and not fk_errors else "FAIL",
        "summary": {
            "legacy_files_found": inventory["summary"]["discovered_file_count"],
            "migratable_files": inventory["summary"]["migratable_file_count"],
            "signal_total_candidates": counters.signal_candidates,
            "signals_imported_this_run": counters.signal_inserted,
            "signals_skipped_duplicate_this_run": counters.signal_duplicates,
            "signals_failed_this_run": counters.signal_failed,
            "outcomes_imported_this_run": counters.outcome_inserted,
            "outcomes_skipped_duplicate_this_run": counters.outcome_duplicates,
            "outcomes_failed_this_run": counters.outcome_failed,
            "legacy_signals_in_database": legacy_count,
            "earliest_trade_date": signal_stats[2],
            "latest_trade_date": signal_stats[3],
            "stock_count": int(signal_stats[1] or 0),
            "with_t1_result": int(outcome_stats[0] or 0),
            "with_t3_result": int(outcome_stats[1] or 0),
            "with_t7_result": int(outcome_stats[2] or 0),
            "pit_safe_records": int(signal_stats[4] or 0),
            "pit_unsafe_records": int(signal_stats[5] or 0),
            "meta_training_eligible_samples": meta_eligible,
            "primary_signal_meta_eligible_samples": primary_meta_eligible,
            "legacy_pushed_signals": pushed_signals,
            "duplicate_provenance_rows": duplicate_provenance,
        },
        "source_safety": {
            "legacy_connections_read_only": True,
            "source_hashes_unchanged": not changed_sources,
            "changed_source_files": changed_sources,
        },
        "database_checks": {
            "integrity_check": integrity,
            "foreign_key_errors": fk_errors,
        },
        "field_missing_counts": missing_fields,
        "pit_warnings": counters.warnings,
        "failures": counters.failures,
        "research_reference_rows_not_imported_as_signals": inventory["summary"][
            "research_reference_rows_not_signals"
        ],
        "future_function_risk": {
            "detected_warning_count": sum(
                any("future-function" in warning or "future-like" in warning for warning in item["warnings"])
                for item in counters.warnings
            ),
            "policy": "Rows with missing/unparseable timestamps or outcome-like signal fields are not PIT safe",
        },
        "training_gate": {
            "meta_enabled": False,
            "cpcv_enabled": False,
            "conformal_enabled": False,
            "reason": (
                "No eligible legacy samples"
                if meta_eligible == 0
                else (
                    f"Only {meta_eligible} PIT-safe labeled rows and {primary_meta_eligible} confirmed/A/A+ rows; sample insufficient"
                )
            ),
        },
    }


def audit_text(audit: Mapping[str, Any]) -> str:
    item = audit["summary"]
    missing = audit["field_missing_counts"]
    lines = [
        "V8 HISTORY IMPORT AUDIT",
        "=" * 72,
        f"Generated at: {audit['generated_at']}",
        f"Status: {audit['status']}",
        f"Source root: {audit['source_root']}",
        f"Target database: {audit['target_database']}",
        "Legacy source access: READ ONLY",
        "",
        f"Legacy files found: {item['legacy_files_found']}",
        f"Migratable/reference files: {item['migratable_files']}",
        f"Signal candidates: {item['signal_total_candidates']}",
        f"Signals imported this run: {item['signals_imported_this_run']}",
        f"Signals skipped as duplicates: {item['signals_skipped_duplicate_this_run']}",
        f"Signals failed: {item['signals_failed_this_run']}",
        f"Legacy signals now in unified DB: {item['legacy_signals_in_database']}",
        f"Date range: {item['earliest_trade_date']} -> {item['latest_trade_date']}",
        f"Stocks: {item['stock_count']}",
        "",
        f"With T+1 outcome: {item['with_t1_result']}",
        f"With T+3 outcome: {item['with_t3_result']}",
        f"With T+7 outcome: {item['with_t7_result']}",
        f"PIT-safe records: {item['pit_safe_records']}",
        f"PIT-unsafe records: {item['pit_unsafe_records']}",
        f"Eligible Meta samples: {item['meta_training_eligible_samples']}",
        f"Eligible confirmed/A/A+ Meta samples: {item['primary_signal_meta_eligible_samples']}",
        f"Signals with linked SENT evidence: {item['legacy_pushed_signals']}",
        "",
        f"Duplicate provenance rows in DB: {item['duplicate_provenance_rows']}",
        f"Legacy source hashes unchanged: {audit['source_safety']['source_hashes_unchanged']}",
        f"SQLite integrity: {audit['database_checks']['integrity_check']}",
        f"Foreign-key errors: {len(audit['database_checks']['foreign_key_errors'])}",
        f"Future-function warnings: {audit['future_function_risk']['detected_warning_count']}",
        "",
        "FIELD COMPLETENESS",
        "-" * 72,
    ]
    if missing:
        lines.extend(f"{key}: {value} missing" for key, value in sorted(missing.items()))
    else:
        lines.append("No imported legacy signals; field completeness cannot yet be measured.")
    lines.extend(
        [
            "",
            "DECISION",
            "-" * 72,
            f"Meta/CPCV/Conformal activation: BLOCKED ({audit['training_gate']['reason']})",
            "No A+ live activation or strategy parameter change was performed.",
            "Missing fields remain NULL; no current quotes or future outcomes were used as historical features.",
        ]
    )
    if audit["failures"]:
        lines.extend(["", "FAILURES", "-" * 72])
        for failure in audit["failures"]:
            lines.append(f"{failure['file']} / {failure['row_id']}: {'; '.join(failure['reasons'])}")
    return "\n".join(lines) + "\n"


def write_audit(audit: Mapping[str, Any], output_dir: Path) -> None:
    write_json(output_dir / "V8_IMPORT_AUDIT.json", audit)
    (output_dir / "V8_IMPORT_AUDIT.txt").write_text(audit_text(audit), encoding="utf-8")


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="v8_legacy_import_") as temp:
        root = Path(temp)
        source = root / "radar_v82.db"
        target = root / "live_pit.db"
        with closing(sqlite3.connect(source)) as connection:
            connection.executescript(
                """
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, ts_code TEXT,
                    name TEXT, pool TEXT, score REAL, rr REAL, entry_price REAL,
                    payload TEXT
                );
                CREATE TABLE outcomes (
                    signal_id INTEGER PRIMARY KEY, t1_return REAL, t3_return REAL,
                    t5_return REAL, max_favorable REAL, max_adverse REAL, updated_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO signals VALUES (1,?,?,?,?,?,?,?,?,?)",
                (
                    "2026-08-20 10:15:00",
                    "20260820",
                    "000001.SZ",
                    "Sample",
                    "A",
                    81.0,
                    2.0,
                    10.0,
                    json.dumps({"observed_at": "2026-08-20 10:14:59", "trend_score": 70}),
                ),
            )
            connection.execute(
                "INSERT INTO outcomes VALUES (1,0.01,0.02,0.03,0.05,-0.01,'2026-08-27 15:00:00')"
            )
            connection.commit()
        first_inventory = build_inventory(root, target)
        first = import_history(root, target, first_inventory)
        second_inventory = build_inventory(root, target)
        second = import_history(root, target, second_inventory)
        assert first["summary"]["signals_imported_this_run"] == 1
        assert first["summary"]["with_t1_result"] == 1
        assert first["summary"]["pit_safe_records"] == 1
        assert second["summary"]["signals_imported_this_run"] == 0
        assert second["summary"]["signals_skipped_duplicate_this_run"] == 1
        assert second["summary"]["outcomes_skipped_duplicate_this_run"] == 1
        assert first["source_safety"]["source_hashes_unchanged"] is True
    print("legacy_v8_importer self-test: PASS")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Read-only V8 legacy history inventory and PIT-safe import")
    parser.add_argument("--source", type=Path, default=project_root, help="Legacy V8 source directory")
    parser.add_argument(
        "--target", type=Path, default=project_root / "data" / "live_pit.db", help="Latest unified PIT database"
    )
    parser.add_argument("--output-dir", type=Path, default=project_root, help="Audit report directory")
    parser.add_argument("--inventory-only", action="store_true", help="Only write inventory; do not change target DB")
    parser.add_argument("--self-test", action="store_true", help="Run isolated importer regression test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        run_self_test()
        return 0
    root = args.source.expanduser().resolve()
    target = args.target.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: legacy source directory not found: {root}", file=sys.stderr)
        return 2
    inventory = build_inventory(root, target)
    write_reports(inventory, output_dir)
    print(f"Legacy files found: {inventory['summary']['discovered_file_count']}")
    print(f"Signal candidates found: {inventory['summary']['signal_candidate_rows']}")
    print(
        "Observed value range: "
        f"{inventory['summary']['earliest_value']} -> {inventory['summary']['latest_value']}"
    )
    if args.inventory_only:
        print("Inventory only: target database was not changed.")
        return 0
    audit = import_history(root, target, inventory)
    write_audit(audit, output_dir)
    summary = audit["summary"]
    print(f"Signals imported: {summary['signals_imported_this_run']}")
    print(f"Signals skipped as duplicates: {summary['signals_skipped_duplicate_this_run']}")
    print(f"Signals failed: {summary['signals_failed_this_run']}")
    print(f"PIT-safe records: {summary['pit_safe_records']}")
    print(f"Meta-eligible samples: {summary['meta_training_eligible_samples']}")
    print(f"Audit status: {audit['status']}")
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
