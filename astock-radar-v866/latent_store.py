# -*- coding: utf-8 -*-
from __future__ import annotations
import json, sqlite3, threading
from contextlib import closing
from datetime import datetime
from config import DATA_DIR

DB=DATA_DIR/"latent_opportunity.db"
SCHEMA="""
CREATE TABLE IF NOT EXISTS latent_signal(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 trade_date TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 ts_code TEXT NOT NULL,
 name TEXT,
 score REAL,
 stage TEXT,
 close REAL,
 payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_latent_code_date ON latent_signal(ts_code,trade_date);

CREATE TABLE IF NOT EXISTS missed_case(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 review_date TEXT NOT NULL,
 ts_code TEXT NOT NULL,
 name TEXT,
 runup_start TEXT,
 ret_5d REAL,
 ret_10d REAL,
 had_latent INTEGER,
 best_latent_score REAL,
 best_latent_stage TEXT,
 miss_reason TEXT,
 pre_features TEXT,
 payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_missed_date_code ON missed_case(review_date,ts_code);
"""
class LatentStore:
    def __init__(self,path=DB):
        self.path=str(path);self.lock=threading.Lock()
        with closing(sqlite3.connect(self.path)) as c, c:c.executescript(SCHEMA)

    def save_latent(self,item):
        with self.lock,closing(sqlite3.connect(self.path)) as c,c:
            return c.execute("""INSERT INTO latent_signal(trade_date,observed_at,ts_code,name,score,stage,close,payload)
                VALUES(?,?,?,?,?,?,?,?)""",(
                item.get("trade_date",datetime.now().strftime("%Y%m%d")),
                item.get("observed_at",datetime.now().isoformat(timespec="seconds")),
                item.get("ts_code",""),item.get("name",""),item.get("latent_score",0),
                item.get("latent_stage","NONE"),item.get("close",0),
                json.dumps(item,ensure_ascii=False))).lastrowid

    def best_before(self,ts_code,end_date,lookback_start=None):
        q="SELECT score,stage,trade_date,payload FROM latent_signal WHERE ts_code=? AND trade_date<=?"
        args=[ts_code,end_date]
        if lookback_start:
            q+=" AND trade_date>=?";args.append(lookback_start)
        q+=" ORDER BY score DESC,id DESC LIMIT 1"
        with closing(sqlite3.connect(self.path)) as c:r=c.execute(q,args).fetchone()
        if not r:return None
        return {"score":r[0],"stage":r[1],"trade_date":r[2],"payload":json.loads(r[3])}

    def save_missed(self,x):
        with self.lock,closing(sqlite3.connect(self.path)) as c,c:
            return c.execute("""INSERT INTO missed_case(review_date,ts_code,name,runup_start,ret_5d,ret_10d,
                had_latent,best_latent_score,best_latent_stage,miss_reason,pre_features,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(
                x.get("review_date",""),x.get("ts_code",""),x.get("name",""),x.get("runup_start",""),
                x.get("ret_5d"),x.get("ret_10d"),1 if x.get("had_latent") else 0,
                x.get("best_latent_score"),x.get("best_latent_stage",""),x.get("miss_reason",""),
                json.dumps(x.get("pre_features",{}),ensure_ascii=False),json.dumps(x,ensure_ascii=False))).lastrowid

    def metrics(self):
        with closing(sqlite3.connect(self.path)) as c:
            latent=c.execute("SELECT COUNT(*) FROM latent_signal WHERE stage IN ('WATCH','READY','START')").fetchone()[0]
            missed=c.execute("SELECT COUNT(*) FROM missed_case").fetchone()[0]
            covered=c.execute("SELECT COUNT(*) FROM missed_case WHERE had_latent=1").fetchone()[0]
        return {"latent_signals":latent,"missed_cases":missed,"big_mover_recall":covered/missed if missed else None}
