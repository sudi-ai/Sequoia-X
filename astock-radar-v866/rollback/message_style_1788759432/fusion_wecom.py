"""Dedicated new-version robot. Never falls back to legacy configuration."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent


def channel(secret_file=None, environ=None):
    env = os.environ if environ is None else environ
    path = Path(secret_file) if secret_file else ROOT / 'secrets.local.json'
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
        cfg = cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        cfg = {}
    url = str(env.get('FUSION_WECOM_WEBHOOK') or cfg.get('fusion_wecom_webhook') or '').strip()
    parsed = urlparse(url)
    valid = (parsed.scheme == 'https' and parsed.netloc == 'qyapi.weixin.qq.com'
             and parsed.path == '/cgi-bin/webhook/send' and not parsed.fragment
             and set(parse_qs(parsed.query)) == {'key'}
             and len(parse_qs(parsed.query).get('key', [])) == 1)
    return (url if valid else ''), {
        'configured': bool(valid), 'dedicated': True, 'legacy_fallback': False,
        'fingerprint': hashlib.sha256(url.encode()).hexdigest()[:12] if valid else None,
        'status': 'CONFIGURED' if valid else 'NEW_GROUP_NOT_CONFIGURED',
    }


def send_text(content, transport=None, secret_file=None, environ=None):
    url, info = channel(secret_file, environ)
    if not url:
        return dict(status='FAILED', error='NEW_GROUP_NOT_CONFIGURED', channel=info)
    if len(str(content).encode('utf-8')) > 1800:
        return dict(status='FAILED', error='MESSAGE_TOO_LONG', channel=info)
    if transport is None:
        import requests
        transport = requests.post
    try:
        reply = transport(url, json={'msgtype':'text','text':{'content':str(content)}},
                          timeout=10, allow_redirects=False)
        if reply.status_code != 200:
            return dict(status='UNCERTAIN', error='HTTP_ACK_UNCONFIRMED', channel=info)
        body = reply.json()
        if not isinstance(body, dict) or type(body.get('errcode')) is not int:
            return dict(status='UNCERTAIN', error='ACK_MISSING', channel=info)
        return dict(status='SENT' if body['errcode'] == 0 else 'FAILED',
                    error='' if body['errcode'] == 0 else 'WECOM_REJECTED',
                    errcode=body['errcode'], channel=info)
    except Exception:
        return dict(status='UNCERTAIN', error='DELIVERY_UNCONFIRMED_NO_AUTO_RETRY', channel=info)
