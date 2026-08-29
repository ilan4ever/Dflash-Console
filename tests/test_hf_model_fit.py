from __future__ import annotations

from core.hf_model_fit import assess_hf_model_fit, quant_sizes_gb


def test_quant_sizes_gb_sums_shards():
    files = [
        {'filename': 'model-00001-of-00003.gguf', 'size_bytes': 5_000_000},
        {'filename': 'model-00002-of-00003.gguf', 'size_bytes': 8_000_000_000},
        {'filename': 'model-00003-of-00003.gguf', 'size_bytes': 9_000_000_000},
    ]
    sizes = quant_sizes_gb(files)
    assert len(sizes) == 1
    assert sizes[0] > 15.0


def test_assess_hf_model_fit_true_when_quant_fits():
    row = {'id': 'org/example', 'has_gguf': True, 'accelerator_only': False, 'size_gb': 3.73}
    files = [
        {'filename': 'tiny-q4.gguf', 'size_bytes': 4_000_000_000},
        {'filename': 'huge-q8.gguf', 'size_bytes': 120_000_000_000},
    ]
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row, gguf_files=files)
    assert result['fits_machine'] is True
    assert result['best_fit_quant_gb'] == 3.73


def test_assess_hf_model_fit_false_when_displayed_quant_too_large():
    row = {'id': 'org/example', 'has_gguf': True, 'size_gb': 120.0}
    files = [
        {'filename': 'tiny-q4.gguf', 'size_bytes': 4_000_000_000},
        {'filename': 'huge-q8.gguf', 'size_bytes': 120_000_000_000},
    ]
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row, gguf_files=files)
    assert result['fits_machine'] is False
    assert result['fits_machine_reason'] == 'too_large'


def test_machine_fit_budget_uses_largest_single_gpu(monkeypatch):
    from core.hf_model_fit import machine_fit_budget_gb

    monkeypatch.setattr(
        'core.hf_model_fit._gpu_snapshot',
        lambda _cfg: [
            {'index': 0, 'vram_gb': 24.0, 'vram_free_gb': 20.0},
            {'index': 1, 'vram_gb': 24.0, 'vram_free_gb': 22.0},
        ],
    )
    machine_fit_budget_gb.__globals__['_budget_cache'] = None
    machine_fit_budget_gb.__globals__['_budget_cache_at'] = 0.0
    budget = machine_fit_budget_gb()
    assert budget['fits_budget_gb'] < 24.0
    assert budget['fits_budget_multi_gpu_gb'] > budget['fits_budget_gb']


def test_assess_hf_model_fit_true_for_small_accelerator_repo():
    row = {'id': 'org/dflash-draft', 'accelerator_only': True, 'catalog_ready_to_load': False, 'size_gb': 0.22}
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row, gguf_files=[{'filename': 'draft.gguf', 'size_bytes': 220_000_000}])
    assert result['fits_machine'] is True
    assert result['fits_machine_reason'] == 'fits'


def test_assess_hf_model_fit_uses_small_size_for_accelerator_search_row():
    row = {'id': 'org/dflash-draft', 'accelerator_only': True, 'size_gb': 0.22}
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row)
    assert result['fits_machine'] is True
    assert result['fits_machine_reason'] == 'fits'


def test_assess_hf_model_fit_true_for_small_gguf_search_row():
    row = {'id': 'org/whisper-gguf', 'has_gguf': True, 'size_gb': 0.46}
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row)
    assert result['fits_machine'] is True
    assert result['fits_machine_reason'] == 'fits'


def test_assess_hf_model_fit_true_for_small_embedding_repo():
    row = {'id': 'sentence-transformers/all-MiniLM-L6-v2', 'has_gguf': False, 'size_gb': 0.02}
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row)
    assert result['fits_machine'] is True
    assert result['fits_machine_reason'] == 'fits'


def test_assess_hf_model_fit_uncertain_when_repo_size_unknown():
    row = {'id': 'BAAI/bge-m3', 'has_gguf': False, 'size_gb': 0.0}
    result = assess_hf_model_fit(row)
    assert result['fits_machine'] is False
    assert result['fits_machine_uncertain'] is True


def test_assess_hf_model_fit_true_for_bge_m3_download_files():
    row = {'id': 'BAAI/bge-m3', 'has_gguf': False, 'size_gb': 0.0}
    files = [
        {'filename': 'pytorch_model.bin', 'size_bytes': 2_280_000_000},
        {'filename': 'config.json', 'size_bytes': 1200},
    ]
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row, download_files=files)
    assert result['fits_machine'] is True
    assert result['fits_machine_reason'] == 'fits'
    assert result['smallest_quant_gb'] == 2.12


def test_assess_hf_model_fit_true_for_tiny_tts_repo():
    row = {'id': 'hexgrad/Kokoro-82M', 'has_gguf': False, 'size_gb': 0.0}
    files = [{'filename': 'model.pth', 'size_bytes': 28_264_709}]
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row, download_files=files)
    assert result['fits_machine'] is True
    assert result['fits_machine_reason'] == 'fits'
    row = {
        'id': 'org/target-dflash-gguf',
        'accelerator_only': True,
        'catalog_ready_to_load': True,
    }
    files = [{'filename': 'target-q4.gguf', 'size_bytes': 1_000_000_000}]
    with __import__('unittest').mock.patch(
        'core.hf_model_fit.machine_fit_budget_gb',
        return_value={'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1},
    ):
        result = assess_hf_model_fit(row, gguf_files=files)
    assert result['fits_machine'] is True
