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
_MODEL_LOAD_FAILURE_HINTS = (
    'cudaMalloc failed: out of memory',
    'out of memory',
    'failed to allocate cuda',
    'unable to allocate cuda',
    'failed to fit params to free device memory',
    'failed to load model',
    'error loading model',
    'exiting due to model loading error',
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


def _last_model_load_start(lines: list[str]) -> int:
    best = -1
    for index, line in enumerate(lines):
        lower = line.lower()
        if 'load: spawning server instance' in lower or 'load_model: loading model' in lower:
            best = index
    return best


def is_active_model_load(lines: list[str]) -> bool:
    """Return whether the latest on-demand router load is still in progress."""
    start = _last_model_load_start(lines)
    if start < 0:
        return False
    for line in reversed(lines[start:]):
        lower = line.lower()
        state_match = _ROUTER_STATE_RE.search(line)
        if state_match:
            try:
                payload = json.loads(state_match.group(1))
            except json.JSONDecodeError:
                payload = {}
            state = str(payload.get('state') or '').lower() if isinstance(payload, dict) else ''
            if state in {'ready', 'error'}:
                return False
            if state == 'loading':
                return True
        if 'model loaded' in lower or 'exiting due to model loading error' in lower:
            return False
        if 'instance name=' in lower and 'exited with status' in lower:
            return False
        if 'load_model: loading model' in lower or 'load: spawning server instance' in lower:
            return True
    return False


def _model_load_failure_lines(lines: list[str]) -> list[str]:
    start = _last_model_load_start(lines)
    if start < 0:
        return []
    segment = lines[start:]
    return [
        line
        for line in segment
        if any(hint in line.lower() for hint in _MODEL_LOAD_FAILURE_HINTS)
    ]


def model_load_failure_message(lines: list[str]) -> str | None:
    """Return a concise user-facing explanation for the latest model load failure."""
    failures = _model_load_failure_lines(lines)
    if not failures:
        return None

    combined = '\n'.join(failures)
    lower = combined.lower()
    allocation = re.search(
        r'allocating\s+([\d.]+)\s+miB\s+on\s+device\s+(\d+)',
        combined,
        flags=re.I,
    )
    allocation_text = ''
    if allocation:
        try:
            gib = float(allocation.group(1)) / 1024.0
            allocation_text = f' while allocating about {gib:.1f} GiB on GPU {allocation.group(2)}'
        except (TypeError, ValueError):
            allocation_text = ''

    if 'out of memory' in lower or 'failed to fit params to free device memory' in lower:
        return (
            'Model load failed: not enough GPU memory'
            f'{allocation_text}. Lower GPU layers, reduce context or parallel slots, '
            'unload another GPU model, or enable multi-GPU splitting.'
        )
    if 'unable to allocate cuda' in lower or 'failed to allocate cuda' in lower:
        return (
            'Model load failed: the model could not be allocated in GPU memory. '
            'Lower GPU layers, reduce context or parallel slots, or choose a smaller model.'
        )
    arch_match = re.search(r"unknown model architecture:\s*'([^']+)'", combined, flags=re.I)
    if arch_match:
        arch = arch_match.group(1)
        return (
            f'Model load failed: llama-server does not support the {arch} architecture yet. '
            'Update llama.cpp to the latest CUDA build, or use a model format this engine supports.'
        )
    if 'failed to load draft' in lower or 'wrong number of tensors' in lower:
        return (
            'Model load failed: the D-Flash draft file is not compatible with this llama-server build. '
            'The main model can still load without D-Flash acceleration.'
        )
    return 'Model load failed. Open the engine log for the detailed loader message.'


def mark_boot_failed(server_id: str, reason: str) -> None:
    append_log(server_id, f"=== boot failed {time.strftime('%Y-%m-%d %H:%M:%S')} reason={reason} ===")


def read_log_tail(server_id: str, *, max_lines: int = 400) -> list[str]:
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


_LOAD_VRAM_BASELINE: dict[str, float] = {}


def clear_vram_progress_baseline(server_id: str) -> None:
    if server_id:
        _LOAD_VRAM_BASELINE.pop(server_id, None)


def estimate_vram_load_progress(
    server_id: str,
    current_vram_gb: float | None,
    model_size_gb: float | None,
    *,
    active: bool,
) -> float | None:
    """Rough load % from VRAM growth when router log progress is unavailable."""
    if not server_id or not active:
        clear_vram_progress_baseline(server_id)
        return None
    try:
        current = float(current_vram_gb or 0)
        target = float(model_size_gb or 0)
    except (TypeError, ValueError):
        return None
    if current <= 0 or target <= 0:
        return None
    baseline = _LOAD_VRAM_BASELINE.get(server_id)
    if baseline is None:
        _LOAD_VRAM_BASELINE[server_id] = current
        return None
    delta = max(0.0, current - baseline)
    if delta <= 0:
        return None
    return round(min(95.0, delta / target * 100.0), 1)


def merge_load_progress(*values: float | None) -> float | None:
    chosen: float | None = None
    for value in values:
        if value is None:
            continue
        try:
            pct = float(value)
        except (TypeError, ValueError):
            continue
        pct = min(100.0, max(0.0, pct))
        if chosen is None or pct > chosen:
            chosen = pct
    return chosen


def parse_load_progress(lines: list[str]) -> float | None:
    segment = boot_segment(lines)
    if not segment:
        start = _last_model_load_start(lines)
        if start < 0:
            return None
        segment = lines[start:]

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
        state = str(payload.get('state') or '').lower()
        if state == 'loading':
            progress = _progress_from_router_state(payload)
            if progress is not None:
                return progress
        elif state in {'ready', 'error'}:
            break

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
