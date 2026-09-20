from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MANIFEST=ROOT/'protected_v66_manifest.json'

def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def compare(base:Path)->dict:
    m=json.loads(MANIFEST.read_text(encoding='utf-8')); same=[]; different=[]; missing=[]
    for item in m.get('files',[]):
        rel=item['path']; p=base/rel
        if not p.is_file(): missing.append(rel); continue
        actual=sha(p)
        if actual==item['sha256']: same.append(rel)
        else: different.append({'path':rel,'expected':item['sha256'],'actual':actual})
    passed=not different and not missing and len(same)==len(m.get('files',[]))
    return {'baseline_dir':str(base),'status':'通过外部基线验证' if passed else '未通过外部基线验证','same':same,'different':different,'missing':missing}

def main():
    ap=argparse.ArgumentParser(description='只读比较当前运行V6.6与protected manifest')
    ap.add_argument('directory',nargs='?')
    a=ap.parse_args(); raw=a.directory or input('请输入当前正在运行的V6.6目录：').strip(); base=Path(raw).expanduser().resolve()
    r=compare(base); print(json.dumps(r,ensure_ascii=False,indent=2)); return 0 if r['status']=='通过外部基线验证' else 2
if __name__=='__main__': raise SystemExit(main())
