from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.config as config
import core.engine_state as engine_state


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / 'config.json'
    path.write_text(
        json.dumps({
            'ui_port': 8900,
            'servers': [{
                'id': 'gemma-31b-dflash',
                'enabled': True,
                'host': '127.0.0.1',
                'port': 8090,
                'api_url': 'http://127.0.0.1:8090/v1',
                'model_id': 'demo',
                'engine_on': True,
            }],
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    return path


def test_runtime_saved_in_config(config_file: Path):
    engine_state.note_engine_on('gemma-31b-dflash')
    saved = json.loads(config_file.read_text(encoding='utf-8'))
    row = saved['servers'][0]
    assert row['engine_on'] is True
    assert 'checkpoint_loaded' not in row


def test_stop_clears_config(config_file: Path):
    engine_state.note_engine_on('gemma-31b-dflash')
    engine_state.note_user_stopped('gemma-31b-dflash')
    saved = json.loads(config_file.read_text(encoding='utf-8'))
    row = saved['servers'][0]
    assert row['engine_on'] is False


def test_restore_skips_when_engine_off(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    engine_state.note_user_stopped('gemma-31b-dflash')
    calls: list[str] = []

    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: False)
    monkeypatch.setattr(
        'core.server_boot.start_router_listener',
        lambda *args, **kwargs: calls.append('start') or {'success': True},
    )

    results = engine_state.restore_engines()
    assert results[0]['action'] == 'skipped_engine_off'
    assert calls == []


def test_restore_adopts_loaded_checkpoint(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(engine_state, 'probe_models', lambda api_url: ['demo-model'])
    monkeypatch.setattr(
        'core.server_boot.adopt_running_engine',
        lambda *args, **kwargs: {'success': True, 'adopted': True},
    )
    release_calls: list[str] = []
    monkeypatch.setattr(
        engine_state,
        'release_gpu_checkpoints',
        lambda *args, **kwargs: release_calls.append('release') or {'unloaded': True},
    )

    results = engine_state.restore_engines()
    assert results[0]['action'] == 'adopted_loaded'
    assert results[0]['models'] == ['demo-model']
    assert release_calls == []


def test_restore_never_auto_loads_checkpoint(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    load_calls: list[str] = []

    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: False)
    monkeypatch.setattr(
        'core.server_boot.load_server_checkpoint',
        lambda *args, **kwargs: load_calls.append('load') or {'success': True},
    )
    monkeypatch.setattr(
        'core.server_boot.start_router_listener',
        lambda *args, **kwargs: {'success': True},
    )

    engine_state.restore_engines()
    assert load_calls == []
