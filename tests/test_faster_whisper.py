"""Tests for the faster-whisper (CTranslate2) STT runtime adapter."""

from __future__ import annotations

import json
from pathlib import Path

import core.runtimes.faster_whisper as fw_mod
from core.runtimes import get_runtime_adapter
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_SPEECH_TO_TEXT, RUNTIME_FASTER_WHISPER


def test_adapter_registered_in_registry():
    adapter = get_runtime_adapter(RUNTIME_FASTER_WHISPER)
    assert adapter is not None
    assert adapter.runtime_id == RUNTIME_FASTER_WHISPER
    assert adapter.modalities == (MODALITY_SPEECH_TO_TEXT,)
    assert adapter.execution_mode == EXECUTION_MODE_SERVER
    assert adapter.openai_routes() == ['/v1/audio/transcriptions']
    # Process identity must be a path-segment token, never a bare name.
    token = adapter.process_identity_tokens[0]
    assert 'server.py' in token or 'faster-whisper' in token.lower()
    assert token.startswith('runtimes')


def test_health_reports_not_installed_when_venv_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fw_mod, 'FW_VENV_PY', tmp_path / 'missing' / 'python.exe')
    monkeypatch.setattr(fw_mod, 'FW_SERVER', tmp_path / 'missing-server.py')
    adapter = fw_mod.FasterWhisperRuntimeAdapter()
    health = adapter.health()
    assert health['runtime_id'] == RUNTIME_FASTER_WHISPER
    assert health['installed'] is False
    assert health['running'] is False


def test_load_rejects_gguf_and_non_existent_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fw_mod, 'FW_VENV_PY', tmp_path / 'missing' / 'python.exe')
    monkeypatch.setattr(fw_mod, 'FW_SERVER', tmp_path / 'missing-server.py')
    adapter = fw_mod.FasterWhisperRuntimeAdapter()
    gguf = tmp_path / 'model.gguf'
    gguf.write_bytes(b'x')
    res = adapter.load({'path': str(gguf)})
    assert res['success'] is False
    assert 'whisper.cpp' in res['error'] or 'GGUF' in res['error']
    res2 = adapter.load({'path': str(tmp_path / 'does-not-exist')})
    assert res2['success'] is False


def test_load_requires_model_bin(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / 'small-en'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text('{}')
    monkeypatch.setattr(fw_mod, 'FW_VENV_PY', tmp_path / 'missing' / 'python.exe')
    monkeypatch.setattr(fw_mod, 'FW_SERVER', tmp_path / 'missing-server.py')
    adapter = fw_mod.FasterWhisperRuntimeAdapter()
    res = adapter.load({'path': str(model_dir)})
    assert res['success'] is False
    assert 'model.bin' in res['error']


def test_is_faster_whisper_dir(tmp_path: Path):
    good = tmp_path / 'good'
    good.mkdir()
    (good / 'model.bin').write_bytes(b'x')
    bad = tmp_path / 'bad'
    bad.mkdir()
    assert fw_mod.is_faster_whisper_dir(good) is True
    assert fw_mod.is_faster_whisper_dir(bad) is False
    assert fw_mod.is_faster_whisper_dir(tmp_path / 'missing') is False


def test_write_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fw_mod, 'FW_MANIFEST', tmp_path / 'manifest.json')
    adapter = fw_mod.FasterWhisperRuntimeAdapter()
    manifest = adapter.write_manifest()
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['runtime_id'] == RUNTIME_FASTER_WHISPER
    assert payload['execution_mode'] == EXECUTION_MODE_SERVER
