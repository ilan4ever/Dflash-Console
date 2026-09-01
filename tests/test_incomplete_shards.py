from __future__ import annotations

from pathlib import Path

from core.local_models import _annotate_hf_dir_completeness, _weight_shard_status


def test_weight_shard_status_detects_incomplete_safetensors(tmp_path: Path):
    (tmp_path / 'config.json').write_text('{"model_type":"deepseek"}', encoding='utf-8')
    (tmp_path / 'model-00001-of-00048.safetensors').write_bytes(b'x' * 1024)
    status = _weight_shard_status(tmp_path)
    assert status['incomplete'] is True
    assert status['shard_present'] == 1
    assert status['shard_total'] == 48


def test_weight_shard_status_complete_when_all_present(tmp_path: Path):
    for index in range(1, 4):
        (tmp_path / f'model-{index:05d}-of-00003.safetensors').write_bytes(b'x' * 64)
    status = _weight_shard_status(tmp_path)
    assert status['incomplete'] is False
    assert status['shard_present'] == 3
    assert status['shard_total'] == 3


def test_annotate_marks_row_incomplete(tmp_path: Path):
    (tmp_path / 'config.json').write_text('{"model_type":"deepseek"}', encoding='utf-8')
    (tmp_path / 'model-00001-of-00048.safetensors').write_bytes(b'x' * 2048)
    row = {'size_gb': 0.01, 'loadable': True}
    _annotate_hf_dir_completeness(row, tmp_path)
    assert row['incomplete'] is True
    assert row['loadable'] is False
    assert row['shard_present'] == 1
    assert row['shard_total'] == 48
