import core.memory_guardrails as guardrails


def test_assess_load_blocks_when_max_vram_exceeded(monkeypatch):
    server = {
        'id': 'demo',
        'model_id': 'demo',
        'context_size': 8192,
        'load_settings': {'gpu_layers': 99},
    }
    cfg = {'hardware_settings': {'max_vram_usage_gb': 1.0}}
    monkeypatch.setattr(
        guardrails,
        '_load_plan',
        lambda *args, **kwargs: {
            'gpu_count': 1,
            'allocations': [{'required_gb': 2.0, 'vram_free_gb': 10.0}],
            'fits': True,
            'usage_ratio': 0.2,
            'gpu_required_gb': 2.0,
        },
    )
    plan = guardrails.assess_load(server, cfg)
    assert plan['level'] == 'block'
    assert 'max_vram_usage_gb' in plan['message']


def test_assess_load_allows_when_max_vram_not_set(monkeypatch):
    server = {
        'id': 'demo',
        'model_id': 'demo',
        'context_size': 8192,
        'load_settings': {'gpu_layers': 99},
    }
    cfg = {'hardware_settings': {'max_vram_usage_gb': 0}}
    monkeypatch.setattr(
        guardrails,
        '_load_plan',
        lambda *args, **kwargs: {
            'gpu_count': 1,
            'allocations': [{'required_gb': 2.0, 'vram_free_gb': 10.0}],
            'fits': True,
            'usage_ratio': 0.2,
            'gpu_required_gb': 2.0,
        },
    )
    plan = guardrails.assess_load(server, cfg)
    assert plan['level'] == 'ok'


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


def test_assess_load_rebalances_even_split_when_second_gpu_is_occupied(monkeypatch):
    monkeypatch.setattr(
        guardrails,
        '_load_components',
        lambda server, cfg: {'target_gb': 22.43, 'draft_gb': 1.07},
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
                'vram_free_gb': 23.4,
            },
            {
                'index': 1,
                'name': 'NVIDIA TITAN RTX',
                'display_name': 'TITAN',
                'vram_gb': 24.0,
                'vram_free_gb': 13.2,
            },
        ],
    )

    result = guardrails.assess_load(
        {
            'id': 'qwen3-8-27b-q6-k-l-dflash',
            'model_id': 'qwen3.8-27b-q6-k-l',
            'gpu_device': 'auto',
            'context_size': 32768,
            'load_settings': {'gpu_layers': 99},
        },
        {
            'hardware_settings': {
                'gpu_strategy': 'split_evenly',
                'enabled_gpu_indices': [0, 1],
                'offload_kv_cache_to_gpu': True,
            },
        },
    )

    assert result['level'] != 'block'
    assert result['fits'] is True
    assert result['split_mode'] == 'layer'
    shares = [float(part) for part in str(result['tensor_split']).split(',') if part.strip()]
    assert len(shares) == 2
    assert shares[0] > shares[1]

