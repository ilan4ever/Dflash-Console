"""Run a heavy generation while polling live inference stats."""
from __future__ import annotations

import json
import threading
import time
import urllib.request


def run_generation() -> None:
    body = json.dumps({
        'model': 'gemma-4-31b-it-dflash',
        'messages': [{
            'role': 'user',
            'content': (
                'Write a very long, detailed essay about GPU inference, memory bandwidth, '
                'KV cache growth, batching strategies, and speculative decoding. '
                'Keep writing with many sections until you reach the token limit.'
            ),
        }],
        'max_tokens': 1024,
    }).encode()
    req = urllib.request.Request(
        'http://127.0.0.1:8900/api/servers/gemma-31b-dflash/v1/chat/completions',
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    started = time.time()
    payload = json.loads(urllib.request.urlopen(req, timeout=600).read().decode())
    usage = payload.get('usage') or {}
    timings = payload.get('timings') or {}
    print(
        f"DONE {round(time.time() - started, 1)}s | "
        f"{usage.get('completion_tokens')} tok | "
        f"{round(float(timings.get('predicted_per_second') or 0), 1)} t/s"
    )


def poll_stats() -> None:
    time.sleep(0.5)
    for i in range(40):
        data = json.loads(urllib.request.urlopen('http://127.0.0.1:8900/api/servers').read())
        server = next(item for item in data['servers'] if item['id'] == 'gemma-31b-dflash')
        stats = server.get('inference_stats') or {}
        print(
            f"{i:02d} generating={stats.get('generating')} secs={stats.get('generating_seconds')} "
            f"out={stats.get('generation_tokens')} tps={stats.get('tokens_per_second')}"
        )
        if not stats.get('generating') and i > 3 and stats.get('generation_tokens'):
            break
        time.sleep(1)


if __name__ == '__main__':
    worker = threading.Thread(target=run_generation, daemon=True)
    worker.start()
    poll_stats()
    worker.join(timeout=620)
