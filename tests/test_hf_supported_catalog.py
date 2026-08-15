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
        with patch('core.huggingface._is_console_supported_model', return_value=True):
            payload = _search_supported_models('Kwaipilot/KAT-Coder-V2.5-Dev', limit=5)
    lookup.assert_called_once()
    assert payload['success'] is True
    assert payload['models'][0]['id'] == 'Kwaipilot/KAT-Coder-V2.5-Dev'
