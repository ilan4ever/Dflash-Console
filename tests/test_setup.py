from __future__ import annotations

from pathlib import Path

from core.setup import auto_approve_library, candidate_to_library, ensure_setup_libraries, is_setup_complete


def test_setup_complete_defaults_for_existing_libraries():
    cfg = {'model_libraries': [{'id': 'a', 'path': 'C:/models'}]}
    assert is_setup_complete(cfg) is True


def test_setup_complete_for_legacy_server_configuration():
    cfg = {'servers': [{'id': 'legacy-server', 'port': 8090}]}
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


def test_auto_approve_suggests_hf_cache_with_models():
    row = {
        'preset': 'custom',
        'path': 'C:/Users/me/.cache/huggingface/hub',
        'model_count': 40,
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


def test_setup_routes_are_registered():
    from api.app import app

    routes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, 'methods', None) or set())
    }
    assert ('/api/setup/scan', 'GET') in routes
    assert ('/api/setup/complete', 'POST') in routes


def test_ensure_setup_libraries_keeps_selected_hf_cache(tmp_path: Path, monkeypatch):
    models_root = tmp_path / 'models'
    models_root.mkdir()
    (models_root / 'gemma.gguf').write_bytes(b'gguf' * 16)
    hf_cache = tmp_path / '.cache' / 'huggingface' / 'hub'
    hf_cache.mkdir(parents=True)

    monkeypatch.setattr('core.setup.get_models_root', lambda _cfg=None: models_root)
    monkeypatch.setattr('core.setup._preset_path', lambda preset, _cfg: tmp_path / preset)

    selected = [{
        'path': str(hf_cache),
        'preset': 'custom',
        'label': 'HF cache',
        'enabled': True,
    }]
    libraries = ensure_setup_libraries(selected, cfg={})
    paths = {row['path'] for row in libraries}
    # The GGUF models root is auto-merged, and the folder the user explicitly
    # approved (the HF cache) must survive — it holds the SafeTensors models.
    assert str(models_root.resolve()) in paths
    assert str(hf_cache.resolve()) in paths
    # GGUF folders stay the download default.
    defaults = [row for row in libraries if row.get('download_default')]
    assert defaults and str(defaults[0].get('path') or '') != str(hf_cache.resolve())
