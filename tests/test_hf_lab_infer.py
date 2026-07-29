from __future__ import annotations

from core.huggingface import infer_model_lab


def test_infer_model_lab_from_repo_name():
    assert infer_model_lab(repo_id='google/gemma-2-2b-gguf', author='google') == 'Google'
    assert infer_model_lab(repo_id='Alittlehammmer/Qwen3.6-35B-A3B-DFlash-GGUF', author='Alittlehammmer') == 'Qwen'
    assert infer_model_lab(repo_id='z-lab/Qwen3.6-35B-A3B-DFlash', author='z-lab') == 'Qwen'


def test_infer_model_lab_from_base_model():
    assert infer_model_lab(
        repo_id='Alittlehammmer/Qwen3.6-35B-A3B-DFlash-GGUF-llama.cpp',
        author='Alittlehammmer',
        base_model='z-lab/Qwen3.6-35B-A3B-DFlash',
    ) == 'Qwen'
