"""Live CPU, RAM, and GPU utilization for the sysbar."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from core.gpu_devices import format_gpu_display_name


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


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
        '$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average; '
        '$os = Get-CimInstance Win32_OperatingSystem; '
        '$total = [double]$os.TotalVisibleMemorySize; '
        '$free = [double]$os.FreePhysicalMemory; '
        '$used = $total - $free; '
        '$pct = if ($total -gt 0) { [math]::Round($used / $total * 100) } else { 0 }; '
        '@{ cpu = [int][math]::Round($cpu); ram_percent = [int]$pct; '
        'ram_used_gb = [math]::Round($used / 1MB, 1); ram_total_gb = [math]::Round($total / 1MB, 1) } | ConvertTo-Json -Compress'
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return None, {}
    if result.returncode != 0 or not result.stdout.strip():
        return None, {}
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None, {}
    cpu = payload.get('cpu')
    cpu_percent = int(cpu) if isinstance(cpu, (int, float)) else None
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
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return {'name': 'Unknown CPU', 'arch': 'x86_64', 'features': ['AVX2']}
    if result.returncode != 0 or not result.stdout.strip():
        return {'name': 'Unknown CPU', 'arch': 'x86_64', 'features': ['AVX2']}
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {'name': 'Unknown CPU', 'arch': 'x86_64', 'features': ['AVX2']}
    name = str(payload.get('name') or 'Unknown CPU').strip()
    return {'name': name, 'arch': 'x86_64', 'features': ['AVX', 'AVX2']}
