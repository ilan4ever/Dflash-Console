from __future__ import annotations

from core.hf_catalog_draft import catalog_draft_primary_row


def test_draft_primary_when_card_size_is_sidecar_for_27b():
    row = {
        'id': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF',
        'has_gguf': True,
        'size_gb': 0.86,
        'size_bytes': 16_546_361_507,
        'accelerator_only': False,
    }
    assert catalog_draft_primary_row(row) is True


def test_not_draft_when_full_quant_size_on_card():
    row = {
        'id': 'Bucoid/Qwen3.8-27B-Uncensored-IQ4-XS-MTP-16GB-VRAM-GGUF',
        'has_gguf': True,
        'size_gb': 12.95,
        'accelerator_only': False,
    }
    assert catalog_draft_primary_row(row) is False


def test_not_draft_for_dflash_accelerator_repo():
    row = {
        'id': 'some/model-dflash-gguf',
        'has_gguf': True,
        'size_gb': 0.5,
        'accelerator_only': True,
    }
    assert catalog_draft_primary_row(row) is False
