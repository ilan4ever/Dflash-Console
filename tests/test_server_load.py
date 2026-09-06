from __future__ import annotations

from fastapi import HTTPException

import api.app as app
from core.server_boot import (
    _checkpoint_id_loaded,
    checkpoints_match,
    find_target_loaded_elsewhere,
    resolve_load_target_path,
)


def _cfg() -> dict:
    return {
        'ui_port': 8900,
        'hardware_settings': {'enabled_gpu_indices': [0]},
        'servers': [{
            'id': 'qwen-engine',
            'label': 'Qwen 3.8 27B',
            'profile': 'qwen-ar',
            'port': 8096,
            'host': '127.0.0.1',
            'api_url': 'http://127.0.0.1:8096/v1',
            'model_id': 'qwen3.8-27b-q6-k-l',
            'engine_on': True,
        }],
    }


def test_server_load_returns_already_loaded_before_vram_check(monkeypatch):
    monkeypatch.setattr(app, 'load_config', lambda: _cfg())
    monkeypatch.setattr(
        'core.server_boot.checkpoint_already_loaded',
        lambda *args, **kwargs: {
            'success': True,
            'port': 8096,
            'loaded': True,
            'already_loaded': True,
            'model': 'qwen3.8-27b-q6-k-l',
        },
    )
    monkeypatch.setattr(
        'core.memory_guardrails.assess_load',
        lambda *args, **kwargs: {
            'level': 'block',
            'message': 'Cannot load model: insufficient VRAM',
        },
    )
    load_called = {'value': False}

    def _fail_load(*args, **kwargs):
        load_called['value'] = True
        return {'success': True, 'loaded': True}

    monkeypatch.setattr(app, 'load_server_checkpoint', _fail_load)
    monkeypatch.setattr('core.engine_state.note_engine_loaded', lambda *args, **kwargs: None)

    result = app.server_load('qwen-engine', request=type('Req', (), {'headers': {}})())

    assert result['already_loaded'] is True
    assert load_called['value'] is False


def test_server_load_plan_reports_already_loaded(monkeypatch):
    monkeypatch.setattr(app, 'load_config', lambda: _cfg())
    monkeypatch.setattr(
        'core.server_boot.checkpoint_already_loaded',
        lambda *args, **kwargs: {
            'success': True,
            'port': 8096,
            'loaded': True,
            'already_loaded': True,
            'model': 'qwen3.8-27b-q6-k-l',
        },
    )
    monkeypatch.setattr(
        'core.memory_guardrails.assess_load',
        lambda *args, **kwargs: {
            'level': 'block',
            'message': 'Cannot load model: insufficient VRAM',
        },
    )

    result = app.server_load_plan('qwen-engine', model_path='', model_id='')

    assert result['level'] == 'already_loaded'
    assert result['already_loaded'] is True
    assert 'already loaded' in result['message'].lower()


def test_server_load_still_blocks_different_model_without_vram(monkeypatch):
    monkeypatch.setattr(app, 'load_config', lambda: _cfg())
    monkeypatch.setattr('core.server_boot.checkpoint_already_loaded', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'core.memory_guardrails.assess_load',
        lambda *args, **kwargs: {
            'level': 'block',
            'message': 'Cannot load model: insufficient VRAM',
        },
    )

    try:
        app.server_load('qwen-engine', request=type('Req', (), {'headers': {}})())
    except HTTPException as exc:
        assert exc.status_code == 400
        assert 'insufficient VRAM' in str(exc.detail)
    else:
        raise AssertionError('expected HTTPException')


def test_dflash_missing_draft_returns_repair_before_already_loaded(monkeypatch, tmp_path):
    target = tmp_path / 'Qwen3.8-27B-Q6_K_L.gguf'
    target.write_bytes(b'target')
    cfg = _cfg()
    cfg['servers'][0].update({
        'profile': 'qwen-dflash',
        'target_path': str(target),
        'model_id': 'qwen3-8-27b-q6-k-l',
    })
    monkeypatch.setattr(app, 'load_config', lambda: cfg)
    checkpoint_called = {'value': False}
    monkeypatch.setattr(
        'core.server_boot.checkpoint_already_loaded',
        lambda *args, **kwargs: checkpoint_called.update(value=True) or None,
    )

    try:
        app.server_load('qwen-engine', request=type('Req', (), {'headers': {}})())
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail['reason_code'] == 'draft-required'
        assert exc.detail['repair']['action'] == 'attach_draft'
    else:
        raise AssertionError('expected DFlash repair response')
    assert checkpoint_called['value'] is False


def test_dflash_already_loaded_is_reused_only_with_live_draft(monkeypatch):
    cfg = _cfg()
    cfg['servers'][0]['profile'] = 'qwen-dflash'
    cfg['servers'][0].update({
        'target_path': r'C:\models\target.gguf',
        'draft_path': r'C:\models\draft-DFlash2.gguf',
    })
    monkeypatch.setattr(app, 'load_config', lambda: cfg)
    monkeypatch.setattr(
        'core.server_boot.validate_dflash_stack',
        lambda *args, **kwargs: {'success': True, 'valid': True, 'required': True},
    )
    monkeypatch.setattr('core.server_boot.dflash_live_launch_state', lambda server: True)
    monkeypatch.setattr(
        'core.server_boot.checkpoint_already_loaded',
        lambda *args, **kwargs: {
            'success': True,
            'port': 8096,
            'loaded': True,
            'already_loaded': True,
            'model': 'qwen3.8-27b-q6-k-l',
        },
    )
    monkeypatch.setattr(
        'core.memory_guardrails.assess_load',
        lambda *args, **kwargs: {
            'level': 'block',
            'message': 'Cannot load model: insufficient VRAM',
        },
    )

    result = app.server_load('qwen-engine', request=type('Req', (), {'headers': {}})())
    assert result['already_loaded'] is True


