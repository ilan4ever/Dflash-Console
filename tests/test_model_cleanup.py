from pathlib import Path

from api.app import _cleanup_empty_model_directories


def test_cleanup_empty_model_directories_removes_empty_descendants(monkeypatch, tmp_path: Path):
    root = tmp_path / 'models'
    root.mkdir()
    empty_nested = root / 'old' / 'nested'
    empty_nested.mkdir(parents=True)
    (root / 'keep').mkdir()
    (root / 'keep' / 'model.gguf').write_bytes(b'weights')

    monkeypatch.setattr('api.app.get_models_root', lambda _cfg: root)

    cleaned = _cleanup_empty_model_directories({})

    assert empty_nested in {Path(path) for path in cleaned}
    assert not (root / 'old').exists()
    assert (root / 'keep').is_dir()
    assert root.is_dir()


def test_cleanup_empty_model_directories_limits_delete_path_to_model_root(monkeypatch, tmp_path: Path):
    root = tmp_path / 'models'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'empty').mkdir()

    monkeypatch.setattr('api.app.get_models_root', lambda _cfg: root)

    cleaned = _cleanup_empty_model_directories({}, start_paths=[outside / 'empty'])

    assert cleaned == []
    assert (outside / 'empty').is_dir()
