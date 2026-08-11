"""Tests for library import planning."""

from pathlib import Path

from core.library_import import import_plan


def test_import_plan_link_mode(tmp_path, monkeypatch):
    source = tmp_path / 'external-models'
    source.mkdir()
    (source / 'demo.gguf').write_bytes(b'0' * 128)

    cfg = {'dflash_root': str(tmp_path / 'dflash')}
    plan = import_plan(str(source), preset='dflash', mode='link', cfg=cfg)

    assert plan['mode'] == 'link'
    assert plan['source_path'] == str(source.resolve())
    assert plan['file_count'] >= 1
    assert plan['already_in_library_home'] is False


def _fw_source(tmp_path: Path, name: str = 'faster-whisper-small.en') -> Path:
    source = tmp_path / name
    source.mkdir()
    (source / 'model.bin').write_bytes(b'0' * 256)
    (source / 'config.json').write_text('{}')
    (source / 'tokenizer.json').write_text('{}')
    return source


def test_import_single_model_file_copies_faster_whisper_dir(tmp_path):
    from core.library_import import import_single_model_file

    source = _fw_source(tmp_path)
    cfg = {'dflash_root': str(tmp_path / 'dflash'), 'models_root': str(tmp_path / 'models')}
    result = import_single_model_file(str(source), mode='copy', cfg=cfg)
    assert result['success'] is True
    assert result['runtime_id'] == 'faster-whisper'
    assert result['model_kind'] == 'faster-whisper'
    dest = Path(result['library_path'])
    assert dest.is_dir()
    assert (dest / 'model.bin').is_file()
    assert source.is_dir()  # copy keeps the original


def test_import_single_model_file_names_faster_whisper_dir_from_repo(tmp_path):
    from core.library_import import import_single_model_file

    # HF snapshot layout: importing should name the Console folder after the
    # repo (faster-whisper-small.en), not the raw snapshot hash, so the copy
    # stays recognisable as a whisper model.
    source = tmp_path / 'models--Systran--faster-whisper-small.en' / 'snapshots' / 'abc123'
    source.mkdir(parents=True)
    (source / 'model.bin').write_bytes(b'0' * 128)
    cfg = {'dflash_root': str(tmp_path / 'dflash'), 'models_root': str(tmp_path / 'models')}
    result = import_single_model_file(str(source), mode='copy', cfg=cfg)
    dest = Path(result['library_path'])
    assert dest.name == 'faster-whisper-small.en'
    assert 'faster-whisper' in dest.name


def test_import_single_model_file_moves_faster_whisper_dir(tmp_path):
    from core.library_import import import_single_model_file

    source = _fw_source(tmp_path, name='fw-move')
    cfg = {'dflash_root': str(tmp_path / 'dflash'), 'models_root': str(tmp_path / 'models')}
    result = import_single_model_file(str(source), mode='move', cfg=cfg)
    assert result['success'] is True
    assert result['runtime_id'] == 'faster-whisper'
    assert not source.exists()  # moved away
    assert Path(result['library_path']).is_dir()


def test_import_single_model_file_rejects_other_formats(tmp_path):
    from core.library_import import import_single_model_file

    source = tmp_path / 'some-model.onnx'
    source.write_bytes(b'x')
    cfg = {'dflash_root': str(tmp_path / 'dflash'), 'models_root': str(tmp_path / 'models')}
    try:
        import_single_model_file(str(source), mode='copy', cfg=cfg)
        raise AssertionError('expected ValueError for non-importable format')
    except ValueError:
        pass


def test_import_duplicate_returns_exists_and_requires_overwrite(tmp_path):
    from core.library_import import import_single_model_file

    source = _fw_source(tmp_path, name='faster-whisper-small.en')
    cfg = {'dflash_root': str(tmp_path / 'dflash'), 'models_root': str(tmp_path / 'models')}
    first = import_single_model_file(str(source), mode='copy', cfg=cfg)
    assert first['success'] is True
    dest = Path(first['library_path'])

    # Second import without overwrite must NOT silently create name-2.
    dup = import_single_model_file(str(source), mode='copy', cfg=cfg)
    assert dup['success'] is False
    assert dup['exists'] is True
    assert dup['existing_path'] == str(dest)
    assert not (dest.parent / 'faster-whisper-small.en-2').exists()

    # Overwrite replaces the existing folder (fresh model.bin content).
    (source / 'model.bin').write_bytes(b'NEW' * 32)
    replaced = import_single_model_file(str(source), mode='copy', overwrite=True, cfg=cfg)
    assert replaced['success'] is True
    assert replaced['library_path'] == str(dest)
    assert (dest / 'model.bin').read_bytes()[:3] == b'NEW'
    assert not (dest.parent / 'faster-whisper-small.en-2').exists()


def test_import_duplicate_gguf_returns_exists(tmp_path):
    from core.library_import import import_single_model_file

    source = tmp_path / 'demo.gguf'
    source.write_bytes(b'0' * 128)
    cfg = {'dflash_root': str(tmp_path / 'dflash'), 'models_root': str(tmp_path / 'models')}
    first = import_single_model_file(str(source), mode='copy', cfg=cfg)
    assert first['success'] is True
    dup = import_single_model_file(str(source), mode='copy', cfg=cfg)
    assert dup['success'] is False
    assert dup['exists'] is True
    assert not (Path(first['library_path']).parent.parent / 'demo-2').exists()

