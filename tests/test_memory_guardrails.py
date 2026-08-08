import core.memory_guardrails as guardrails


def test_assess_load_blocks_single_gpu_when_model_exceeds_free_vram(monkeypatch):
    monkeypatch.setattr(
        guardrails,
        '_load_components',
        lambda server, cfg: {'target_gb': 68.09, 'draft_gb': 0.0},
    )
    monkeypatch.setattr(
        guardrails,
        '_gpu_snapshot',
        lambda cfg: [
            {
                'index': 0,
                'name': 'NVIDIA GeForce RTX 4090 D',
                'display_name': 'RTX 4090',
                'vram_gb': 48.0,
                'vram_free_gb': 30.8,
            },
            {
                'index': 1,
                'name': 'NVIDIA TITAN RTX',
                'display_name': 'TITAN',
                'vram_gb': 24.0,
                'vram_free_gb': 24.0,
            },
        ],
    )

    result = guardrails.assess_load(
        {
            'id': 'large-model',
            'model_id': 'large-model',
            'gpu_device': 'auto',
            'context_size': 65536,
            'load_settings': {'gpu_layers': 99},
        },
        {
            'hardware_settings': {
                'gpu_strategy': 'single_largest',
                'enabled_gpu_indices': [0, 1],
                'offload_kv_cache_to_gpu': True,
            },
        },
    )

    assert result['level'] == 'block'
    assert 'one GPU only' in result['message']
    assert result['main_gpu'] == 0
    assert result['target_gb'] == 68.09

