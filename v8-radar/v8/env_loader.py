from __future__ import annotations
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_LIVE_ENV=ROOT/'.env.v8'


def load_v8_env(path:Path|None=None,override:bool=False)->int:
    p=path or ROOT/'.env.v8'
    n=0
    lines=p.read_text(encoding='utf-8-sig').splitlines() if p.exists() else []
    for raw in lines:
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key,value=line.split('=',1); key=key.strip(); value=value.strip().strip('"\'')
        if not key.replace('_','').isalnum(): continue
        if override or key not in os.environ: os.environ[key]=value; n+=1
    # Paid-interface credentials are owned by V8's private environment file.
    # V8 must not depend on a live V7 directory after migration.
    source=Path(os.getenv('V8_PAID_CREDENTIAL_SOURCE',str(DEFAULT_LIVE_ENV)))
    if not os.getenv('TUSHARE_TOKEN') and source.exists():
        allowed={'TUSHARE_TOKEN','TUSHARE_HTTP_URL'}
        for raw in source.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in raw:continue
            key,value=raw.split('=',1);key=key.strip()
            if key in allowed and key not in os.environ:
                os.environ[key]=value.strip().strip('"\'');n+=1
    return n
