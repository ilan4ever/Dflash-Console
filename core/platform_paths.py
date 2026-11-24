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
