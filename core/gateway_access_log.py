"""Append-only log of OpenAI gateway chat routing (local engine vs cloud provider)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from core.log_utils import rotate_log

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / 'logs' / 'gateway-access.log'
_lock = threading.Lock()


def record_gateway_route(
    *,
    model: str,
    route: str,
    target: str,
    client: str = '',
    status: int = 0,
    duration_ms: float = 0.0,
    note: str = '',
) -> None:
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    line = (
        f'[{stamp}] model={model!r} route={route} target={target!r} '
        f'status={int(status or 0)} ms={round(float(duration_ms or 0.0), 2)}'
    )
    if client:
        line += f' client={client!r}'
    if note:
        line += f' note={note!r}'
    with _lock:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rotate_log(LOG_PATH)
        with LOG_PATH.open('a', encoding='utf-8') as fh:
            fh.write(line + '\n')
