from __future__ import annotations

from pathlib import Path

from core.huggingface import _missing_weight_shard_filenames


def test_missing_weight_shard_filenames_lists_remaining(tmp_path):
    model_dir = tmp_path / 'deepseek-ai' / 'DeepSeek-V4-Flash-0731'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    for index in (1, 2, 3):
        (model_dir / f'model-{index:05d}-of-00048.safetensors').write_bytes(b'x' * 128)

    missing = _missing_weight_shard_filenames(model_dir)
    assert missing[0] == 'model-00004-of-00048.safetensors'
    assert len(missing) == 45
    assert missing[-1] == 'model-00048-of-00048.safetensors'
