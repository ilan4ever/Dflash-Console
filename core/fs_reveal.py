"""Reveal a file or folder in the system file manager."""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


def _explorer_pids() -> set[int]:
    out = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq explorer.exe', '/FO', 'CSV', '/NH'],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    pids: set[int] = set()
    for line in out.stdout.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[0].lower() == 'explorer.exe':
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _explorer_window_handles(user32: Any, explorer_pids: set[int]) -> list[int]:
    hwnds: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in explorer_pids:
            hwnds.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return hwnds


def _force_foreground(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)

    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return

    fg_thread = wintypes.DWORD()
    target_thread = wintypes.DWORD()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(fg_thread))
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_thread))
    current_thread = kernel32.GetCurrentThreadId()

    user32.AttachThreadInput(current_thread, target_thread, True)
    user32.AttachThreadInput(fg_thread, target_thread, True)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(fg_thread, target_thread, False)
    user32.AttachThreadInput(current_thread, target_thread, False)


def _reveal_windows(target: Path) -> None:
    path_str = str(target)
    args = f'/select,"{path_str}"' if target.is_file() else f'"{path_str}"'

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    before_pids = _explorer_pids()
    before_hwnds = set(_explorer_window_handles(user32, before_pids))
    rc = shell32.ShellExecuteW(None, 'open', 'explorer.exe', args, None, 1)
    if rc <= 32:
        raise OSError(f'ShellExecute failed ({rc})')

    for _ in range(20):
        time.sleep(0.05)
        after_pids = _explorer_pids()
        after_hwnds = _explorer_window_handles(user32, after_pids)
        new_hwnds = [h for h in after_hwnds if h not in before_hwnds]
        if new_hwnds:
            _force_foreground(new_hwnds[-1])
            return

    if after_hwnds := _explorer_window_handles(user32, _explorer_pids()):
        _force_foreground(after_hwnds[-1])


def reveal_path(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if not target.exists():
        return {'success': False, 'error': 'path not found'}
    try:
        if sys.platform == 'win32':
            _reveal_windows(target)
        elif sys.platform == 'darwin':
            args = ['open', '-R', str(target)] if target.is_file() else ['open', str(target)]
            subprocess.Popen(args)
        else:
            folder = str(target if target.is_dir() else target.parent)
            subprocess.Popen(['xdg-open', folder])
    except OSError as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'path': str(target)}
