"""The Windows startup script must not reuse another Python's package stamp."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'server.ps1').read_text(encoding='utf-8')


def test_startup_creates_its_own_python_environment():
    assert ".venv\\Scripts\\python.exe" in SCRIPT
    assert 'Creating a local Python environment for DFlash Console...' in SCRIPT


def test_startup_reinstalls_when_the_python_or_packages_change():
    assert '$requirementsHash|$pythonPath' in SCRIPT
    assert 'import fastapi, uvicorn' in SCRIPT
    assert '-not $pythonReady' in SCRIPT


def test_startup_ignores_pip_notes_on_powershell_7():
    assert '$PSNativeCommandUseErrorActionPreference = $false' in SCRIPT
