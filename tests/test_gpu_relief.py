import time

from core.gpu_relief import (
    detect_generation_stall,
    gpu_is_saturated,
    gpu_relief_enabled,
    tick_gpu_relief,
)


def test_gpu_relief_enabled_defaults_true():
    assert gpu_relief_enabled({'hardware_settings': {}}) is True
    assert gpu_relief_enabled({'hardware_settings': {'gpu_relief_enabled': False}}) is False


def test_gpu_is_saturated():
    assert gpu_is_saturated({'load_percent': 95, 'vram_percent': 50}) is True
    assert gpu_is_saturated({'load_percent': 50, 'vram_percent': 90}) is True
    assert gpu_is_saturated({'load_percent': 50, 'vram_percent': 50}) is False


def test_detect_prefill_stall_on_saturated_gpu():
    now = 1_000.0
    track: dict = {}
    stats = {
        'generating': True,
        'generating_seconds': 90.0,
        'generating_tokens': 0,
        'prefill_tokens': 12000,
    }
    track['last_prefill'] = 12000
    track['last_prefill_at'] = now - 35.0
    track['stall_since'] = now - 35.0
    assert detect_generation_stall(
        stats,
        track,
        now=now,
        gpu_saturated=True,
    ) is True


def test_detect_decode_stall():
    now = 2_000.0
    track = {
        'last_decode_tokens': 42,
        'last_decode_at': now - 35.0,
    }
    stats = {
        'generating': True,
        'generating_seconds': 120.0,
        'generating_tokens': 42,
    }
    assert detect_generation_stall(
        stats,
        track,
        now=now,
        gpu_saturated=True,
    ) is True


def test_tick_gpu_relief_unloads_idle_engine(monkeypatch):
    cfg = {
        'hardware_settings': {'gpu_relief_enabled': True},
        'servers': [
            {
                'id': 'big',
                'enabled': True,
                'host': '127.0.0.1',
                'port': 8091,
                'api_url': 'http://127.0.0.1:8091/v1',
                'model_id': 'big-model',
            },
            {
                'id': 'small',
                'enabled': True,
                'host': '127.0.0.1',
                'port': 8092,
                'api_url': 'http://127.0.0.1:8092/v1',
                'model_id': 'small-model',
            },
        ],
    }

    def fake_proxy_generating(server_id: str) -> bool:
        return server_id == 'big'

    monkeypatch.setattr('core.inference_stats.is_proxy_generating', fake_proxy_generating)
    monkeypatch.setattr(
        'core.inference_stats.fetch_inference_stats',
        lambda *args, **kwargs: {
            'generating': True,
            'generating_seconds': 120.0,
            'generating_tokens': 0,
            'prefill_tokens': 14000,
        },
    )
    monkeypatch.setattr(
        'core.system_stats.get_system_stats_payload',
        lambda: {
            'gpus': [
                {'index': 0, 'load_percent': 100, 'vram_percent': 97, 'vram_used_gb': 22.0, 'vram_total_gb': 24.0},
                {'index': 1, 'load_percent': 40, 'vram_percent': 50, 'vram_used_gb': 12.0, 'vram_total_gb': 24.0},
            ],
        },
    )
    monkeypatch.setattr(
        'core.gpu_relief.count_loaded_console_engines',
        lambda cfg, exclude_server_id=None: (
            2,
            [
                {'id': 'big', 'estimated_gb': 17.0, 'gpu_index': 0},
                {'id': 'small', 'estimated_gb': 8.0, 'gpu_index': 0},
            ],
        ),
    )

    unloaded: list[str] = []

    def fake_unload(server_id: str, *, cfg=None):
        unloaded.append(server_id)
        return {'success': True, 'unloaded': True}

    monkeypatch.setattr('core.gpu_relief.unload_console_engine_for_relief', fake_unload)
    monkeypatch.setattr('core.gpu_relief.save_config', lambda cfg: None)

    with monkeypatch.context() as patch:
        patch.setattr('core.gpu_relief._STALL_TRACK', {'big': {
            'last_prefill': 14000,
            'last_prefill_at': time.time() - 40.0,
            'stall_since': time.time() - 40.0,
        }})
        result = tick_gpu_relief(cfg)

    assert result['actions']
    assert unloaded == ['small']
    assert result['actions'][0]['alternate_gpu'] == 1
