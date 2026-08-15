from __future__ import annotations

from pathlib import Path

from core.local_models import _has_vision_support, model_matches_source


def test_model_matches_source_filters_ollama_and_dflash():
    ollama = {'id': 'ollama:llama3:latest', 'source': 'ollama'}
    studio = {'id': 'gemma', 'source': 'lmstudio'}
    stack = {'id': 'gemma-31b', 'source': 'dflash-stack', 'dflash_stack': True}
    assert model_matches_source(ollama, 'ollama')
    assert not model_matches_source(studio, 'ollama')
    assert model_matches_source(studio, 'lmstudio')
    assert model_matches_source(stack, 'dflash')
    assert model_matches_source(ollama, 'all')


def test_server_catalog_row_non_dflash_is_loadable_when_target_ready(tmp_path: Path):
    from core.local_models import _server_catalog_row

    target = tmp_path / 'nomic-embed-text-v1.5.Q8_0.gguf'
    target.write_bytes(b'gguf')
    server = {
        'id': 'nomic-embed',
        'model_id': 'nomic-embed-text-v1.5',
        'label': 'nomic-embed-text-v1.5.Q8_0',
        'profile': 'nomic-embed',
        'port': 8093,
        'enabled': True,
        'engine_mode': 'embedding',
        'target_path': str(target),
        'model_stack': [
            {'role': 'target', 'path': str(target), 'label': target.name},
        ],
    }
    row = _server_catalog_row(server, cfg={'model_paths': {}})
    assert row['loadable'] is True
    assert row['stack_status'] == ''


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


def test_scan_ollama_models(tmp_path: Path, monkeypatch):
    import json as json_mod

    from core.local_models import _scan_ollama_models

    manifests = tmp_path / 'manifests' / 'registry.ollama.ai' / 'library' / 'qwen3.5'
    manifests.mkdir(parents=True)
    blobs = tmp_path / 'blobs'
    blobs.mkdir()

    config_digest = 'configblob123'
    model_digest = 'modelblob456'
    manifest = {
        'schemaVersion': 2,
        'config': {
            'mediaType': 'application/vnd.docker.container.image.v1+json',
            'digest': f'sha256:{config_digest}',
            'size': 400,
        },
        'layers': [
            {
                'mediaType': 'application/vnd.ollama.image.model',
                'digest': f'sha256:{model_digest}',
                'size': 6_597_000_000,
            },
            {
                'mediaType': 'application/vnd.ollama.image.license',
                'digest': 'sha256:license123',
                'size': 1000,
            },
        ],
    }
    (manifests / '9b').write_text(json_mod.dumps(manifest), encoding='utf-8')
    (blobs / f'sha256-{config_digest}').write_text(
        json_mod.dumps({
            'model_format': 'gguf',
            'model_family': 'qwen35',
            'model_type': '9.7B',
            'file_type': 'Q4_K_M',
        }),
        encoding='utf-8',
    )
    (blobs / f'sha256-{model_digest}').write_bytes(b'GGUF' + b'\x00' * 12 + b'weights')

    monkeypatch.setattr('core.local_models._ollama_manifests_root', lambda: tmp_path / 'manifests')
    monkeypatch.setattr('core.local_models._ollama_blobs_root', lambda: blobs)

    rows = _scan_ollama_models()
    assert len(rows) == 1
    row = rows[0]
    assert row['label'] == 'qwen3.5:9b'
    assert row['source'] == 'ollama'
    assert row['size_gb'] == 6.14  # only the model layer (~6.14 GB)
    assert row['arch'] == 'qwen35'
    assert row['params'] == '9.7B'
    assert row['quant'] == 'Q4_K_M'
    assert row['loadable'] is True
    assert row['plain_gguf'] is True
    assert row['model_id'] == 'qwen3.5-9b'
    assert row['path'].endswith(f'sha256-{model_digest}')


