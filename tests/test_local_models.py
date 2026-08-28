from __future__ import annotations

from pathlib import Path

from core.local_models import _catalog_cache_key, _has_vision_support, model_matches_source


def test_model_matches_source_filters_ollama_and_dflash():
    ollama = {'id': 'ollama:llama3:latest', 'source': 'ollama'}
    studio = {'id': 'gemma', 'source': 'lmstudio'}
    stack = {'id': 'gemma-31b', 'source': 'dflash-stack', 'dflash_stack': True}
    assert model_matches_source(ollama, 'ollama')
    assert not model_matches_source(studio, 'ollama')
    assert model_matches_source(studio, 'lmstudio')
    assert model_matches_source(stack, 'dflash')
    assert model_matches_source(ollama, 'all')
    hf = {'id': 'qwen', 'source': 'library', 'runtime_id': 'vllm', 'engines': ['vllm', 'transformers']}
    assert model_matches_source(hf, 'vllm')
    assert model_matches_source(hf, 'transformers')


def test_scan_hf_llm_finds_safetensors_dir(tmp_path: Path):
    import json

    from core.local_models import _scan_hf_llm

    model_dir = tmp_path / 'opt-125m'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'opt'}), encoding='utf-8')
    (model_dir / 'model.safetensors').write_text('x', encoding='utf-8')
    rows = _scan_hf_llm(tmp_path, source='library')
    assert len(rows) == 1
    assert rows[0]['kind'] == 'dir'
    assert 'vllm' in rows[0]['engines']
    assert rows[0]['runtime_id'] in {'vllm', 'transformers'}


def test_scan_hf_llm_uses_hub_repo_name_not_snapshot_hash(tmp_path: Path):
    import json

    from core.local_models import _scan_hf_llm

    snapshot = tmp_path / 'hub' / 'models--Qwen--Qwen3-0.6B' / 'snapshots' / '10e65a9951a1e922cd109a95e8aba9357b62144b'
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen3'}), encoding='utf-8')
    (snapshot / 'model.safetensors').write_text('x', encoding='utf-8')
    rows = _scan_hf_llm(tmp_path, source='library')
    assert len(rows) == 1
    assert rows[0]['label'] == 'Qwen3-0.6B'
    assert rows[0]['publisher'] == 'Qwen'


def test_scan_hf_llm_reports_total_size_for_sharded_model(tmp_path: Path):
    import json

    model_dir = tmp_path / 'sharded'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'opt'}), encoding='utf-8')
    (model_dir / 'model-00001-of-00002.safetensors').write_bytes(b'a' * 1024)
    (model_dir / 'model-00002-of-00002.safetensors').write_bytes(b'b' * 2048)
    rows = __import__('core.local_models', fromlist=['_scan_hf_llm'])._scan_hf_llm(tmp_path, source='library')
    assert rows[0]['size_gb'] is not None


def test_hf_size_uses_blobs_when_snapshot_is_tiny(tmp_path: Path):
    import json

    from core.local_models import _model_dir_size_gb, _scan_hf_llm

    repo = tmp_path / 'hub' / 'models--Qwen--Qwen2.5-32B-Instruct'
    blobs = repo / 'blobs'
    snapshot = repo / 'snapshots' / 'deadbeef'
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}), encoding='utf-8')
    (snapshot / 'model.safetensors.index.json').write_text(
        json.dumps({'metadata': {'total_size': 1024}, 'weight_map': {}}),
        encoding='utf-8',
    )
    (snapshot / 'model-00001-of-00002.safetensors').write_bytes(b'stub')
    (blobs / 'sha256-big').write_bytes(b'x' * (80 * 1024 * 1024))
    assert _model_dir_size_gb(snapshot) >= 0.07
    rows = _scan_hf_llm(tmp_path / 'hub', source='library')
    assert len(rows) == 1
    assert rows[0]['size_gb'] >= 0.07
    assert rows[0]['hf_repo'] == 'Qwen/Qwen2.5-32B-Instruct'


def test_resolve_model_delete_dir_uses_hub_repo(tmp_path: Path):
    from core.local_models import resolve_model_delete_dir

    repo = tmp_path / 'hub' / 'models--Qwen--Qwen2.5-72B-Instruct'
    snapshot = repo / 'snapshots' / 'deadbeef'
    snapshot.mkdir(parents=True)
    assert resolve_model_delete_dir(snapshot) == repo.resolve()


