from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.gateway import list_models
from core.gateway_routing import (
    catalog_model_id,
    model_ids_compatible,
    normalize_model_token,
    resolve_chat_server,
)


def _cfg() -> dict:
    return {
        'ui_port': 8900,
        'gateway_port': 8001,
        'gateway_server_id': '',
        'servers': [
            {
                'id': 'gemma-12b-ar',
                'enabled': True,
                'port': 8191,
                'label': 'Gemma 12B',
                'model_id': 'gemma-4-12b-it-qat',
            },
            {
                'id': 'qwen3-8-27b-q6-k-l-dflash',
                'enabled': True,
                'port': 8091,
                'label': 'Qwen 27B',
                'profile': 'qwen-dflash',
                'model_id': 'qwen3.8-27b-q6-k-l',
                'target_path': r'C:\models\qwen.gguf',
            },
            {
                'id': 'gemma-4-12b-it-q4-k-m-dflash',
                'enabled': True,
                'port': 8092,
                'label': 'Gemma 12B DFlash',
                'profile': 'gemma-12-dflash',
                'model_id': 'gemma-4-12b-it-q4-k-m',
                'target_path': r'C:\models\gemma12.gguf',
            },
            {
                'id': 'gemma-4-31b-q4-0-it-dflash',
                'enabled': True,
                'port': 8094,
                'label': 'Gemma 31B',
                'profile': 'gemma-chat',
                'model_id': 'gemma-4-31b-q4-0-it',
                'target_path': r'C:\models\gemma31.gguf',
            },
        ],
    }


def test_normalize_model_token_maps_dots_and_dflash_suffix():
    assert normalize_model_token('qwen3.8-27b-q6-k-l') == 'qwen3-8-27b-q6-k-l'
    assert normalize_model_token('qwen3-8-27b-q6-k-l-dflash') == 'qwen3-8-27b-q6-k-l'


def test_model_ids_compatible_allows_quant_suffix():
    assert model_ids_compatible('translategemma-12b-it', 'translategemma-12b-it-q4-k-s')
    assert model_ids_compatible('gemma-4-12b-it-q4-k-m', 'gemma-4-12b-it-qat') is False
    assert not model_ids_compatible('translategemma-12b-it', 'gemma-4-12b-it-q4-k-m')


def test_resolve_chat_server_matches_catalog_model_id():
    cfg = _cfg()
    server = resolve_chat_server(cfg, 'qwen3-8-27b-q6-k-l')
    assert server['id'] == 'qwen3-8-27b-q6-k-l-dflash'


def test_resolve_chat_server_matches_server_id_with_dflash_suffix():
    cfg = _cfg()
    server = resolve_chat_server(cfg, 'gemma-4-31b-q4-0-it-dflash')
    assert server['id'] == 'gemma-4-31b-q4-0-it-dflash'


def test_resolve_chat_server_unknown_returns_404():
    cfg = _cfg()
    with pytest.raises(HTTPException) as exc:
        resolve_chat_server(cfg, 'does-not-exist')
    assert exc.value.status_code == 404
    assert exc.value.detail['error']['code'] == 'model_not_found'


def test_resolve_chat_server_empty_uses_first_enabled():
    cfg = _cfg()
    server = resolve_chat_server(cfg, '')
    assert server['id'] == 'gemma-12b-ar'


def test_catalog_model_id_prefers_model_id():
    assert catalog_model_id({'id': 'qwen3-8-27b-q6-k-l-dflash', 'model_id': 'qwen3.8-27b-q6-k-l'}) == 'qwen3-8-27b-q6-k-l'


def test_gateway_list_models_uses_catalog_ids(tmp_path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / 'model.gguf'
    draft = tmp_path / 'model-DFlash-F16.gguf'
    target31 = tmp_path / 'model31.gguf'
    draft31 = tmp_path / 'model31-DFlash-F16.gguf'
    mmproj = tmp_path / 'mmproj-model.gguf'
    target.write_bytes(b'model')
    draft.write_bytes(b'draft')
    target31.write_bytes(b'model31')
    draft31.write_bytes(b'draft31')
    mmproj.write_bytes(b'mmproj')
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    cfg = _cfg()
    cfg['servers'][2]['target_path'] = str(target)
    cfg['servers'][2]['draft_path'] = str(draft)
    cfg['servers'][2]['mmproj_path'] = str(mmproj)
    cfg['servers'][3]['target_path'] = str(target31)
    cfg['servers'][3]['draft_path'] = str(draft31)
    cfg['servers'][3]['mmproj_path'] = str(mmproj)
    with patch('api.gateway.load_config', return_value=cfg):
        result = asyncio.run(list_models())
    by_id = {row['id']: row for row in result['data']}
    assert 'gemma-4-12b-it-q4-k-m' in by_id
    assert by_id['gemma-4-12b-it-q4-k-m']['meta']['server_id'] == 'gemma-4-12b-it-q4-k-m-dflash'
    assert by_id['gemma-4-31b-q4-0-it']['meta']['supports_vision'] is True
    assert by_id['gemma-4-31b-q4-0-it']['meta']['imageInput'] is True


def test_gateway_list_models_deduplicates_registered_model_ids(tmp_path):
    target = tmp_path / 'gemma31.gguf'
    draft = tmp_path / 'gemma31-dflash.gguf'
    target.write_bytes(b'target')
    draft.write_bytes(b'draft')
    cfg = _cfg()
    cfg['servers'][3]['target_path'] = str(target)
    cfg['servers'][3]['draft_path'] = str(draft)
    cfg['servers'].append({
        **cfg['servers'][3],
        'id': 'gemma-4-31b-q4-0-it-dflash-2',
        'port': 8093,
    })
    with patch('api.gateway.load_config', return_value=cfg):
        result = asyncio.run(list_models())

    rows = [row for row in result['data'] if row['id'] == 'gemma-4-31b-q4-0-it']
    assert len(rows) == 1
    assert rows[0]['meta']['server_id'] == 'gemma-4-31b-q4-0-it-dflash'


def test_gateway_list_models_hides_incomplete_dflash_profiles(monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg()
    with patch('api.gateway.load_config', return_value=cfg):
        result = asyncio.run(list_models())
    ids = {row['meta']['server_id'] for row in result['data']}
    assert 'qwen3-8-27b-q6-k-l-dflash' not in ids
    assert 'gemma-4-12b-it-q4-k-m-dflash' not in ids
