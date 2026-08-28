from __future__ import annotations

from core.gpu_devices import estimate_model_vram_gb, resolve_auto_gpu_launch


def test_estimate_31b_needs_more_than_12b():
    assert estimate_model_vram_gb('gemma-4-31b-it-dflash', context_size=65536) > estimate_model_vram_gb(
        'gemma-4-12b-it-qat',
        context_size=65536,
    )


def test_single_largest_prefers_4090_over_titan():
    gpus = [
        {'index': 0, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 24.0, 'vram_free_gb': 22.0},
        {'index': 1, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 23.0},
    ]
    launch = resolve_auto_gpu_launch(
        'gemma-4-12b-it-qat',
        gpus,
        {'gpu_strategy': 'single_largest'},
        context_size=8192,
    )
    assert launch['main_gpu'] == 0
    assert launch['split_mode'] == 'none'


def test_spill_to_titan_when_4090_full():
    gpus = [
        {'index': 0, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 24.0, 'vram_free_gb': 1.5},
        {'index': 1, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 22.0},
    ]
    launch = resolve_auto_gpu_launch(
        'gemma-4-12b-it-qat',
        gpus,
        {'gpu_strategy': 'single_largest'},
        context_size=65536,
    )
    assert launch['main_gpu'] == 1
    assert launch['split_mode'] == 'none'


def test_never_layer_split_on_single_largest():
    gpus = [
        {'index': 0, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 24.0, 'vram_free_gb': 20.0},
        {'index': 1, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 22.0},
    ]
    launch = resolve_auto_gpu_launch(
        'gemma-4-31b-it-dflash',
        gpus,
        {'gpu_strategy': 'single_largest'},
        context_size=65536,
    )
    assert launch['split_mode'] == 'none'
    assert not launch.get('tensor_split')


def test_even_split_rebalances_when_titan_cannot_take_half():
    gpus = [
        {'index': 0, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 48.0, 'vram_free_gb': 23.4},
        {'index': 1, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 13.2},
    ]
    launch = resolve_auto_gpu_launch(
        'qwen3.8-27b-q6-k-l',
        gpus,
        {'gpu_strategy': 'split_evenly', 'enabled_gpu_indices': [0, 1]},
        context_size=32768,
        required_gb=25.1,
    )
    assert launch['split_mode'] == 'layer'
    shares = [float(part) for part in str(launch['tensor_split']).split(',') if part.strip()]
    assert shares[0] > 0.55
    assert shares[1] < 0.45


def test_split_evenly_keeps_12b_on_fastest_gpu():
    gpus = [
        {'index': 0, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 22.0},
        {'index': 1, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 24.0, 'vram_free_gb': 21.0},
    ]
    launch = resolve_auto_gpu_launch(
        'gemma-4-12b-it-q4-k-m-dflash',
        gpus,
        {'gpu_strategy': 'split_evenly', 'enabled_gpu_indices': [0, 1]},
        context_size=8192,
    )
    assert launch['main_gpu'] == 1
    assert launch['split_mode'] == 'none'
    assert not launch.get('tensor_split')


def test_split_evenly_biases_required_split_toward_fastest_gpu():
    gpus = [
        {'index': 0, 'name': 'NVIDIA TITAN RTX', 'vram_gb': 24.0, 'vram_free_gb': 12.0},
        {'index': 1, 'name': 'NVIDIA GeForce RTX 4090 D', 'vram_gb': 24.0, 'vram_free_gb': 20.0},
    ]
    launch = resolve_auto_gpu_launch(
        'gemma-4-31b-it-dflash',
        gpus,
        {'gpu_strategy': 'split_evenly', 'enabled_gpu_indices': [0, 1]},
        context_size=65536,
        required_gb=22.0,
    )
    assert launch['split_mode'] == 'layer'
    assert launch['main_gpu'] == 1
    shares = [float(part) for part in str(launch['tensor_split']).split(',') if part.strip()]
    assert shares[1] > shares[0]