def test_hf_size_estimates_when_only_tokenizer_is_cached(tmp_path: Path):
    import json

    from core.local_models import _model_dir_size_gb

    snapshot = tmp_path / 'hub' / 'models--Qwen--Qwen2.5-32B-Instruct' / 'snapshots' / 'abc123'
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}), encoding='utf-8')
    (snapshot / 'tokenizer.json').write_bytes(b'tiny')
    assert _model_dir_size_gb(snapshot) >= 50


def test_hf_size_falls_back_to_safetensors_index(tmp_path: Path):
    import json

    from core.local_models import _model_dir_size_gb

    repo = tmp_path / 'hub' / 'models--Qwen--Qwen2.5-72B-Instruct'
    snapshot = repo / 'snapshots' / 'abc123'
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}), encoding='utf-8')
    (snapshot / 'model.safetensors.index.json').write_text(
        json.dumps({'metadata': {'total_size': 64 * (1024 ** 3)}}),
        encoding='utf-8',
    )
    (snapshot / 'tokenizer.json').write_bytes(b'tiny')
    assert _model_dir_size_gb(snapshot) == 64.0


def test_size_gb_sums_gguf_split_shards(tmp_path: Path):
    from core.local_models import _size_gb

    shard1 = tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf'
    shard2 = tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00002-of-00003.gguf'
    shard3 = tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00003-of-00003.gguf'
    shard1.write_bytes(b'a' * (3 * 1024 * 1024))
    shard2.write_bytes(b'b' * (3 * 1024 * 1024))
    shard3.write_bytes(b'c' * (3 * 1024 * 1024))
    size = _size_gb(shard1)
    assert size is not None
    assert size >= 0.01


def test_hf_size_estimates_lmstudio_plain_folder(tmp_path: Path):
    import json

    from core.local_models import _model_dir_size_gb, _scan_hf_llm

    model_dir = tmp_path / '.lmstudio' / 'models' / 'Qwen' / 'Qwen2.5-32B-Instruct'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}), encoding='utf-8')
    (model_dir / 'tokenizer.json').write_bytes(b'tiny')
    assert _model_dir_size_gb(model_dir) >= 50
    rows = _scan_hf_llm(tmp_path / '.lmstudio' / 'models', source='lmstudio')
    assert len(rows) == 1
    assert rows[0]['size_gb'] >= 50


def test_catalog_repo_size_reads_nested_cache(monkeypatch):
    from core.local_models import _catalog_repo_size_gb

    monkeypatch.setattr(
        'core.hf_catalog_cache.get_cached_detail',
        lambda **_kwargs: {'payload': {'model': {'size_gb': 641.3}}},
    )
    assert _catalog_repo_size_gb('deepseek-ai/DeepSeek-V3') == 641.3


def test_lookup_hf_repo_size_fetches_when_cache_miss(monkeypatch):
    from core import local_models

    local_models._HF_REPO_SIZE_CACHE.clear()
    monkeypatch.setattr(local_models, '_catalog_repo_size_gb', lambda _repo: None)
    monkeypatch.setattr(
        'core.huggingface._fetch_repo_siblings_with_blobs',
        lambda _repo: [{'rfilename': 'model.safetensors', 'size': 70 * (1024 ** 3)}],
    )
    assert local_models._lookup_hf_repo_size_gb('deepseek-ai/DeepSeek-V3') == 70.0


def test_model_dir_size_uses_hf_lookup_for_metadata_only_snapshot(tmp_path: Path, monkeypatch):
    import json

    from core import local_models

    local_models._HF_REPO_SIZE_CACHE.clear()
    snapshot = tmp_path / 'hub' / 'models--deepseek-ai--DeepSeek-V3' / 'snapshots' / 'abc123'
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'deepseek'}), encoding='utf-8')
    (snapshot / 'tokenizer.json').write_bytes(b'tiny')
    monkeypatch.setattr(local_models, '_catalog_repo_size_gb', lambda _repo: None)
    monkeypatch.setattr(local_models, '_lookup_hf_repo_size_gb', lambda _repo, **kwargs: 641.3)
    assert local_models._model_dir_size_gb(snapshot) == 641.3


