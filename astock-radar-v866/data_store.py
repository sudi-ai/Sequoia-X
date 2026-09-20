# -*- coding: utf-8 -*-
import json,sqlite3,threading
from contextlib import closing
from datetime import datetime
from config import DB_FILE
SCHEMA="""CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT,trade_date TEXT,ts_code TEXT,name TEXT,pool TEXT,score REAL,t1_probability REAL,rr REAL,overnight_risk REAL,market_phase TEXT,sector TEXT,entry_price REAL,reason TEXT,veto TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS outcomes(signal_id INTEGER PRIMARY KEY,t1_return REAL,t3_return REAL,t5_return REAL,max_favorable REAL,max_adverse REAL,benchmark_t1 REAL,updated_at TEXT);"""
class Store:
    def __init__(self,path=DB_FILE):
        self.path=str(path);self.lock=threading.Lock()
        with closing(sqlite3.connect(self.path)) as c,c:c.executescript(SCHEMA)
    def put_signal(self,s):
        cols=("ts","trade_date","ts_code","name","pool","score","t1_probability","rr","overnight_risk","market_phase","sector","entry_price","reason","veto","payload")
        vals=[s.get(k,"") for k in cols[:-1]]+[json.dumps(s,ensure_ascii=False)]
        with self.lock,closing(sqlite3.connect(self.path)) as c,c:return c.execute("INSERT INTO signals("+",".join(cols)+") VALUES("+",".join(["?"]*len(cols))+")",vals).lastrowid
    def stats(self):
        with closing(sqlite3.connect(self.path)) as c:
            total=c.execute("SELECT COUNT(*) FROM signals").fetchone()[0];a=c.execute("SELECT COUNT(*) FROM signals WHERE pool='A'").fetchone()[0]
            n=c.execute("SELECT COUNT(*) FROM outcomes WHERE t1_return IS NOT NULL").fetchone()[0];wins=c.execute("SELECT COUNT(*) FROM outcomes WHERE t1_return>0").fetchone()[0]
        return {"signals":total,"a_signals":a,"evaluated":n,"t1_win_rate":wins/n if n else None}
