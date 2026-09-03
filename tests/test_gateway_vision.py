from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from api.gateway import list_models


def test_gateway_list_models_includes_vision_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / 'model.gguf'
    mmproj = tmp_path / 'mmproj-model.gguf'
    target.write_bytes(b'model')
    mmproj.write_bytes(b'mmproj')
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    cfg = {
        'ui_port': 8900,
        'gateway_port': 8001,
        'servers': [{
            'id': 'gemma-12b-ar',
            'enabled': True,
            'port': 8301,
            'label': 'Gemma 12B',
            'profile': 'gemma-12-dflash',
            'model_id': 'gemma-4-12b-it-q4-k-m',
            'target_path': str(target),
            'mmproj_path': str(mmproj),
        }],
    }
    with patch('api.gateway.load_config', return_value=cfg):
        result = asyncio.run(list_models())
    row = next(item for item in result['data'] if item['id'] == 'gemma-4-12b-it-q4-k-m')
    assert row['meta']['supports_vision'] is True
    assert row['meta']['imageInput'] is True
    assert row['meta']['mmproj_path'] == str(mmproj.resolve())


def test_gateway_list_models_supports_vision_for_gemma_chat_31b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    mmproj = tmp_path / 'mmproj-BF16.gguf'
    target.write_bytes(b'model')
    mmproj.write_bytes(b'mmproj')
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    cfg = {
        'ui_port': 8900,
        'gateway_port': 8001,
        'servers': [{
            'id': 'gemma-4-31b-q4-0-it-dflash',
            'enabled': True,
            'port': 8094,
            'label': 'Gemma 4 31B D-Flash',
            'profile': 'gemma-chat',
            'model_id': 'gemma-4-31b-q4-0-it',
            'target_path': str(target),
            'mmproj_path': str(mmproj),
            'draft_path': str(tmp_path / 'draft.gguf'),
        }],
    }
    (tmp_path / 'draft.gguf').write_bytes(b'draft')
    with patch('api.gateway.load_config', return_value=cfg):
        result = asyncio.run(list_models())
    row = next(item for item in result['data'] if item['id'] == 'gemma-4-31b-q4-0-it')
    assert row['meta']['supports_vision'] is True
    assert row['meta']['imageInput'] is True
    assert row['meta']['server_id'] == 'gemma-4-31b-q4-0-it-dflash'
