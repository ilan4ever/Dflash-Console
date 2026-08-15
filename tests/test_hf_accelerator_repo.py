from core.huggingface import _is_accelerator_only_repo, _summaries_from_models


def test_accelerator_only_repo_detects_dflash_gguf_files():
    siblings = [
        {'rfilename': 'Qwen3.6-27B-DFlash-Q4_K_M.gguf', 'size': 1_030_000_000},
        {'rfilename': 'Qwen3.6-27B-DFlash-Q8_0.gguf', 'size': 1_720_000_000},
    ]
    assert _is_accelerator_only_repo(siblings, repo_id='user/Qwen3.6-27B-DFlash-GGUF', size_gb=0.96) is True


def test_accelerator_only_repo_false_for_full_target_gguf():
    siblings = [
        {'rfilename': 'Kwaipilot_KAT-Coder-V2.5-Dev-Q4_K_M.gguf', 'size': 21_400_000_000},
        {'rfilename': 'Kwaipilot_KAT-Coder-V2.5-Dev-IQ2_XXS.gguf', 'size': 9_800_000_000},
    ]
    assert _is_accelerator_only_repo(siblings, repo_id='bartowski/Kwaipilot', size_gb=19.92) is False


def test_accelerator_only_repo_false_for_large_self_accelerating_gguf():
    siblings = [
        {
            'rfilename': 'Ornith-1.0-35B-ROCmFP4-STRIX_LEAN-DFLASH-Q4_K_M.gguf',
            'size': int(18.0 * 1024 ** 3),
        },
    ]
    assert _is_accelerator_only_repo(
        siblings,
        repo_id='gsrunion/Ornith-1.0-35B-ROCmFP4-STRIX_LEAN-DFLASH-GGUF',
        size_gb=18.0,
    ) is False


def test_accelerator_only_repo_false_for_large_repo_name_without_siblings():
    assert _is_accelerator_only_repo(
        None,
        repo_id='paragon-of-brah/Ornith-1.0-397B-DFLASH-GGUF',
        size_gb=None,
    ) is False


def test_search_summary_uses_hf_tree_size_for_self_accelerating_repo(monkeypatch):
    repo_id = 'gsrunion/Ornith-1.0-35B-ROCmFP4-STRIX_LEAN-DFLASH-GGUF'
    filename = 'Ornith-1.0-35B-STRIX_LEAN-DFLASH.gguf'
    raw = {
        'id': repo_id,
        'author': 'gsrunion',
        'downloads': 1,
        'siblings': [{'rfilename': filename}],
        'tags': ['gguf', 'dflash'],
    }
    monkeypatch.setattr(
        'core.huggingface._resolve_repo_tree',
        lambda repo, siblings=None: [{'path': filename, 'size': int(18.0 * 1024 ** 3)}],
    )

    row = _summaries_from_models([raw], enrich_sizes=True)[0]

    assert row['size_gb'] == 18.0
    assert row['accelerator_only'] is False
