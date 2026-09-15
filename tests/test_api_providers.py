"""Tests for OpenAI-compatible API providers (config, catalog, active models)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import HTTPException

from core.api_providers import (
    DEEPSEEK_DEFAULT_MODELS,
    DEEPSEEK_MODEL_LABELS,
    PROVIDER_CATALOG,
    active_model_ids,
    apply_fetched_models,
    catalog_rows_for_providers,
    chat_completions_url,
    cloud_model_entries,
    default_model_label,
    delete_api_provider,
    ensure_api_providers,
    list_provider_catalog,
    mask_api_key,
    merge_api_provider_patch,
    meta_from_upstream_rows,
    models_endpoint_candidates,
    normalize_api_providers,
    normalize_model_entries,
    public_api_provider,
    redact_api_providers_in_config,
    resolve_cloud_provider_for_model,
    resolve_model_label,
    resolve_provider_api_key,
    upsert_api_providers,
)


def test_normalize_empty_does_not_auto_inject():
    providers = normalize_api_providers([])
    assert providers == []


def test_normalize_map_form_migrates_string_models_to_active_objects():
    providers = normalize_api_providers({
        'deepseek': {
            'enabled': True,
            'api_key': 'sk-test',
            'models': ['deepseek-flash'],
        }
    })
    deepseek = next(row for row in providers if row['id'] == 'deepseek')
    assert deepseek['enabled'] is True
    assert deepseek['api_key'] == 'sk-test'
    assert deepseek['models'] == [{'id': 'deepseek-flash', 'active': True, 'label': 'DeepSeek-V4.1-Flash'}]
    assert active_model_ids(deepseek) == ['deepseek-flash']


def test_normalize_model_entries_legacy_strings_are_active():
    rows = normalize_model_entries(['a', 'b', 'a'])
    assert rows == [{'id': 'a', 'active': True, 'label': 'a'}, {'id': 'b', 'active': True, 'label': 'b'}]


def test_normalize_model_entries_objects_preserve_active():
    rows = normalize_model_entries([
        {'id': 'keep', 'active': True},
        {'id': 'off', 'active': False},
    ])
    assert rows == [
        {'id': 'keep', 'active': True, 'label': 'keep'},
        {'id': 'off', 'active': False, 'label': 'off'},
    ]


def test_catalog_includes_known_openai_compatible_presets():
    catalog = list_provider_catalog(configured_ids={'deepseek'})
    ids = {row['id'] for row in catalog}
    assert {'deepseek', 'openai', 'groq', 'openrouter', 'together', 'fireworks', 'mistral'} <= ids
    deepseek = next(row for row in catalog if row['id'] == 'deepseek')
    assert deepseek['already_configured'] is True
    assert deepseek['default_base_url'] == 'https://api.deepseek.com'
    openai = next(row for row in catalog if row['id'] == 'openai')
    assert openai['default_base_url'] == 'https://api.openai.com/v1'
    assert openai['already_configured'] is False


def test_mask_and_public_view_hides_raw_key():
    provider = normalize_api_providers([{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-abcdefghijklmnop',
        'models': [{'id': 'deepseek-flash', 'active': True}],
    }])[0]
    public = public_api_provider(provider)
    assert 'sk-abcdefghijklmnop' not in str(public)
    assert public['api_key_set'] is True
    assert public['api_key_preview'].endswith('mnop')
    assert mask_api_key('sk-abcdefghijklmnop').endswith('mnop')
    assert public['active_models'] == ['deepseek-flash']


def test_env_override_wins_for_resolve_key(monkeypatch):
    provider = normalize_api_providers([{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-stored',
    }])[0]
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'sk-from-env')
    assert resolve_provider_api_key(provider) == 'sk-from-env'
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    assert resolve_provider_api_key(provider) == 'sk-stored'


def test_cloud_model_entries_only_active_models():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [
            {'id': 'deepseek-flash', 'active': True},
            {'id': 'deepseek-v4-pro', 'active': False},
        ],
    }]}
    ensure_api_providers(cfg)
    entries = cloud_model_entries(cfg)
    assert {row['id'] for row in entries} == {'deepseek-flash'}

    cfg['api_providers'][0]['enabled'] = False
    assert cloud_model_entries(cfg) == []


def test_legacy_string_models_still_advertise():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': ['deepseek-flash', 'deepseek-v4-pro'],
    }]}
    ensure_api_providers(cfg)
    assert {row['id'] for row in cloud_model_entries(cfg)} == {
        'deepseek-flash',
        'deepseek-v4-pro',
    }


def test_resolve_cloud_provider_for_model_ignores_inactive():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [
            {'id': 'deepseek-flash', 'active': True},
            {'id': 'deepseek-v4-pro', 'active': False},
        ],
    }]}
    ensure_api_providers(cfg)
    matched = resolve_cloud_provider_for_model(cfg, 'deepseek-flash')
    assert matched is not None
    assert matched['id'] == 'deepseek'
    assert resolve_cloud_provider_for_model(cfg, 'deepseek-v4-pro') is None
    assert resolve_cloud_provider_for_model(cfg, 'gpt-4o') is None


def test_merge_keeps_key_on_mask_and_omission():
    existing = normalize_api_providers([{
        'id': 'deepseek',
        'api_key': 'sk-keep-me-1234',
        'enabled': False,
        'models': [{'id': 'deepseek-flash', 'active': True}],
    }])[0]
    merged = merge_api_provider_patch(existing, {'id': 'deepseek', 'enabled': True})
    assert merged['api_key'] == 'sk-keep-me-1234'
    assert merged['enabled'] is True
    merged2 = merge_api_provider_patch(existing, {'id': 'deepseek', 'api_key': '********1234'})
    assert merged2['api_key'] == 'sk-keep-me-1234'
    merged3 = merge_api_provider_patch(existing, {'id': 'deepseek', 'api_key': ''})
    assert merged3['api_key'] == ''


def test_chat_completions_url_styles():
    assert chat_completions_url({'base_url': 'https://api.deepseek.com'}) == (
        'https://api.deepseek.com/chat/completions'
    )
    assert chat_completions_url({'base_url': 'https://api.deepseek.com/v1'}) == (
        'https://api.deepseek.com/v1/chat/completions'
    )


def test_models_endpoint_candidates_deepseek_and_openai():
    assert models_endpoint_candidates('https://api.deepseek.com') == [
        'https://api.deepseek.com/models',
        'https://api.deepseek.com/v1/models',
    ]
    assert models_endpoint_candidates('https://api.openai.com/v1') == [
        'https://api.openai.com/v1/models',
    ]
    assert models_endpoint_candidates('https://api.example.com/custom') == [
        'https://api.example.com/custom/models',
        'https://api.example.com/custom/v1/models',
    ]


def test_apply_fetched_models_keeps_previous_active_and_deepseek_defaults():
    provider = normalize_api_providers([{
        'id': 'deepseek',
        'models': [],
    }])[0]
    updated = apply_fetched_models(provider, [
        'deepseek-flash',
        'deepseek-v4-pro',
        'deepseek-reasoner',
    ])
    active = {m['id'] for m in updated['models'] if m['active']}
    assert active == set(DEEPSEEK_DEFAULT_MODELS)
    assert updated['available_models'] == [
        'deepseek-flash',
        'deepseek-v4-pro',
        'deepseek-reasoner',
    ]

    provider2 = normalize_api_providers([{
        'id': 'openai',
        'models': [{'id': 'gpt-4o', 'active': True}],
    }])[0]
    updated2 = apply_fetched_models(provider2, ['gpt-4o', 'gpt-4o-mini'])
    active2 = {m['id']: m['active'] for m in updated2['models']}
    assert active2['gpt-4o'] is True
    assert active2['gpt-4o-mini'] is False


def test_redact_config_strips_raw_keys():
    cfg = {
        'ui_port': 8900,
        'api_providers': [{
            'id': 'deepseek',
            'enabled': True,
            'api_key': 'sk-secret-value',
            'models': [{'id': 'deepseek-flash', 'active': True}],
        }],
    }
    ensure_api_providers(cfg)
    redacted = redact_api_providers_in_config(cfg)
    blob = str(redacted)
    assert 'sk-secret-value' not in blob
    assert redacted['api_providers'][0]['api_key_set'] is True


def test_catalog_rows_for_playground_active_only():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [
            {'id': 'deepseek-flash', 'active': True},
            {'id': 'deepseek-v4-pro', 'active': False},
        ],
    }]}
    rows = catalog_rows_for_providers(cfg)
    assert len(rows) == 1
    assert rows[0]['cloud'] is True
    assert rows[0]['id'] == 'deepseek-flash'
    assert rows[0]['cloud_provider'] == 'deepseek'
    assert rows[0]['source'] == 'deepseek'
    assert rows[0]['source_label'] == 'DeepSeek'
    assert rows[0]['provider'] == 'DeepSeek'
    assert rows[0]['always_ready'] is True
    assert rows[0]['loadable'] is False
    assert rows[0]['path'] == ''
    assert rows[0]['label'] == 'DeepSeek-V4.1-Flash'
    assert rows[0]['chat_model_key'] == 'cloud::deepseek::deepseek-flash'
    assert rows[0]['library_label'] == 'DeepSeek'
    assert rows[0]['owned_by'] == 'deepseek'
    assert rows[0]['publisher'] == 'deepseek'
    assert rows[0]['author'] == 'deepseek'
    assert rows[0]['api'] is True
    assert rows[0]['name'] == 'DeepSeek-V4.1-Flash (API)'
    assert rows[0]['arch'] == 'Cloud API'
    assert rows[0]['family'] == 'Cloud API'
    assert rows[0]['backend'] == 'Cloud API'
    assert rows[0]['runtime_label'] == 'Cloud API'
    assert rows[0]['params'] == 'API'
    assert rows[0]['modified'] == 'Cloud'
    assert rows[0]['size_gb'] is None


def test_delete_api_provider():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [{'id': 'deepseek-flash', 'active': True}],
    }, {
        'id': 'openai',
        'enabled': False,
        'api_key': '',
        'base_url': 'https://api.openai.com/v1',
        'models': [],
    }]}
    ensure_api_providers(cfg)
    assert delete_api_provider(cfg, 'deepseek') is True
    assert [row['id'] for row in cfg['api_providers']] == ['openai']
    assert delete_api_provider(cfg, 'missing') is False


def test_gateway_lists_only_active_cloud_models():
    import asyncio

    from api.gateway import list_models

    cfg = {
        'ui_port': 8900,
        'gateway_port': 8001,
        'gateway_server_id': '',
        'servers': [],
        'runtimes': [],
        'api_providers': [{
            'id': 'deepseek',
            'enabled': True,
            'api_key': 'sk-test',
            'models': [
                {'id': 'deepseek-flash', 'active': True},
                {'id': 'deepseek-v4-pro', 'active': False},
            ],
        }],
    }
    with patch('api.gateway.load_config', return_value=cfg), \
         patch('api.gateway.list_servers', return_value=[]), \
         patch('api.gateway.list_runtimes', return_value=[]), \
         patch('core.gateway_routing.default_gateway_chat_server', side_effect=HTTPException(status_code=503)), \
         patch('api.gateway.default_gateway_chat_server', side_effect=HTTPException(status_code=503)):
        payload = asyncio.run(list_models())
    ids = {row['id'] for row in payload['data']}
    assert 'deepseek-flash' in ids
    assert 'deepseek-v4-pro' not in ids
    deepseek_row = next(row for row in payload['data'] if row['id'] == 'deepseek-flash')
    assert deepseek_row['name'].endswith(' (API)')
    assert deepseek_row['name'] == 'DeepSeek-V4.1-Flash (API)'
    assert deepseek_row['owned_by'] == 'DeepSeek (API)'
    assert deepseek_row['meta']['cloud'] is True
    assert deepseek_row['meta'].get('api') is True or deepseek_row['meta'].get('source') == 'api'
    assert deepseek_row['meta'].get('display_name') == deepseek_row['name']
    assert deepseek_row['meta'].get('model_label') == 'DeepSeek-V4.1-Flash'


def test_upsert_api_providers_persists_active_flags():
    cfg = {'api_providers': []}
    ensure_api_providers(cfg)
    upsert_api_providers(cfg, [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-new',
        'models': [
            {'id': 'deepseek-flash', 'active': True},
            {'id': 'deepseek-v4-pro', 'active': False},
        ],
        'available_models': ['deepseek-flash', 'deepseek-v4-pro'],
    }])
    deepseek = next(row for row in cfg['api_providers'] if row['id'] == 'deepseek')
    assert deepseek['enabled'] is True
    assert deepseek['api_key'] == 'sk-new'
    assert deepseek['models'] == [
        {'id': 'deepseek-flash', 'active': True, 'label': 'DeepSeek-V4.1-Flash'},
        {'id': 'deepseek-v4-pro', 'active': False, 'label': 'DeepSeek-V4-Pro'},
    ]
    assert deepseek['available_models'] == ['deepseek-flash', 'deepseek-v4-pro']


def test_custom_provider_normalize():
    providers = normalize_api_providers([{
        'id': 'my-proxy',
        'label': 'My Proxy',
        'enabled': True,
        'api_key': 'sk-x',
        'base_url': 'https://proxy.example.com/v1',
        'models': [{'id': 'local-model', 'active': True}],
    }])
    assert len(providers) == 1
    assert providers[0]['id'] == 'my-proxy'
    assert providers[0]['base_url'] == 'https://proxy.example.com/v1'
    assert 'anthropic' not in PROVIDER_CATALOG


def test_normalize_does_not_seed_deepseek_defaults_before_fetch():
    providers = normalize_api_providers([{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
    }])
    deepseek = providers[0]
    assert deepseek['models'] == []
    assert deepseek['available_models'] == []
    assert deepseek['models_fetched'] is False


def test_apply_fetched_models_keeps_manual_active_ids_and_meta():
    provider = normalize_api_providers([{
        'id': 'openai',
        'models': [
            {'id': 'gpt-4o', 'active': True},
            {'id': 'my-custom-finetune', 'active': True, 'manual': True},
        ],
    }])[0]
    upstream = [
        {'id': 'gpt-4o', 'owned_by': 'openai', 'object': 'model'},
        {'id': 'gpt-4o-mini', 'owned_by': 'openai'},
    ]
    updated = apply_fetched_models(
        provider,
        ['gpt-4o', 'gpt-4o-mini'],
        upstream=upstream,
    )
    by_id = {m['id']: m for m in updated['models']}
    assert by_id['gpt-4o']['active'] is True
    assert by_id['gpt-4o-mini']['active'] is False
    assert by_id['my-custom-finetune']['active'] is True
    assert by_id['my-custom-finetune']['manual'] is True
    assert updated['available_models'] == ['gpt-4o', 'gpt-4o-mini']
    assert updated['models_fetched'] is True
    assert updated['available_models_meta']['gpt-4o']['owned_by'] == 'openai'
    # Manual id is kept in models checklist but not in available_models (fetched-only cache).
    assert 'my-custom-finetune' not in updated['available_models']


def test_meta_from_upstream_rows_preserves_id_extras():
    meta = meta_from_upstream_rows([
        {'id': 'deepseek-flash', 'owned_by': 'deepseek', 'object': 'model'},
        {'id': '', 'owned_by': 'x'},
        'skip-me',
    ])
    assert list(meta.keys()) == ['deepseek-flash']
    assert meta['deepseek-flash']['owned_by'] == 'deepseek'


def test_merge_patch_persists_models_meta_and_fetched_flag():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [],
    }]}
    ensure_api_providers(cfg)
    upsert_api_providers(cfg, [{
        'id': 'deepseek',
        'models': [
            {'id': 'deepseek-flash', 'active': True},
            {'id': 'custom-ds', 'active': True, 'manual': True},
        ],
        'available_models': ['deepseek-flash'],
        'available_models_meta': {'deepseek-flash': {'owned_by': 'deepseek'}},
        'models_fetched': True,
    }])
    deepseek = cfg['api_providers'][0]
    assert deepseek['models_fetched'] is True
    assert deepseek['available_models'] == ['deepseek-flash']
    assert deepseek['available_models_meta']['deepseek-flash']['owned_by'] == 'deepseek'
    assert {'id': 'custom-ds', 'active': True, 'manual': True, 'label': 'custom-ds'} in deepseek['models']


def test_public_api_provider_exposes_meta_and_manual():
    provider = normalize_api_providers([{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [{'id': 'deepseek-flash', 'active': True, 'manual': True}],
        'available_models': ['deepseek-flash'],
        'available_models_meta': {'deepseek-flash': {'owned_by': 'deepseek'}},
        'models_fetched': True,
    }])[0]
    public = public_api_provider(provider)
    assert public['models_fetched'] is True
    assert public['available_models_meta']['deepseek-flash']['owned_by'] == 'deepseek'
    assert public['models'][0]['manual'] is True

def test_default_model_labels_deepseek_map():
    assert DEEPSEEK_MODEL_LABELS['deepseek-flash'] == 'DeepSeek-V4.1-Flash'
    assert DEEPSEEK_MODEL_LABELS['deepseek-v4-pro'] == 'DeepSeek-V4-Pro'
    assert default_model_label('deepseek-flash') == 'DeepSeek-V4.1-Flash'
    assert default_model_label('deepseek-v4-pro') == 'DeepSeek-V4-Pro'
    assert default_model_label('gpt-4o') == 'gpt-4o'


def test_normalize_migrates_missing_label_and_preserves_custom():
    rows = normalize_model_entries([
        {'id': 'deepseek-flash', 'active': True},
        {'id': 'deepseek-v4-pro', 'active': False, 'label': 'My Pro'},
        {'id': 'deepseek-flash-custom', 'active': True, 'label': ''},
        {'id': 'gpt-4o', 'active': True},
    ])
    by_id = {row['id']: row for row in rows}
    assert by_id['deepseek-flash']['label'] == 'DeepSeek-V4.1-Flash'
    assert by_id['deepseek-v4-pro']['label'] == 'My Pro'
    assert 'label' not in by_id['deepseek-flash-custom']
    assert resolve_model_label(by_id['deepseek-flash-custom']) == 'deepseek-flash-custom'
    assert by_id['gpt-4o']['label'] == 'gpt-4o'


def test_catalog_rows_use_friendly_label_keep_api_id():
    cfg = {'api_providers': [{
        'id': 'deepseek',
        'enabled': True,
        'api_key': 'sk-test',
        'models': [
            {'id': 'deepseek-flash', 'active': True, 'label': 'Flash Friendly'},
            {'id': 'deepseek-v4-pro', 'active': True},
        ],
    }]}
    rows = catalog_rows_for_providers(cfg)
    by_id = {row['id']: row for row in rows}
    assert by_id['deepseek-flash']['label'] == 'Flash Friendly'
    assert by_id['deepseek-flash']['model_id'] == 'deepseek-flash'
    assert by_id['deepseek-flash']['filename'] == 'deepseek-flash'
    assert by_id['deepseek-flash']['chat_model_key'] == 'cloud::deepseek::deepseek-flash'
    assert by_id['deepseek-v4-pro']['label'] == 'DeepSeek-V4-Pro'
    assert by_id['deepseek-v4-pro']['id'] == 'deepseek-v4-pro'


def test_apply_fetched_models_preserves_custom_labels():
    provider = normalize_api_providers([{
        'id': 'deepseek',
        'models': [
            {'id': 'deepseek-flash', 'active': True, 'label': 'My Flash'},
        ],
    }])[0]
    updated = apply_fetched_models(provider, [
        'deepseek-flash',
        'deepseek-v4-pro',
    ])
    by_id = {m['id']: m for m in updated['models']}
    assert by_id['deepseek-flash']['label'] == 'My Flash'
    assert by_id['deepseek-v4-pro']['label'] == 'DeepSeek-V4-Pro'

