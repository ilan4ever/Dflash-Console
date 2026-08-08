"""Tests for the Piper TTS runtime adapter (Phase 1)."""

from __future__ import annotations

import json
from pathlib import Path

import core.runtimes.piper as piper_mod
from core.runtimes import get_runtime_adapter
from core.runtimes.base import EXECUTION_MODE_CLI, MODALITY_TEXT_TO_SPEECH, RUNTIME_PIPER


def _make_voice_dir(tmp_path: Path) -> Path:
    voices = tmp_path / 'voices'
    voices.mkdir(parents=True)
    (voices / 'en_US-test-medium.onnx').write_bytes(b'onnx-bytes')
    (voices / 'en_US-test-medium.onnx.json').write_text('{"audio": {"sample_rate": 22050}}', encoding='utf-8')
    return voices


def test_adapter_registered_in_registry():
    adapter = get_runtime_adapter(RUNTIME_PIPER)
    assert adapter is not None
    assert adapter.runtime_id == RUNTIME_PIPER
    assert adapter.modalities == (MODALITY_TEXT_TO_SPEECH,)
    assert adapter.execution_mode == EXECUTION_MODE_CLI
    assert 'runtimes\\piper\\piper.exe' in adapter.process_identity_tokens
    assert 'piper.exe' not in adapter.process_identity_tokens
    assert adapter.openai_routes() == ['/v1/audio/speech']


def test_health_reports_installed_and_voices(monkeypatch, tmp_path: Path):
    voices = _make_voice_dir(tmp_path)
    monkeypatch.setattr(piper_mod, 'PIPER_EXE', tmp_path / 'piper.exe')
    monkeypatch.setattr(piper_mod, 'PIPER_VOICES', voices)
    monkeypatch.setattr(piper_mod, 'PIPER_BUNDLE', tmp_path)
    # Installed state is derived from the exe path; force it for this test.
    (tmp_path / 'piper.exe').write_bytes(b'x')

    adapter = piper_mod.PiperRuntimeAdapter()
    health = adapter.health()
    assert health['runtime_id'] == RUNTIME_PIPER
    assert health['installed'] is True
    assert health['voices'] == 1
    assert health['execution_mode'] == EXECUTION_MODE_CLI

    voices_list = adapter.list_voices()
    assert len(voices_list) == 1
    assert voices_list[0]['id'] == 'en_US-test-medium'
    assert voices_list[0]['path'].endswith('.onnx')


def test_write_manifest_records_voices(monkeypatch, tmp_path: Path):
    voices = _make_voice_dir(tmp_path)
    monkeypatch.setattr(piper_mod, 'PIPER_EXE', tmp_path / 'piper.exe')
    monkeypatch.setattr(piper_mod, 'PIPER_VOICES', voices)
    monkeypatch.setattr(piper_mod, 'PIPER_MANIFEST', tmp_path / 'manifest.json')

    adapter = piper_mod.PiperRuntimeAdapter()
    manifest = adapter.write_manifest()
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['runtime_id'] == RUNTIME_PIPER
    assert payload['voices'] == ['en_US-test-medium']


def test_load_and_unload_voice(monkeypatch, tmp_path: Path):
    voices = _make_voice_dir(tmp_path)
    monkeypatch.setattr(piper_mod, 'PIPER_EXE', tmp_path / 'piper.exe')
    monkeypatch.setattr(piper_mod, 'PIPER_VOICES', voices)

    adapter = piper_mod.PiperRuntimeAdapter()
    loaded = adapter.load({'id': 'en_US-test-medium'})
    assert loaded['success'] is True
    assert loaded['voice'] == 'en_US-test-medium'
    assert adapter.health()['active_voice'] == 'en_US-test-medium'

    unloaded = adapter.unload()
    assert unloaded['success'] is True
    assert adapter.health()['active_voice'] == ''


def test_synthesize_reports_error_when_not_installed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(piper_mod, 'PIPER_EXE', tmp_path / 'missing-piper.exe')
    monkeypatch.setattr(piper_mod, 'PIPER_VOICES', tmp_path / 'voices')

    adapter = piper_mod.PiperRuntimeAdapter()
    result = adapter.synthesize('hello', voice='x')
    assert result['success'] is False
    assert 'not installed' in result['error']
