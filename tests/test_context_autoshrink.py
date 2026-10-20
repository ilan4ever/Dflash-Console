"""Idle auto-shrink: multi-slot engines return to one slot when nobody uses them."""

from __future__ import annotations

import pytest

from core import context_maintenance as maintenance


@pytest.fixture(autouse=True)
def _reset_idle():
    maintenance.reset_idle_tracking()
    yield
    maintenance.reset_idle_tracking()


def _config():
    return {
        'context_max': 131072,
        'servers': [
            {
                'id': 'qwen-shared',
                'enabled': True,
                'engine_on': True,
                'host': '127.0.0.1',
                'port': 8097,
                'api_url': 'http://127.0.0.1:8097/v1',
                'model_id': 'qwen',
                'context_size': 131072,
                'context_max': 131072,
                'load_settings': {'parallel_slots': 4},
            }
        ],
    }


def _patch_environment(monkeypatch: pytest.MonkeyPatch, *, active: list[str] | None = None, reloads: list | None = None):
    reloads = reloads if reloads is not None else []
    monkeypatch.setattr('core.runtime.tcp_port_open', lambda *a, **k: True)
    monkeypatch.setattr('core.client_identity.list_active_clients', lambda sid: list(active or []))
    monkeypatch.setattr('core.inference_stats.is_proxy_generating', lambda sid: False)
    monkeypatch.setattr('core.runtime.loaded_per_slot_context', lambda server: 32768)
    monkeypatch.setattr('core.model_presets.context_max_for_server', lambda server, cfg=None: 131072)
    monkeypatch.setattr('core.config.save_config', lambda cfg: None)
    monkeypatch.setattr(
        'core.server_boot.reload_server',
        lambda server, **kwargs: reloads.append(dict(server.get('load_settings') or {})) or {'success': True},
    )
    return reloads


def test_idle_multi_slot_engine_is_shrunk_to_one_slot(monkeypatch: pytest.MonkeyPatch):
    cfg = _config()
    reloads = _patch_environment(monkeypatch)

    # First pass only starts the quiet timer.
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=1000.0) == []
    rows = maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=1200.0)

    assert rows and rows[0]['action'] == 'shrunk_idle'
    assert rows[0]['to_slots'] == 1
    assert reloads and reloads[0]['parallel_slots'] == 1
    # Context per slot is preserved: a returning client cannot hit a smaller window.
    assert cfg['servers'][0]['context_size'] == 32768
    assert cfg['servers'][0]['load_settings']['parallel_slots'] == 1


def test_active_clients_prevent_the_shrink(monkeypatch: pytest.MonkeyPatch):
    cfg = _config()
    reloads = _patch_environment(monkeypatch, active=['OneVoice AI'])

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=1000.0)
    rows = maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=5000.0)

    assert rows == []
    assert reloads == []


def test_single_slot_engine_is_left_alone(monkeypatch: pytest.MonkeyPatch):
    cfg = _config()
    cfg['servers'][0]['load_settings'] = {'parallel_slots': 1}
    cfg['servers'][0]['context_size'] = 32768
    reloads = _patch_environment(monkeypatch)

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=1000.0)
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=5000.0) == []
    assert reloads == []


def test_auto_shrink_can_be_disabled(monkeypatch: pytest.MonkeyPatch):
    cfg = _config()
    cfg['context_auto_shrink'] = False
    reloads = _patch_environment(monkeypatch)

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=1000.0)
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=5000.0) == []
    assert reloads == []
