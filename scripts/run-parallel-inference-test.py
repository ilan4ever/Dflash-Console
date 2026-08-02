"""Fire two long parallel chat jobs against Gemma 31B for UI slot testing."""
from __future__ import annotations

import json
import threading
import time
import urllib.request


URL = 'http://127.0.0.1:8900/api/servers/gemma-31b-dflash/v1/chat/completions'
MODEL = 'gemma-4-31b-it-dflash'


def stream_job(job_id: int) -> None:
    body = json.dumps({
        'model': MODEL,
        'stream': True,
        'messages': [{
            'role': 'user',
            'content': (
                f'Job {job_id}: Write a long detailed essay about parallel GPU inference, '
                'KV cache slots, batching, memory bandwidth, and speculative decoding. '
                'Use many sections and keep writing until you hit the token limit.'
            ),
        }],
        'max_tokens': 512,
    }).encode()
    req = urllib.request.Request(
        URL,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    started = time.time()
    print(f'job {job_id} started', flush=True)
    with urllib.request.urlopen(req, timeout=600) as resp:
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
    print(f'job {job_id} done in {round(time.time() - started, 1)}s', flush=True)


def poll_slots() -> None:
    time.sleep(1.0)
    for i in range(60):
        try:
            servers = json.loads(urllib.request.urlopen('http://127.0.0.1:8900/api/servers', timeout=5).read())
            server = next(item for item in servers['servers'] if item['id'] == 'gemma-31b-dflash')
            stats = server.get('inference_stats') or {}
            slots = stats.get('slots') or []
            raw = json.loads(
                urllib.request.urlopen(
                    'http://127.0.0.1:8090/slots?model=gemma-4-31b-it-dflash',
                    timeout=5,
                ).read()
            )
            processing = sum(1 for row in raw if isinstance(row, dict) and row.get('is_processing'))
            print(
                f'{i:02d} api_slots={len(slots)} raw_slots={len(raw)} processing={processing} '
                f'generating={stats.get("generating")} visible={[row.get("slot_id") for row in slots]}',
                flush=True,
            )
        except Exception as exc:
            print(f'{i:02d} poll error: {exc}', flush=True)
        time.sleep(1)


if __name__ == '__main__':
    poller = threading.Thread(target=poll_slots, daemon=True)
    poller.start()
    workers = [
        threading.Thread(target=stream_job, args=(1,), daemon=True),
        threading.Thread(target=stream_job, args=(2,), daemon=True),
    ]
    for worker in workers:
        worker.start()
        time.sleep(0.15)
    for worker in workers:
        worker.join(timeout=620)
    time.sleep(2)
