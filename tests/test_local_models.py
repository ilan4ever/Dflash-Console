from __future__ import annotations

from pathlib import Path

from core.local_models import _has_vision_support


def test_vision_detects_mmproj_sibling(tmp_path: Path):
    target = tmp_path / 'gemma-4-12b-it.gguf'
    projector = tmp_path / 'mmproj-gemma-4-12b-it-f16.gguf'
    target.write_bytes(b'gguf')
    projector.write_bytes(b'gguf')
    assert _has_vision_support(target) is True


def test_guess_arch_known_families():
    from core.local_models import _guess_arch

    assert _guess_arch('ATH-MaaS_OvisOCR2-Q8_0.gguf') == 'ovis'
    assert _guess_arch('Qwen3.5-27B-Q4_K_M.gguf') == 'qwen'
    assert _guess_arch('gemma-4-31B_q4_0-it.gguf') == 'gemma4'
    assert _guess_arch('DeepSeek-R1-Distill-70B-Q4_K_S.gguf') == 'deepseekv2'
    assert _guess_arch('Llama-3.3-70B-Q4_K_M.gguf') == 'llama'
    assert _guess_arch('Mistral-7B-Instruct-Q4_K_M.gguf') == 'mistral'
    assert _guess_arch('whisper-large-v3-q8_0.gguf') == 'whisper'
    assert _guess_arch('Chandra-OCR-Q4_K_S.gguf') == 'chandra'
    assert _guess_arch('GLM-4.7-Flash-REAP-23B-Q4_K_S.gguf') == 'glm'
    assert _guess_arch('gpt-4o-mini-Q4_K_M.gguf') == 'gpt'
    assert _guess_arch('phi-4-14b-Q4_K_M.gguf') == 'phi'
    assert _guess_arch('sophia-test-Q4_K_M.gguf') == 'unknown'


def test_vision_detects_vl_name(tmp_path: Path):
    target = tmp_path / 'qwen2-vl-7b.gguf'
    target.write_bytes(b'gguf')
    assert _has_vision_support(target) is True


def test_guess_reasoning_known_names():
    from core.local_models import _guess_reasoning

    # Explicit reasoning markers.
    assert _guess_reasoning('DeepSeek-R1-Distill-70B-Q4_K_S.gguf') is True
    assert _guess_reasoning('Qwen3.5-QwQ-32B-Q4_K_M.gguf') is True
    assert _guess_reasoning('glm-4.5-air-14B.gguf') is True
    assert _guess_reasoning('o3-mini-14b.gguf') is True
    assert _guess_reasoning('phi-4-reasoning-14b.gguf') is True
    assert _guess_reasoning('kimi-thinking-32b.gguf') is True
    # Thinking templates from the architecture.
    assert _guess_reasoning('gemma-4-12b-it-qat-q4-0.gguf') is True
    assert _guess_reasoning('gemma-4-31B_q4_0-it.gguf') is True
    assert _guess_reasoning('Qwen3.5-27B-Q4_K_M.gguf') is True
    # Non-reasoning families.
    assert _guess_reasoning('Llama-3.3-70B-Q4_K_M.gguf') is False
    assert _guess_reasoning('Mistral-7B-Instruct-Q4_K_M.gguf') is False
    assert _guess_reasoning('nomic-embed-text-v1.5.gguf') is False
    assert _guess_reasoning('whisper-large-v3-q8_0.gguf') is False
    assert _guess_reasoning('') is False


def test_model_has_reasoning_from_caps_and_names():
    from core.local_models import model_has_reasoning

    assert model_has_reasoning({'capabilities': ['instruct', 'reasoning'], 'label': 'X'}) is True
    assert model_has_reasoning({'reasoning': True, 'label': 'X'}) is True
    # Raw server entries fall back to name heuristics.
    assert model_has_reasoning({'label': 'Gemma 4 12B DFlash', 'model_id': 'gemma-4-12b-it-qat'}) is True
    assert model_has_reasoning({'label': 'nomic-embed', 'model_id': 'nomic-embed-text-v1.5'}) is False


def test_scanned_gguf_reasoning_capability(tmp_path: Path):
    from core.local_models import _scan_gguf

    root = tmp_path / 'models' / 'gguf'
    root.mkdir(parents=True)
    (root / 'gemma-4-12b-it-q4-k-m.gguf').write_bytes(b'gguf')
    (root / 'nomic-embed-text-v1.5.gguf').write_bytes(b'gguf')
    rows = _scan_gguf(root, source='library')
    by_name = {row['filename']: row for row in rows}
    assert 'reasoning' in by_name['gemma-4-12b-it-q4-k-m.gguf']['capabilities']
    assert by_name['gemma-4-12b-it-q4-k-m.gguf']['reasoning'] is True
    assert 'reasoning' not in by_name['nomic-embed-text-v1.5.gguf']['capabilities']
    assert by_name['nomic-embed-text-v1.5.gguf']['reasoning'] is False


