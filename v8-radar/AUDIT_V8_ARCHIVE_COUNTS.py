from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


TABLES = (
    "v8_daily_bars",
    "v8_minute_bars",
    "v8_auction_assessments",
    "v8_event_radar",
    "v8_event_evidence",
    "v8_event_clusters",
    "v8_event_forward",
    "v8_company_profiles",
    "v8_discovery_candidates",
    "v8_persistence_observations",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    args = parser.parse_args()
    path = Path(args.db).resolve()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        existing = {
            str(row[0])
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
        result = {
            table: (
                int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                if table in existing
                else None
            )
            for table in TABLES
        }
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
