from __future__ import annotations

from pathlib import Path

from core.local_models import _has_vision_support


def test_vision_detects_mmproj_sibling(tmp_path: Path):
    target = tmp_path / 'gemma-4-12b-it.gguf'
    projector = tmp_path / 'mmproj-gemma-4-12b-it-f16.gguf'
    target.write_bytes(b'gguf')
    projector.write_bytes(b'gguf')
    assert _has_vision_support(target) is True


def test_vision_detects_vl_name(tmp_path: Path):
    target = tmp_path / 'qwen2-vl-7b.gguf'
    target.write_bytes(b'gguf')
    assert _has_vision_support(target) is True


def test_scanned_gguf_is_loadable(tmp_path: Path):
    from core.local_models import _scan_gguf

    root = tmp_path / 'models' / 'gguf'
    root.mkdir(parents=True)
    model = root / 'DeepSeek-V2-Lite-Q4_K_M.gguf'
    model.write_bytes(b'gguf')
    rows = _scan_gguf(root, source='library')
    assert len(rows) == 1
    row = rows[0]
    assert row['arch'] == 'deepseekv2'


def test_split_gguf_shards_are_grouped_with_combined_size(tmp_path: Path):
    from core.local_models import _collapse_split_shards

    rows = []
    for index, size in enumerate((2, 3, 4), start=1):
        path = tmp_path / f'Laguna-S-2.1-UD-Q4_K_M-{index:05d}-of-00003.gguf'
        row = {'path': str(path), 'filename': path.name, 'size_gb': float(size)}
        rows.append(row)

    grouped = _collapse_split_shards(rows)

    assert len(grouped) == 1
    assert grouped[0]['split_count'] == 3
    assert grouped[0]['split_total'] == 3
    assert grouped[0]['size_gb'] == 9.0
    assert grouped[0]['split_files'] == [row['path'] for row in rows]


def test_identical_files_in_multiple_roots_collapse_to_one_row(tmp_path: Path):
    from core.local_models import _collapse_identical_files

    first = tmp_path / 'dflash' / 'gemma-4-12B-it-Q4_K_M.gguf'
    second = tmp_path / 'lmstudio' / 'gemma-4-12B-it-Q4_K_M.gguf'
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b'identical gguf payload')
    second.write_bytes(first.read_bytes())

    rows = _collapse_identical_files([
        {'filename': first.name, 'path': str(first), 'source': 'dflash-stack'},
        {'filename': second.name, 'path': str(second), 'source': 'lmstudio'},
    ])

    assert len(rows) == 1
    assert rows[0]['path'] == str(first)
    assert rows[0]['duplicate_identical'] is True
    assert rows[0]['duplicate_count'] == 2
    assert set(rows[0]['duplicate_paths']) == {str(first), str(second)}


def test_same_name_different_content_is_not_collapsed(tmp_path: Path):
    from core.local_models import _collapse_identical_files

    first = tmp_path / 'first' / 'model.gguf'
    second = tmp_path / 'second' / 'model.gguf'
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b'first payload')
    second.write_bytes(b'second payload')

    rows = _collapse_identical_files([
        {'filename': first.name, 'path': str(first)},
        {'filename': second.name, 'path': str(second)},
    ])

    assert len(rows) == 2


def test_plain_gguf_catalog_entry_is_loadable(tmp_path: Path, monkeypatch):
    from core import local_models as lm
    from core.config import load_config

    root = tmp_path / 'models' / 'gguf'
    root.mkdir(parents=True)
    model = root / 'DeepSeek-V2-Lite-Q4_K_M.gguf'
    model.write_bytes(b'gguf')

    cfg = load_config()
    cfg = {
        **cfg,
        'model_libraries': [{
            'id': 'test-lib',
            'path': str(root.parent),
            'enabled': True,
            'model_types': ['gguf'],
        }],
    }
    monkeypatch.setattr(lm, 'disk_scan_roots', lambda _cfg: [(root, 'library')])
    monkeypatch.setattr(lm, '_profile_catalog', lambda _cfg: {})
    monkeypatch.setattr(lm, '_dflash_stack_supplement', lambda *args, **kwargs: [])
    lm.invalidate_model_catalog_cache()
    payload = lm.list_local_models(cfg=cfg, scan_disk=True, force_refresh=True)
    plain = [row for row in payload['models'] if row.get('plain_gguf')]
    assert len(plain) == 1
    assert plain[0]['loadable'] is True
    assert plain[0]['server_id'] == ''


def test_stack_path_access_matches_enabled_model_libraries(tmp_path: Path):
    from core.local_models import _mark_stack_path_access

    allowed = tmp_path / 'allowed'
    outside = tmp_path / 'outside'
    allowed.mkdir()
    outside.mkdir()
    cfg = {
        'model_libraries': [{
            'id': 'allowed',
            'path': str(allowed),
            'enabled': True,
            'preset': 'custom',
        }],
    }
    rows = [
        {'path': str(allowed / 'target.gguf')},
        {'path': str(outside / 'target.gguf')},
        {'path': ''},
    ]

    _mark_stack_path_access(rows, cfg)

    assert [row['stack_path_allowed'] for row in rows] == [True, False, False]
