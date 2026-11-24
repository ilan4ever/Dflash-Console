"""Linux behaviour of the platform helpers and runtime adapters (runs on any OS)."""

from __future__ import annotations

import json
from pathlib import Path

import core.huggingface as hf_mod
import core.platform_paths as pp


def test_venv_python_per_platform(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pp, 'IS_WINDOWS', True)
    assert pp.venv_python(tmp_path) == tmp_path / 'Scripts' / 'python.exe'
    monkeypatch.setattr(pp, 'IS_WINDOWS', False)
    assert pp.venv_python(tmp_path) == tmp_path / 'bin' / 'python'


def test_install_script_command_per_platform(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pp, 'IS_WINDOWS', True)
    win = pp.install_script_command(tmp_path, 'install-x', ['-A', '1'], ['--a', '1'])
    assert win[0] == 'powershell' and win[-3:] == [str(tmp_path / 'install-x.ps1'), '-A', '1']
    monkeypatch.setattr(pp, 'IS_WINDOWS', False)
    assert pp.install_script_command(tmp_path, 'install-x', ['-A', '1'], ['--a', '1']) == [
        'bash', str(tmp_path / 'install-x.sh'), '--a', '1',
    ]


def test_hf_requests_session_is_a_session_not_the_function(monkeypatch):
    monkeypatch.setattr(hf_mod, '_REQUESTS_IPV4_SESSION', None)
    session = hf_mod._requests_ipv4_session()
    assert hasattr(session, 'get') and not callable(getattr(session, '__code__', None))
    assert hf_mod._requests_ipv4_session() is session


def test_vllm_ignores_wsl_manifest_off_windows(monkeypatch, tmp_path: Path):
    import core.runtimes.vllm as vllm_mod

    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'backend': 'wsl', 'wsl_distro': 'Ubuntu', 'wsl_python': '/usr/bin/python3'}))
    monkeypatch.setattr(vllm_mod, 'VLLM_MANIFEST', manifest)
    monkeypatch.setattr(vllm_mod, 'VLLM_VENV_PY', tmp_path / 'venv' / 'bin' / 'python')
    monkeypatch.setattr(vllm_mod, '_VLLM_INSTALLED_CACHE', (0.0, False))
    monkeypatch.setattr(vllm_mod.sys, 'platform', 'linux')
    assert vllm_mod.VllmRuntimeAdapter.is_installed() is False
    ok, detail = vllm_mod.verify_vllm_installation()
    assert ok is False and 'WSL' in detail


def test_vllm_native_manifest_counts_off_windows(monkeypatch, tmp_path: Path):
    import core.runtimes.vllm as vllm_mod

    py = tmp_path / 'venv' / 'bin' / 'python'
    py.parent.mkdir(parents=True)
    py.write_text('')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'backend': 'native', 'python': str(py)}))
    monkeypatch.setattr(vllm_mod, 'VLLM_MANIFEST', manifest)
    monkeypatch.setattr(vllm_mod, 'VLLM_VENV_PY', py)
    monkeypatch.setattr(vllm_mod.sys, 'platform', 'linux')
    assert vllm_mod.VllmRuntimeAdapter.is_installed() is True


def test_vllm_unload_does_not_create_manifest(monkeypatch, tmp_path: Path):
    import core.runtimes.vllm as vllm_mod

    manifest = tmp_path / 'manifest.json'
    monkeypatch.setattr(vllm_mod, 'VLLM_MANIFEST', manifest)
    monkeypatch.setattr(vllm_mod, 'VLLM_BUNDLE', tmp_path)
    monkeypatch.setattr(vllm_mod, 'LOG_DIR', tmp_path / 'logs')
    monkeypatch.setattr(vllm_mod, 'VLLM_LOG', tmp_path / 'logs' / 'vllm.log')
    vllm_mod.VllmRuntimeAdapter().unload()
    assert not manifest.exists()


def test_freetoken_native_manifest(monkeypatch, tmp_path: Path):
    import core.runtimes.freetoken as ft_mod

    ft = tmp_path / 'ft'
    ft.write_text('')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'backend': 'native', 'python': '/x/python', 'ft': str(ft)}))
    monkeypatch.setattr(ft_mod, 'FREETOKEN_MANIFEST', manifest)
    monkeypatch.setattr(ft_mod.sys, 'platform', 'linux')
    assert ft_mod.FreeTokenRuntimeAdapter.is_installed() is True
    # A WSL manifest can't run on Linux.
    manifest.write_text(json.dumps({'backend': 'wsl', 'wsl_distro': 'U', 'wsl_python': '/p', 'wsl_ft': '/f'}))
    assert ft_mod.FreeTokenRuntimeAdapter.is_installed() is False


def test_cached_catalog_rows_follow_installed_engines(monkeypatch):
    import core.hf_catalog_cache as cache_mod
    import core.huggingface as hf

    rows = [
        {'id': 'a/hf', 'engines': ['vllm', 'transformers'], 'runnable': False},
        {'id': 'b/gguf', 'engines': [], 'runtime_id': 'llama-server', 'runnable': True},
    ]
    monkeypatch.setattr(hf, '_transformers_runtime_available', lambda: True)
    monkeypatch.setattr(hf, '_vllm_runtime_available', lambda: False)
    cache_mod._refresh_runnable(rows)
    assert rows[0]['runnable'] is True and rows[1]['runnable'] is True
    monkeypatch.setattr(hf, '_transformers_runtime_available', lambda: False)
    cache_mod._refresh_runnable(rows)
    assert rows[0]['runnable'] is False and rows[1]['runnable'] is True


def test_linux_proc_parsers(monkeypatch):
    import core.fast_probe as fp

    stat_lines = iter(['cpu  100 0 100 800 0 0 0 0 0 0\n', 'cpu  200 0 200 1000 0 0 0 0 0 0\n'])

    class _F:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def readline(self):
            return self.text

        def __iter__(self):
            return iter(self.text.splitlines(True))

    meminfo = 'MemTotal:       16000000 kB\nMemFree:  1000000 kB\nMemAvailable:   12000000 kB\n'

    def fake_open(path, *a, **k):
        if path == '/proc/stat':
            return _F(next(stat_lines))
        if path == '/proc/meminfo':
            return _F(meminfo)
        raise OSError(path)

    monkeypatch.setattr('builtins.open', fake_open)
    monkeypatch.setattr(fp, '_IS_WIN', False)
    monkeypatch.setattr(fp.sys, 'platform', 'linux')
    monkeypatch.setattr(fp, '_CPU_LAST', None)
    monkeypatch.setattr(fp.time, 'sleep', lambda s: None)
    # 2nd sample: +200 busy, +200 idle -> 50 %
    assert fp.cpu_percent() == 50
    ram = fp.ram_info()
    assert ram == {'ram_percent': 25, 'ram_used_gb': 3.8, 'ram_total_gb': 15.3}