def test_directory_size_gb_follows_symlinks(tmp_path: Path):
    import json

    from core.local_models import _directory_size_gb, _scan_hf_llm

    blob = tmp_path / 'blobs' / 'sha256-abc'
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b'x' * (16 * 1024 * 1024))
    snapshot = tmp_path / 'hub' / 'models--Qwen--Qwen3-0.6B' / 'snapshots' / 'deadbeef'
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen3'}), encoding='utf-8')
    try:
        (snapshot / 'model.safetensors').symlink_to(blob)
    except OSError:
        pytest = __import__('pytest')
        pytest.skip('symlinks unavailable on this platform')
    assert _directory_size_gb(snapshot) >= 0.01
    rows = _scan_hf_llm(tmp_path / 'hub', source='library')
    assert len(rows) == 1
    assert rows[0]['size_gb'] >= 0.01


def test_scan_hf_llm_prefers_refs_main_snapshot(tmp_path: Path):
    import json

    from core.local_models import _scan_hf_llm

    repo = tmp_path / 'hub' / 'models--Qwen--Qwen3-0.6B'
    active = repo / 'snapshots' / 'active123'
    stale = repo / 'snapshots' / 'stale456'
    active.mkdir(parents=True)
    stale.mkdir(parents=True)
    for snapshot in (active, stale):
        (snapshot / 'config.json').write_text(json.dumps({'model_type': 'qwen3'}), encoding='utf-8')
        (snapshot / 'model.safetensors').write_text('active' if snapshot is active else 'stale', encoding='utf-8')
    (repo / 'refs').mkdir(parents=True)
    (repo / 'refs' / 'main').write_text('snapshots/active123', encoding='utf-8')
    rows = _scan_hf_llm(tmp_path / 'hub', source='library')
    assert len(rows) == 1
    assert rows[0]['path'].endswith('active123')


def test_collapse_hf_hub_repos_keeps_largest_snapshot(tmp_path: Path):
    from core.local_models import _collapse_hf_hub_repos

    rows = [
        {
            'path': str(tmp_path / 'hub' / 'models--Qwen--Qwen3-0.6B' / 'snapshots' / 'small'),
            'filename': 'Qwen3-0.6B',
            'label': 'Qwen3-0.6B',
            'kind': 'dir',
            'size_gb': 0.01,
        },
        {
            'path': str(tmp_path / 'hub' / 'models--Qwen--Qwen3-0.6B' / 'snapshots' / 'large'),
            'filename': 'Qwen3-0.6B',
            'label': 'Qwen3-0.6B',
            'kind': 'dir',
            'size_gb': 12.5,
        },
    ]
    collapsed = _collapse_hf_hub_repos(rows)
    assert len(collapsed) == 1
    assert collapsed[0]['size_gb'] == 12.5
    assert collapsed[0]['duplicate_count'] == 2


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


def test_projector_rows_are_marked_non_loadable(tmp_path: Path):
    from core.local_models import _annotate_projector_row, _scan_gguf

    projector = tmp_path / 'translategemma-12b-it.mmproj-f16.gguf'
    target = tmp_path / 'translategemma-12b-it.Q4_K_S.gguf'
    projector.write_bytes(b'gguf')
    target.write_bytes(b'gguf')
    rows = _scan_gguf(tmp_path, source='lmstudio')
    by_name = {row['filename']: row for row in rows}
    proj = by_name[projector.name]
    main = by_name[target.name]
    assert proj['is_projector'] is True
    assert proj['loadable'] is False
    assert 'projector' in proj['capabilities']
    assert 'reasoning' not in proj['capabilities']
    assert proj['modality'] == 'projector'
    assert main['is_projector'] is False
    assert main.get('loadable') is not False or 'llm' in main.get('capabilities', [])
    _annotate_projector_row(main)
    assert main.get('is_projector') is False


def test_stack_supplement_uses_scanned_extras(tmp_path: Path, monkeypatch):
    from core.local_models import _dflash_stack_supplement

    target = tmp_path / 'Qwen3.5-27B-Q4_K_M.gguf'
    draft = tmp_path / 'Qwen3.5-27B-DFlash-F16.gguf'
    target.write_bytes(b'x' * 32)
    draft.write_bytes(b'y' * 16)
    extras = [
        {
            'path': str(target),
            'filename': target.name,
            'label': target.name,
            'size_gb': 15.59,
            'source': 'library',
        },
        {
            'path': str(draft),
            'filename': draft.name,
            'label': draft.name,
            'size_gb': 3.98,
            'source': 'library',
        },
    ]
    monkeypatch.setattr(
        'core.stack_match.list_local_models',
        lambda **_kwargs: {'models': [], 'partial': True},
    )
    rows = _dflash_stack_supplement({'servers': []}, {}, extras, cfg={'servers': []})
    assert len(rows) == 1
    assert rows[0]['filename'] == target.name
    assert rows[0]['label'] == 'Qwen 3.5 27B D-Flash'
    assert rows[0]['stack_status'] == 'unregistered'
    assert rows[0]['draft_filename'] == draft.name


