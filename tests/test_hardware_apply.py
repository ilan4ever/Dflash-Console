from __future__ import annotations

import api.app as app
from core.hardware_apply import hardware_reload_plan
from api.app import HardwarePatch


def test_reload_plan_skips_matching_launch(monkeypatch):
    cfg = {
        'hardware_settings': {'gpu_strategy': 'single_largest'},
        'servers': [{
            'id': 'gemma-12b',
            'label': 'Gemma 12B',
            'port': 8091,
            'host': '127.0.0.1',
            'api_url': 'http://127.0.0.1:8091',
            'model_id': 'gemma-4-12b-it-qat',
            'gpu_device': 'auto',
            'enabled': True,
            'engine_kind': 'llama',
        }],
    }
    desired = {
        'main_gpu': 0,
        'split_mode': 'none',
        'tensor_split': '',
        'offload_kv_cache_to_gpu': True,
    }
    monkeypatch.setattr('core.hardware_apply.query_gpu_devices', lambda: [
        {'index': 0, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 24.0, 'vram_free_gb': 22.0},
        {'index': 1, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 23.0},
    ])
    monkeypatch.setattr('core.runtime.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.runtime.probe_models', lambda api_url: ['gemma-4-12b-it-qat'])
    monkeypatch.setattr('core.hardware_apply.get_started_launch', lambda port: dict(desired))
    monkeypatch.setattr(
        'core.hardware_apply._desired_signature',
        lambda entry, cfg, gpus, model_id: dict(desired),
    )
    plan = hardware_reload_plan(cfg, gpus=[])
    assert plan['reload_needed'] is False
    assert plan['reload_targets'] == []
    assert plan['reload_unchanged'][0]['server_id'] == 'gemma-12b'


def test_patch_hardware_forces_reload_when_settings_change(monkeypatch):
    cfg = {
        'hardware_settings': {'gpu_strategy': 'single_largest'},
        'servers': [],
    }
    calls: list[bool] = []

    monkeypatch.setattr(app, 'load_config', lambda: cfg)
    monkeypatch.setattr(app, '_save_config_checked', lambda value: None)

    def fake_plan(value, *, force_reload=False):
        calls.append(force_reload)
        return {'reload_needed': False, 'reload_targets': [], 'reload_unchanged': []}

    monkeypatch.setattr('core.hardware_apply.hardware_reload_plan', fake_plan)

    result = app.patch_hardware(HardwarePatch(gpu_strategy='split_evenly'))

    assert result['hardware_settings']['gpu_strategy'] == 'split_evenly'
    assert calls == [True]


def test_reload_plan_targets_split_change(monkeypatch):
    cfg = {
        'hardware_settings': {'gpu_strategy': 'split_evenly'},
        'servers': [{
            'id': 'gemma-12b',
            'label': 'Gemma 12B',
            'port': 8091,
            'host': '127.0.0.1',
            'api_url': 'http://127.0.0.1:8091',
            'model_id': 'gemma-4-12b-it-qat',
            'gpu_device': 'auto',
            'enabled': True,
            'engine_kind': 'llama',
        }],
    }
    monkeypatch.setattr('core.runtime.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.runtime.probe_models', lambda api_url: ['gemma-4-12b-it-qat'])
    monkeypatch.setattr(
        'core.hardware_apply.get_started_launch',
        lambda port: {
            'main_gpu': 0,
            'split_mode': 'none',
            'tensor_split': '',
            'offload_kv_cache_to_gpu': True,
        },
    )
    monkeypatch.setattr(
        'core.hardware_apply._desired_signature',
        lambda entry, cfg, gpus, model_id: {
            'main_gpu': 0,
            'split_mode': 'layer',
            'tensor_split': '0.5000,0.5000',
            'offload_kv_cache_to_gpu': True,
        },
    )
    plan = hardware_reload_plan(cfg, gpus=[])
    assert plan['reload_needed'] is True
    assert plan['reload_targets'][0]['server_id'] == 'gemma-12b'
    assert 'Reloading now' in plan['reload_message']


def test_reload_plan_force_reloads_loaded_engine_after_settings_change(monkeypatch):
    cfg = {
        'hardware_settings': {'gpu_strategy': 'split_evenly'},
        'servers': [{
            'id': 'gemma-12b',
            'label': 'Gemma 12B',
            'port': 8091,
            'host': '127.0.0.1',
            'api_url': 'http://127.0.0.1:8091',
            'model_id': 'gemma-4-12b-it-qat',
            'gpu_device': 'auto',
            'enabled': True,
            'engine_kind': 'llama',
        }],
    }
    desired = {
        'main_gpu': 0,
        'split_mode': 'layer',
        'tensor_split': '0.5000,0.5000',
        'offload_kv_cache_to_gpu': True,
    }
    monkeypatch.setattr('core.hardware_apply.query_gpu_devices', lambda: [])
    monkeypatch.setattr('core.runtime.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.runtime.probe_models', lambda api_url: ['gemma-4-12b-it-qat'])
    monkeypatch.setattr('core.hardware_apply.get_started_launch', lambda port: dict(desired))
    monkeypatch.setattr(
        'core.hardware_apply._desired_signature',
        lambda entry, cfg, gpus, model_id: dict(desired),
    )

    plan = hardware_reload_plan(cfg, gpus=[], force_reload=True)

    assert plan['reload_needed'] is True
    assert plan['reload_targets'][0]['server_id'] == 'gemma-12b'
    assert plan['reload_unchanged'] == []


def test_reload_plan_ignores_stopped_engines(monkeypatch):
    cfg = {
        'hardware_settings': {'gpu_strategy': 'split_evenly'},
        'servers': [{
            'id': 'idle',
            'label': 'Idle',
            'port': 8099,
            'host': '127.0.0.1',
            'api_url': 'http://127.0.0.1:8099',
            'model_id': 'gemma-4-12b-it-qat',
            'gpu_device': 'auto',
            'enabled': True,
            'engine_kind': 'llama',
        }],
    }
    monkeypatch.setattr('core.runtime.tcp_port_open', lambda host, port: False)
    monkeypatch.setattr('core.runtime.probe_models', lambda api_url: [])
    plan = hardware_reload_plan(cfg, gpus=[])
    assert plan['reload_needed'] is False
    assert plan['reload_targets'] == []
