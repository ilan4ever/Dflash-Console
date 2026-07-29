from pathlib import Path

from core.hf_local_match import find_local_matches, primary_local_match


def test_find_exact_hf_layout_match(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    target = root / 'Alittlehammmer' / 'Qwen3.6-27B-DFlash-GGUF-llama.cpp' / 'Qwen3.6-27B-DFlash-Q4_K_M.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'gguf')

    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'DFlash models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])

    matches = find_local_matches(
        'Alittlehammmer/Qwen3.6-27B-DFlash-GGUF-llama.cpp',
        'Qwen3.6-27B-DFlash-Q4_K_M.gguf',
        cfg=cfg,
    )
    assert len(matches) == 1
    assert matches[0]['path'] == str(target.resolve())
    assert matches[0]['match_type'] == 'exact_path'


def test_primary_local_match_missing(tmp_path, monkeypatch):
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'DFlash models',
            'path': str(tmp_path / 'models'),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])

    assert primary_local_match('author/repo', 'missing.gguf', cfg=cfg) is None
