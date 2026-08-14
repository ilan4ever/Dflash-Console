"""Tests for model path validation helpers."""

from __future__ import annotations

import json
from pathlib import Path

from core.model_paths import is_deletable_model_path, validate_deletable_model_path


def test_is_deletable_model_path_transformers_dir(tmp_path: Path):
    model_dir = tmp_path / 'Qwen3-0.6B'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'model.safetensors').write_text('x', encoding='utf-8')
    assert is_deletable_model_path(model_dir) is True


def test_validate_deletable_model_path_rejects_outside_roots(tmp_path: Path):
    model_dir = tmp_path / 'outside' / 'model'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    try:
        validate_deletable_model_path(str(model_dir), allowed_dirs=[tmp_path / 'library'])
    except ValueError as exc:
        assert 'under allowed' in str(exc)
    else:
        raise AssertionError('expected ValueError')
