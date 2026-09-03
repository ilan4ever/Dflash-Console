from __future__ import annotations

import base64
from pathlib import Path

import pytest

from core.chat_vision import (
    chat_messages_contain_images,
    ensure_vision_ready_for_chat,
    live_loaded_has_mmproj,
    normalize_multimodal_chat_body,
    router_registration_stale,
    vision_capability,
    _model_entry_has_draft,
    _model_entry_has_mmproj,
    _preset_has_draft,
)


def _tiny_png_data_url() -> str:
    # 1x1 transparent PNG
    raw = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    )
    return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')


def test_chat_messages_contain_images_detects_image_url():
    body = {
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'what color?'},
                    {'type': 'image_url', 'image_url': {'url': _tiny_png_data_url()}},
                ],
            }
        ]
    }
    assert chat_messages_contain_images(body) is True


def test_normalize_multimodal_chat_body_normalizes_raw_base64():
    raw_b64 = base64.b64encode(b'fake-image').decode('ascii')
    body = {
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'image_url', 'image_url': {'url': raw_b64}},
                ],
            }
        ]
    }
    normalized, changed = normalize_multimodal_chat_body(body)
    assert changed is True
    part = normalized['messages'][0]['content'][0]
    assert part['type'] == 'image_url'
    assert part['image_url']['url'].startswith('data:image/png;base64,')


def test_vision_capability_requires_mmproj_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    target = tmp_path / 'model.gguf'
    mmproj = tmp_path / 'mmproj-model.gguf'
    target.write_bytes(b'model')
    server = {'id': 'test', 'target_path': str(target), 'profile': 'gemma-12-dflash'}
    assert vision_capability(server)['supports_vision'] is False
    mmproj.write_bytes(b'mmproj')
    assert vision_capability(server)['supports_vision'] is True


def test_vision_capability_true_for_gemma_chat_when_mmproj_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    target = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    mmproj = tmp_path / 'mmproj-BF16.gguf'
    target.write_bytes(b'model')
    mmproj.write_bytes(b'mmproj')
    server = {
        'id': 'gemma-4-31b-q4-0-it-dflash',
        'profile': 'gemma-chat',
        'target_path': str(target),
        'mmproj_path': str(mmproj),
    }
    cap = vision_capability(server)
    assert cap['supports_vision'] is True
    assert cap['imageInput'] is True
    assert cap['mmproj_path'] == str(mmproj.resolve())


def test_ensure_vision_ready_for_chat_rejects_without_mmproj(tmp_path: Path):
    target = tmp_path / 'model.gguf'
    target.write_bytes(b'model')
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'target_path': str(target),
        'port': 8191,
        'host': '127.0.0.1',
    }
    result = ensure_vision_ready_for_chat(server, cfg={'servers': [server]})
    assert result['success'] is False
    assert result['reason'] == 'no_mmproj'


def test_ensure_vision_ready_for_chat_writes_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / 'model.gguf'
    mmproj = tmp_path / 'mmproj-model.gguf'
    target.write_bytes(b'model')
    mmproj.write_bytes(b'mmproj')
    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    monkeypatch.setattr(
        'core.vision_setup._is_allowed_model_path',
        lambda path, cfg: True,
    )
    server = {
        'id': 'test-vision-chat',
        'label': 'Gemma 12B',
        'model_id': 'model',
        'profile': 'gemma-12-dflash',
        'target_path': str(target),
        'draft_path': str(tmp_path / 'draft.gguf'),
        'context_size': 8192,
        'load_settings': {'gpu_layers': 88},
        'gpu_device': 'auto',
        'port': 8191,
        'host': '127.0.0.1',
    }
    (tmp_path / 'draft.gguf').write_bytes(b'draft')
    cfg = {'servers': [dict(server)]}
    monkeypatch.setattr('core.config.save_config', lambda payload: None)
    monkeypatch.setattr('core.chat_vision.tcp_port_open', lambda host, port: False)
    monkeypatch.setattr('core.chat_vision.live_loaded_has_mmproj', lambda entry: None)
    result = ensure_vision_ready_for_chat(server, cfg=cfg)
    assert result['success'] is True
    preset = preset_dir / 'test-vision-chat.ini'
    assert preset.is_file()
    assert f'mmproj = {mmproj}' in preset.read_text(encoding='utf-8')


def test_model_entry_has_mmproj_from_worker_args():
    assert _model_entry_has_mmproj({
        'status': {
            'value': 'loaded',
            'args': ['llama-server.exe', '--model', 'x.gguf', '--mmproj', 'mmproj.gguf'],
        }
    }) is True
    assert _model_entry_has_mmproj({
        'status': {'value': 'loaded', 'args': ['llama-server.exe', '--model', 'x.gguf']},
    }) is False


