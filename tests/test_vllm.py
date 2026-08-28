"""Tests for the vLLM runtime adapter and on-demand install status."""

from __future__ import annotations

import json
from pathlib import Path

from core.hf_engines import preferred_hf_runtime
from core.runtimes import get_runtime_adapter
from core.runtimes.vllm import VllmRuntimeAdapter, is_vllm_model_dir


def test_registry_includes_vllm():
    adapter = get_runtime_adapter('vllm')
    assert adapter is not None
    assert adapter.runtime_id == 'vllm'


def test_health_reports_not_installed_when_bundle_missing(monkeypatch, tmp_path: Path):
    import core.runtimes.vllm as vllm_mod

    monkeypatch.setattr(vllm_mod, 'VLLM_VENV_PY', tmp_path / 'missing-python.exe')
    monkeypatch.setattr(vllm_mod, 'VLLM_MANIFEST', tmp_path / 'missing-manifest.json')
    adapter = VllmRuntimeAdapter()
    health = adapter.health()
    assert health['installed'] is False
    assert health['running'] is False


def test_is_vllm_model_dir_safetensors(tmp_path: Path):
    model_dir = tmp_path / 'qwen'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}), encoding='utf-8')
    (model_dir / 'model.safetensors').write_text('x', encoding='utf-8')
    assert is_vllm_model_dir(model_dir) is True


def test_load_without_install_returns_error(monkeypatch, tmp_path: Path):
    import core.runtimes.vllm as vllm_mod

    monkeypatch.setattr(vllm_mod, 'VLLM_VENV_PY', tmp_path / 'missing-python.exe')
    monkeypatch.setattr(vllm_mod, 'VLLM_MANIFEST', tmp_path / 'missing-manifest.json')
    adapter = VllmRuntimeAdapter()
    result = adapter.load({'path': str(tmp_path)})
    assert result['success'] is False
    assert 'not installed' in str(result.get('error') or '').lower()


def test_preferred_hf_runtime_falls_back_to_transformers(monkeypatch):
    monkeypatch.setattr('core.runtimes.vllm.VllmRuntimeAdapter.is_installed', staticmethod(lambda: False))
    assert preferred_hf_runtime() == 'transformers'


def test_install_status_idle_when_missing():
    from core.vllm_runtime_install import install_status

    status = install_status()
    assert 'installed' in status
    assert status['status'] in {'idle', 'installed', 'installing', 'error'}
