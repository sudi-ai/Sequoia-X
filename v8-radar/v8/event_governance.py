from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .config import ROOT
from .storage import connect, initialize

CONFIG_PATH=ROOT/"config"/"event_governance.json"
DEFAULT={"config_version":1,"blacklist_codes":[],"blacklist_phrases":[],"whitelist_codes":[],"whitelist_topics":[]}
_lock=threading.RLock();_cache:dict[str,Any]={"mtime":None,"value":DEFAULT.copy()}


def load_config()->dict[str,Any]:
    with _lock:
        if not CONFIG_PATH.exists():return dict(DEFAULT)
        mtime=CONFIG_PATH.stat().st_mtime
        if _cache["mtime"]==mtime:return dict(_cache["value"])
        try:
            value=json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(value,dict) or int(value.get("config_version",0))<1:raise ValueError("invalid config_version")
            for key in DEFAULT:
                if key!="config_version" and not isinstance(value.get(key,[]),list):raise ValueError(f"invalid {key}")
            merged={**DEFAULT,**value};_cache.update({"mtime":mtime,"value":merged});return dict(merged)
        except Exception:
            return dict(_cache["value"])


def govern_event(event:Mapping[str,Any],now:datetime|None=None)->dict[str,Any]:
    cfg=load_config();code=str(event.get("code") or "");text=f"{event.get('title','')} {event.get('summary','')}"
    hard=str(event.get("alert_type"))=="HOLDING_HARD_RISK"
    blocked=(code in {str(x) for x in cfg["blacklist_codes"]} or
             any(str(x) and str(x) in text for x in cfg["blacklist_phrases"]))
    priority=(code in {str(x) for x in cfg["whitelist_codes"]} or
              str(event.get("topic") or "") in {str(x) for x in cfg["whitelist_topics"]})
    # Governance can suppress noise, but can never suppress a holding hard risk.
    allowed=hard or not blocked
    result={"allowed":allowed,"observation_priority":priority,"hard_risk_bypass":hard and blocked,
            "config_version":cfg["config_version"],"reason":"HARD_RISK_BYPASS" if hard and blocked else
            ("BLACKLIST_SUPPRESS" if blocked else ("WHITELIST_OBSERVE" if priority else "NORMAL"))}
    initialize();c=connect();c.execute("""insert into v8_event_governance_audit(event_id,checked_at,decision,reason,
      config_version,details_json) values(?,?,?,?,?,?)""",(event.get("event_id"),(now or datetime.now().astimezone()).isoformat(timespec="seconds"),
      "ALLOW" if allowed else "SUPPRESS",result["reason"],cfg["config_version"],json.dumps(result,ensure_ascii=False)));c.commit();c.close()
    return result
