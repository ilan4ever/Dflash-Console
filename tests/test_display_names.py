from __future__ import annotations

from core.display_names import build_model_catalog


def test_build_model_catalog_gemma_12b_q4():
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'profile': 'gemma-12-dflash',
        'model_id': 'gemma-4-12b-it-qat',
    }
    stack = [
        {'role': 'alias', 'id': 'gemma-4-12b-it-qat', 'source': 'api'},
        {
            'role': 'target',
            'id': 'gemma-4-12b-it-qat-q4-0',
            'path': r'C:\Users\ilanp\.lmstudio\models\google\gemma-4-12B-it-qat-q4_0-gguf\gemma-4-12B_q4_0-it.gguf',
            'source': 'lmstudio',
            'label': 'Gemma 4 12B (target)',
        },
        {
            'role': 'draft-dflash',
            'id': 'gemma-4-12b-it-dflash-q4-k-m',
            'path': r'C:\dev\Dflash\models\gemma-draft\gemma-4-12B-it-DFlash-Q4_K_M.gguf',
            'source': 'dflash',
        },
    ]
    catalog = build_model_catalog(server, stack)
    assert catalog['display_name'] == 'Gemma 4 12B it qat dflash'
    assert catalog['display_name_full'] == 'Gemma 4 12B it qat dflash Q4'
    assert 'display_name_ui' not in catalog
    assert catalog['source_suffix'] == 'it qat'
    assert catalog['lab'] == 'Google'
    assert catalog['parameter_size'] == '12B'
    assert catalog['variant'] == 'it-qat'


def test_build_model_catalog_qwen_27b():
    server = {
        'id': 'qwen-dflash',
        'label': 'Qwen 27B',
        'profile': 'qwen-dflash',
        'model_id': 'qwen3.5-27b-dflash',
    }
    stack = [
        {'role': 'alias', 'id': 'qwen3.5-27b-dflash', 'source': 'api'},
        {
            'role': 'target',
            'id': 'qwen3.5-27b-q4-k-m',
            'path': r'C:\dev\Dflash\models\Qwen3.5-27B-Q4_K_M.gguf',
            'source': 'dflash',
        },
    ]
    catalog = build_model_catalog(server, stack)
    assert catalog['display_name'] == 'Qwen 3.5 27B dflash'
    assert catalog['display_name_full'] == 'Qwen 3.5 27B dflash Q4'
    assert catalog['source_suffix'] == 'dflash'
    assert catalog['lab'] == 'Qwen'
