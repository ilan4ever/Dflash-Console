from __future__ import annotations

from core.display_names import build_model_catalog, friendly_stack_label


def test_friendly_stack_label_keeps_model_name():
    assert friendly_stack_label('Qwen_Qwen3.6-27B-Q4_K_S.gguf') == 'Qwen 3.6 27B D-Flash'
    assert friendly_stack_label('Qwen3.5-27B-Q4_K_M.gguf') == 'Qwen 3.5 27B D-Flash'
    assert friendly_stack_label('gemma-4-31B_q4_0-it.gguf') == 'Gemma 4 31B D-Flash'
    assert friendly_stack_label('Qwen_Qwen3.6-35B-A3B-Q4_K_S.gguf') == 'Qwen 3.6 35B A3B D-Flash'


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
            'path': r'C:\Users\example\.lmstudio\models\google\gemma-4-12B-it-qat-q4_0-gguf\gemma-4-12B_q4_0-it.gguf',
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
    assert catalog['display_name'] == 'Gemma 4 12B it qat — DFlash 1'
    assert catalog['display_name_full'] == 'Gemma 4 12B it qat — DFlash 1 Q4'
    assert catalog['engine_mode'] == 'DFlash 1'
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
    assert catalog['display_name'] == 'Qwen 3.5 27B — DFlash 1'
    assert catalog['display_name_full'] == 'Qwen 3.5 27B — DFlash 1 Q4'
    assert catalog['engine_mode'] == 'DFlash 1'
    assert catalog['source_suffix'] == ''
    assert catalog['lab'] == 'Qwen'


def test_build_model_catalog_uses_qwen38_target_name_over_api_alias():
    server = {
        'id': 'qwen3-8-27b-q6-k-l-dflash',
        'label': 'qwen3 8 27b q6 k l dflash',
        'profile': 'qwen3-8-27b-q6-k-l-dflash',
        'model_id': 'qwen3-8-27b-q6-k-l-dflash',
    }
    stack = [
        {'role': 'alias', 'id': server['model_id'], 'source': 'api'},
        {
            'role': 'target',
            'id': 'qwen3.8-27b-q6-k-l',
            'path': r'C:\dev\Dflash\models\bartowski\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q6_K_L.gguf',
            'source': 'custom',
        },
    ]

    catalog = build_model_catalog(server, stack)

    assert catalog['display_name'] == 'Qwen 3.8 27B — DFlash 1'
    assert catalog['display_name_full'] == 'Qwen 3.8 27B — DFlash 1 Q6'
    assert catalog['engine_mode'] == 'DFlash 1'
    assert catalog['target_filename'] == 'Qwen3.8-27B-Q6_K_L.gguf'
    assert catalog['source_suffix'] == ''


def test_build_model_catalog_ar_profile_distinct_from_dflash():
    shared_target = r'C:\dev\Dflash\models\gemma-4-12B_q4_0-it.gguf'
    ar_server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'profile': 'gemma-12-ar',
        'model_id': 'gemma-4-12b-it-qat',
    }
    dflash_server = {
        'id': 'gemma-12b-dflash',
        'label': 'Gemma 12B',
        'profile': 'gemma-12-dflash',
        'model_id': 'gemma-4-12b-it-qat',
    }
    ar_stack = [
        {'role': 'alias', 'id': 'gemma-4-12b-it-qat', 'source': 'api'},
        {'role': 'target', 'id': 'gemma-4-12b-it-qat-q4-0', 'path': shared_target, 'source': 'lmstudio'},
    ]
    dflash_stack = [
        {'role': 'alias', 'id': 'gemma-4-12b-it-qat', 'source': 'api'},
        {'role': 'target', 'id': 'gemma-4-12b-it-qat-q4-0', 'path': shared_target, 'source': 'lmstudio'},
        {
            'role': 'draft-dflash',
            'id': 'gemma-4-12b-it-dflash-q4-k-m',
            'path': r'C:\dev\Dflash\models\gemma-draft\gemma-4-12B-it-DFlash-Q4_K_M.gguf',
            'source': 'dflash',
        },
    ]
    ar_catalog = build_model_catalog(ar_server, ar_stack)
    dflash_catalog = build_model_catalog(dflash_server, dflash_stack)
    assert ar_catalog['display_name'] == 'Gemma 4 12B it qat — AR'
    assert dflash_catalog['display_name'] == 'Gemma 4 12B it qat — DFlash 1'
    assert ar_catalog['display_name'] != dflash_catalog['display_name']
