"""Parse llama-server boot logs for load progress."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from core.log_utils import read_tail_lines, rotate_log

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'logs'

_PROGRESS_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%')
_ROUTER_STATE_RE = re.compile(r'cmd_child_to_router:state:(\{.*\})')
_LOAD_HINTS = ('load', 'progress', 'tensor', 'offload', 'ggml', 'llama')
_BOOT_FAILURE_HINTS = (
    "couldn't bind http server socket",
    'exiting due to http server error',
)
_LOG_LOCK = threading.Lock()


def _last_boot_index(lines: list[str]) -> int:
    best = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('=== boot failed '):
            continue
        if '=== boot ' in line or '=== embedding boot ' in line:
            best = index
    return best


def boot_failure_message(lines: list[str]) -> str | None:
    boot_index = _last_boot_index(lines)
    if boot_index < 0:
        return None
    failed_index = _last_marker_index(lines, '=== boot failed ')
    if failed_index > boot_index:
        for line in reversed(lines[failed_index:]):
            text = line.strip()
            if text.startswith('=== boot failed ') and 'reason=' in text:
                return text.split('reason=', 1)[-1].strip().rstrip('=').strip() or 'Engine failed to start'
        return 'Engine failed to start (see developer logs)'
    for line in reversed(lines[boot_index:]):
        lower = line.lower()
        if "couldn't bind http server socket" in lower:
            return 'Port already in use — free the port or stop the other engine'
    for line in reversed(lines[boot_index:]):
        lower = line.lower()
        if 'exiting due to http server error' in lower:
            return 'Engine exited during startup (see developer logs)'
    return None


def mark_boot_failed(server_id: str, reason: str) -> None:
    append_log(server_id, f"=== boot failed {time.strftime('%Y-%m-%d %H:%M:%S')} reason={reason} ===")


def read_log_tail(server_id: str, *, max_lines: int = 120) -> list[str]:
    log_path = LOG_DIR / f'{server_id}.log'
    lines, _ = read_tail_lines(log_path, max_lines=max_lines)
    return lines


def append_log(server_id: str, line: str) -> None:
    if not server_id:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'{server_id}.log'
    with _LOG_LOCK:
        rotate_log(log_path)
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write(line.rstrip() + '\n')


def _last_marker_index(lines: list[str], marker: str) -> int:
    for index in range(len(lines) - 1, -1, -1):
        if marker in lines[index]:
            return index
    return -1


def _last_cycle_end_index(lines: list[str]) -> int:
    best = -1
    for marker in ('=== stop ', '=== model unload ', '=== router idle ready '):
        idx = _last_marker_index(lines, marker)
        if idx > best:
            best = idx
    return best


def is_active_boot(lines: list[str]) -> bool:
    boot_index = _last_boot_index(lines)
    if boot_index < 0:
        return False
    if boot_failure_message(lines):
        return False
    return _last_cycle_end_index(lines) < boot_index


def boot_segment(lines: list[str]) -> list[str]:
    if not is_active_boot(lines):
        return []
    boot_index = _last_boot_index(lines)
    end_index = _last_cycle_end_index(lines)
    start = boot_index if end_index < boot_index else end_index + 1
    return lines[start:]


def _progress_from_router_state(payload: dict) -> float | None:
    if str(payload.get('state') or '').lower() != 'loading':
        return None
    inner = payload.get('payload') or {}
    value = inner.get('value')
    if not isinstance(value, (int, float)):
        return None
    stages = inner.get('stages') or []
    current = str(inner.get('current') or '')
    if isinstance(stages, list) and stages:
        try:
            stage_index = stages.index(current) if current in stages else 0
        except ValueError:
            stage_index = 0
        overall = (stage_index + float(value)) / max(len(stages), 1) * 100.0
        return round(min(100.0, max(0.0, overall)), 2)
    pct = float(value) * 100.0 if float(value) <= 1.0 else float(value)
    return round(min(100.0, max(0.0, pct)), 2)


def parse_load_progress(lines: list[str]) -> float | None:
    segment = boot_segment(lines)
    if not segment:
        return None

    for line in reversed(segment):
        match = _ROUTER_STATE_RE.search(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        progress = _progress_from_router_state(payload)
        if progress is not None:
            return progress

    for line in reversed(segment):
        lower = line.lower()
        if not any(hint in lower for hint in _LOAD_HINTS):
            continue
        match = _PROGRESS_RE.search(line)
        if match:
            value = float(match.group(1))
            if 0 <= value <= 100:
                return round(value, 2)
    return None


def boot_marker_recent(lines: list[str], *, within_last: int = 15) -> bool:
    """Legacy helper — prefer is_active_boot()."""
    tail = lines[-within_last:]
    return is_active_boot(lines) and any('=== boot ' in line for line in tail)


def stop_log_line() -> str:
    return f"=== stop {time.strftime('%Y-%m-%d %H:%M:%S')} ==="
