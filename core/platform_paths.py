"""Small cross-platform helpers for runtime bundles (Windows + Linux)."""

from __future__ import annotations

import sys
from pathlib import Path

IS_WINDOWS = sys.platform == 'win32'


def venv_python(venv: Path) -> Path:
    """Interpreter inside a venv: ``Scripts/python.exe`` on Windows, ``bin/python`` elsewhere."""
    if IS_WINDOWS:
        return venv / 'Scripts' / 'python.exe'
    return venv / 'bin' / 'python'


def venv_cuda_lib_env(venv: Path) -> dict[str, str] | None:
    """Linux: environment with the venv's pip CUDA libraries (``nvidia/*/lib``) on LD_LIBRARY_PATH.

    Returns ``None`` on Windows or when the venv has no such libraries.
    """
    if IS_WINDOWS:
        return None
    import os

    lib_dirs = sorted(str(p) for p in venv.glob('lib/python3*/site-packages/nvidia/*/lib') if p.is_dir())
    if not lib_dirs:
        return None
    env = os.environ.copy()
    existing = env.get('LD_LIBRARY_PATH', '')
    env['LD_LIBRARY_PATH'] = os.pathsep.join(lib_dirs + ([existing] if existing else []))
    return env


def exe_name(base: str) -> str:
    """Native binary file name: ``base.exe`` on Windows, ``base`` elsewhere."""
    return f'{base}.exe' if IS_WINDOWS else base


def install_script_command(script_dir: Path, stem: str, ps_args: list[str], sh_args: list[str]) -> list[str]:
    """Command line for ``scripts/<stem>.ps1`` on Windows or ``scripts/<stem>.sh`` elsewhere."""
    if IS_WINDOWS:
        return [
            'powershell',
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            str(script_dir / f'{stem}.ps1'),
            *ps_args,
        ]
    return ['bash', str(script_dir / f'{stem}.sh'), *sh_args]


def install_script_path(script_dir: Path, stem: str) -> Path:
    return script_dir / (f'{stem}.ps1' if IS_WINDOWS else f'{stem}.sh')
