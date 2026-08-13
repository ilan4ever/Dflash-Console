from __future__ import annotations

from core.huggingface import _is_console_supported_model


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
