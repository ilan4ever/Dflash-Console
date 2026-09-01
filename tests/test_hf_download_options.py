from __future__ import annotations

from core.huggingface import build_download_options


def _shard(name: str, size_bytes: int) -> dict:
    return {'filename': name, 'size_bytes': size_bytes}


def test_build_download_options_collapses_safetensors_shards():
    files = [
        _shard('model-00001-of-00048.safetensors', 1_010_000_000),
        _shard('model-00002-of-00048.safetensors', 3_300_000_000),
        _shard('model-00003-of-00048.safetensors', 3_300_000_000),
    ]
    options = build_download_options(files)
    assert len(options) == 1
    row = options[0]
    assert row['kind'] == 'sharded'
    assert row['shard_count'] == 48
    assert row['file_count'] == 3
    assert row['label'] == 'Full model (48 files)'
    assert row['size_gb'] is not None
    assert row['size_gb'] > 7


def test_build_download_options_sums_gguf_quant_shards():
    files = [
        _shard('Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf', 3_700_000),
        _shard('Laguna-S-2.1-UD-Q4_K_M-00002-of-00003.gguf', 50_000_000_000),
        _shard('Laguna-S-2.1-UD-Q4_K_M-00003-of-00003.gguf', 23_000_000_000),
    ]
    options = build_download_options(files)
    assert len(options) == 1
    row = options[0]
    assert row['kind'] == 'quant'
    assert row['shard_count'] == 3
    assert row['size_gb'] is not None
    assert row['size_gb'] > 65


def test_preferred_download_size_sums_safetensors_shards():
    from core.huggingface import _preferred_download_size

    siblings = [
        {'rfilename': 'model-00001-of-00048.safetensors', 'size': 1_010_000_000},
        {'rfilename': 'model-00002-of-00048.safetensors', 'size': 3_300_000_000},
        {'rfilename': 'model-00003-of-00048.safetensors', 'size': 3_300_000_000},
    ]
    size_gb, label = _preferred_download_size(siblings)
    assert size_gb is not None
    assert size_gb > 7
    assert 'GB' in label


def test_build_download_options_keeps_distinct_gguf_quants_separate():
    files = [
        _shard('model-Q4_K_M.gguf', 70_000_000_000),
        _shard('model-Q8_0.gguf', 120_000_000_000),
    ]
    options = build_download_options(files)
    assert len(options) == 2
    labels = {row['label'] for row in options}
    assert 'model-Q4_K_M.gguf' in labels
    assert 'model-Q8_0.gguf' in labels
