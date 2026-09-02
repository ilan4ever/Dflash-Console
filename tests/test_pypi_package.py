"""PyPI wheel must include the CLI and core packages."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_imports_core_version(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / 'dist'
    built = subprocess.run(
        [sys.executable, '-m', 'build', '--outdir', str(out)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr or built.stdout
    wheels = list(out.glob('dflash_console-*.whl'))
    assert wheels, 'expected a built wheel'
    wheel = wheels[0]
    names = zipfile.ZipFile(wheel).namelist()
    assert 'core/version.py' in names
    assert 'dflash_cli/cli.py' in names
