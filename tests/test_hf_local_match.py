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


def test_catalog_ready_matches_hf_layout_stack(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    target = root / 'google' / 'gemma-4-12B-it-qat-q4_0-gguf' / 'gemma-4-12b-it-qat-q4_0.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'gguf')
    draft = tmp_path / 'gemma-4-12B-it-DFlash-Q4_K_M.gguf'
    draft.write_bytes(b'draft')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [{
            'id': 'gemma-12',
            'model_id': 'gemma-12',
            'label': 'Gemma 4 12B DFlash',
            'enabled': True,
            'profile': 'gemma-chat',
            'target_path': str(target),
            'draft_path': str(draft),
            'port': 8081,
        }],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: cfg['servers'])
    monkeypatch.setattr('core.local_models._CATALOG_CACHE', None)
    monkeypatch.setattr('core.local_models._CATALOG_CACHE_AT', 0.0)

    from core.hf_local_match import is_catalog_ready_to_load

    assert is_catalog_ready_to_load('google/gemma-4-12B-it-qat-q4_0-gguf', cfg=cfg) is False
    assert is_catalog_ready_to_load('google/gemma-4-12B-it-qat-q4_0-gguf-dflash', tags=['dflash'], cfg=cfg) is True
    assert is_catalog_ready_to_load('google/gemma-4-31B-it-qat-q4_0-gguf-dflash', tags=['dflash'], cfg=cfg) is False


def test_catalog_ready_rejects_qwen36_when_stack_is_qwen35(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    root.mkdir(parents=True)
    target = root / 'Qwen3.5-27B-Q4_K_M.gguf'
    target.write_bytes(b'gguf')
    draft = root / 'Qwen3.5-27B-DFlash-F16.gguf'
    draft.write_bytes(b'draft')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [{
            'id': 'qwen-35',
            'model_id': 'qwen-35',
            'label': 'Qwen3.5 27B DFlash',
            'enabled': True,
            'profile': 'qwen-dflash',
            'target_path': str(target),
            'draft_path': str(draft),
            'port': 8082,
        }],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: cfg['servers'])
    monkeypatch.setattr('core.local_models._CATALOG_CACHE', None)
    monkeypatch.setattr('core.local_models._CATALOG_CACHE_AT', 0.0)

    from core.hf_local_match import is_catalog_ready_to_load

    assert is_catalog_ready_to_load(
        'Alittlehammmer/Qwen3.5-27B-DFlash-GGUF-llama.cpp',
        title='Qwen3.5 27B DFlash',
        tags=['dflash'],
        cfg=cfg,
    ) is True
    assert is_catalog_ready_to_load(
        'Alittlehammmer/Qwen3.6-27B-DFlash-GGUF-llama.cpp',
        title='Qwen3.6 27B DFlash',
        tags=['dflash'],
        cfg=cfg,
    ) is False