def test_checkpoint_id_loaded_matches_library_file_alias_and_filename():
    assert _checkpoint_id_loaded('library-file:gemma-4-31b-q4-0-it', ['gemma-4-31b-q4-0-it'])
    assert _checkpoint_id_loaded('gemma-4-31B_q4_0-it.gguf', ['gemma-4-31b-q4-0-it'])
    assert not _checkpoint_id_loaded('qwen3.8-27b-q6-k-l', ['gemma-4-31b-q4-0-it'])


def test_find_target_loaded_elsewhere_detects_same_gguf(monkeypatch, tmp_path):
    gguf = tmp_path / 'gemma-4-12B-it-Q4_K_M.gguf'
    gguf.write_bytes(b'gguf' * 16)
    target = str(gguf)
    cfg = {
        'servers': [
            {
                'id': 'gemma-dflash',
                'label': 'Gemma DFlash',
                'port': 8092,
                'host': '127.0.0.1',
                'api_url': 'http://127.0.0.1:8092/v1',
                'model_id': 'gemma-4-12b-it-q4-k-m-dflash',
                'target_path': target,
                'enabled': True,
            },
            {
                'id': 'gemma-plain',
                'label': 'Gemma plain',
                'port': 8093,
                'host': '127.0.0.1',
                'api_url': 'http://127.0.0.1:8093/v1',
                'model_id': 'gemma-4-12b-it-q4-k-m',
                'target_path': target,
                'enabled': True,
            },
        ],
    }

    def _status(server, *, cfg=None, **kwargs):
        sid = str(server.get('id') or '')
        if sid == 'gemma-dflash':
            return {'status': 'loaded', 'loaded_models': ['gemma-4-12b-it-q4-k-m-dflash']}
        return {'status': 'stopped', 'loaded_models': []}

    monkeypatch.setattr('core.config.load_config', lambda: cfg)
    monkeypatch.setattr('core.runtime.build_server_status', _status)

    assert resolve_load_target_path(cfg['servers'][1], cfg=cfg) == resolve_load_target_path(cfg['servers'][0], cfg=cfg)
    elsewhere = find_target_loaded_elsewhere(
        cfg['servers'][1],
        cfg=cfg,
        exclude_server_id='gemma-plain',
    )
    assert elsewhere is not None
    assert elsewhere['server_id'] == 'gemma-dflash'


def test_checkpoints_match_same_basename_and_size(tmp_path):
    left = tmp_path / 'models-a' / 'gemma-4-31B_q4_0-it.gguf'
    right = tmp_path / 'models-b' / 'gemma-4-31B_q4_0-it.gguf'
    left.parent.mkdir(parents=True)
    right.parent.mkdir(parents=True)
    payload = b'gguf' * 32
    left.write_bytes(payload)
    right.write_bytes(payload)
    assert checkpoints_match(str(left), str(right))


def test_find_target_loaded_elsewhere_matches_same_basename_copy(monkeypatch, tmp_path):
    left = tmp_path / 'console' / 'gemma-4-31B_q4_0-it.gguf'
    right = tmp_path / 'lmstudio' / 'gemma-4-31B_q4_0-it.gguf'
    left.parent.mkdir(parents=True)
    right.parent.mkdir(parents=True)
    payload = b'gguf' * 32
    left.write_bytes(payload)
    right.write_bytes(payload)
    cfg = {
        'servers': [
            {
                'id': 'gemma-4-31b-q4-0-it-dflash',
                'label': 'Primary 31B',
                'port': 8094,
                'host': '127.0.0.1',
                'api_url': 'http://127.0.0.1:8094/v1',
                'model_id': 'gemma-4-31b-q4-0-it',
                'target_path': str(left),
                'enabled': True,
            },
            {
                'id': 'gemma-4-31b-q4-0-it-dflash-2',
                'label': 'Secondary 31B',
                'port': 8093,
                'host': '127.0.0.1',
                'api_url': 'http://127.0.0.1:8093/v1',
                'model_id': 'gemma-4-31b-q4-0-it',
                'target_path': str(right),
                'enabled': True,
            },
        ],
    }

    def _status(server, *, cfg=None, **kwargs):
        sid = str(server.get('id') or '')
        if sid == 'gemma-4-31b-q4-0-it-dflash':
            return {'status': 'loaded', 'loaded_models': ['gemma-4-31b-q4-0-it']}
        return {'status': 'stopped', 'loaded_models': []}

    monkeypatch.setattr('core.config.load_config', lambda: cfg)
    monkeypatch.setattr('core.runtime.build_server_status', _status)

    elsewhere = find_target_loaded_elsewhere(
        cfg['servers'][1],
        cfg=cfg,
        exclude_server_id='gemma-4-31b-q4-0-it-dflash-2',
    )
    assert elsewhere is not None
    assert elsewhere['server_id'] == 'gemma-4-31b-q4-0-it-dflash'
