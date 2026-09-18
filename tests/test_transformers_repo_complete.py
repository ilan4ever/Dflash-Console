from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from core.huggingface import (
    _transformers_companion_allow_patterns,
    _transformers_completion_remote_files,
    _transformers_hub_local_dir,
    _transformers_ignore_patterns,
    _transformers_remote_prefix,
)


def test_remote_prefix_from_quant_dest_folder(tmp_path: Path):
    dest = tmp_path / 'Aniemore' / 'model' / 'fp8'
    dest.mkdir(parents=True)
    assert _transformers_remote_prefix(dest) == 'fp8/'
    assert _transformers_remote_prefix(tmp_path / 'Aniemore' / 'model') == ''


def test_remote_prefix_from_nested_filename_when_dest_is_root(tmp_path: Path):
    dest = tmp_path / 'repo'
    dest.mkdir()
    assert _transformers_remote_prefix(dest, filename='fp8/model.safetensors') == 'fp8/'
    assert _transformers_remote_prefix(dest, filename='model.safetensors') == ''


def test_companion_allow_patterns_scoped_to_prefix():
    allows = _transformers_companion_allow_patterns('fp8/')
    assert 'fp8/config.json' in allows
    assert 'fp8/tokenizer*' in allows
    assert 'fp8/model-*-of-*.safetensors' in allows
    assert not any(item.startswith('int4/') for item in allows)
    assert not any(item.startswith('int8/') for item in allows)
    root = _transformers_companion_allow_patterns('')
    assert 'config.json' in root
    assert 'model-*-of-*.safetensors' in root


def test_ignore_patterns_exclude_unrelated_quant_folders():
    ignores = _transformers_ignore_patterns('fp8/')
    assert 'int4/**' in ignores
    assert 'int8/**' in ignores
    assert 'model.safetensors' in ignores
    assert 'fp8/**' not in ignores

    root_ignores = _transformers_ignore_patterns('')
    assert '*/model.safetensors' in root_ignores
    assert 'fp8/**' in root_ignores
    assert 'int4/**' in root_ignores


def test_hub_local_dir_maps_prefix_folder(tmp_path: Path):
    repo = tmp_path / 'repo'
    fp8 = repo / 'fp8'
    fp8.mkdir(parents=True)
    assert _transformers_hub_local_dir(fp8, 'fp8/') == repo
    assert _transformers_hub_local_dir(repo, '') == repo
    assert _transformers_hub_local_dir(repo, 'fp8/') == repo


def test_completion_remote_files_skips_other_quant_packs(tmp_path: Path):
    dest = tmp_path / 'fp8'
    dest.mkdir()
    (dest / 'model.safetensors').write_bytes(b'weight')

    siblings: list[dict[str, Any]] = [
        {'rfilename': 'fp8/model.safetensors', 'size': 1000},
        {'rfilename': 'fp8/config.json', 'size': 120},
        {'rfilename': 'fp8/tokenizer_config.json', 'size': 80},
        {'rfilename': 'fp8/recipe.yaml', 'size': 40},
        {'rfilename': 'fp8/helper.py', 'size': 50},
        {'rfilename': 'int4/model.safetensors', 'size': 900},
        {'rfilename': 'int4/config.json', 'size': 110},
        {'rfilename': 'int8/model.safetensors', 'size': 950},
        {'rfilename': 'model.safetensors', 'size': 5000},
        {'rfilename': 'config.json', 'size': 200},
    ]

    with patch('core.huggingface._fetch_repo_siblings_with_blobs', return_value=siblings):
        files = _transformers_completion_remote_files('org/model', dest, prefix='fp8/')

    remotes = [name for name, _ in files]
    assert 'fp8/config.json' in remotes
    assert 'fp8/tokenizer_config.json' in remotes
    assert 'fp8/recipe.yaml' in remotes
    assert 'fp8/helper.py' in remotes
    assert 'int4/model.safetensors' not in remotes
    assert 'int4/config.json' not in remotes
    assert 'int8/model.safetensors' not in remotes
    assert 'model.safetensors' not in remotes
    assert 'config.json' not in remotes


def test_completion_remote_files_root_skips_nested_quants(tmp_path: Path):
    dest = tmp_path / 'repo'
    dest.mkdir()
    (dest / 'model.safetensors').write_bytes(b'weight')

    siblings: list[dict[str, Any]] = [
        {'rfilename': 'model.safetensors', 'size': 5000},
        {'rfilename': 'config.json', 'size': 200},
        {'rfilename': 'tokenizer.json', 'size': 300},
        {'rfilename': 'fp8/model.safetensors', 'size': 1000},
        {'rfilename': 'fp8/config.json', 'size': 120},
        {'rfilename': 'int4/model.safetensors', 'size': 900},
    ]

    with patch('core.huggingface._fetch_repo_siblings_with_blobs', return_value=siblings):
        files = _transformers_completion_remote_files('org/model', dest, prefix='')

    remotes = [name for name, _ in files]
    assert 'config.json' in remotes
    assert 'tokenizer.json' in remotes
    assert 'fp8/model.safetensors' not in remotes
    assert 'fp8/config.json' not in remotes
    assert 'int4/model.safetensors' not in remotes
