from __future__ import annotations

from core.huggingface import _gguf_files, _preferred_gguf_file, _preferred_gguf_size, _quant_rank


def test_quant_rank_prefers_q4_k_m():
    assert _quant_rank('model-Q4_K_M.gguf') < _quant_rank('model-IQ2_XXS.gguf')
    assert _quant_rank('model-Q4_K_M.gguf') < _quant_rank('model-Q4_K_S.gguf')
    assert _quant_rank('model-F16.gguf') > _quant_rank('model-Q4_K_M.gguf')


def test_preferred_gguf_file_picks_q4_k_m():
    siblings = [
        {'rfilename': 'Kwaipilot_KAT-Coder-V2.5-Dev-IQ2_XXS.gguf', 'size': 9_800_000_000},
        {'rfilename': 'Kwaipilot_KAT-Coder-V2.5-Dev-Q4_K_M.gguf', 'size': 21_400_000_000},
        {'rfilename': 'Kwaipilot_KAT-Coder-V2.5-Dev-F16.gguf', 'size': 39_700_000_000},
    ]
    files = _gguf_files(siblings)
    preferred = _preferred_gguf_file(files)
    assert preferred is not None
    assert preferred['filename'].endswith('Q4_K_M.gguf')

    size_gb, size_label = _preferred_gguf_size(siblings)
    assert size_gb is not None
    assert 'GB' in size_label
    assert size_gb < 30
