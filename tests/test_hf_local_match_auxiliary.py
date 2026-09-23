from __future__ import annotations

from core.hf_local_match import is_auxiliary_gguf_filename


def test_draft_gguf_is_auxiliary():
    assert is_auxiliary_gguf_filename('Qwen3.8-27B-Uncensored-draft-Q4_0.gguf')
    assert is_auxiliary_gguf_filename('model_draft.gguf')


def test_full_quant_is_not_auxiliary():
    assert not is_auxiliary_gguf_filename('Qwen3.8-27B-Uncensored-Q4_K_M.gguf')
    assert not is_auxiliary_gguf_filename('Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf')


def test_mmproj_paths_are_auxiliary():
    assert is_auxiliary_gguf_filename('mmproj/Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf')
