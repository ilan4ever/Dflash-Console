from __future__ import annotations

from pathlib import Path

import pytest

import core.vision_setup as vision_setup


def test_infer_hf_repo_from_lmstudio_layout():
    path = Path('C:/Users/me/.lmstudio/models/google/gemma-4-31B-it-qat-q4_0-gguf/model.gguf')
    assert vision_setup.infer_hf_repo_from_path(path) == 'google/gemma-4-31B-it-qat-q4_0-gguf'


def test_infer_hf_repo_from_flat_gemma_folder():
    path = Path('C:/dev/Dflash-Console/models/gemma-4-12b-it/gemma-4-12B-it-Q4_K_M.gguf')
    assert vision_setup.infer_hf_repo_from_path(path) == 'lmstudio-community/gemma-4-12b-it-GGUF'


def test_infer_hf_repo_from_qwen_folder():
    path = Path('C:/dev/Dflash-Console/models/bartowski/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q6_K_L.gguf')
    assert vision_setup.infer_hf_repo_from_path(path) == 'bartowski/Qwen3.8-27B-GGUF'


def test_pick_mmproj_prefers_matching_size(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        vision_setup,
        '_fetch_mmproj_filenames',
        lambda repo: ['mmproj-other.gguf', 'gemma-4-31B-it-mmproj.gguf'],
    )
    picked = vision_setup.pick_mmproj_filename(
        'google/gemma-4-31B-it-qat-q4_0-gguf',
        'gemma-4-31B_q4_0-it.gguf',
    )
    assert picked == 'gemma-4-31B-it-mmproj.gguf'


def test_vision_plan_wires_local_mmproj_without_huggingface(tmp_path: Path):
    model = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    projector = tmp_path / 'mmproj-BF16.gguf'
    model.write_bytes(b'gguf')
    projector.write_bytes(b'mmproj')
    cfg = {
        'model_libraries': [{
            'id': 'test',
            'label': 'Test models',
            'path': str(tmp_path),
            'enabled': True,
            'preset': 'custom',
            'download_default': True,
        }],
        'servers': [{'id': 'gemma-4-31b-q4-0-it', 'target_path': str(model)}],
    }
    plan = vision_setup.vision_plan(
        model_path=str(model),
        server_id='gemma-4-31b-q4-0-it',
        cfg=cfg,
    )
    assert plan['success'] is True
    assert plan['ready'] is False
    assert plan['needs_download'] is False
    assert plan['mmproj_path'] == str(projector)


def test_server_supports_vision_chat_gemma_chat_with_mmproj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vision_setup, '_is_allowed_model_path', lambda path, cfg: True)
    target = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    projector = tmp_path / 'mmproj-BF16.gguf'
    target.write_bytes(b'gguf')
    projector.write_bytes(b'mmproj')
    server = {
        'id': 'gemma-4-31b-q4-0-it-dflash',
        'profile': 'gemma-chat',
        'target_path': str(target),
        'mmproj_path': str(projector),
    }
    assert vision_setup.server_supports_vision_chat(server) is True
    server_off = {**server, 'vision': False}
    assert vision_setup.server_supports_vision_chat(server_off) is False


def test_server_supports_vision_chat_qwen_ar_with_mmproj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vision_setup, '_is_allowed_model_path', lambda path, cfg: True)
    target = tmp_path / 'Qwen3.8-27B-IQ3_XXS.gguf'
    projector = tmp_path / 'mmproj-BF16.gguf'
    target.write_bytes(b'gguf')
    projector.write_bytes(b'mmproj')
    server = {
        'id': 'qwen-ar',
        'profile': 'qwen-ar',
        'target_path': str(target),
        'mmproj_path': str(projector),
    }
    assert vision_setup.server_supports_vision_chat(server) is True


def test_local_mmproj_filename_prefixes_hf_folder_projectors():
    assert vision_setup.local_mmproj_filename(
        'mmproj/Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf'
    ) == 'mmproj-Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf'
    assert vision_setup.local_mmproj_filename('gemma-4-31B-it-mmproj.gguf') == 'gemma-4-31B-it-mmproj.gguf'


def test_mmproj_siblings_find_hf_vision_sidecar(tmp_path: Path):
    target = tmp_path / 'Qwen3.8-27B-gsq-rco-mtp-IQ3_XXS.gguf'
    projector = tmp_path / 'Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf'
    target.write_bytes(b'g' * 4000)
    projector.write_bytes(b'p' * 200)
    siblings = vision_setup._mmproj_siblings(target)
    assert siblings == [projector]