def test_model_entry_has_draft_from_worker_args():
    assert _model_entry_has_draft({
        'status': {
            'value': 'unloaded',
            'args': ['llama-server.exe', '--model', 'x.gguf', '--model-draft', 'draft.gguf'],
        }
    }) is True
    assert _model_entry_has_draft({
        'status': {'value': 'unloaded', 'args': ['llama-server.exe', '--model', 'x.gguf']},
    }) is False


def test_router_registration_stale_when_mmproj_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    preset_dir = tmp_path / 'presets'
    preset_dir.mkdir()
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    preset_dir.joinpath('gemma.ini').write_text(
        '[gemma]\nmodel = C:\\models\\x.gguf\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(
        'core.runtime._fetch_models_payload',
        lambda api_url: [{
            'id': 'gemma',
            'status': {
                'value': 'unloaded',
                'args': ['llama-server.exe', '--model', 'x.gguf', '--mmproj', 'mmproj.gguf'],
            },
        }],
    )
    server = {
        'id': 'gemma',
        'api_url': 'http://127.0.0.1:8094/v1',
    }
    assert router_registration_stale(server, load_id='gemma') is True


def test_router_registration_stale_false_when_preset_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    preset_dir = tmp_path / 'presets'
    preset_dir.mkdir()
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    preset_dir.joinpath('gemma.ini').write_text(
        '[gemma]\nmodel = C:\\models\\x.gguf\nmodel-draft = C:\\models\\draft.gguf\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(
        'core.runtime._fetch_models_payload',
        lambda api_url: [{
            'id': 'gemma',
            'status': {
                'value': 'unloaded',
                'args': ['llama-server.exe', '--model', 'x.gguf', '--model-draft', 'draft.gguf'],
            },
        }],
    )
    server = {
        'id': 'gemma',
        'api_url': 'http://127.0.0.1:8094/v1',
    }
    assert router_registration_stale(server, load_id='gemma') is False
    assert _preset_has_draft('gemma') is True


def test_live_loaded_has_mmproj_ignores_unloaded_preset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        'core.runtime._fetch_models_payload',
        lambda api_url: [{
            'id': 'qwen',
            'status': {
                'value': 'unloaded',
                'args': ['llama-server.exe', '--mmproj', 'mmproj.gguf'],
                'preset': 'mmproj = C:\\models\\mmproj.gguf\n',
            },
        }],
    )
    assert live_loaded_has_mmproj({'api_url': 'http://127.0.0.1:8090/v1'}) is None


def test_ensure_vision_reloads_when_live_worker_missing_mmproj(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / 'model.gguf'
    mmproj = tmp_path / 'mmproj-model.gguf'
    target.write_bytes(b'model')
    mmproj.write_bytes(b'mmproj')
    preset_dir = tmp_path / 'presets'
    preset_dir.mkdir()
    preset_dir.joinpath('qwen.ini').write_text(
        f'[qwen]\nmodel = {target}\nmmproj = {mmproj}\n',
        encoding='utf-8',
    )
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    monkeypatch.setattr('core.config.save_config', lambda payload: None)
    monkeypatch.setattr('core.vision_setup._is_allowed_model_path', lambda path, cfg: True)
    monkeypatch.setattr('core.chat_vision.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_vision.live_loaded_has_mmproj', lambda entry: False)
    monkeypatch.setattr(
        'core.server_boot.listener_is_managed_engine',
        lambda host, port: True,
    )
    stops: list[int] = []
    loads: list[str] = []
    monkeypatch.setattr(
        'core.runtime.stop_server',
        lambda **kwargs: stops.append(int(kwargs.get('port') or 0)) or {'success': True},
    )
    monkeypatch.setattr(
        'core.server_boot.load_server_checkpoint',
        lambda entry, cfg=None: loads.append(str(entry.get('id') or '')) or {'success': True},
    )
    server = {
        'id': 'qwen',
        'label': 'Qwen 27B',
        'model_id': 'qwen',
        'profile': 'qwen-dflash',
        'target_path': str(target),
        'mmproj_path': str(mmproj),
        'port': 8097,
        'host': '127.0.0.1',
        'api_url': 'http://127.0.0.1:8097/v1',
    }
    result = ensure_vision_ready_for_chat(server, cfg={'servers': [dict(server)]})
    assert result['success'] is True
    assert result['reloaded'] is True
    assert stops == [8097]
    assert loads == ['qwen']
