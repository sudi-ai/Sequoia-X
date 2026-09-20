from __future__ import annotations
import argparse
from v7.env_loader import load_project_env

def main()->int:
    ap=argparse.ArgumentParser(description='V7.2 Shadow测试群单次UTF-8发送入口')
    ap.add_argument('--send',action='store_true',help='显式发送到V7 Shadow新群；默认仅Mock')
    a=ap.parse_args(); load_project_env()
    from v7.wework_shadow_push import send_message, _load_webhook
    text='【V7.2 Shadow测试】\n中文UTF-8：主升延续 / 健康洗盘 / 真实转弱\nEmoji：✅ ⚠️ 📊\n仅测试V7新群，不读取V6.6原群。'
    if not a.send:
        ok,detail=send_message(text,webhook='https://example.invalid/redacted',post_func=lambda u,p,t:(True,'mock_ok'))
    else:
        webhook=_load_webhook();
        if not webhook: print('SEND_FAILED: V7 Shadow webhook missing'); return 2
        ok,detail=send_message(text,webhook=webhook)
    print('SEND_OK' if ok else f'SEND_FAILED: {detail}'); return 0 if ok else 1
if __name__=='__main__': raise SystemExit(main())
