#!/usr/bin/env python3
"""Reproduce DeepSeek Harness-style gateway chat and report client attribution."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

GATEWAY = 'http://127.0.0.1:8001'
CONSOLE = 'http://127.0.0.1:8900'
MODEL = 'qwen3-8-27b-gsq-rco-iq3-xxs-mtp-dflash'

SYSTEM = (
    'You are a helpful assistant embedded in a long-running agent harness. '
    'Follow tool instructions precisely. '
    + ('Context padding line. ' * 400)
)

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': 'Read a file from the workspace',
            'parameters': {
                'type': 'object',
                'properties': {'path': {'type': 'string'}},
                'required': ['path'],
            },
        },
    },
]


def _get_json(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace'))


def _server_row(server_id: str) -> dict | None:
    payload = _get_json(f'{CONSOLE}/api/servers?include_external=0', timeout=15.0)
    servers = payload.get('servers') if isinstance(payload, dict) else payload
    if not isinstance(servers, list):
        return None
    for row in servers:
        if str(row.get('id') or '') == server_id:
            return row
    return None


def _extract_error(body: object) -> dict:
    if not isinstance(body, dict):
        return {'message': str(body)}
    detail = body.get('detail')
    if isinstance(detail, dict):
        err = detail.get('error')
        if isinstance(err, dict):
            return err
        if isinstance(err, str):
            return {**detail, 'reason': err}
        return detail
    err = body.get('error')
    if isinstance(err, dict):
        return err
    return body


def main() -> int:
    payload = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': 'Reply with exactly: harness-ok'},
        ],
        'tools': TOOLS,
        'tool_choice': 'auto',
        'stream': True,
        'max_tokens': 64,
        'temperature': 0.2,
    }
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer local',
        'X-DFlash-Client': 'DeepSeek Harness',
        'X-Disable-Reasoning': '1',
        'X-DFlash-Load-Context': '32768',
    }
    req = urllib.request.Request(
        f'{GATEWAY}/v1/chat/completions',
        data=body,
        headers=headers,
        method='POST',
    )

    active_samples: list[str] = []
    stop = threading.Event()

    def poll_active() -> None:
        while not stop.is_set():
            try:
                row = _server_row(MODEL)
                if row:
                    label = str(row.get('loaded_by') or row.get('app_label') or '')
                    clients = row.get('active_clients') or []
                    if isinstance(clients, list) and clients:
                        first = clients[0]
                        label = str(first if isinstance(first, str) else first.get('label') or label)
                    if label:
                        active_samples.append(label)
            except Exception:
                pass
            time.sleep(0.4)

    poller = threading.Thread(target=poll_active, daemon=True)
    poller.start()

    status = 0
    err_info: dict = {}
    content_parts: list[str] = []
    server_hdr = ''
    try:
        with urllib.request.urlopen(req, timeout=600.0) as resp:
            status = resp.status
            server_hdr = resp.headers.get('X-DFlash-Server-Id', '')
            for raw_line in resp:
                line = raw_line.decode('utf-8', errors='replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if 'error' in chunk:
                    err_info = _extract_error(chunk)
                choices = chunk.get('choices') or []
                if choices and isinstance(choices[0], dict):
                    delta = choices[0].get('delta') or {}
                    piece = delta.get('content')
                    if piece:
                        content_parts.append(str(piece))
    except urllib.error.HTTPError as exc:
        status = exc.code
        server_hdr = exc.headers.get('X-DFlash-Server-Id', '')
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {'detail': raw}
        err_info = _extract_error(parsed)
    finally:
        stop.set()
        poller.join(timeout=2.0)

    print(f'HTTP {status} X-DFlash-Server-Id={server_hdr!r}')
    if err_info:
        print('error:', json.dumps(err_info, indent=2))
        reason = str(err_info.get('reason') or err_info.get('error') or '')
        if reason == 'insufficient_vram':
            req_gb = err_info.get('gpu_required_gb')
            free_gb = err_info.get('vram_free_gb')
            print(f'VRAM: required≈{req_gb} GB, free≈{free_gb} GB')
        if reason == 'reasoning_budget_too_low':
            print('reasoning_budget_too_low: X-Disable-Reasoning should be applied upstream; check gateway policy.')
    else:
        text = ''.join(content_parts).strip()
        print(f'content: {text[:200]!r}')

    unique_active = sorted(set(active_samples))
    print(f'active_client_samples: {unique_active}')
    harness_seen = any('DeepSeek Harness' in s for s in unique_active)
    print(f'engines_active_client_harness: {harness_seen}')
    return 0 if status == 200 and harness_seen else 1


if __name__ == '__main__':
    raise SystemExit(main())
