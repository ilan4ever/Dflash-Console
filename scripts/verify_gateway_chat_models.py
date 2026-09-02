#!/usr/bin/env python3
"""Verify gateway routing + chat for the three DFlash chat models."""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request

GATEWAY = 'http://127.0.0.1:8001'
TINY_PNG = (
    'data:image/png;base64,'
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
)


def _post(path: str, payload: dict, *, timeout: float = 300.0) -> tuple[int, dict | str, dict[str, str]]:
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f'{GATEWAY}{path}',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'X-Disable-Reasoning': '1',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            headers = {k.lower(): v for k, v in resp.headers.items()}
            try:
                return resp.status, json.loads(raw), headers
            except json.JSONDecodeError:
                return resp.status, raw, headers
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        headers = {k.lower(): v for k, v in exc.headers.items()}
        try:
            return exc.code, json.loads(raw), headers
        except json.JSONDecodeError:
            return exc.code, raw, headers


def _models() -> list[dict]:
    with urllib.request.urlopen(f'{GATEWAY}/v1/models', timeout=10) as resp:
        payload = json.loads(resp.read().decode('utf-8', errors='replace'))
    return list(payload.get('data') or [])


def main() -> int:
    print('=== GET /v1/models ===')
    rows = _models()
    for row in rows:
        meta = row.get('meta') or {}
        if meta.get('embedding'):
            continue
        print(
            f"  id={row.get('id')} server_id={meta.get('server_id')} "
            f"vision={meta.get('supports_vision')}"
        )

    tests = [
        ('gemma-4-12b-it-q4-k-m', 'text'),
        ('gemma-4-12b-it-q4-k-m', 'image'),
        ('qwen3-8-27b-q6-k-l', 'text'),
        ('qwen3-8-27b-q6-k-l', 'image'),
        ('gemma-4-31b-q4-0-it-dflash', 'text'),
        ('gemma-4-31b-q4-0-it-dflash', 'image'),
        ('totally-unknown-model', 'text'),
    ]

    print('\n=== POST /v1/chat/completions ===')
    failures = 0
    for model_id, kind in tests:
        if kind == 'text':
            payload = {
                'model': model_id,
                'messages': [{'role': 'user', 'content': 'Reply with exactly: pong'}],
                'stream': False,
                'max_tokens': 256,
            }
        else:
            payload = {
                'model': model_id,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': 'What color is this image? One word.'},
                        {'type': 'image_url', 'image_url': {'url': TINY_PNG}},
                    ],
                }],
                'stream': False,
                'max_tokens': 256,
            }
        status, body, headers = _post('/v1/chat/completions', payload)
        server_id = headers.get('x-dflash-server-id', '')
        if isinstance(body, dict):
            detail = body.get('detail')
            err = body.get('error')
            if err is None and isinstance(detail, dict):
                err = detail.get('error')
            if err is None and isinstance(detail, str):
                err = {'message': detail}
            err_msg = ''
            if isinstance(err, dict):
                err_msg = str(err.get('message') or err.get('reason') or '')
            content = ''
            choices = body.get('choices') or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get('message') or {}
                content = str(message.get('content') or '')[:80]
            print(
                f'  {model_id:32} {kind:5} -> HTTP {status} server={server_id!r} '
                f'model={body.get("model","")!r} content={content!r} err={err_msg[:100]!r}'
            )
            if model_id == 'totally-unknown-model':
                if status != 404:
                    failures += 1
            elif model_id == 'gemma-4-31b-q4-0-it-dflash' and kind == 'image':
                if status != 400 or 'vision projector' not in err_msg.lower():
                    failures += 1
            elif status != 200:
                failures += 1
        else:
            print(f'  {model_id:32} {kind:5} -> HTTP {status} body={str(body)[:160]!r}')
            if model_id != 'totally-unknown-model' and status != 200:
                failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
