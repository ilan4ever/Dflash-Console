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


def _requirement_name(line: str) -> str:
    import re

    return re.split(r'[\s<>=!~;\[]', line.strip(), maxsplit=1)[0].lower().replace('_', '-')


def test_wheel_dependencies_cover_requirements_txt():
    """pip users get every runtime package that a checkout install gets.

    The gateway imports httpx. huggingface_hub 2.x no longer pulls it in, so a
    fresh `pip install dflash-console` had no OpenAI gateway.
    """
    import pytest

    tomllib = pytest.importorskip('tomllib')
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    declared = {_requirement_name(row) for row in project['dependencies']}
    needed = {
        _requirement_name(line)
        for line in (root / 'requirements.txt').read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    }
    assert needed <= declared, f'missing from pyproject dependencies: {sorted(needed - declared)}'
