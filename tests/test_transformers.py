"""Tests for the Transformers / PyTorch runtime adapter."""

from __future__ import annotations

import json
from pathlib import Path

from core.runtimes.transformers_hf import TransformersRuntimeAdapter, is_transformers_model_dir


def test_process_identity_token():
    adapter = TransformersRuntimeAdapter()
    assert adapter.process_identity_tokens
    token = adapter.process_identity_tokens[0]
    assert 'transformers' in token.lower()


def test_is_transformers_model_dir_safetensors(tmp_path: Path):
    model_dir = tmp_path / 'opt-125m'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'opt'}), encoding='utf-8')
    (model_dir / 'model.safetensors').write_text('x', encoding='utf-8')
    assert is_transformers_model_dir(model_dir) is True


def test_is_transformers_model_dir_rejects_vibevoice(tmp_path: Path):
    model_dir = tmp_path / 'vv'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(
        json.dumps({'model_type': 'vibevoice_streaming', 'architectures': ['VibeVoiceStreamingModel']}),
        encoding='utf-8',
    )
    (model_dir / 'model.safetensors').write_text('x', encoding='utf-8')
    assert is_transformers_model_dir(model_dir) is False


def test_health_reports_not_installed_when_venv_missing(monkeypatch, tmp_path: Path):
    import core.runtimes.transformers_hf as tf_mod

    monkeypatch.setattr(tf_mod, 'TF_SERVER', tmp_path / 'missing-server.py')
    monkeypatch.setattr(tf_mod, 'TF_VENV_PY', tmp_path / 'missing-python.exe')
    adapter = TransformersRuntimeAdapter()
    health = adapter.health()
    assert health['installed'] is False