def test_wire_vision_accepts_hf_vision_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / 'Qwen3.8-27B-gsq-rco-mtp-IQ3_XXS.gguf'
    projector = tmp_path / 'Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf'
    target.write_bytes(b'gguf')
    projector.write_bytes(b'mmproj')
    cfg = {
        'model_libraries': [{
            'id': 'test',
            'label': 'Test models',
            'path': str(tmp_path),
            'enabled': True,
            'preset': 'custom',
            'download_default': True,
        }],
        'servers': [{'id': 'qwen-iq3', 'target_path': str(target)}],
    }
    monkeypatch.setattr(vision_setup, 'save_config', lambda config: None)
    monkeypatch.setattr(vision_setup, 'write_server_preset', lambda *args, **kwargs: None)
    monkeypatch.setattr(vision_setup, 'invalidate_model_catalog_cache', lambda: None)
    result = vision_setup.wire_vision(
        model_path=str(target),
        mmproj_path=str(projector),
        server_id='qwen-iq3',
        cfg=cfg,
    )
    assert result['success'] is True
    assert result['vision_ready'] is True


def test_wire_vision_after_download_raises_when_wire_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        vision_setup,
        'wire_vision',
        lambda **kwargs: {'success': False, 'error': 'projector filename must be a GGUF vision projector'},
    )
    with pytest.raises(RuntimeError, match='projector filename'):
        vision_setup.wire_vision_after_download({
            'model_path': 'model.gguf',
            'mmproj_path': 'sidecar.gguf',
            'server_id': 'qwen-iq3',
        })


def test_vision_plan_needs_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    model = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    model.write_bytes(b'gguf')
    monkeypatch.setattr(vision_setup, 'infer_hf_repo_from_path', lambda path: 'google/gemma-4-31B-it-qat-q4_0-gguf')
    monkeypatch.setattr(vision_setup, 'pick_mmproj_filename', lambda repo, path: 'gemma-4-31B-it-mmproj.gguf')
    cfg = {
        'model_libraries': [{
            'id': 'test',
            'label': 'Test models',
            'path': str(tmp_path),
            'enabled': True,
            'preset': 'custom',
            'download_default': True,
        }],
        'servers': [],
    }
    plan = vision_setup.vision_plan(model_path=str(model), cfg=cfg)
    assert plan['success'] is True
    assert plan['needs_download'] is True
    assert plan['filename'] == 'gemma-4-31B-it-mmproj.gguf'
    assert plan['dest_path'].endswith('gemma-4-31B-it-mmproj.gguf')


def test_vision_plan_prefixes_dest_for_nested_hf_mmproj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    model = tmp_path / 'Qwen3.8-27B-gsq-rco-mtp-IQ3_XXS.gguf'
    model.write_bytes(b'gguf')
    monkeypatch.setattr(vision_setup, 'infer_hf_repo_from_path', lambda path: 'bartowski/Qwen3.8-27B-GGUF')
    monkeypatch.setattr(
        vision_setup,
        'pick_mmproj_filename',
        lambda repo, path: 'mmproj/Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf',
    )
    cfg = {
        'model_libraries': [{
            'id': 'test',
            'label': 'Test models',
            'path': str(tmp_path),
            'enabled': True,
            'preset': 'custom',
            'download_default': True,
        }],
        'servers': [],
    }
    plan = vision_setup.vision_plan(model_path=str(model), cfg=cfg)
    assert plan['success'] is True
    assert plan['needs_download'] is True
    assert plan['dest_path'].endswith('mmproj-Qwen3.8-27B-Uncensored-vision-Q4_K_S.gguf')


def test_vision_plan_rejects_model_outside_allowed_library(tmp_path: Path):
    model = tmp_path / 'outside.gguf'
    model.write_bytes(b'gguf')
    cfg = {
        'model_libraries': [{
            'id': 'allowed',
            'label': 'Allowed models',
            'path': str(tmp_path / 'allowed'),
            'enabled': True,
            'preset': 'custom',
            'download_default': True,
        }],
        'servers': [],
    }
    plan = vision_setup.vision_plan(model_path=str(model), cfg=cfg)
    assert plan['success'] is False
    assert 'allowed model directory' in plan['error']


def test_wire_vision_rejects_projector_outside_allowed_library(tmp_path: Path):
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'gguf')
    projector = tmp_path.parent / 'mmproj-model.gguf'
    projector.write_bytes(b'gguf')
    cfg = {
        'model_libraries': [{
            'id': 'allowed',
            'label': 'Allowed models',
            'path': str(tmp_path),
            'enabled': True,
            'preset': 'custom',
            'download_default': True,
        }],
        'servers': [],
    }
    result = vision_setup.wire_vision(
        model_path=str(model),
        mmproj_path=str(projector),
        cfg=cfg,
    )
    assert result['success'] is False
    assert 'allowed model directory' in result['error']
