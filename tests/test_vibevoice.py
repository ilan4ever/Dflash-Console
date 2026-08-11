"""Tests for the VibeVoice (Microsoft) realtime TTS runtime adapter."""

from __future__ import annotations

import json
from pathlib import Path

import core.runtimes.vibevoice as vv_mod
from core.runtimes import get_runtime_adapter
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_TEXT_TO_SPEECH, RUNTIME_VIBEVOICE


def test_adapter_registered_in_registry():
    adapter = get_runtime_adapter(RUNTIME_VIBEVOICE)
    assert adapter is not None
    assert adapter.runtime_id == RUNTIME_VIBEVOICE
    assert adapter.modalities == (MODALITY_TEXT_TO_SPEECH,)
    assert adapter.execution_mode == EXECUTION_MODE_SERVER
    assert adapter.openai_routes() == ['/v1/audio/speech']
    token = adapter.process_identity_tokens[0]
    assert 'server.py' in token or 'vibevoice' in token.lower()
    assert token.startswith('runtimes')


def test_health_reports_not_installed_when_venv_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(vv_mod, 'VV_VENV_PY', tmp_path / 'missing' / 'python.exe')
    monkeypatch.setattr(vv_mod, 'VV_SERVER', tmp_path / 'missing-server.py')
    adapter = vv_mod.VibeVoiceRuntimeAdapter()
    health = adapter.health()
    assert health['runtime_id'] == RUNTIME_VIBEVOICE
    assert health['installed'] is False
    assert health['running'] is False


def test_load_rejects_non_vibevoice_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(vv_mod, 'VV_VENV_PY', tmp_path / 'missing' / 'python.exe')
    monkeypatch.setattr(vv_mod, 'VV_SERVER', tmp_path / 'missing-server.py')
    adapter = vv_mod.VibeVoiceRuntimeAdapter()
    # Missing dir
    res = adapter.load({'path': str(tmp_path / 'does-not-exist')})
    assert res['success'] is False
    # Dir without model.safetensors
    bare = tmp_path / 'bare'
    bare.mkdir()
    (bare / 'config.json').write_text('{}')
    res2 = adapter.load({'path': str(bare)})
    assert res2['success'] is False
    assert 'model.safetensors' in res2['error']


def test_list_voices_scans_bundle(monkeypatch, tmp_path: Path):
    voices = tmp_path / 'voices'
    voices.mkdir()
    (voices / 'en-Carter_man.pt').write_bytes(b'x')
    (voices / 'en-Emma_woman.pt').write_bytes(b'x')
    monkeypatch.setattr(vv_mod, 'VV_VOICES', voices)
    adapter = vv_mod.VibeVoiceRuntimeAdapter()
    rows = adapter.list_voices()
    assert {r['id'] for r in rows} == {'en-Carter_man', 'en-Emma_woman'}


def test_write_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(vv_mod, 'VV_MANIFEST', tmp_path / 'manifest.json')
    voices = tmp_path / 'voices'
    voices.mkdir()
    (voices / 'en-Carter_man.pt').write_bytes(b'x')
    monkeypatch.setattr(vv_mod, 'VV_VOICES', voices)
    adapter = vv_mod.VibeVoiceRuntimeAdapter()
    manifest = adapter.write_manifest()
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['runtime_id'] == RUNTIME_VIBEVOICE
    assert payload['execution_mode'] == EXECUTION_MODE_SERVER
    assert payload['voices'] == ['en-Carter_man']
