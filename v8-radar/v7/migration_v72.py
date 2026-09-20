from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .signal_lab import DEFAULT_DB

SCHEMA_VERSION='7.2.0-research-shadow'


def _connect(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(str(path),timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma busy_timeout=30000'); return c


def backup_database(db_path:Path=DEFAULT_DB,backup_dir:Path|None=None)->Path|None:
    if not db_path.exists():return None
    backup_dir=backup_dir or db_path.parent/'backups'; backup_dir.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S'); out=backup_dir/f'{db_path.stem}_pre_v72_{stamp}{db_path.suffix}'
    # sqlite online backup avoids copying a partially-written DB.
    src=_connect(db_path); dst=_connect(out)
    try: src.backup(dst); dst.commit()
    finally: src.close(); dst.close()
    return out


def current_schema_version(db_path:Path=DEFAULT_DB)->str|None:
    if not db_path.exists():return None
    c=_connect(db_path)
    try:
        c.execute('create table if not exists v72_schema_meta(key text primary key,value text not null,updated_at text not null)')
        r=c.execute("select value from v72_schema_meta where key='schema_version'").fetchone(); c.commit(); return r['value'] if r else None
    finally:c.close()


def migrate(db_path:Path=DEFAULT_DB,*,make_backup:bool=True)->dict[str,Any]:
    backup=backup_database(db_path) if make_backup else None
    try:
        # Import lazily so each module applies only additive/idempotent CREATE/ALTER operations.
        from .signal_lab import initialize
        from .portfolio import ensure_schema as portfolio_schema
        from .forward_v72 import ensure_schema as forward_schema
        initialize(db_path); portfolio_schema(db_path); forward_schema(db_path)
        c=_connect(db_path); now=datetime.now().astimezone().isoformat(timespec='seconds')
        c.execute('create table if not exists v72_schema_meta(key text primary key,value text not null,updated_at text not null)')
        c.execute("insert into v72_schema_meta(key,value,updated_at) values('schema_version',?,?) on conflict(key) do update set value=excluded.value,updated_at=excluded.updated_at",(SCHEMA_VERSION,now)); c.commit(); c.close()
        return {'ok':True,'schema_version':SCHEMA_VERSION,'backup':str(backup) if backup else None}
    except Exception as exc:
        rollback_ok = None
        if backup and backup.exists():
            try:
                shutil.copy2(backup,db_path); rollback_ok = True
            except Exception as rollback_exc:
                rollback_ok = False
                try:
                    from .runtime_log_v72 import log_event
                    log_event("migration", "数据库迁移失败且回滚失败", exc=rollback_exc, degraded=True, event="migration_rollback")
                except Exception:
                    pass
        try:
            from .runtime_log_v72 import log_event
            log_event("migration", "数据库迁移失败", exc=exc, degraded=True, event="migration")
        except Exception:
            pass
        return {'ok':False,'schema_version':current_schema_version(db_path),'backup':str(backup) if backup else None,'rollback_ok':rollback_ok,'error':type(exc).__name__}