def test_scan_ollama_models_skips_when_missing(tmp_path: Path, monkeypatch):
    from core.local_models import _scan_ollama_models

    monkeypatch.setattr('core.local_models._ollama_manifests_root', lambda: tmp_path / 'manifests')
    monkeypatch.setattr('core.local_models._ollama_blobs_root', lambda: tmp_path / 'blobs')
    assert _scan_ollama_models() == []


def test_delete_ollama_model_removes_manifest_and_blobs(tmp_path: Path, monkeypatch):
    import json as json_mod
    import urllib.error

    from core.local_models import _delete_ollama_model

    manifests = tmp_path / 'manifests' / 'registry.ollama.ai' / 'library' / 'qwen3.5'
    manifests.mkdir(parents=True)
    blobs = tmp_path / 'blobs'
    blobs.mkdir()

    manifest = {
        'schemaVersion': 2,
        'config': {'digest': 'sha256:cfg123', 'size': 100},
        'layers': [
            {'mediaType': 'application/vnd.ollama.image.model', 'digest': 'sha256:model456', 'size': 1000},
        ],
    }
    manifest_path = manifests / '9b'
    manifest_path.write_text(json_mod.dumps(manifest), encoding='utf-8')
    (blobs / 'sha256-cfg123').write_bytes(b'cfg')
    (blobs / 'sha256-model456').write_bytes(b'model')

    monkeypatch.setattr('core.local_models._ollama_manifests_root', lambda: tmp_path / 'manifests')
    monkeypatch.setattr('core.local_models._ollama_blobs_root', lambda: blobs)
    # Simulate the Ollama daemon being offline so we exercise manual removal.
    monkeypatch.setattr(
        'urllib.request.urlopen',
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError('offline')),
    )

    result = _delete_ollama_model('', model_id='qwen3.5:9b')
    assert result['success'] is True
    assert result['method'] == 'files'
    assert not manifest_path.exists()
    assert not (blobs / 'sha256-cfg123').exists()
    assert not (blobs / 'sha256-model456').exists()


def test_delete_ollama_model_keeps_shared_blobs(tmp_path: Path, monkeypatch):
    import json as json_mod
    import urllib.error

    from core.local_models import _delete_ollama_model

    manifests = tmp_path / 'manifests' / 'registry.ollama.ai' / 'library'
    a = manifests / 'qwen3.5'
    b = manifests / 'other'
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    blobs = tmp_path / 'blobs'
    blobs.mkdir()

    shared = {'mediaType': 'application/vnd.ollama.image.model', 'digest': 'sha256:shared1', 'size': 500}
    (a / '9b').write_text(json_mod.dumps({'config': {'digest': 'sha256:cfg1', 'size': 10}, 'layers': [shared]}), encoding='utf-8')
    (b / 'other-model').write_text(json_mod.dumps({'config': {'digest': 'sha256:cfg2', 'size': 10}, 'layers': [shared]}), encoding='utf-8')
    (blobs / 'sha256-shared1').write_bytes(b'shared')
    (blobs / 'sha256-cfg1').write_bytes(b'cfg1')
    (blobs / 'sha256-cfg2').write_bytes(b'cfg2')

    monkeypatch.setattr('core.local_models._ollama_manifests_root', lambda: tmp_path / 'manifests')
    monkeypatch.setattr('core.local_models._ollama_blobs_root', lambda: blobs)
    monkeypatch.setattr(
        'urllib.request.urlopen',
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError('offline')),
    )

    result = _delete_ollama_model('', model_id='qwen3.5:9b')
    assert result['success'] is True
    assert not (a / '9b').exists()
    # Shared blob must survive (referenced by the other manifest).
    assert (blobs / 'sha256-shared1').exists()
    assert not (blobs / 'sha256-cfg1').exists()
    assert (blobs / 'sha256-cfg2').exists()


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
    monkeypatch.setattr(lm, '_scan_ollama_models', lambda: [])
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


