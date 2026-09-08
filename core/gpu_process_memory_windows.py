"""Per-process GPU memory on Windows via WDDM performance counters."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Any

_CACHE: dict[str, Any] = {'at': 0.0, 'map': {}}
_CACHE_TTL_SECONDS = 4.0
_MAX_PROCESS_BYTES = 64 * (1024 ** 3)

_ROW_RE = re.compile(
    r'pid_(\d+)_luid_[^_]+_[^_]+_phys_(\d+)',
    re.I,
)


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


def _fetch_windows_process_gpu_bytes() -> dict[tuple[int, int], int]:
    script = r"""
$rows = Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -ErrorAction SilentlyContinue
$agg = @{}
foreach ($r in $rows) {
  if ($r.Name -match 'pid_(\d+)_luid_[^_]+_[^_]+_phys_(\d+)') {
    $procId = [int]$matches[1]
    $phys = [int]$matches[2]
    $key = "$procId|$phys"
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
  $out += @{ pid = [int]$parts[0]; gpu_index = [int]$parts[1]; bytes = [long]$kv.Value }
}
$out | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {}
    rows = payload if isinstance(payload, list) else [payload]
    mapping: dict[tuple[int, int], int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get('pid') or 0)
            gpu_index = int(row.get('gpu_index') or 0)
            bytes_val = int(row.get('bytes') or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or bytes_val <= 0 or bytes_val > _MAX_PROCESS_BYTES:
            continue
        key = (pid, gpu_index)
        prior = mapping.get(key, 0)
        if bytes_val > prior:
            mapping[key] = bytes_val
    return mapping


def query_windows_process_gpu_bytes() -> dict[tuple[int, int], int]:
    """Return {(pid, gpu_index): dedicated_bytes} from Windows GPU counters."""
    if sys.platform != 'win32':
        return {}
    now = time.time()
    cached_at = float(_CACHE.get('at') or 0.0)
    cached_map = _CACHE.get('map')
    if isinstance(cached_map, dict) and (now - cached_at) < _CACHE_TTL_SECONDS:
        return dict(cached_map)
    mapping = _fetch_windows_process_gpu_bytes()
    _CACHE['at'] = now
    _CACHE['map'] = dict(mapping)
    return mapping


def lookup_windows_process_vram_gb(
    pid: int,
    gpu_index: int,
    mapping: dict[tuple[int, int], int] | None = None,
) -> float | None:
    """Return per-process VRAM in GB, matching GPU when possible else best PID match."""
    if pid <= 0:
        return None
    data = mapping if mapping is not None else query_windows_process_gpu_bytes()
    if not data:
        return None
    bytes_val = int(data.get((pid, gpu_index)) or 0)
    if bytes_val <= 0:
        candidates = [int(val) for (proc_id, _gpu), val in data.items() if proc_id == pid and int(val) > 0]
        if not candidates:
            return None
        bytes_val = max(candidates)
    gb = round(bytes_val / (1024 ** 3), 3)
    return gb if gb > 0 else None


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
