"""Local budget reservations and credential-safe APIYI metadata probe.

No completion requests are made by this module. Provider billing remains
authoritative; local reservations cannot replace provider-side spending caps.
"""
import argparse
import base64
import contextlib
import ctypes
from ctypes import wintypes
import datetime as dt
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
SCALE = Decimal(1000000)


def money(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError('Invalid monetary value')
    return int((number * SCALE).to_integral_value(rounding=ROUND_CEILING))


def config():
    value = json.loads((ROOT / 'fusion_ai.local.json').read_text(encoding='utf-8'))
    if value.get('base_url') != 'https://api.apiyi.com/v1' or value.get('model') != 'deepseek-v4-pro':
        raise ValueError('Unexpected provider or model')
    return value


def credential(value):
    if os.name != 'nt' or value.get('credential_storage') != 'windows_dpapi_current_user':
        raise RuntimeError('Windows account required')

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value['api_key_dpapi'], validate=True)
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Credential decryption failed')
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode('utf-8')
    finally:
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_models(value):
    """Only return sanitized metadata, never headers, key, or raw server errors."""
    try:
        if value.get('base_url') != 'https://api.apiyi.com/v1':
            raise ValueError('Unexpected endpoint')
        key = credential(value)
        request = urllib.request.Request(value['base_url'] + '/models', headers={
            'Authorization': 'Bearer ' + key, 'User-Agent': 'FusionRadar/1.0',
        })
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=15) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Response too large')
            result = json.loads(raw)
        rows = result.get('data')
        if not isinstance(rows, list):
            raise ValueError('Unexpected response')
        available = any(isinstance(item, dict) and item.get('id') == value['model'] for item in rows)
        return {'status': 'MODEL_LISTED' if available else 'MODEL_NOT_LISTED', 'http_status': 200,
                'selected_model_available': available, 'completion_requested': False,
                'note': 'Model listing is not proof of upstream model identity or completion permission.'}
    except urllib.error.HTTPError as exc:
        return {'status': 'HTTP_ERROR', 'http_status': exc.code, 'completion_requested': False}
    except Exception as exc:
        return {'status': 'PROBE_FAILED', 'error_type': type(exc).__name__, 'completion_requested': False}


class Budget:
    """Single-host persistent budget. Uncertain requests retain reservations."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, day TEXT NOT NULL, month TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER, status TEXT NOT NULL); CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), blocked INTEGER NOT NULL); INSERT OR IGNORE INTO control VALUES (1,0);')

    def reserve(self, estimate, daily, monthly, now=None):
        now = now or dt.datetime.now(TZ)
        if now.tzinfo is None:
            raise ValueError('Timezone required')
        now = now.astimezone(TZ)
        day, month = now.strftime('%Y-%m-%d'), now.strftime('%Y-%m')
        reserve, day_limit, month_limit = money(estimate), money(daily), money(monthly)
        if min(reserve, day_limit, month_limit) <= 0:
            raise ValueError('Positive limits required')
        with contextlib.closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT blocked FROM control WHERE id=1').fetchone()[0]:
                raise RuntimeError('BILLING_RECONCILIATION_REQUIRED')
            pending = db.execute("SELECT COALESCE(SUM(reserved),0) FROM requests WHERE status IN ('RESERVED','UNKNOWN')").fetchone()[0]
            daily_used = db.execute("SELECT COALESCE(SUM(actual),0) FROM requests WHERE day=? AND status='SETTLED'", (day,)).fetchone()[0]
            monthly_used = db.execute("SELECT COALESCE(SUM(actual),0) FROM requests WHERE month=? AND status='SETTLED'", (month,)).fetchone()[0]
            if daily_used + pending + reserve > day_limit or monthly_used + pending + reserve > month_limit:
                raise RuntimeError('BUDGET_EXHAUSTED')
            identity = uuid.uuid4().hex
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)', (identity, day, month, reserve, None, 'RESERVED'))
            return identity

    def settle(self, identity, actual=None):
        with contextlib.closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT reserved,status,actual FROM requests WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('Unknown request')
            if row[1] == 'SETTLED':
                if actual is not None and row[2] == money(actual):
                    return
                raise ValueError('Settlement is immutable')
            if actual is None:
                db.execute("UPDATE requests SET status='UNKNOWN' WHERE id=?", (identity,))
                return
            amount = money(actual)
            db.execute("UPDATE requests SET status='SETTLED',actual=? WHERE id=?", (amount, identity))
            if amount > row[0]:
                db.execute('UPDATE control SET blocked=1 WHERE id=1')


def readiness(value):
    missing = []
    for name in ('input_usd_per_million', 'output_usd_per_million', 'cny_per_usd'):
        try:
            if value.get(name) is None or money(value[name]) <= 0:
                missing.append(name)
        except Exception:
            missing.append(name)
    return {'paid_calls_allowed': False, 'status': 'PRICING_REQUIRED' if missing else 'COMPLETION_INTEGRATION_PENDING',
            'missing_pricing_fields': missing, 'daily_budget_cny': value.get('daily_budget_cny'),
            'monthly_budget_cny': value.get('monthly_budget_cny'),
            'scope': 'Single-host budget module implemented; not yet attached to a completion worker or cloud service.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-models', action='store_true')
    args = parser.parse_args()
    try:
        value = config()
        report = readiness(value)
        if args.probe_models:
            report['metadata_probe'] = probe_models(value)
        print(json.dumps(report, ensure_ascii=True, indent=2))
    except Exception as exc:
        print(json.dumps({'status': 'CONFIG_ERROR', 'error_type': type(exc).__name__}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
