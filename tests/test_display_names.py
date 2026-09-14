from __future__ import annotations

from core.display_names import (
    build_engine_client_metadata,
    build_model_catalog,
    disambiguate_engine_display_names,
    friendly_stack_label,
    label_with_quant_suffix,
)


def test_label_with_quant_suffix_appends_brackets():
    base = 'Qwen 3.8 27B gsq rco mtp — DFlash 2'
    out = label_with_quant_suffix(
        base,
        quant='IQ3_XXS',
        filename='Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf',
    )
    assert out == f'{base} (IQ3_XXS)'
    assert label_with_quant_suffix(out, quant='IQ3_XXS') == out


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


def test_disambiguate_colliding_qwen_iq_profiles():
    shared_stack_tail = {
        'profile': 'qwen3-8-27b-gsq-rco-iq3-xxs-mtp-dflash',
        'label': 'Qwen 27B',
    }
    iq3_server = {
        'id': 'qwen3-8-27b-gsq-rco-iq3-xxs-mtp-dflash',
        'model_id': 'qwen3-8-27b-gsq-rco-iq3-xxs-mtp-dflash',
        **shared_stack_tail,
    }
    iq2_server = {
        'id': 'qwen3-8-27b-gsq-rco-iq2-s-mtp-dflash',
        'model_id': 'qwen3-8-27b-gsq-rco-iq2-s-mtp-dflash',
        **shared_stack_tail,
    }
    iq3_stack = [
        {'role': 'alias', 'id': iq3_server['model_id'], 'source': 'api'},
        {
            'role': 'target',
            'id': 'qwen3.8-27b-gsq-rco-iq3-xxs-mtp',
            'path': r'C:\models\Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf',
            'source': 'dflash',
        },
        {
            'role': 'draft-dflash',
            'id': 'draft-iq3',
            'path': r'C:\models\draft-iq3.gguf',
            'source': 'dflash',
        },
    ]
    iq2_stack = [
        {'role': 'alias', 'id': iq2_server['model_id'], 'source': 'api'},
        {
            'role': 'target',
            'id': 'qwen3.8-27b-gsq-rco-iq2-s-mtp',
            'path': r'C:\models\Qwen3.8-27B-GSQ-RCO-IQ2_S-mtp.gguf',
            'source': 'dflash',
        },
        {
            'role': 'draft-dflash',
            'id': 'draft-iq2',
            'path': r'C:\models\draft-iq2.gguf',
            'source': 'dflash',
        },
    ]
    rows = [
        {**build_engine_client_metadata(iq3_server, iq3_stack), 'id': iq3_server['id'], 'model_id': iq3_server['model_id']},
        {**build_engine_client_metadata(iq2_server, iq2_stack), 'id': iq2_server['id'], 'model_id': iq2_server['model_id']},
    ]
    assert rows[0]['display_name'] == rows[1]['display_name']
    disambiguate_engine_display_names(rows)
    assert rows[0]['display_name'] != rows[1]['display_name']
    assert 'IQ3' in rows[0]['display_name'].upper()
    assert 'IQ2' in rows[1]['display_name'].upper()


def test_disambiguate_colliding_gemma_profiles_by_engine_id():
    base = {
        'profile': 'gemma-4-31b-dflash',
        'label': 'Gemma 31B',
        'model_id': 'gemma-4-31b-q4-0-it-dflash',
    }
    stack = [
        {'role': 'alias', 'id': 'gemma-4-31b-q4-0-it-dflash', 'source': 'api'},
        {
            'role': 'target',
            'id': 'gemma-4-31b-q4-0-it',
            'path': r'C:\models\gemma-4-31B_q4_0-it.gguf',
            'source': 'lmstudio',
        },
        {
            'role': 'draft-dflash',
            'id': 'draft-a',
            'path': r'C:\models\draft-a.gguf',
            'source': 'dflash',
        },
    ]
    stack_b = [
        *stack[:2],
        {
            'role': 'draft-dflash',
            'id': 'draft-b',
            'path': r'C:\models\draft-b.gguf',
            'source': 'dflash',
        },
    ]
    s1 = {'id': 'gemma-4-31b-q4-0-it-dflash', **base}
    s2 = {'id': 'gemma-4-31b-q4-0-it-dflash-2', **base}
    rows = [
        {**build_engine_client_metadata(s1, stack), 'id': s1['id'], 'model_id': s1['model_id']},
        {**build_engine_client_metadata(s2, stack_b), 'id': s2['id'], 'model_id': s2['model_id']},
    ]
    disambiguate_engine_display_names(rows)
    assert rows[0]['display_name'] != rows[1]['display_name']
