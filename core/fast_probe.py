"""In-process Windows probes that replace short-lived nvidia-smi / PowerShell children.

Added by the 2026-11-04 polling fix. Every function returns ``None`` when the
fast path is unavailable so callers can fall back to the original subprocess.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from typing import Any

_IS_WIN = sys.platform == 'win32'

# --------------------------------------------------------------------------- NVML
_NVML_LOCK = threading.Lock()
_NVML: Any = None
_NVML_FAILED_AT = 0.0
_NVML_RETRY_SECONDS = 60.0
_NA_ULL = 0xFFFFFFFFFFFFFFFF


class _NvmlMemory(ctypes.Structure):
    _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong)]


class _NvmlMemoryV2(ctypes.Structure):
    _fields_ = [
        ('version', ctypes.c_uint), ('total', ctypes.c_ulonglong), ('reserved', ctypes.c_ulonglong),
        ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong),
    ]


def _nvml_memory(lib: Any, h: Any) -> Any:
    # v2 excludes driver-reserved memory, matching what nvidia-smi prints.
    fn = getattr(lib, 'nvmlDeviceGetMemoryInfo_v2', None)
    if fn is not None:
        mem2 = _NvmlMemoryV2()
        mem2.version = ctypes.sizeof(_NvmlMemoryV2) | (2 << 24)
        if fn(h, ctypes.byref(mem2)) == 0:
            return mem2
    mem = _NvmlMemory()
    if lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(mem)) != 0:
        return None
    return mem


class _NvmlUtil(ctypes.Structure):
    _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]


class _NvmlProcV3(ctypes.Structure):
    _fields_ = [
        ('pid', ctypes.c_uint),
        ('usedGpuMemory', ctypes.c_ulonglong),
        ('gpuInstanceId', ctypes.c_uint),
        ('computeInstanceId', ctypes.c_uint),
    ]


def _nvml() -> Any:
    global _NVML, _NVML_FAILED_AT
    if _NVML is not None:
        return _NVML
    if not _IS_WIN:
        return None
    if _NVML_FAILED_AT and time.time() - _NVML_FAILED_AT < _NVML_RETRY_SECONDS:
        return None
    candidates = [
        os.path.join(os.environ.get('SystemRoot') or r'C:\Windows', 'System32', 'nvml.dll'),
        os.path.join(os.environ.get('ProgramFiles') or r'C:\Program Files', 'NVIDIA Corporation', 'NVSMI', 'nvml.dll'),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            lib = ctypes.CDLL(path)
            if lib.nvmlInit_v2() != 0:
                continue
            _NVML = lib
            return lib
        except Exception:
            continue
    _NVML_FAILED_AT = time.time()
    return None


def _nvml_handles(lib: Any) -> list[Any]:
    count = ctypes.c_uint(0)
    if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
        return []
    out = []
    for i in range(count.value):
        h = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(i), ctypes.byref(h)) != 0:
            return []
        out.append(h)
    return out


def _nvml_str(fn: Any, h: Any) -> str | None:
    buf = ctypes.create_string_buffer(96)
    if fn(h, buf, ctypes.c_uint(96)) != 0:
        return None
    return buf.value.decode('utf-8', 'replace')


_GPU_FIELDS = {
    'index', 'uuid', 'name', 'memory.total', 'memory.used', 'memory.free',
    'utilization.gpu', 'utilization.memory',
}
_APP_FIELDS = {'gpu_uuid', 'pid', 'process_name', 'used_gpu_memory', 'used_memory'}


def _parse_args(args: list[str]) -> tuple[str, list[str], bool] | None:
    kind = ''
    fields: list[str] = []
    fmt = ''
    for arg in args:
        if arg.startswith('--query-gpu='):
            kind, fields = 'gpu', arg.split('=', 1)[1].split(',')
        elif arg.startswith('--query-compute-apps='):
            kind, fields = 'apps', arg.split('=', 1)[1].split(',')
        elif arg.startswith('--format='):
            fmt = arg.split('=', 1)[1]
        else:
            return None
    opts = set(fmt.split(','))
    if not kind or 'csv' not in opts or 'noheader' not in opts or opts - {'csv', 'noheader', 'nounits'}:
        return None
    fields = [f.strip() for f in fields if f.strip()]
    allowed = _GPU_FIELDS if kind == 'gpu' else _APP_FIELDS
    if not fields or any(f not in allowed for f in fields):
        return None
    return kind, fields, 'nounits' in opts


def nvidia_smi_emulated(args: list[str]) -> str | None:
    """Answer a supported ``nvidia-smi --query-*`` call from NVML in-process.

    Returns the same CSV text nvidia-smi would print, or ``None`` when the
    query is not supported (the caller then runs the real nvidia-smi).
    """
    parsed = _parse_args(list(args))
    if parsed is None:
        return None
    kind, fields, nounits = parsed
    with _NVML_LOCK:
        lib = _nvml()
        if lib is None:
            return None
        try:
            handles = _nvml_handles(lib)
            if not handles:
                return None
            lines: list[str] = []
            if kind == 'gpu':
                for idx, h in enumerate(handles):
                    mem = None
                    util = _NvmlUtil()
                    need_mem = any(f.startswith('memory.') for f in fields)
                    need_util = any(f.startswith('utilization.') for f in fields)
                    if need_mem:
                        mem = _nvml_memory(lib, h)
                        if mem is None:
                            return None
                    if need_util and lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(util)) != 0:
                        return None
                    vals = []
                    for f in fields:
                        if f == 'index':
                            vals.append(str(idx))
                        elif f == 'uuid':
                            v = _nvml_str(lib.nvmlDeviceGetUUID, h)
                            if v is None:
                                return None
                            vals.append(v)
                        elif f == 'name':
                            v = _nvml_str(lib.nvmlDeviceGetName, h)
                            if v is None:
                                return None
                            vals.append(v)
                        elif f.startswith('memory.'):
                            mib = getattr(mem, f.split('.', 1)[1]) // (1024 * 1024)
                            vals.append(str(mib) if nounits else f'{mib} MiB')
                        else:
                            pct = util.gpu if f == 'utilization.gpu' else util.memory
                            vals.append(str(pct) if nounits else f'{pct} %')
                    lines.append(', '.join(vals))
                return '\n'.join(lines)
            # compute apps
            for h in handles:
                uuid = _nvml_str(lib.nvmlDeviceGetUUID, h) or ''
                procs: list[tuple[int, int]] = []
                for fn_name in ('nvmlDeviceGetComputeRunningProcesses_v3', 'nvmlDeviceGetGraphicsRunningProcesses_v3'):
                    fn = getattr(lib, fn_name, None)
                    if fn is None:
                        return None
                    size = 256
                    while True:
                        count = ctypes.c_uint(size)
                        arr = (_NvmlProcV3 * size)()
                        rc = fn(h, ctypes.byref(count), arr)
                        if rc == 7 and size < 8192:  # NVML_ERROR_INSUFFICIENT_SIZE
                            size *= 4
                            continue
                        if rc != 0:
                            return None
                        break
                    for i in range(count.value):
                        procs.append((int(arr[i].pid), int(arr[i].usedGpuMemory)))
                seen: set[int] = set()
                for pid, used in procs:
                    if pid in seen:
                        continue
                    seen.add(pid)
                    vals = []
                    for f in fields:
                        if f == 'gpu_uuid':
                            vals.append(uuid)
                        elif f == 'pid':
                            vals.append(str(pid))
                        elif f == 'process_name':
                            vals.append(process_image_path(pid) or '[Insufficient Permissions]')
                        else:
                            if used in (_NA_ULL, 0):
                                vals.append('[N/A]')
                            else:
                                mib = used // (1024 * 1024)
                                vals.append(str(mib) if nounits else f'{mib} MiB')
                    lines.append(', '.join(vals))
            return '\n'.join(lines)
        except Exception:
            return None


# --------------------------------------------------------------------- kernel32
if _IS_WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _ntdll = ctypes.WinDLL('ntdll')
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    _ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]

    class _PE32(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
            ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
            ('szExeFile', ctypes.c_wchar * 260),
        ]

    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [('Length', ctypes.c_ushort), ('MaximumLength', ctypes.c_ushort), ('Buffer', ctypes.c_void_p)]

    class _FILETIME(ctypes.Structure):
        _fields_ = [('lo', wintypes.DWORD), ('hi', wintypes.DWORD)]

    class _MEMSTAT(ctypes.Structure):
        _fields_ = [
            ('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
            ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
            ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
            ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
        ]

_PQLI = 0x1000


def process_image_path(pid: int) -> str | None:
    if not _IS_WIN or pid <= 0:
        return None
    h = _k32.OpenProcess(_PQLI, False, int(pid))
    if not h:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if not _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        _k32.CloseHandle(h)


def _process_command_line(h: Any) -> str:
    ret = ctypes.c_ulong(0)
    _ntdll.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(ret))
    if ret.value <= 0 or ret.value > 1 << 20:
        return ''
    buf = ctypes.create_string_buffer(ret.value)
    if _ntdll.NtQueryInformationProcess(h, 60, buf, ret.value, ctypes.byref(ret)) != 0:
        return ''
    us = _UNICODE_STRING.from_buffer(buf)
    if not us.Buffer or not us.Length:
        return ''
    return ctypes.wstring_at(us.Buffer, us.Length // 2)


def win32_process_rows() -> list[dict[str, Any]] | None:
    """Rows shaped like ``Get-CimInstance Win32_Process`` (ProcessId, ParentProcessId,
    Name, CommandLine, ExecutablePath) without launching PowerShell."""
    if not _IS_WIN:
        return None
    try:
        snap = _k32.CreateToolhelp32Snapshot(0x2, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        entries: list[tuple[int, int, str]] = []
        try:
            pe = _PE32()
            pe.dwSize = ctypes.sizeof(_PE32)
            ok = _k32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                entries.append((int(pe.th32ProcessID), int(pe.th32ParentProcessID), pe.szExeFile))
                ok = _k32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            _k32.CloseHandle(snap)
        rows: list[dict[str, Any]] = []
        for pid, ppid, name in entries:
            cmd = ''
            exe = ''
            if pid > 4:
                h = _k32.OpenProcess(_PQLI, False, pid)
                if h:
                    try:
                        size = wintypes.DWORD(1024)
                        buf = ctypes.create_unicode_buffer(1024)
                        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                            exe = buf.value
                        cmd = _process_command_line(h)
                    except Exception:
                        pass
                    finally:
                        _k32.CloseHandle(h)
            rows.append({
                'ProcessId': pid, 'ParentProcessId': ppid, 'Name': name,
                'CommandLine': cmd or None, 'ExecutablePath': exe or None,
            })
        return rows if rows else None
    except Exception:
        return None


# ------------------------------------------------------------------ CPU / RAM
_CPU_LOCK = threading.Lock()
_CPU_LAST: tuple[int, int] | None = None


def _ft(f: Any) -> int:
    return (int(f.hi) << 32) | int(f.lo)


def _system_times() -> tuple[int, int] | None:
    idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None
    return _ft(idle), _ft(kern) + _ft(user)


def cpu_percent() -> int | None:
    """System CPU % since the previous call (GetSystemTimes)."""
    global _CPU_LAST
    if not _IS_WIN:
        return None
    with _CPU_LOCK:
        cur = _system_times()
        if cur is None:
            return None
        prev = _CPU_LAST
        if prev is None:
            time.sleep(0.15)
            prev, cur = cur, _system_times()
            if cur is None:
                return None
        _CPU_LAST = cur
        d_idle = cur[0] - prev[0]
        d_total = cur[1] - prev[1]
        if d_total <= 0:
            return None
        return max(0, min(100, int(round((1 - d_idle / d_total) * 100))))


def ram_info() -> dict[str, Any] | None:
    if not _IS_WIN:
        return None
    m = _MEMSTAT()
    m.dwLength = ctypes.sizeof(_MEMSTAT)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    total = float(m.ullTotalPhys)
    used = total - float(m.ullAvailPhys)
    gib = 1024.0 ** 3
    return {
        'ram_percent': int(round(used / total * 100)) if total > 0 else 0,
        'ram_used_gb': round(used / gib, 1),
        'ram_total_gb': round(total / gib, 1),
    }


_CPU_NAME: str | None = None


def cpu_name() -> str | None:
    global _CPU_NAME
    if _CPU_NAME is not None:
        return _CPU_NAME or None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            _CPU_NAME = str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).strip()
    except Exception:
        _CPU_NAME = ''
    return _CPU_NAME or None


# ------------------------------------------------------------- TCP listeners
def tcp_listen_pids_v4() -> dict[int, int] | None:
    """listen port -> pid for IPv4 TCP listeners (same view as ``netstat -ano -p tcp``)."""
    if not _IS_WIN:
        return None
    try:
        iphlp = ctypes.WinDLL('iphlpapi')
        size = wintypes.DWORD(0)
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
        for _ in range(3):
            buf = ctypes.create_string_buffer(size.value + 4096)
            size = wintypes.DWORD(len(buf))
            rc = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 3, 0)
            if rc == 0:
                break
            if rc != 122:
                return None
        else:
            return None
        n = ctypes.c_uint32.from_buffer(buf, 0).value
        rows = (ctypes.c_uint32 * (6 * n)).from_buffer(buf, 4)
        found: dict[int, int] = {}
        for i in range(n):
            state, _laddr, lport, _raddr, _rport, pid = rows[i * 6:(i + 1) * 6]
            if state != 2:  # MIB_TCP_STATE_LISTEN
                continue
            port = ((lport & 0xFF) << 8) | ((lport >> 8) & 0xFF)
            if port > 0 and pid > 0:
                found[port] = int(pid)
        return found
    except Exception:
        return None


# --------------------------------------------------- GPU process memory (PDH)
_PDH_LOCK = threading.Lock()
_PDH: dict[str, Any] = {}
_PDH_ROW_RE = re.compile(r'pid_(\d+)_luid_((?:0x)?[0-9a-f]+)_((?:0x)?[0-9a-f]+)_phys_(\d+)', re.I)


def _pdh_array(pdh: Any, counter: Any) -> dict[str, int] | None:
    size = wintypes.DWORD(0)
    count = wintypes.DWORD(0)
    PDH_FMT_LARGE = 0x400
    rc = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None) & 0xFFFFFFFF
    if rc not in (0, 0x800007D2):  # PDH_MORE_DATA
        return None
    if size.value == 0:
        return {}
    buf = ctypes.create_string_buffer(size.value)
    rc = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf)
    if rc != 0:
        return None
    out: dict[str, int] = {}
    item = 24  # LPWSTR + {DWORD CStatus; pad; LONGLONG}
    base = ctypes.addressof(buf)
    for i in range(count.value):
        name_ptr = ctypes.c_void_p.from_address(base + i * item).value
        status = ctypes.c_uint32.from_address(base + i * item + 8).value
        value = ctypes.c_longlong.from_address(base + i * item + 16).value
        if not name_ptr or status not in (0, 1):
            continue
        out[ctypes.wstring_at(name_ptr)] = int(value)
    return out


def gpu_process_memory_rows() -> list[dict[str, Any]] | None:
    """Same rows as the Win32_PerfFormattedData_GPUProcessMemory PowerShell script:
    [{pid, luid, bytes}] with DedicatedUsage (else TotalCommitted), max per pid+luid."""
    if not _IS_WIN:
        return None
    with _PDH_LOCK:
        try:
            if not _PDH:
                pdh = ctypes.WinDLL('pdh')
                q = ctypes.c_void_p()
                if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
                    return None
                ded = ctypes.c_void_p()
                com = ctypes.c_void_p()
                if pdh.PdhAddEnglishCounterW(q, '\\GPU Process Memory(*)\\Dedicated Usage', None, ctypes.byref(ded)) != 0:
                    return None
                if pdh.PdhAddEnglishCounterW(q, '\\GPU Process Memory(*)\\Total Committed', None, ctypes.byref(com)) != 0:
                    return None
                _PDH.update({'pdh': pdh, 'q': q, 'ded': ded, 'com': com})
            pdh = _PDH['pdh']
            if pdh.PdhCollectQueryData(_PDH['q']) != 0:
                return None
            ded = _pdh_array(pdh, _PDH['ded'])
            com = _pdh_array(pdh, _PDH['com'])
            if ded is None or com is None:
                return None
        except Exception:
            return None
    agg: dict[tuple[int, str], int] = {}
    for name in set(ded) | set(com):
        m = _PDH_ROW_RE.search(name)
        if not m:
            continue
        nbytes = int(ded.get(name) or 0)
        if nbytes <= 0:
            nbytes = int(com.get(name) or 0)
        if nbytes <= 0:
            continue
        key = (int(m.group(1)), (m.group(2) + '_' + m.group(3)).lower())
        if nbytes > agg.get(key, 0):
            agg[key] = nbytes
    return [{'pid': k[0], 'luid': k[1], 'bytes': v} for k, v in agg.items()]
