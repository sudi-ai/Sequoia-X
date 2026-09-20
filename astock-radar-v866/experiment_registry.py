# -*- coding: utf-8 -*-
"""
Champion / Challenger 实验登记。
新阈值/新模型必须先以 Challenger 跑样本外，再允许升为 Champion。
"""
from __future__ import annotations
import json, sqlite3
from datetime import datetime
from config import DATA_DIR

DB=DATA_DIR/"experiments.db"
SCHEMA="""CREATE TABLE IF NOT EXISTS experiment(
id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,version TEXT,role TEXT,created_at TEXT,
config TEXT,metrics TEXT,status TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_name_ver ON experiment(name,version);"""

class ExperimentRegistry:
    def __init__(self,path=DB):
        self.path=str(path)
        with sqlite3.connect(self.path) as c:c.executescript(SCHEMA)

    def register(self,name,version,config,role="challenger",status="testing"):
        with sqlite3.connect(self.path) as c:
            c.execute("""INSERT OR REPLACE INTO experiment(name,version,role,created_at,config,metrics,status)
                         VALUES(?,?,?,?,?,?,?)""",
                      (name,version,role,datetime.now().isoformat(timespec="seconds"),
                       json.dumps(config,ensure_ascii=False),json.dumps({},ensure_ascii=False),status))

    def update_metrics(self,name,version,metrics,status=None):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE experiment SET metrics=?,status=COALESCE(?,status) WHERE name=? AND version=?",
                      (json.dumps(metrics,ensure_ascii=False),status,name,version))

    def list(self):
        with sqlite3.connect(self.path) as c:
            rows=c.execute("SELECT name,version,role,created_at,config,metrics,status FROM experiment ORDER BY id DESC").fetchall()
        return [{"name":r[0],"version":r[1],"role":r[2],"created_at":r[3],
                 "config":json.loads(r[4]),"metrics":json.loads(r[5]),"status":r[6]} for r in rows]
