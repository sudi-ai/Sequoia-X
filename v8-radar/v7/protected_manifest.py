from __future__ import annotations
import hashlib,json
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
PATTERNS=['market_radar_V6_6_Pro.py','v66_*.py','START_RADAR_V6.6_PRO.cmd','START_RADAR_V6.6_PRO.vbs']
def _sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest()
def build_manifest(path:Path|None=None):
    files=[]
    for pat in PATTERNS:
        for p in sorted(ROOT.glob(pat)):
            files.append({'path':p.relative_to(ROOT).as_posix(),'sha256':_sha(p)})
    out={'generated_at':datetime.now().astimezone().isoformat(timespec='seconds'),'files':files,'allow_changes':[]}
    if path: path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    return out
def verify_manifest(manifest:dict):
    problems=[]
    for item in manifest.get('files',[]):
        p=ROOT/item['path']
        if not p.exists(): problems.append({'path':item['path'],'reason':'missing'})
        elif _sha(p)!=item['sha256']: problems.append({'path':item['path'],'reason':'hash_changed'})
    return {'ok':not problems,'problems':problems}
