from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import app


def test_runtime_logs_supports_legacy_log_path_and_clear(tmp_path, monkeypatch):
    runtime = {'id': 'runtime-transformers', 'runtime_id': 'transformers'}
    state = {'servers': [], 'runtimes': [runtime]}
    log_path = tmp_path / 'logs' / 'runtimes' / 'transformers.log'
    log_path.parent.mkdir(parents=True)
    log_path.write_text('first\nsecond\n', encoding='utf-8')

    monkeypatch.setattr('api.app.ROOT', tmp_path)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app.list_runtimes', lambda _cfg: [runtime])

    client = TestClient(app)

    legacy = client.get('/api/logs/transformers?tail=1')
    assert legacy.status_code == 200
    assert legacy.json()['runtime_id'] == 'transformers'
    assert legacy.json()['lines'] == ['second']

    direct = client.get('/api/runtimes/transformers/logs')
    assert direct.status_code == 200
    assert direct.json()['lines'] == ['first', 'second']

    cleared = client.delete('/api/logs/runtime-transformers')
    assert cleared.status_code == 200
    assert cleared.json()['runtime_id'] == 'transformers'
    assert log_path.read_text(encoding='utf-8') == ''


def test_runtime_logs_reject_unknown_ids_without_touching_files(monkeypatch):
    state = {'servers': [], 'runtimes': [{'id': 'runtime-transformers', 'runtime_id': 'transformers'}]}
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app.list_runtimes', lambda _cfg: state['runtimes'])

    response = TestClient(app).get('/api/runtimes/unknown-runtime/logs')
    assert response.status_code == 404
    assert 'unknown runtime' in response.json()['detail']
