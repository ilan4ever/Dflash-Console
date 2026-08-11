from __future__ import annotations

from core.huggingface import infer_model_lab


def test_infer_model_lab_from_repo_name():
    assert infer_model_lab(repo_id='google/gemma-2-2b-gguf', author='google') == 'Google'
    assert infer_model_lab(repo_id='Alittlehammmer/Qwen3.6-35B-A3B-DFlash-GGUF', author='Alittlehammmer') == 'Qwen'
    # Publisher-first: z-lab is the author, so its models are labeled z-lab even
    # though the base model is a Qwen (fixes microsoft/VibeVoice -> Microsoft).
    assert infer_model_lab(repo_id='z-lab/Qwen3.6-35B-A3B-DFlash', author='z-lab') == 'z-lab'


def test_infer_model_lab_author_alias_wins_over_base_model():
    # microsoft/VibeVoice-Realtime-0.5B is built on Qwen but must be labeled
    # Microsoft (the publisher) so the lab filter works.
    assert infer_model_lab(
        repo_id='microsoft/VibeVoice-Realtime-0.5B',
        author='microsoft',
        tags=['base_model:Qwen/Qwen2.5-0.5B', 'text-to-speech'],
        title='VibeVoice-Realtime-0.5B',
    ) == 'Microsoft'


def test_infer_model_lab_from_base_model():
    assert infer_model_lab(
        repo_id='Alittlehammmer/Qwen3.6-35B-A3B-DFlash-GGUF-llama.cpp',
        author='Alittlehammmer',
        base_model='z-lab/Qwen3.6-35B-A3B-DFlash',
    ) == 'Qwen'
