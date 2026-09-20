# -*- coding: utf-8 -*-
from __future__ import annotations
import json, sqlite3, threading
from contextlib import closing
from datetime import datetime
from config import DATA_DIR

DB=DATA_DIR/"limitup_ecology.db"
SCHEMA="""
CREATE TABLE IF NOT EXISTS limitup_day(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 trade_date TEXT NOT NULL,
 ts_code TEXT NOT NULL,
 name TEXT,
 board INTEGER,
 sector TEXT,
 payload TEXT NOT NULL,
 UNIQUE(trade_date,ts_code)
);
CREATE INDEX IF NOT EXISTS idx_limitup_day_date_board ON limitup_day(trade_date,board);

CREATE TABLE IF NOT EXISTS first_board_sample(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 trade_date TEXT NOT NULL,
 ts_code TEXT NOT NULL,
 name TEXT,
 label INTEGER,
 max_future_board INTEGER,
 features TEXT NOT NULL,
 payload TEXT NOT NULL,
 UNIQUE(trade_date,ts_code)
);

CREATE TABLE IF NOT EXISTS ecology_daily(
 trade_date TEXT PRIMARY KEY,
 score REAL,
 phase TEXT,
 max_board INTEGER,
 multi_board_count INTEGER,
 promotion TEXT,
 payload TEXT NOT NULL
);
"""

class LimitupStore:
    def __init__(self,path=DB):
        self.path=str(path);self.lock=threading.Lock()
        with closing(sqlite3.connect(self.path)) as c,c:c.executescript(SCHEMA)

    def save_day(self,trade_date,rows):
        with self.lock,closing(sqlite3.connect(self.path)) as c,c:
            for x in rows:
                c.execute("""INSERT OR REPLACE INTO limitup_day(trade_date,ts_code,name,board,sector,payload)
                    VALUES(?,?,?,?,?,?)""",(trade_date,x.get("ts_code",""),x.get("name",""),
                    int(x.get("board",1)),x.get("sector",""),json.dumps(x,ensure_ascii=False)))

    def load_day(self,trade_date):
        with closing(sqlite3.connect(self.path)) as c:
            rows=c.execute("SELECT payload FROM limitup_day WHERE trade_date=? ORDER BY board DESC,id",(trade_date,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_ecology(self,trade_date,eco):
        with self.lock,closing(sqlite3.connect(self.path)) as c,c:
            c.execute("""INSERT OR REPLACE INTO ecology_daily(trade_date,score,phase,max_board,multi_board_count,promotion,payload)
                VALUES(?,?,?,?,?,?,?)""",(trade_date,eco.get("score",0),eco.get("phase",""),
                eco.get("max_board",0),eco.get("multi_board_count",0),
                json.dumps(eco.get("promotion"),ensure_ascii=False),json.dumps(eco,ensure_ascii=False)))

    def save_first_board_sample(self,s):
        with self.lock,closing(sqlite3.connect(self.path)) as c,c:
            return c.execute("""INSERT OR REPLACE INTO first_board_sample(
                trade_date,ts_code,name,label,max_future_board,features,payload)
                VALUES(?,?,?,?,?,?,?)""",(s.get("trade_date",""),s.get("ts_code",""),s.get("name",""),
                int(s.get("label",0)),int(s.get("max_future_board",1)),
                json.dumps(s.get("features",{}),ensure_ascii=False),
                json.dumps(s,ensure_ascii=False))).lastrowid

    def training_stats(self):
        with closing(sqlite3.connect(self.path)) as c:
            n=c.execute("SELECT COUNT(*) FROM first_board_sample").fetchone()[0]
            pos=c.execute("SELECT COUNT(*) FROM first_board_sample WHERE label=1").fetchone()[0]
            neg=c.execute("SELECT COUNT(*) FROM first_board_sample WHERE label=0").fetchone()[0]
        return {"n":n,"positive":pos,"negative":neg}
