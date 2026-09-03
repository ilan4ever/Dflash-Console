from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.gateway import list_models
from core.gateway_routing import (
    catalog_model_id,
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
    mmproj = tmp_path / 'mmproj-model.gguf'
    target.write_bytes(b'model')
    mmproj.write_bytes(b'mmproj')
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    cfg = _cfg()
    cfg['servers'][2]['target_path'] = str(target)
    cfg['servers'][2]['mmproj_path'] = str(mmproj)
    cfg['servers'][3]['target_path'] = str(target)
    cfg['servers'][3]['mmproj_path'] = str(mmproj)
    with patch('api.gateway.load_config', return_value=cfg):
        result = asyncio.run(list_models())
    by_id = {row['id']: row for row in result['data']}
    assert 'gemma-4-12b-it-q4-k-m' in by_id
    assert by_id['gemma-4-12b-it-q4-k-m']['meta']['server_id'] == 'gemma-4-12b-it-q4-k-m-dflash'
    assert by_id['gemma-4-31b-q4-0-it']['meta']['supports_vision'] is True
    assert by_id['gemma-4-31b-q4-0-it']['meta']['imageInput'] is True
