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
