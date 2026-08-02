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
    plan = vision_setup.vision_plan(model_path=str(model))
    assert plan['success'] is True
    assert plan['needs_download'] is True
    assert plan['filename'] == 'gemma-4-31B-it-mmproj.gguf'
    assert plan['dest_path'].endswith('gemma-4-31B-it-mmproj.gguf')
