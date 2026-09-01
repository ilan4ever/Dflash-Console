from __future__ import annotations

from unittest.mock import patch

from core.huggingface import (
    _is_console_supported_model,
    _is_repo_id_query,
    _search_supported_models,
)


def test_supported_model_accepts_gguf_rows():
    row = {
        'id': 'org/model-GGUF',
        'has_gguf': True,
        'modality': 'llm',
        'runnable': True,
    }
    assert _is_console_supported_model(row) is True


def test_supported_model_rejects_full_model_only_llm():
    row = {
        'id': 'org/model',
        'has_gguf': False,
        'modality': 'llm',
        'downloadable': True,
        'has_files': True,
        'tags': ['safetensors', 'transformers'],
    }
    assert _is_console_supported_model(row) is False


def test_supported_model_accepts_speech_repo():
    row = {
        'id': 'org/whisper-model',
        'has_gguf': False,
        'modality': 'speech-to-text',
        'downloadable': True,
        'has_files': True,
    }
    assert _is_console_supported_model(row) is True


def test_repo_id_query_detection():
    assert _is_repo_id_query('Kwaipilot/KAT-Coder-V2.5-Dev') is True
    assert _is_repo_id_query('gemma') is False
    assert _is_repo_id_query('') is False


def test_supported_repo_query_uses_fast_path():
    sample = {
        'id': 'Kwaipilot/KAT-Coder-V2.5-Dev',
        'has_gguf': False,
        'modality': 'llm',
        'runtime_id': 'transformers',
        'downloadable': True,
        'has_files': True,
    }
    with patch('core.huggingface._lookup_hf_repo_models', return_value=[sample]) as lookup:
        with patch('core.huggingface._fetch_repo_summary_light', return_value=None):
            with patch('core.huggingface._is_console_supported_model', return_value=True):
                payload = _search_supported_models('Kwaipilot/KAT-Coder-V2.5-Dev', limit=5)
    lookup.assert_called_once()
    assert payload['success'] is True
    assert payload['models'][0]['id'] == 'Kwaipilot/KAT-Coder-V2.5-Dev'


def test_supported_repo_query_prefers_light_summary():
    sample = {
        'id': 'Kwaipilot/KAT-Coder-V2.5-Dev',
        'has_gguf': True,
        'modality': 'llm',
        'runnable': True,
    }
    with patch('core.huggingface._fetch_repo_summary_light', return_value=sample) as light:
        with patch('core.huggingface._lookup_hf_repo_models') as lookup:
            with patch('core.huggingface._is_console_supported_model', return_value=True):
                payload = _search_supported_models('Kwaipilot/KAT-Coder-V2.5-Dev', limit=5)
    light.assert_called_once()
    lookup.assert_not_called()
    assert payload['models'][0]['id'] == sample['id']


def test_search_models_repo_id_uses_direct_lookup(monkeypatch):
    from core.huggingface import search_models

    sample = {'id': 'nvidia/Qwen3.6-35B-A3B-NVFP4', 'downloads': 1000, 'has_files': True}
    monkeypatch.setattr('core.huggingface._fetch_repo_summary_light', lambda *a, **k: sample)
    monkeypatch.setattr(
        'core.huggingface._lookup_hf_repo_models',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('lookup should be skipped')),
    )
    monkeypatch.setattr('core.huggingface._finalize_search_models', lambda models, **k: models)
    payload = search_models('nvidia/Qwen3.6-35B-A3B-NVFP4', category='all', enrich_sizes=False)
    assert payload['success'] is True
    assert payload['models'][0]['id'] == sample['id']


def test_supported_repo_query_skips_size_enrich_when_files_present(monkeypatch):
    from core.huggingface import _search_supported_repo_query

    sample = {
        'id': 'bartowski/ATH-MaaS_OvisOCR2-GGUF',
        'has_gguf': True,
        'gguf_files': [{'filename': 'ATH-MaaS_OvisOCR2-Q8_0.gguf', 'size_gb': 8.0}],
        'download_files': [{'filename': 'ATH-MaaS_OvisOCR2-Q8_0.gguf', 'size_gb': 8.0}],
        'size_gb': 8.0,
        'size_label': '8 GB',
    }

    def fail_enrich(*args, **kwargs):
        raise AssertionError('size enrich should be skipped')

    monkeypatch.setattr('core.huggingface._lookup_hf_repo_models', lambda *a, **k: [sample])
    monkeypatch.setattr('core.huggingface._is_console_supported_model', lambda row: True)
    monkeypatch.setattr('core.huggingface._enrich_summaries_sizes', fail_enrich)
    monkeypatch.setattr('core.hf_model_fit.annotate_hf_models_fit', lambda models, **k: None)

    payload = _search_supported_repo_query('bartowski/ATH-MaaS_OvisOCR2-GGUF', limit=5, sort='downloads')
    assert payload['success'] is True
    assert payload['models'][0]['id'] == sample['id']
