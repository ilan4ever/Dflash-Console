from __future__ import annotations

from pathlib import Path

import pytest


def _cfg_with_duplicate_filenames(tmp_path: Path) -> dict:
    file_a = tmp_path / 'ATH-MaaS_OvisOCR2-Q8_0' / 'ATH-MaaS_OvisOCR2-Q8_0.gguf'
    file_b = tmp_path / 'ATH-MaaS_OvisOCR2-Q8_0-2' / 'ATH-MaaS_OvisOCR2-Q8_0.gguf'
    file_a.parent.mkdir(parents=True)
    file_b.parent.mkdir(parents=True)
    file_a.write_bytes(b'a' * 128)
    file_b.write_bytes(b'b' * 128)
    return {
        'ui_port': 8900,
        'dflash_root': str(tmp_path),
        'models_root': str(tmp_path),
        'servers': [
            {
                'id': 'ath-maas-ovisocr2-q8-0',
                'label': 'Profile A',
                'profile': 'generic-ar',
                'port': 8091,
                'host': '127.0.0.1',
                'api_url': 'http://127.0.0.1:8091/v1',
                'model_id': 'ath-maas-ovisocr2-q8-0',
                'target_path': str(file_a),
                'enabled': True,
            },
            {
                'id': 'ath-maas-ovisocr2-q8-0-2',
                'label': 'Profile B',
                'profile': 'generic-ar',
                'port': 8090,
                'host': '127.0.0.1',
                'api_url': 'http://127.0.0.1:8090/v1',
                'model_id': 'ath-maas-ovisocr2-q8-0',
                'target_path': str(file_b),
                'enabled': True,
            },
        ],
    }


def test_servers_referencing_model_matches_exact_path_only(tmp_path: Path):
    from api.app import _servers_referencing_model

    cfg = _cfg_with_duplicate_filenames(tmp_path)
    file_b = Path(cfg['servers'][1]['target_path'])
    matches = _servers_referencing_model(file_b, cfg)
    assert len(matches) == 1
    assert matches[0]['id'] == 'ath-maas-ovisocr2-q8-0-2'


def test_delete_model_file_removes_only_selected_duplicate(tmp_path: Path, monkeypatch):
    from api.app import delete_model_file

    cfg = _cfg_with_duplicate_filenames(tmp_path)
    file_a = Path(cfg['servers'][0]['target_path'])
    file_b = Path(cfg['servers'][1]['target_path'])
    monkeypatch.setattr('api.app.load_config', lambda: cfg)
    monkeypatch.setattr('core.config.save_config', lambda _cfg: None)
    monkeypatch.setattr('api.app.server_unload', lambda *_args, **_kwargs: None)
    monkeypatch.setattr('api.app.invalidate_model_catalog_cache', lambda: None)
    monkeypatch.setattr('api.app._allowed_model_roots', lambda _cfg: [tmp_path.resolve()])

    result = delete_model_file(path=str(file_b), server_id='')
    assert result['success'] is True
    assert not file_b.exists()
    assert file_a.is_file()
    remaining_ids = {row['id'] for row in cfg['servers']}
    assert 'ath-maas-ovisocr2-q8-0' in remaining_ids
    assert 'ath-maas-ovisocr2-q8-0-2' not in remaining_ids


def test_delete_hf_hub_repo_removes_folder(tmp_path: Path, monkeypatch):
    import json

    from api.app import delete_model_file

    repo = tmp_path / 'hub' / 'models--Qwen--Qwen2.5-32B-Instruct'
    snapshot = repo / 'snapshots' / 'abc123'
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}), encoding='utf-8')
    (snapshot / 'tokenizer.json').write_text('tiny', encoding='utf-8')
    (repo / 'blobs').mkdir()
    (repo / 'blobs' / 'sha').write_bytes(b'x' * 32)

    cfg = {'ui_port': 8900, 'dflash_root': str(tmp_path), 'models_root': str(tmp_path), 'servers': []}
    monkeypatch.setattr('api.app.load_config', lambda: cfg)
    monkeypatch.setattr('core.config.save_config', lambda _cfg: None)
    monkeypatch.setattr('api.app.server_unload', lambda *_args, **_kwargs: None)
    monkeypatch.setattr('api.app.invalidate_model_catalog_cache', lambda: None)
    monkeypatch.setattr('api.app._delete_allowed_roots', lambda _cfg: [tmp_path.resolve()])

    result = delete_model_file(path=str(snapshot), server_id='')
    assert result['success'] is True
    assert result['model'] == 'Qwen/Qwen2.5-32B-Instruct'
    assert not repo.exists()
    assert not snapshot.exists()


def test_delete_hf_hub_refuses_library_root(tmp_path: Path, monkeypatch):
    from api.app import delete_model_file
    from fastapi import HTTPException

    hub = tmp_path / 'hub'
    hub.mkdir()
    cfg = {'ui_port': 8900, 'dflash_root': str(tmp_path), 'models_root': str(tmp_path), 'servers': []}
    monkeypatch.setattr('api.app.load_config', lambda: cfg)
    monkeypatch.setattr('api.app.invalidate_model_catalog_cache', lambda: None)
    monkeypatch.setattr('api.app._delete_allowed_roots', lambda _cfg: [hub.resolve()])

    try:
        delete_model_file(path=str(hub), server_id='')
        raise AssertionError('expected refuse library root')
    except HTTPException as exc:
        assert exc.status_code == 403
