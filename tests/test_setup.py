from __future__ import annotations

from pathlib import Path

from core.setup import auto_approve_library, candidate_to_library, is_setup_complete


def test_setup_complete_defaults_for_existing_libraries():
    cfg = {'model_libraries': [{'id': 'a', 'path': 'C:/models'}]}
    assert is_setup_complete(cfg) is True


def test_setup_incomplete_when_flag_false():
    cfg = {'setup_complete': False, 'model_libraries': [{'id': 'a', 'path': 'C:/models'}]}
    assert is_setup_complete(cfg) is False


def test_auto_approve_lmstudio_path():
    row = {
        'preset': 'lmstudio',
        'path': 'C:/Users/me/.lmstudio/models',
        'model_count': 3,
    }
    assert auto_approve_library(row) is True


def test_candidate_to_library_preserves_path(tmp_path: Path):
    root = tmp_path / 'models'
    root.mkdir()
    row = {
        'path': str(root),
        'preset': 'dflash',
        'label': 'Console models',
        'model_count': 2,
    }
    lib = candidate_to_library(row, index=0)
    assert lib['path'] == str(root)
    assert lib['download_default'] is True
