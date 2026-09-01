"""Monitor GPU while loading DeepSeek via FreeToken."""
from __future__ import annotations

import json
import sys
import time
import urllib.request

API = 'http://127.0.0.1:8900'
MODEL_PATH = r'C:\dev\Dflash-Console\models\deepseek-ai\DeepSeek-V4-Flash-0731'
MODEL_ID = 'deepseek-ai/DeepSeek-V4-Flash-0731'


def get_json(url: str, *, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def post_json(url: str, payload: dict, *, timeout: float = 0) -> dict:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout or None) as resp:
        return json.loads(resp.read().decode('utf-8'))


def gpu_line() -> str:
    stats = get_json(f'{API}/api/system-stats', timeout=10)
    parts = []
    for gpu in stats.get('gpus') or []:
        parts.append(
            f"{gpu.get('display_name')}: {gpu.get('vram_used_gb')} / {gpu.get('vram_total_gb')} GB "
            f"({gpu.get('vram_percent')}%)"
        )
    return ' | '.join(parts) if parts else 'no gpus'


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else 'monitor'
    if mode == 'load':
        print('Starting FreeToken load...')
        print('Baseline GPU:', gpu_line())
        payload = {
            'path': MODEL_PATH,
            'model_id': MODEL_ID,
            'runtime_id': 'freetoken',
            'load_settings': {
                'enable_cache_report': True,
            },
        }
        try:
            result = post_json(f'{API}/api/models/load', payload, timeout=0)
            print('LOAD RESULT:', json.dumps(result, indent=2))
        except Exception as exc:
            print('LOAD FAILED:', exc)
            return 1
        print('Post-load GPU:', gpu_line())
        return 0

    deadline = time.time() + 900
    print('Monitoring GPU during FreeToken load (15 min max)...')
    while time.time() < deadline:
        try:
            runtimes = get_json(f'{API}/api/runtimes/freetoken/health', timeout=10)
            running = runtimes.get('running')
            port = runtimes.get('port')
            model = runtimes.get('active_model') or ''
            print(
                time.strftime('%H:%M:%S'),
                'running=' + str(running),
                f'port={port}',
                f'model={model[-40:] if model else ""}',
                '|',
                gpu_line(),
            )
            if running and port:
                print('FreeToken is ready.')
                return 0
        except Exception as exc:
            print(time.strftime('%H:%M:%S'), 'poll error:', exc)
        time.sleep(10)
    print('Timed out waiting for FreeToken')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
