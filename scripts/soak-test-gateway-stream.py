"""Soak-test DFlash Console gateway streaming stability.

Examples:
  python scripts/soak-test-gateway-stream.py --model qwen3-8-27b-q6-k-l-dflash
  python scripts/soak-test-gateway-stream.py --gateway http://127.0.0.1:8001/v1 --stream-seconds 180
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _post_json(url: str, body: dict, *, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace') or '{}')


def _stream_chat(url: str, body: dict, *, label: str, min_bytes: int = 32) -> None:
    payload = dict(body)
    payload['stream'] = True
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    started = time.time()
    content_parts: list[str] = []
    raw = bytearray()
    saw_done = False
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                raw.extend(chunk)
                text = chunk.decode('utf-8', errors='replace')
                for line in text.splitlines():
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        saw_done = True
                        continue
                    if not data:
                        continue
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    error = parsed.get('error')
                    if isinstance(error, dict) and error.get('message'):
                        raise RuntimeError(str(error['message']))
                    choices = parsed.get('choices') or []
                    if choices and isinstance(choices[0], dict):
                        delta = choices[0].get('delta') or {}
                        piece = delta.get('content')
                        if isinstance(piece, str) and piece.strip():
                            content_parts.append(piece)
                        reasoning = delta.get('reasoning_content')
                        if isinstance(reasoning, str) and reasoning.strip():
                            content_parts.append(reasoning)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{label} HTTP {exc.code}: {detail}') from exc
    elapsed = round(time.time() - started, 1)
    content = ''.join(content_parts)
    if len(raw) < min_bytes and not content.strip():
        raise RuntimeError(f'{label} returned too little stream data ({len(raw)} bytes in {elapsed}s)')
    if not saw_done:
        raise RuntimeError(f'{label} ended without [DONE] after {elapsed}s ({len(content)} content chars)')
    print(f'{label}: OK in {elapsed}s | {len(content)} content chars | {len(raw)} raw bytes')


def main() -> int:
    parser = argparse.ArgumentParser(description='Soak-test DFlash Console gateway streaming')
    parser.add_argument('--gateway', default='http://127.0.0.1:8001/v1')
    parser.add_argument('--model', default='qwen3-8-27b-q6-k-l-dflash')
    parser.add_argument('--max-tokens', type=int, default=512)
    parser.add_argument('--stream-seconds', type=int, default=120, help='Target generation size via prompt length')
    args = parser.parse_args()

    base = args.gateway.rstrip('/')
    chat_url = f'{base}/chat/completions'

    short = _post_json(chat_url, {
        'model': args.model,
        'messages': [{'role': 'user', 'content': 'Reply with exactly OK.'}],
        'max_tokens': 64,
        'stream': False,
    })
    content = (((short.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
    if not content:
        raise SystemExit(f'Non-stream probe returned empty content: {short}')
    print(f'Non-stream probe: {content[:80]!r}')

    long_prompt = (
        'Write a detailed multi-section essay about GPU inference, KV cache growth, '
        'speculative decoding, tool-call orchestration, and long-running agent loops. '
        'Keep writing continuously with numbered sections until you exhaust the token budget. '
    ) * max(1, args.stream_seconds // 20)

    _stream_chat(chat_url, {
        'model': args.model,
        'messages': [{'role': 'user', 'content': long_prompt}],
        'max_tokens': args.max_tokens,
    }, label='Long stream')

    tool_prompt = (
        'You are a coding agent. First summarize the task, then produce a long patch-style answer '
        'with multiple files, functions, tests, and edge cases. Include JSON tool-call shaped blocks '
        'and continue generating code until the token budget is exhausted.\n\n'
        'Task: implement a resilient SSE chat proxy with reconnect-safe keep-alives.'
    )
    _stream_chat(chat_url, {
        'model': args.model,
        'messages': [{'role': 'user', 'content': tool_prompt}],
        'max_tokens': max(args.max_tokens, 768),
    }, label='Tool-style stream')

    print('Gateway streaming soak test passed.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'FAILED: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
