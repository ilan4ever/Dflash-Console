"""Slot shaping: engines grow for a client cohort and shrink when idle."""

from __future__ import annotations

import pytest

from core import context_maintenance as maintenance
from core.config import effective_parallel_slots


@pytest.fixture(autouse=True)
def _reset_idle():
    maintenance.reset_idle_tracking()
    yield
    maintenance.reset_idle_tracking()


def _config(*, slots=1, mode='auto', context_size=8192, cap=4):
    load = {'parallel_slots': cap, 'parallel_slots_mode': mode}
    if slots:
        load['requested_parallel_slots'] = slots
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
                'context_size': context_size,
                'context_max': 131072,
                'load_settings': load,
            }
        ],
    }


def _patch(monkeypatch, *, active=None, reloads=None):
    reloads = reloads if reloads is not None else []
    monkeypatch.setattr('core.runtime.tcp_port_open', lambda *a, **k: True)
    monkeypatch.setattr('core.client_identity.list_active_clients', lambda sid: list(active or []))
    monkeypatch.setattr('core.inference_stats.is_proxy_generating', lambda sid: False)
    monkeypatch.setattr('core.runtime.loaded_per_slot_context', lambda server: 8192)
    monkeypatch.setattr('core.model_presets.context_max_for_server', lambda server, cfg=None: 131072)
    monkeypatch.setattr('core.config.save_config', lambda cfg: None)

    def _reload(server, **kwargs):
        reloads.append(dict(server.get('load_settings') or {}))
        return {'success': True}

    monkeypatch.setattr('core.server_boot.reload_server', _reload)
    return reloads


# --- load shape -------------------------------------------------------------


def test_auto_mode_starts_engines_with_one_slot():
    """`parallel_slots` is the cap; the engine still starts minimal."""
    server = {'load_settings': {'parallel_slots': 4, 'parallel_slots_mode': 'auto'}}
    assert effective_parallel_slots(server) == 1


def test_auto_mode_honours_the_requested_shape():
    server = {
        'load_settings': {
            'parallel_slots': 4,
            'parallel_slots_mode': 'auto',
            'requested_parallel_slots': 3,
        }
    }
    assert effective_parallel_slots(server) == 3


def test_requested_shape_never_exceeds_the_cap():
    server = {
        'load_settings': {
            'parallel_slots': 2,
            'parallel_slots_mode': 'auto',
            'requested_parallel_slots': 9,
        }
    }
    assert effective_parallel_slots(server) == 2


def test_fixed_mode_keeps_the_configured_slots():
    server = {'load_settings': {'parallel_slots': 4, 'parallel_slots_mode': 'fixed'}}
    assert effective_parallel_slots(server) == 4


# --- idle shrink ------------------------------------------------------------


def test_idle_multi_slot_engine_shrinks_to_one_slot(monkeypatch):
    cfg = _config(slots=4, context_size=32768)
    reloads = _patch(monkeypatch)

    # First pass only starts the quiet timer.
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=1000.0) == []
    rows = maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=1200.0)

    assert rows and rows[0]['action'] == 'shrunk_idle'
    assert rows[0]['to_slots'] == 1
    assert reloads and reloads[0]['requested_parallel_slots'] == 1
    # Per-slot context is preserved, total follows the slot count.
    assert cfg['servers'][0]['context_size'] == 8192
    # The user's cap is untouched.
    assert cfg['servers'][0]['load_settings']['parallel_slots'] == 4


def test_active_clients_prevent_the_shrink(monkeypatch):
    cfg = _config(slots=4, context_size=32768)
    reloads = _patch(monkeypatch, active=['OneVoice AI'])

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=1000.0)
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=60, now=5000.0) == []
    assert reloads == []


def test_two_recent_clients_keep_the_slots(monkeypatch):
    cfg = _config(slots=4, context_size=32768)
    reloads = _patch(monkeypatch)
    maintenance.note_engine_active('qwen-shared', 'OneVoice AI')
    maintenance.note_engine_active('qwen-shared', 'DeepSeek Harness')

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=1000.0)
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=1200.0) == []
    assert reloads == []


def test_single_slot_engine_is_left_alone(monkeypatch):
    cfg = _config(slots=1, context_size=8192)
    reloads = _patch(monkeypatch)

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=1000.0)
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=5000.0) == []
    assert reloads == []


def test_auto_shrink_can_be_disabled(monkeypatch):
    cfg = _config(slots=4, context_size=32768)
    cfg['context_auto_shrink'] = False
    reloads = _patch(monkeypatch)

    maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=1000.0)
    assert maintenance.auto_shrink_pass(cfg=cfg, idle_seconds=0, now=5000.0) == []
    assert reloads == []


# --- cohort growth ----------------------------------------------------------


def test_second_client_grows_the_engine_to_two_slots(monkeypatch):
    cfg = _config(slots=1, context_size=8192)
    reloads = _patch(monkeypatch)
    maintenance.note_engine_active('qwen-shared', 'OneVoice AI')
    maintenance.note_engine_active('qwen-shared', 'DeepSeek Harness')

    rows = maintenance.grow_for_clients_pass(cfg=cfg, now=1000.0)

    assert rows and rows[0]['action'] == 'grew_for_clients'
    assert rows[0]['to_slots'] == 2
    assert reloads[0]['requested_parallel_slots'] == 2
    assert cfg['servers'][0]['context_size'] == 16384  # 8192 per slot x 2


def test_growth_never_runs_while_a_request_is_in_flight(monkeypatch):
    cfg = _config(slots=1, context_size=8192)
    reloads = _patch(monkeypatch, active=['OneVoice AI'])
    maintenance.note_engine_active('qwen-shared', 'OneVoice AI')
    maintenance.note_engine_active('qwen-shared', 'DeepSeek Harness')

    assert maintenance.grow_for_clients_pass(cfg=cfg, now=1000.0) == []
    assert reloads == []


def test_growth_respects_the_configured_cap(monkeypatch):
    cfg = _config(slots=1, context_size=8192, cap=2)
    _patch(monkeypatch)
    for client in ('a', 'b', 'c'):
        maintenance.note_engine_active('qwen-shared', client)

    rows = maintenance.grow_for_clients_pass(cfg=cfg, now=1000.0)
    assert rows[0]['to_slots'] == 2  # cap


def test_growth_respects_model_capability(monkeypatch):
    # per_slot 65536 with a 131072 model cap -> only 2 slots are possible.
    cfg = _config(slots=1, context_size=65536, cap=8)
    _patch(monkeypatch)
    monkeypatch.setattr('core.runtime.loaded_per_slot_context', lambda server: 65536)
    for client in ('a', 'b', 'c'):
        maintenance.note_engine_active('qwen-shared', client)

    rows = maintenance.grow_for_clients_pass(cfg=cfg, now=1000.0)
    assert rows[0]['to_slots'] == 2


def test_single_client_never_grows(monkeypatch):
    cfg = _config(slots=1, context_size=8192)
    reloads = _patch(monkeypatch)
    maintenance.note_engine_active('qwen-shared', 'OneVoice AI')

    assert maintenance.grow_for_clients_pass(cfg=cfg, now=1000.0) == []
    assert reloads == []