def test_stack_supplement_keeps_one_row_for_duplicate_target_copies(tmp_path: Path, monkeypatch):
    from core.local_models import _dflash_stack_supplement

    console_dir = tmp_path / 'Dflash-Console' / 'models'
    studio_dir = tmp_path / '.lmstudio' / 'models'
    console_dir.mkdir(parents=True)
    studio_dir.mkdir(parents=True)
    draft = tmp_path / 'gemma-4-31B-it-DFlash-Q4_K_M.gguf'
    console_target = console_dir / 'gemma-4-31B_q4_0-it.gguf'
    studio_target = studio_dir / 'gemma-4-31B_q4_0-it.gguf'
    draft.write_bytes(b'y' * 16)
    console_target.write_bytes(b'x' * 32)
    studio_target.write_bytes(b'x' * 32)
    extras = [
        {
            'path': str(studio_target),
            'filename': studio_target.name,
            'label': studio_target.name,
            'size_gb': 16.44,
            'source': 'lmstudio',
        },
        {
            'path': str(console_target),
            'filename': console_target.name,
            'label': console_target.name,
            'size_gb': 16.44,
            'source': 'dflash',
        },
        {
            'path': str(draft),
            'filename': draft.name,
            'label': draft.name,
            'size_gb': 0.85,
            'source': 'dflash',
        },
    ]
    monkeypatch.setattr(
        'core.stack_match.list_local_models',
        lambda **_kwargs: {'models': [], 'partial': True},
    )
    rows = _dflash_stack_supplement({'servers': []}, {}, extras, cfg={'servers': []})
    assert len(rows) == 1
    assert rows[0]['path'] == str(console_target)
    assert rows[0]['filename'] == console_target.name


def test_large_dflash_gguf_is_accelerator_only():
    from core.local_models import _is_accelerator_only_row

    assert _is_accelerator_only_row({
        'filename': 'Qwen3.5-27B-DFlash-F16.gguf',
        'path': r'C:\models\Qwen3.5-27B-DFlash-F16.gguf',
        'size_gb': 3.98,
        'dflash_stack': False,
    }) is True
    assert _is_accelerator_only_row({
        'filename': 'Qwen3.5-27B-Q4_K_M.gguf',
        'path': r'C:\models\Qwen3.5-27B-Q4_K_M.gguf',
        'size_gb': 15.59,
        'dflash_stack': True,
        'draft_path': r'C:\models\Qwen3.5-27B-DFlash-F16.gguf',
    }) is False
    assert _is_accelerator_only_row({
        'label': 'qwen-draft-hf',
        'filename': 'qwen-draft-hf',
        'path': r'C:\models\qwen-draft-hf',
        'kind': 'dir',
    }) is True
    assert _is_accelerator_only_row({
        'label': 'Gemma 4 31B q4 0 it dflash Q4',
        'id': 'Gemma-4-31B-q4-0-it-dflash-Q4',
        'filename': '',
        'path': '',
        'size_gb': 16.44,
        'dflash_stack': False,
    }) is False
    assert _is_accelerator_only_row({
        'filename': 'Ornith-1.0-35B-ROCmFP4-STRIX_LEAN-DFLASH-Q4_K_M.gguf',
        'path': r'C:\models\Ornith-1.0-35B-ROCmFP4-STRIX_LEAN-DFLASH-Q4_K_M.gguf',
        'size_gb': 18.0,
        'dflash_stack': False,
    }) is False


def test_catalog_cache_key_ignores_layout_preferences(tmp_path: Path):
    models_root = tmp_path / 'models'
    models_root.mkdir()
    base = {
        'models_root': str(models_root),
        'model_libraries': [{
            'id': 'local',
            'label': 'Local',
            'path': str(models_root),
            'preset': 'custom',
            'enabled': True,
        }],
        'servers': [],
        'ui_layout': {'inspector_collapsed': False},
    }
    collapsed = {
        **base,
        'ui_layout': {'inspector_collapsed': True},
    }
    assert _catalog_cache_key(base) == _catalog_cache_key(collapsed)


