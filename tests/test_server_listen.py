from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core.config as config
import core.engine_state as engine_state
from api.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
                'profile': 'dflash',
                'engine_on': False,
            }],
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    return path


def test_listen_arms_already_running_listener(
    client: TestClient,
    config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine_state.note_user_stopped('gemma-31b-dflash')

    monkeypatch.setattr('core.runtime.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(
        'core.server_boot.adopt_running_engine',
        lambda *args, **kwargs: {'success': True, 'adopted': True, 'port': 8090},
    )

    def _should_not_boot(*args, **kwargs):
        raise AssertionError('start_router_listener should not run when port is already live')

    monkeypatch.setattr('core.server_boot.start_router_listener', _should_not_boot)

    response = client.post('/api/servers/gemma-31b-dflash/listen')
    assert response.status_code == 200
    body = response.json()
    assert body.get('success') is True
    assert body.get('already_running') is True

    saved = json.loads(config_file.read_text(encoding='utf-8'))
    assert saved['servers'][0]['engine_on'] is True
