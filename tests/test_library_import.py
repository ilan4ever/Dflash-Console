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