def test_regular_model_path_does_not_make_it_an_accelerator():
    from core.local_models import _is_accelerator_only_row

    assert _is_accelerator_only_row({
        'filename': 'Qwen3.8-27B-Q6_K_L.gguf',
        'label': 'Qwen3.8-27B-Q6_K_L.gguf',
        'path': r'C:\dev\Dflash-Console\models\bartowski\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q6_K_L.gguf',
        'dflash_stack': False,
    }) is False


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


def test_annotate_runtime_fields_vision_capable_chat_is_llm(tmp_path: Path):
    from core.local_models import _annotate_runtime_fields

    gguf = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    gguf.write_bytes(b'0' * 128)
    row = {
        'path': str(gguf),
        'label': 'Gemma 4 31B D-Flash',
        'filename': gguf.name,
        'capabilities': ['instruct', 'dflash', 'vision', 'reasoning'],
        'loadable': True,
    }
    _annotate_runtime_fields(row)
    assert row['modality'] == 'llm'


def test_library_file_alias_inherits_profile_port():
    from core.local_models import _library_file_alias_row

    alias = _library_file_alias_row(
        {
            'filename': 'ATH-MaaS_OvisOCR2-Q8_0.gguf',
            'path': r'C:\models\ATH-MaaS_OvisOCR2-Q8_0.gguf',
            'capabilities': ['instruct', 'ocr'],
        },
        {
            'label': 'ATH-MaaS OvisOCR2-Q8 0',
            'server_id': 'ath-maas-ovisocr2-q8-0',
            'port': 8091,
        },
    )
    assert alias is not None
    assert alias['port'] == 8091
    assert alias['bound_profile_id'] == 'ath-maas-ovisocr2-q8-0'
    assert alias['id'] == 'library-file:ath-maas-ovisocr2-q8-0'


def test_library_file_alias_id_is_unique_per_profile():
    from core.local_models import _library_file_alias_row

    alias_a = _library_file_alias_row(
        {'filename': 'ATH-MaaS_OvisOCR2-Q8_0.gguf', 'path': r'C:\models\ATH-MaaS_OvisOCR2-Q8_0\file.gguf', 'capabilities': []},
        {'label': 'Profile A', 'server_id': 'profile-a', 'port': 8091},
    )
    alias_b = _library_file_alias_row(
        {'filename': 'ATH-MaaS_OvisOCR2-Q8_0.gguf', 'path': r'C:\models\ATH-MaaS_OvisOCR2-Q8_0-2\file.gguf', 'capabilities': []},
        {'label': 'Profile B', 'server_id': 'profile-b', 'port': 8090},
    )
    assert alias_a['id'] != alias_b['id']
    assert alias_a['id'] == 'library-file:profile-a'
    assert alias_b['id'] == 'library-file:profile-b'


def test_drop_redundant_library_file_aliases():
    from core.local_models import _drop_redundant_library_file_aliases

    rows = [
        {'source': 'dflash-profile', 'server_id': 'profile-a', 'path': r'C:\models\a\file.gguf', 'label': 'Nice name'},
        {'library_file': True, 'path': r'C:\models\a\file.gguf', 'label': 'file.gguf', 'id': 'library-file:profile-a'},
        {'library_file': True, 'path': r'C:\models\b\file.gguf', 'label': 'file.gguf', 'id': 'library-file:profile-b'},
    ]
    kept = _drop_redundant_library_file_aliases(rows)
    assert len(kept) == 2
    assert kept[0]['server_id'] == 'profile-a'
    assert kept[1]['path'] == r'C:\models\b\file.gguf'


def test_mark_duplicate_files_counts_unique_paths():
    from core.local_models import _mark_duplicate_files

    rows = [
        {'path': r'C:\a\model.gguf', 'filename': 'model.gguf'},
        {'path': r'C:\a\model.gguf', 'filename': 'model.gguf'},
        {'path': r'C:\b\model.gguf', 'filename': 'model.gguf'},
    ]
    _mark_duplicate_files(rows)
    assert rows[0]['duplicate_count'] == 2
    assert rows[0]['duplicate_paths'] == [r'C:\a\model.gguf', r'C:\b\model.gguf']


def test_annotate_path_status_accepts_faster_whisper_dirs(tmp_path: Path):
    from core.local_models import _annotate_path_status

    model_dir = tmp_path / 'fw-small'
    model_dir.mkdir()
    (model_dir / 'model.bin').write_bytes(b'0')
    row = {'path': str(model_dir), 'kind': 'dir', 'runtime_id': 'faster-whisper'}
    _annotate_path_status(row)
    assert row['path_missing'] is False
    assert row.get('loadable') is not False

