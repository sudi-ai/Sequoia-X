from __future__ import annotations

import json
import sqlite3
from v8.env_loader import load_v8_env

load_v8_env()

from v8.paid_month_archive import ARCHIVE_DB, archive_coverage

if __name__ == "__main__":
    data = archive_coverage()
    db = sqlite3.connect(ARCHIVE_DB); db.row_factory = sqlite3.Row
    data["unfinished"] = [dict(row) for row in db.execute("""select api,scope,status,attempts,
      last_error,not_before from v8_paid_archive_queue where status!='DONE' order by priority desc,id limit 20""")]
    db.close()
    print(json.dumps(data, ensure_ascii=False, indent=2))
