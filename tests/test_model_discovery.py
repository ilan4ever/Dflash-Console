from __future__ import annotations

from pathlib import Path

from core.model_discovery import scan_for_preset, seed_scan_roots
from core.setup import auto_approve_library


def test_auto_approve_suggests_hf_cache_with_models():
    row = {
        'preset': 'custom',
        'path': 'C:/Users/me/.cache/huggingface/hub',
        'model_count': 40,
    }
    assert auto_approve_library(row) is True


def test_auto_approve_skips_empty_hf_cache():
    row = {
        'preset': 'custom',
        'path': 'C:/Users/me/.cache/huggingface/hub',
        'model_count': 0,
    }
    assert auto_approve_library(row) is False


def test_scan_finds_gguf_before_hf_cache_exhausts_budget(tmp_path: Path, monkeypatch):
    hf = tmp_path / 'home' / '.cache' / 'huggingface' / 'hub'
    hf.mkdir(parents=True)
    for index in range(30):
        (hf / f'models--org--repo-{index}' / 'snapshots' / 'abc').mkdir(parents=True)
        (hf / f'models--org--repo-{index}' / 'snapshots' / 'abc' / 'config.json').write_text('{}')

    gguf_root = tmp_path / 'models'
    gguf_root.mkdir()
    (gguf_root / 'gemma-4-31b-q4.gguf').write_bytes(b'gguf' * 32)

    monkeypatch.setattr(
        'core.model_discovery.seed_scan_roots',
        lambda _cfg=None: [hf.parent.parent, gguf_root],
    )

    payload = scan_for_preset('custom', cfg={})
    paths = {row['path'] for row in payload.get('candidates') or []}
    assert str(gguf_root.resolve()) in paths


def test_seed_scan_roots_prioritizes_gguf_over_hf_cache(tmp_path: Path, monkeypatch):
    models_root = tmp_path / 'console-models'
    models_root.mkdir()
    monkeypatch.setattr('core.model_discovery.get_models_root', lambda _cfg=None: models_root)
    monkeypatch.setattr('core.model_discovery.get_dflash_root', lambda _cfg=None: tmp_path / 'data')
    monkeypatch.setattr('core.model_discovery._preset_path', lambda _preset, _cfg: tmp_path / 'unused')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'roaming'))

    roots = seed_scan_roots({})
    hf_cache = Path.home() / '.cache' / 'huggingface'
    hf_index = next(i for i, p in enumerate(roots) if p == hf_cache)
    models_index = roots.index(models_root)
    assert models_index > hf_index
