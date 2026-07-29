"""Live CPU, RAM, and GPU utilization for the sysbar."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

from core.gpu_devices import format_gpu_display_name

_cpu_process_load_last: dict[str, float] | None = None
_cpu_load_smoothed: float | None = None
_cpu_counter_cache: tuple[float, int | None] | None = None
_CPU_COUNTER_TTL_SECONDS = 15.0
_CPU_EMA_ALPHA = 0.35


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


def _run_powershell_json(script: str, *, timeout: float = 5) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_cpu_percent(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _apply_cpu_smoothing(cpu_load: int) -> int:
    global _cpu_load_smoothed
    if _cpu_load_smoothed is not None:
        _cpu_load_smoothed = round(
            (_CPU_EMA_ALPHA * cpu_load) + ((1 - _CPU_EMA_ALPHA) * _cpu_load_smoothed)
        )
    else:
        _cpu_load_smoothed = float(cpu_load)
    return int(_cpu_load_smoothed)


def _cpu_from_process_delta(total_cpu_seconds: float) -> int | None:
    """Match Speak-OneVoice: delta process CPU seconds / elapsed / core count."""
    global _cpu_process_load_last
    now_ms = time.time() * 1000
    cpu_load: int | None = None
    prev = _cpu_process_load_last
    if prev is not None:
        delta_cpu_seconds = total_cpu_seconds - prev['total_cpu_seconds']
        elapsed_seconds = max(0.001, (now_ms - prev['sample_time_ms']) / 1000)
        core_count = max(1, os.cpu_count() or 1)
        computed = (delta_cpu_seconds / (elapsed_seconds * core_count)) * 100
        if computed == computed and abs(computed) != float('inf'):
            cpu_load = _normalize_cpu_percent(computed)
    _cpu_process_load_last = {
        'total_cpu_seconds': total_cpu_seconds,
        'sample_time_ms': now_ms,
    }
    if cpu_load is None:
        return None
    return _apply_cpu_smoothing(cpu_load)


def _cpu_from_perf_counter() -> int | None:
    """Fallback: Windows perf counters, cached briefly like Speak-OneVoice."""
    global _cpu_counter_cache
    now = time.time()
    if _cpu_counter_cache and (now - _cpu_counter_cache[0]) < _CPU_COUNTER_TTL_SECONDS:
        cached = _cpu_counter_cache[1]
        return _apply_cpu_smoothing(cached) if cached is not None else None

    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$v = $null;"
        "try { $u = (Get-Counter '\\Processor Information(_Total)\\% Processor Utility' -MaxSamples 2 -SampleInterval 1).CounterSamples; "
        "if ($u -and $u.Count -gt 0) { $v = [double]$u[-1].CookedValue } } catch {} ;"
        "if ($null -eq $v) { try { $t = (Get-Counter '\\Processor(_Total)\\% Processor Time' -MaxSamples 2 -SampleInterval 1).CounterSamples; "
        "if ($t -and $t.Count -gt 0) { $v = [double]$t[-1].CookedValue } } catch {} } ;"
        "if ($null -ne $v) { [int][math]::Max(0,[math]::Min(100,[math]::Round($v))) } else { '' }"
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        _cpu_counter_cache = (now, None)
        return None
    raw = result.stdout.strip()
    counter_value: int | None = None
    if result.returncode == 0 and raw:
        try:
            counter_value = _normalize_cpu_percent(float(raw))
        except (TypeError, ValueError):
            counter_value = None
    _cpu_counter_cache = (now, counter_value)
    if counter_value is None:
        return None
    return _apply_cpu_smoothing(counter_value)


def _resolve_cpu_percent(process_cpu_seconds: float | None) -> int | None:
    cpu_percent: int | None = None
    if process_cpu_seconds is not None:
        cpu_percent = _cpu_from_process_delta(process_cpu_seconds)
    if cpu_percent is None:
        cpu_percent = _cpu_from_perf_counter()
    return cpu_percent


def _query_gpus_live() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                'nvidia-smi',
                '--query-gpu=index,name,utilization.gpu,memory.used,memory.total',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []

    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 5:
            continue
        try:
            index = int(parts[0])
            load_percent = max(0, min(100, int(float(parts[2] or 0))))
            vram_used_mib = float(parts[3] or 0)
            vram_total_mib = float(parts[4] or 0)
        except (TypeError, ValueError):
            continue
        name = parts[1]
        vram_total_gb = round(vram_total_mib / 1024, 1) if vram_total_mib > 0 else None
        vram_used_gb = round(vram_used_mib / 1024, 1) if vram_used_mib >= 0 else None
        vram_percent = (
            max(0, min(100, round(vram_used_mib / vram_total_mib * 100)))
            if vram_total_mib > 0
            else 0
        )
        rows.append({
            'index': index,
            'name': name,
            'display_name': format_gpu_display_name(name, index).replace(' ', ''),
            'load_percent': load_percent,
            'vram_percent': vram_percent,
            'vram_used_gb': vram_used_gb,
            'vram_total_gb': vram_total_gb,
        })
    rows.sort(key=lambda item: item['index'])
    return rows


def _query_cpu_ram_windows() -> tuple[int | None, dict[str, float | int | None]]:
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$sum = (Get-Process | Measure-Object -Property CPU -Sum).Sum;"
        "$os = Get-CimInstance Win32_OperatingSystem;"
        "$total = [double]$os.TotalVisibleMemorySize;"
        "$free = [double]$os.FreePhysicalMemory;"
        "$used = $total - $free;"
        "$pct = if ($total -gt 0) { [math]::Round($used / $total * 100) } else { 0 };"
        "@{ process_cpu = $(if ($null -ne $sum) { [double]$sum } else { $null }); "
        "ram_percent = [int]$pct; ram_used_gb = [math]::Round($used / 1MB, 1); "
        "ram_total_gb = [math]::Round($total / 1MB, 1) } | ConvertTo-Json -Compress"
    )
    payload = _run_powershell_json(script, timeout=5)
    if not payload:
        cpu_percent = _resolve_cpu_percent(None)
        return cpu_percent, {}

    process_cpu = payload.get('process_cpu')
    process_cpu_seconds = float(process_cpu) if isinstance(process_cpu, (int, float)) else None
    cpu_percent = _resolve_cpu_percent(process_cpu_seconds)
    return cpu_percent, {
        'ram_percent': int(payload.get('ram_percent') or 0),
        'ram_used_gb': float(payload.get('ram_used_gb') or 0),
        'ram_total_gb': float(payload.get('ram_total_gb') or 0),
    }


def get_system_stats_payload() -> dict[str, Any]:
    gpus = _query_gpus_live()
    cpu_percent: int | None = None
    ram_percent = 0
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None

    if sys.platform == 'win32':
        cpu_percent, ram_info = _query_cpu_ram_windows()
        ram_percent = int(ram_info.get('ram_percent') or 0)
        ram_used_gb = ram_info.get('ram_used_gb')  # type: ignore[assignment]
        ram_total_gb = ram_info.get('ram_total_gb')  # type: ignore[assignment]

    return {
        'success': True,
        'cpu_percent': cpu_percent,
        'ram_percent': ram_percent,
        'ram_used_gb': ram_used_gb,
        'ram_total_gb': ram_total_gb,
        'gpus': gpus,
    }


def get_cpu_info_payload() -> dict[str, Any]:
    if sys.platform != 'win32':
        return {'name': 'Unknown CPU', 'arch': 'unknown', 'features': []}
    script = (
        '$p = Get-CimInstance Win32_Processor | Select-Object -First 1 Name; '
        '@{ name = [string]$p.Name } | ConvertTo-Json -Compress'
    )
    payload = _run_powershell_json(script, timeout=4)
    if not payload:
        return {'name': 'Unknown CPU', 'arch': 'x86_64', 'features': ['AVX2']}
    name = str(payload.get('name') or 'Unknown CPU').strip()
    return {'name': name, 'arch': 'x86_64', 'features': ['AVX', 'AVX2']}
