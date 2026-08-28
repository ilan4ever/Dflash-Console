"""Tests for runtime uninstall helpers."""

from pathlib import Path


def test_vllm_uninstall_removes_venv(tmp_path, monkeypatch):
    bundle = tmp_path / 'runtimes' / 'vllm'
    venv = bundle / 'venv'
    venv.mkdir(parents=True)
    (venv / 'Scripts').mkdir(parents=True)
    (venv / 'Scripts' / 'python.exe').write_text('', encoding='utf-8')
    manifest = bundle / 'manifest.json'
    manifest.write_text('{"runtime_id":"vllm"}', encoding='utf-8')

    import core.vllm_runtime_install as mod

    monkeypatch.setattr(mod, '_BUNDLE', bundle)
    monkeypatch.setattr(mod, 'is_installed', lambda: False)
    monkeypatch.setattr(mod, '_stop_runtime', lambda: None)

    result = mod.uninstall()
    assert result['success'] is True
    assert not venv.exists()
    assert not manifest.exists()
    assert result['installed'] is False


def test_transformers_uninstall_removes_venv(tmp_path, monkeypatch):
    bundle = tmp_path / 'runtimes' / 'transformers'
    bundle.mkdir(parents=True)
    (bundle / 'server.py').write_text('print("ok")', encoding='utf-8')
    venv = bundle / 'venv'
    venv.mkdir(parents=True)
    (venv / 'Scripts').mkdir(parents=True)
    (venv / 'Scripts' / 'python.exe').write_text('', encoding='utf-8')
    manifest = bundle / 'manifest.json'
    manifest.write_text('{"runtime_id":"transformers"}', encoding='utf-8')

    import core.transformers_runtime_install as mod

    monkeypatch.setattr(mod, '_BUNDLE', bundle)
    monkeypatch.setattr(mod, 'is_installed', lambda: False)
    monkeypatch.setattr(mod, '_stop_runtime', lambda: None)

    result = mod.uninstall()
    assert result['success'] is True
    assert not venv.exists()
    assert not manifest.exists()
    assert (bundle / 'server.py').is_file()
    assert result['installed'] is False
