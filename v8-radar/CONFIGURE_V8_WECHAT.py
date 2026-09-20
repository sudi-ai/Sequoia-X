from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main():
    print("V8只允许配置独立新群，不会修改V7.2群。")
    hook=input("粘贴V8新群企业微信机器人Webhook：").strip()
    if not hook.startswith("https://"):
        print("格式不正确，未保存。");return 1
    (ROOT/'wework_webhook_v8_shadow.txt').write_text(hook,encoding='utf-8')
    env=ROOT/'.env.v8';lines=env.read_text(encoding='utf-8-sig').splitlines() if env.exists() else []
    values={}
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);values[k.strip()]=v.strip()
    values.update({'V8_ENABLE':'true','V8_SHADOW_ONLY':'true','V8_PUSH_ENABLED':'true','V8_AUTO_ORDER_ENABLED':'false',
      'V8_DB_PATH':'data_v8/signal_lab_v8.sqlite3','V8_WEBHOOK_FILE':'wework_webhook_v8_shadow.txt'})
    env.write_text('\n'.join(f'{k}={v}' for k,v in values.items())+'\n',encoding='utf-8')
    print("V8新群已保存。请重新启动V8；V7.2群和程序未改动。")
    return 0


if __name__=='__main__':raise SystemExit(main())
