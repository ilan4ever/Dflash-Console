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


def test_restore_stops_orphan_listener_when_saved_off(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    engine_state.note_user_stopped('gemma-31b-dflash')
    stop_calls: list[str] = []

    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(engine_state, 'probe_models', lambda api_url: ['demo-model'])
    monkeypatch.setattr(
        'core.server_boot.adopt_running_engine',
        lambda *args, **kwargs: {'success': True, 'adopted': True},
    )
    monkeypatch.setattr(
        'core.runtime.stop_server',
        lambda *args, **kwargs: stop_calls.append('stop') or {'success': True},
    )

    results = engine_state.restore_engines()
    assert results[0]['action'] == 'stopped_orphan'
    assert stop_calls == ['stop']
    saved = json.loads(config_file.read_text(encoding='utf-8'))
    assert saved['servers'][0]['engine_on'] is False


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


def test_release_and_stop_all_managed_engines(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    release_calls: list[str] = []
    stop_calls: list[str] = []

    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(
        engine_state,
        'release_gpu_checkpoints',
        lambda server: release_calls.append(str(server.get('id'))) or {'success': True, 'unloaded': True, 'models': ['demo-model']},
    )
    monkeypatch.setattr(
        'core.runtime.stop_server',
        lambda **kwargs: stop_calls.append(str(kwargs.get('port'))) or {'success': True, 'stopped': True},
    )

    results = engine_state.release_and_stop_all_managed_engines()
    assert len(results) == 1
    assert results[0]['server_id'] == 'gemma-31b-dflash'
    assert release_calls == ['gemma-31b-dflash']
    assert stop_calls == ['8090']


def test_restore_adopts_loaded_checkpoint_on_console_boot(config_file: Path, monkeypatch: pytest.MonkeyPatch):
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
        lambda *args, **kwargs: release_calls.append('release') or {'unloaded': True, 'models': ['demo-model']},
    )

    results = engine_state.restore_engines()
    assert results[0]['action'] == 'adopted_idle'
    assert results[0]['models'] == ['demo-model']
    assert release_calls == ['release']
    assert results[0]['release']['unloaded'] is True
    saved = json.loads(config_file.read_text(encoding='utf-8'))
    assert saved['servers'][0]['engine_on'] is True


def test_restore_starts_listener_without_loading_checkpoint(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    listen_calls: list[str] = []

    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: False)
    monkeypatch.setattr(
        'core.server_boot.start_router_listener',
        lambda *args, **kwargs: listen_calls.append('listen') or {'success': True},
    )

    results = engine_state.restore_engines()
    assert listen_calls == ['listen']
    assert results[0]['action'] == 'restarted_listener'


def test_restore_adopts_idle_listener_without_checkpoint(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    stop_calls: list[str] = []

    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(engine_state, 'probe_models', lambda api_url: [])
    monkeypatch.setattr(
        'core.server_boot.adopt_running_engine',
        lambda *args, **kwargs: {'success': True, 'adopted': True},
    )
    monkeypatch.setattr(
        'core.runtime.stop_server',
        lambda *args, **kwargs: stop_calls.append('stop') or {'success': True},
    )

    results = engine_state.restore_engines()
    assert results[0]['action'] == 'adopted_idle'
    assert stop_calls == []
    saved = json.loads(config_file.read_text(encoding='utf-8'))
    assert saved['servers'][0]['engine_on'] is True


def test_restore_starts_embedding_server_for_embedding_profile(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    saved = json.loads(config_file.read_text(encoding='utf-8'))
    saved['servers'][0].update({
        'profile': 'nomic-embed',
        'engine_mode': 'embedding',
        'model_id': 'nomic-embed-text',
    })
    config_file.write_text(json.dumps(saved), encoding='utf-8')

    embedding_calls: list[str] = []
    router_calls: list[str] = []
    monkeypatch.setattr(engine_state, 'tcp_port_open', lambda host, port: False)
    monkeypatch.setattr(
        'core.embedding_server.start_embedding_server',
        lambda *args, **kwargs: embedding_calls.append('embedding') or {'success': True},
    )
    monkeypatch.setattr(
        'core.server_boot.start_router_listener',
        lambda *args, **kwargs: router_calls.append('router') or {'success': True},
    )

    results = engine_state.restore_engines()

    assert results[0]['action'] == 'restarted_listener'
    assert embedding_calls == ['embedding']
    assert router_calls == []
