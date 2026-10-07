"""Per-process GPU memory on Windows via WDDM performance counters."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from typing import Any

_CACHE: dict[str, Any] = {'at': 0.0, 'map': {}}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 20.0
_MAX_PROCESS_BYTES = 64 * (1024 ** 3)

_ROW_RE = re.compile(
    r'pid_(\d+)_luid_((?:0x)?[0-9a-f]+)_((?:0x)?[0-9a-f]+)_phys_(\d+)',
    re.I,
)


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


def _fetch_windows_process_gpu_bytes() -> dict[tuple[int, str], int]:
    # Keep the larger sample per process+adapter. Windows repeats the same
    # allocation on several engine nodes (3D, copy, compute) and both boards
    # report phys_0, so the adapter LUID is what separates the GPUs.
    script = r"""
$rows = Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -ErrorAction SilentlyContinue
$agg = @{}
foreach ($r in $rows) {
  if ($r.Name -match 'pid_(\d+)_luid_((?:0x)?[0-9a-fA-F]+)_((?:0x)?[0-9a-fA-F]+)_phys_(\d+)') {
    $procId = [int]$matches[1]
    $luid = ($matches[2] + '_' + $matches[3]).ToLower()
    $key = "$procId|$luid"
    $bytes = [long]($r.DedicatedUsage)
    if ($bytes -le 0) { $bytes = [long]($r.TotalCommitted) }
    if ($bytes -le 0) { continue }
    if (-not $agg.ContainsKey($key)) { $agg[$key] = [long]0 }
    $current = [long]$agg[$key]
    if ($bytes -gt $current) { $agg[$key] = $bytes }
  }
}
$out = @()
foreach ($kv in $agg.GetEnumerator()) {
  $parts = $kv.Key.Split('|')
  $out += @{ pid = [int]$parts[0]; luid = [string]$parts[1]; bytes = [long]$kv.Value }
}
$out | ConvertTo-Json -Compress
"""
    from core.bounded_proc import run_bounded

    code, text = run_bounded(
        ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script],
        timeout=4,
    )
    if code != 0 or not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    rows = payload if isinstance(payload, list) else [payload]
    mapping: dict[tuple[int, str], int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get('pid') or 0)
            luid = str(row.get('luid') or '').strip().lower()
            bytes_val = int(row.get('bytes') or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or not luid or bytes_val <= 0 or bytes_val > _MAX_PROCESS_BYTES:
            continue
        key = (pid, luid)
        prior = mapping.get(key, 0)
        if bytes_val > prior:
            mapping[key] = bytes_val
    return mapping


def match_luids_to_gpus(
    per_luid_bytes: dict[str, int],
    gpus: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """Pair adapter LUIDs with nvidia-smi GPU indexes.

    Both adapters report ``phys_0``. The LUID whose dedicated total is closest
    to a board's used memory is that board.
    """
    totals = {
        str(luid).lower(): int(nbytes)
        for luid, nbytes in (per_luid_bytes or {}).items()
        if int(nbytes or 0) >= 64 * (1024 ** 2)
    }
    devices: list[tuple[int, int | None]] = []
    for gpu in gpus or []:
        if not isinstance(gpu, dict) or gpu.get('index') is None:
            continue
        try:
            index = int(gpu.get('index'))
        except (TypeError, ValueError):
            continue
        used = gpu.get('vram_used_gb')
        used_bytes: int | None
        try:
            used_bytes = int(float(used) * (1024 ** 3)) if used is not None else None
        except (TypeError, ValueError):
            used_bytes = None
        devices.append((index, used_bytes))
    if not totals:
        return {}
    if len(devices) == 1:
        only = devices[0][0]
        return {luid: only for luid in totals}
    remaining = list(devices)
    assigned: dict[str, int] = {}
    for luid, nbytes in sorted(totals.items(), key=lambda item: item[1], reverse=True):
        if not remaining:
            break
        best_at = 0
        best_err: int | None = None
        for at, (index, used_bytes) in enumerate(remaining):
            if used_bytes is None:
                continue
            err = abs(int(used_bytes) - int(nbytes))
            if best_err is None or err < best_err:
                best_err = err
                best_at = at
        if best_err is None:
            best_at = 0
        index, _used = remaining.pop(best_at)
        assigned[luid] = index
    return assigned


def _gpu_devices_for_luid_match() -> list[dict[str, Any]]:
    try:
        from core.gpu_devices import query_gpu_devices

        devices = query_gpu_devices()
    except Exception:
        return []
    return devices if isinstance(devices, list) else []


def _mapping_from_luid_rows(
    per_pid_luid: dict[tuple[int, str], int],
    gpus: list[dict[str, Any]] | None = None,
) -> dict[tuple[int, int], int]:
    per_luid: dict[str, int] = {}
    for (_pid, luid), nbytes in per_pid_luid.items():
        per_luid[luid] = per_luid.get(luid, 0) + int(nbytes)
    assigned = match_luids_to_gpus(per_luid, gpus if gpus is not None else _gpu_devices_for_luid_match())
    mapping: dict[tuple[int, int], int] = {}
    for (pid, luid), nbytes in per_pid_luid.items():
        gpu_index = assigned.get(luid)
        if gpu_index is None:
            continue
        key = (int(pid), int(gpu_index))
        mapping[key] = mapping.get(key, 0) + int(nbytes)
    return mapping


def query_windows_process_gpu_bytes() -> dict[tuple[int, int], int]:
    """Return {(pid, nvidia gpu index): dedicated_bytes}."""
    if sys.platform != 'win32':
        return {}
    now = time.time()
    cached_at = float(_CACHE.get('at') or 0.0)
    cached_map = _CACHE.get('map')
    if isinstance(cached_map, dict) and (now - cached_at) < _CACHE_TTL_SECONDS:
        return dict(cached_map)
    with _CACHE_LOCK:
        now = time.time()
        cached_at = float(_CACHE.get('at') or 0.0)
        cached_map = _CACHE.get('map')
        if isinstance(cached_map, dict) and (now - cached_at) < _CACHE_TTL_SECONDS:
            return dict(cached_map)
        per_pid_luid = _fetch_windows_process_gpu_bytes()
        mapping = _mapping_from_luid_rows(per_pid_luid)
        _CACHE['at'] = time.time()
        _CACHE['map'] = dict(mapping)
        return mapping


def process_vram_by_gpu(pid: int, mapping: dict[tuple[int, int], int] | None = None) -> dict[int, float]:
    """Gigabytes of one process on each GPU. Empty when this PID has none."""
    if pid <= 0:
        return {}
    data = mapping if mapping is not None else query_windows_process_gpu_bytes()
    found: dict[int, float] = {}
    for (proc_id, gpu_index), nbytes in data.items():
        if int(proc_id) != int(pid) or int(nbytes) <= 0:
            continue
        gb = round(int(nbytes) / (1024 ** 3), 3)
        if gb <= 0:
            continue
        found[int(gpu_index)] = round(found.get(int(gpu_index), 0.0) + gb, 3)
    return found


def lookup_windows_process_vram_gb(
    pid: int,
    gpu_index: int,
    mapping: dict[tuple[int, int], int] | None = None,
) -> float | None:
    """VRAM in GB for this process on this GPU only.

    Memory on another board is not borrowed. That is what made the Titan's
    usage show up as a 4090 number.
    """
    if pid <= 0:
        return None
    found = process_vram_by_gpu(pid, mapping)
    gb = found.get(int(gpu_index))
    return gb if gb and gb > 0 else None


def apply_windows_process_vram(processes: list[dict[str, Any]]) -> bool:
    """Fill missing per-process VRAM on Windows. Returns True when any row was enriched."""
    if sys.platform != 'win32' or not processes:
        return False
    mapping = query_windows_process_gpu_bytes()
    if not mapping:
        return False
    enriched = False
    for proc in processes:
        if not isinstance(proc, dict):
            continue
        try:
            pid = int(proc.get('pid') or 0)
            gpu_index = int(proc.get('gpu_index') or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        if proc.get('vram_gb') is not None and float(proc.get('vram_gb') or 0) > 0:
            continue
        gb = lookup_windows_process_vram_gb(pid, gpu_index, mapping)
        if gb is None:
            continue
        proc['vram_gb'] = gb
        proc['vram_mb'] = round(gb * 1024, 1)
        proc['vram_source'] = 'windows'
        enriched = True
    return enriched
