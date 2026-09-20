from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path


def counts(db: sqlite3.Connection) -> dict[str, int]:
    tables = {str(x[0]) for x in db.execute("select name from sqlite_master where type='table'")}
    wanted = ("v8_paid_archive_rows", "v8_paid_archive_queue", "v8_paid_archive_state", "v8_paid_archive_runs")
    return {name: int(db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in wanted if name in tables}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve(); destination = Path(args.destination).resolve()
    if not source.exists():
        raise SystemExit("source database missing")
    if destination.drive.upper() != "D:":
        raise SystemExit("destination must be on D drive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".migrating")
    if temp.exists():
        temp.unlink()
    source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_db = sqlite3.connect(temp)
    try:
        source_counts = counts(source_db)
        source_db.backup(destination_db, pages=2000)
        destination_db.commit()
        integrity = str(destination_db.execute("pragma integrity_check").fetchone()[0])
        destination_counts = counts(destination_db)
    finally:
        destination_db.close(); source_db.close()
    if integrity.lower() != "ok" or source_counts != destination_counts:
        raise SystemExit(json.dumps({"integrity": integrity, "source": source_counts,
                                     "destination": destination_counts}, ensure_ascii=False))
    os.replace(temp, destination)
    print(json.dumps({"status": "OK", "source": str(source), "destination": str(destination),
                      "integrity": integrity, "counts": destination_counts,
                      "bytes": destination.stat().st_size}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