def test_scanned_gguf_is_loadable(tmp_path: Path):
    from core.local_models import _scan_gguf

    root = tmp_path / 'models' / 'gguf'
    root.mkdir(parents=True)
    model = root / 'DeepSeek-V2-Lite-Q4_K_M.gguf'
    model.write_bytes(b'gguf')
    rows = _scan_gguf(root, source='library')
    assert len(rows) == 1
    row = rows[0]
    assert row['arch'] == 'deepseekv2'


def test_split_gguf_shards_are_grouped_with_combined_size(tmp_path: Path):
    from core.local_models import _collapse_split_shards

    rows = []
    for index, size in enumerate((2, 3, 4), start=1):
        path = tmp_path / f'Laguna-S-2.1-UD-Q4_K_M-{index:05d}-of-00003.gguf'
        row = {'path': str(path), 'filename': path.name, 'size_gb': float(size)}
        rows.append(row)

    grouped = _collapse_split_shards(rows)

    assert len(grouped) == 1
    assert grouped[0]['split_count'] == 3
    assert grouped[0]['split_total'] == 3
    assert grouped[0]['size_gb'] == 9.0
    assert grouped[0]['split_files'] == [row['path'] for row in rows]


def test_identical_files_in_multiple_roots_collapse_to_one_row(tmp_path: Path):
    from core.local_models import _collapse_identical_files

    first = tmp_path / 'dflash' / 'gemma-4-12B-it-Q4_K_M.gguf'
    second = tmp_path / 'lmstudio' / 'gemma-4-12B-it-Q4_K_M.gguf'
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b'identical gguf payload')
    second.write_bytes(first.read_bytes())

    rows = _collapse_identical_files([
        {'filename': first.name, 'path': str(first), 'source': 'dflash-stack'},
        {'filename': second.name, 'path': str(second), 'source': 'lmstudio'},
    ])

    assert len(rows) == 1
    assert rows[0]['path'] == str(first)
    assert rows[0]['duplicate_identical'] is True
    assert rows[0]['duplicate_count'] == 2
    assert set(rows[0]['duplicate_paths']) == {str(first), str(second)}


def test_same_name_different_content_is_not_collapsed(tmp_path: Path):
    from core.local_models import _collapse_identical_files

    first = tmp_path / 'first' / 'model.gguf'
    second = tmp_path / 'second' / 'model.gguf'
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b'first payload')
    second.write_bytes(b'second payload')

    rows = _collapse_identical_files([
        {'filename': first.name, 'path': str(first)},
        {'filename': second.name, 'path': str(second)},
    ])

    assert len(rows) == 2


def test_plain_gguf_catalog_entry_is_loadable(tmp_path: Path, monkeypatch):
    from core import local_models as lm
    from core.config import load_config

    root = tmp_path / 'models' / 'gguf'
    root.mkdir(parents=True)
    model = root / 'DeepSeek-V2-Lite-Q4_K_M.gguf'
    model.write_bytes(b'gguf')

    cfg = load_config()
    cfg = {
        **cfg,
        'model_libraries': [{
            'id': 'test-lib',
            'path': str(root.parent),
            'enabled': True,
            'model_types': ['gguf'],
        }],
    }
    monkeypatch.setattr(lm, 'disk_scan_roots', lambda _cfg: [(root, 'library')])
    monkeypatch.setattr(lm, '_profile_catalog', lambda _cfg: {})
    monkeypatch.setattr(lm, '_dflash_stack_supplement', lambda *args, **kwargs: [])
    lm.invalidate_model_catalog_cache()
    payload = lm.list_local_models(cfg=cfg, scan_disk=True, force_refresh=True)
    plain = [row for row in payload['models'] if row.get('plain_gguf')]
    assert len(plain) == 1
    assert plain[0]['loadable'] is True
    assert plain[0]['server_id'] == ''


def test_stack_path_access_matches_enabled_model_libraries(tmp_path: Path):
    from core.local_models import _mark_stack_path_access

    allowed = tmp_path / 'allowed'
    outside = tmp_path / 'outside'
    allowed.mkdir()
    outside.mkdir()
    cfg = {
        'model_libraries': [{
            'id': 'allowed',
            'path': str(allowed),
            'enabled': True,
            'preset': 'custom',
        }],
    }
    rows = [
        {'path': str(allowed / 'target.gguf')},
        {'path': str(outside / 'target.gguf')},
        {'path': ''},
    ]

    _mark_stack_path_access(rows, cfg)

    assert [row['stack_path_allowed'] for row in rows] == [True, False, False]
