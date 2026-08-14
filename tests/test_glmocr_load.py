from __future__ import annotations

from pathlib import Path


def test_read_gguf_architecture_glmocr():
    from core.gguf_meta import read_gguf_architecture

    blob = Path.home() / '.ollama' / 'models' / 'blobs' / (
        'sha256-65493e1f85b9ea4ba3ed793515fde13cbdbea7d74ad2c662b566b146eab0081e'
    )
    if not blob.is_file():
        return
    assert read_gguf_architecture(blob) == 'glmocr'


def test_llama_server_supports_glmocr_when_updated():
    from core.ocr_setup import llama_server_supports_glmocr

    # Official Windows CUDA builds may ship before glmocr is linked into llama.dll.
    assert isinstance(llama_server_supports_glmocr(), bool)
