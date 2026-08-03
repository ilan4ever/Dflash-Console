from __future__ import annotations

from pathlib import Path

import pytest

import core.vision_setup as vision_setup


def test_infer_hf_repo_from_lmstudio_layout():
    path = Path('C:/Users/me/.lmstudio/models/google/gemma-4-31B-it-qat-q4_0-gguf/model.gguf')
    assert vision_setup.infer_hf_repo_from_path(path) == 'google/gemma-4-31B-it-qat-q4_0-gguf'


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
