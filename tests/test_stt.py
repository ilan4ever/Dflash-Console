"""Tests for the whisper.cpp whisper-server STT adapter (Phase 2 scaffold)."""

from __future__ import annotations

import json
from pathlib import Path

import core.runtimes.stt as stt_mod
from core.runtimes import get_runtime_adapter
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_SPEECH_TO_TEXT, RUNTIME_STT


def test_adapter_registered_in_registry():
    adapter = get_runtime_adapter(RUNTIME_STT)
    assert adapter is not None
    assert adapter.runtime_id == RUNTIME_STT
    assert adapter.modalities == (MODALITY_SPEECH_TO_TEXT,)
    assert adapter.execution_mode == EXECUTION_MODE_SERVER
    assert 'runtimes\\stt\\whisper-server' in adapter.process_identity_tokens
    assert adapter.openai_routes() == ['/v1/audio/transcriptions']


def test_health_reports_not_installed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(stt_mod, 'STT_EXE', tmp_path / 'missing-whisper-server.exe')
    adapter = stt_mod.SttRuntimeAdapter()
    health = adapter.health()
    assert health['runtime_id'] == RUNTIME_STT
    assert health['installed'] is False
    assert health['running'] is False


def test_start_and_load_report_not_installed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(stt_mod, 'STT_EXE', tmp_path / 'missing-whisper-server.exe')
    adapter = stt_mod.SttRuntimeAdapter()
    started = adapter.start({'device_policy': 'gpu'})
    assert started['success'] is False
    assert 'not installed' in started['error']
    loaded = adapter.load({'path': str(tmp_path / 'model.gguf')})
    assert loaded['success'] is False


def test_write_manifest_records_runtime(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(stt_mod, 'STT_MANIFEST', tmp_path / 'manifest.json')
    adapter = stt_mod.SttRuntimeAdapter()
    manifest = adapter.write_manifest()
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['runtime_id'] == RUNTIME_STT
    assert payload['execution_mode'] == EXECUTION_MODE_SERVER


def test_process_identity_token_is_path_specific_not_bare_name():
    # Sandbox guarantee: the token must not match a foreign whisper process by
    # bare name; it must be a path-segment of our own binary only.
    assert 'runtimes\\stt\\whisper-server' in stt_mod.STT_PROCESS_TOKEN
    assert stt_mod.STT_PROCESS_TOKEN.startswith('runtimes')


def test_transcribe_reports_not_running(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(stt_mod, 'STT_EXE', tmp_path / 'missing-whisper-server.exe')
    adapter = stt_mod.SttRuntimeAdapter()
    result = adapter.transcribe(b'RIFF....wav')
    assert result['success'] is False
    assert 'not running' in result['error'] or 'not installed' in result['error']


def test_write_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(stt_mod, 'STT_EXE', tmp_path / 'whisper-server.exe')
    monkeypatch.setattr(stt_mod, 'STT_MANIFEST', tmp_path / 'manifest.json')
    adapter = stt_mod.SttRuntimeAdapter()
    manifest = adapter.write_manifest()
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['runtime_id'] == RUNTIME_STT
    assert payload['execution_mode'] == EXECUTION_MODE_SERVER