def test_scan_faster_whisper_discovers_model_dirs(tmp_path: Path):
    from core.local_models import _scan_faster_whisper

    model_dir = tmp_path / 'models--Systran--faster-whisper-small.en' / 'snapshots' / 'abc123'
    model_dir.mkdir(parents=True)
    (model_dir / 'model.bin').write_bytes(b'0' * 256)

    rows = _scan_faster_whisper(tmp_path, source='test')
    assert len(rows) == 1
    row = rows[0]
    assert row['runtime_id'] == 'faster-whisper'
    assert row['kind'] == 'dir'
    assert Path(row['path']) == model_dir
    assert row['arch'] == 'whisper'
    # Friendly name + publisher derived from the HF repo layout.
    assert row['filename'] == 'faster-whisper-small.en'
    assert row['publisher'] == 'Systran'


def test_scan_faster_whisper_excludes_translation_ctranslate2(tmp_path: Path):
    from core.local_models import _scan_faster_whisper

    # CTranslate2 NLLB/M2M translation packages also ship model.bin but must
    # NOT be listed as faster-whisper STT models.
    trans = tmp_path / 'models' / 'translate' / 'libre' / 'packages' / 'translate-sq_en-1_9' / 'model'
    trans.mkdir(parents=True)
    (trans / 'model.bin').write_bytes(b'0' * 256)
    (trans / 'config.json').write_text('{"model_type": "nllb"}')

    rows = _scan_faster_whisper(tmp_path, source='test')
    assert rows == []


def test_scan_faster_whisper_detects_whisper_config_without_model_type(tmp_path: Path):
    from core.local_models import _scan_faster_whisper

    # Imported faster-whisper copies keep the whisper config.json which often
    # has no model_type field but does carry whisper-specific keys.
    model_dir = tmp_path / 'models' / 'small.en'
    model_dir.mkdir(parents=True)
    (model_dir / 'model.bin').write_bytes(b'0' * 256)
    (model_dir / 'config.json').write_text('{"alignment_heads": [[6, 6]], "lang_ids": [50259]}')

    rows = _scan_faster_whisper(tmp_path, source='test')
    assert len(rows) == 1
    assert rows[0]['runtime_id'] == 'faster-whisper'
    assert Path(rows[0]['path']) == model_dir



def test_annotate_runtime_fields_faster_whisper_for_stt_dirs(tmp_path: Path):
    from core.local_models import _annotate_runtime_fields

    model_dir = tmp_path / 'faster-whisper-small.en'
    model_dir.mkdir()
    (model_dir / 'model.bin').write_bytes(b'0' * 128)
    row = {
        'path': str(model_dir),
        'label': 'faster-whisper-small.en',
        'filename': 'faster-whisper-small.en',
        'loadable': True,
    }
    _annotate_runtime_fields(row)
    assert row['modality'] == 'speech-to-text'
    assert row['runtime_id'] == 'faster-whisper'
    assert row['kind'] == 'dir'


def test_annotate_runtime_fields_whisper_cpp_for_gguf(tmp_path: Path):
    from core.local_models import _annotate_runtime_fields

    gguf = tmp_path / 'whisper-large-v3-q8_0.gguf'
    gguf.write_bytes(b'0' * 128)
    row = {
        'path': str(gguf),
        'label': 'whisper-large-v3-q8_0.gguf',
        'filename': 'whisper-large-v3-q8_0.gguf',
        'loadable': True,
    }
    _annotate_runtime_fields(row)
    assert row['modality'] == 'speech-to-text'
    assert row['runtime_id'] == 'stt'


def test_annotate_path_status_accepts_faster_whisper_dirs(tmp_path: Path):
    from core.local_models import _annotate_path_status

    model_dir = tmp_path / 'fw-small'
    model_dir.mkdir()
    (model_dir / 'model.bin').write_bytes(b'0')
    row = {'path': str(model_dir), 'kind': 'dir', 'runtime_id': 'faster-whisper'}
    _annotate_path_status(row)
    assert row['path_missing'] is False
    assert row.get('loadable') is not False

