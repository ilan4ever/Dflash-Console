"""Shared UI state (pins) used by both the Electron window and the browser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import ui_state


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_state, '_STATE_PATH', tmp_path / 'ui-state.json')


def test_empty_state_has_both_scopes():
    state = ui_state.get_state()
    assert state['pins'] == {'models': [], 'engines': []}
    assert state['rev'] == 0


def test_set_pins_persists_and_bumps_revision():
    result = ui_state.set_pins('models', ['server:a', 'server:b'])
    assert result['success'] is True
    assert result['changed'] is True
    assert result['rev'] == 1
    assert ui_state.get_state()['pins']['models'] == ['server:a', 'server:b']

    # Same list again is a no-op (no revision churn, no needless re-render).
    again = ui_state.set_pins('models', ['server:b', 'server:a'])
    assert again['changed'] is False
    assert again['rev'] == 1


def test_pin_scopes_are_independent():
    ui_state.set_pins('models', ['server:a'])
    ui_state.set_pins('engines', ['path:c:/models/x.gguf'])
    state = ui_state.get_state()
    assert state['pins']['models'] == ['server:a']
    assert state['pins']['engines'] == ['path:c:/models/x.gguf']


def test_set_pins_cleans_and_dedupes():
    ui_state.set_pins('models', ['  server:a  ', 'server:a', '', None, 42])
    assert ui_state.get_state()['pins']['models'] == ['server:a', '42']


def test_set_pins_rejects_unknown_scope():
    result = ui_state.set_pins('nope', ['x'])
    assert result['success'] is False
    assert 'unknown pin scope' in result['error']


def test_toggle_pin_adds_then_removes():
    first = ui_state.toggle_pin('engines', 'server:x')
    assert first['success'] is True
    assert first['pinned'] is True
    assert ui_state.get_state()['pins']['engines'] == ['server:x']

    second = ui_state.toggle_pin('engines', 'server:x')
    assert second['pinned'] is False
    assert ui_state.get_state()['pins']['engines'] == []


def test_toggle_pin_requires_key():
    assert ui_state.toggle_pin('models', '  ')['success'] is False


def test_corrupt_state_file_falls_back_to_empty(tmp_path):
    path = ui_state._state_path()
    Path(path).write_text('{not json', encoding='utf-8')
    state = ui_state.get_state()
    assert state['pins'] == {'models': [], 'engines': []}


def test_state_file_is_json_with_both_scopes():
    ui_state.set_pins('engines', ['server:y'])
    payload = json.loads(Path(ui_state._state_path()).read_text(encoding='utf-8'))
    assert payload['version'] == 1
    assert payload['pins']['engines'] == ['server:y']
    assert payload['pins']['models'] == []


def test_set_pref_persists_value_and_removes_on_none():
    saved = ui_state.set_pref('modelGpuDevices', {'server:a': '1'})
    assert saved['success'] is True
    assert saved['changed'] is True
    assert ui_state.get_state()['prefs']['modelGpuDevices'] == {'server:a': '1'}

    # Same value again is a no-op.
    assert ui_state.set_pref('modelGpuDevices', {'server:a': '1'})['changed'] is False

    removed = ui_state.set_pref('modelGpuDevices', None)
    assert removed['changed'] is True
    assert 'modelGpuDevices' not in ui_state.get_state()['prefs']


def test_set_pref_supports_strings_and_rejects_bad_names():
    ui_state.set_pref('loadEngine', 'transformers')
    assert ui_state.get_state()['prefs']['loadEngine'] == 'transformers'
    assert ui_state.set_pref('', 'x')['success'] is False
    assert ui_state.set_pref('bad name', 'x')['success'] is False


def test_prefs_and_pins_share_one_revision_counter():
    ui_state.set_pins('models', ['server:a'])
    ui_state.set_pref('loadEngine', 'vllm')
    state = ui_state.get_state()
    assert state['rev'] == 2
    assert state['prefs']['loadEngine'] == 'vllm'
    assert state['pins']['models'] == ['server:a']


def test_ui_state_prefs_endpoint_round_trip(monkeypatch):
    from api.app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.put(
        '/api/ui-state/prefs',
        json={'name': 'modelPrefs', 'value': {'m1': {'context_size': 8192}}, 'client': 'test'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['prefs']['modelPrefs'] == {'m1': {'context_size': 8192}}
    assert client.get('/api/ui-state').json()['prefs']['modelPrefs'] == {'m1': {'context_size': 8192}}

    cleared = client.put('/api/ui-state/prefs', json={'name': 'modelPrefs', 'value': None})
    assert cleared.status_code == 200
    assert 'modelPrefs' not in cleared.json()['prefs']


def test_ui_state_endpoints_round_trip(monkeypatch):
    from api.app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)

    assert client.get('/api/ui-state').json()['pins']['models'] == []

    saved = client.put(
        '/api/ui-state/pins',
        json={'scope': 'models', 'keys': ['server:a'], 'client': 'test'},
    )
    assert saved.status_code == 200
    assert saved.json()['pins']['models'] == ['server:a']

    toggled = client.post(
        '/api/ui-state/pins/toggle',
        json={'scope': 'models', 'key': 'server:b'},
    )
    assert toggled.status_code == 200
    assert toggled.json()['pinned'] is True
    assert toggled.json()['pins']['models'] == ['server:a', 'server:b']

    bad = client.put('/api/ui-state/pins', json={'scope': 'bogus', 'keys': []})
    assert bad.status_code == 400
