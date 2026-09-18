from pathlib import Path

from core.hf_local_match import find_local_matches, primary_local_match


def test_find_exact_hf_layout_match(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    target = root / 'Alittlehammmer' / 'Qwen3.6-27B-DFlash-GGUF-llama.cpp' / 'Qwen3.6-27B-DFlash-Q4_K_M.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'gguf')

    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'DFlash models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])

    matches = find_local_matches(
        'Alittlehammmer/Qwen3.6-27B-DFlash-GGUF-llama.cpp',
        'Qwen3.6-27B-DFlash-Q4_K_M.gguf',
        cfg=cfg,
    )
    assert len(matches) == 1
    assert matches[0]['path'] == str(target.resolve())
    assert matches[0]['match_type'] == 'exact_path'


def test_primary_local_match_missing(tmp_path, monkeypatch):
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'DFlash models',
            'path': str(tmp_path / 'models'),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])

    assert primary_local_match('author/repo', 'missing.gguf', cfg=cfg) is None


def test_catalog_ready_matches_hf_layout_stack(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    target = root / 'google' / 'gemma-4-12B-it-qat-q4_0-gguf' / 'gemma-4-12b-it-qat-q4_0.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'gguf')
    draft = tmp_path / 'gemma-4-12B-it-DFlash-Q4_K_M.gguf'
    draft.write_bytes(b'draft')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [{
            'id': 'gemma-12',
            'model_id': 'gemma-12',
            'label': 'Gemma 4 12B DFlash',
            'enabled': True,
            'profile': 'gemma-chat',
            'target_path': str(target),
            'draft_path': str(draft),
            'port': 8081,
        }],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: cfg['servers'])
    monkeypatch.setattr('core.local_models._CATALOG_CACHE', None)
    monkeypatch.setattr('core.local_models._CATALOG_CACHE_AT', 0.0)
    monkeypatch.setattr('core.local_models.disk_scan_roots', lambda _cfg: [(root, 'library', 'custom', 'Test')])

    from core.hf_local_match import is_catalog_ready_to_load

    assert is_catalog_ready_to_load('google/gemma-4-12B-it-qat-q4_0-gguf', cfg=cfg) is False
    assert is_catalog_ready_to_load('google/gemma-4-12B-it-qat-q4_0-gguf-dflash', tags=['dflash'], cfg=cfg) is True
    assert is_catalog_ready_to_load('google/gemma-4-31B-it-qat-q4_0-gguf-dflash', tags=['dflash'], cfg=cfg) is False


def test_catalog_ready_rejects_qwen36_when_stack_is_qwen35(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    root.mkdir(parents=True)
    target = root / 'Qwen3.5-27B-Q4_K_M.gguf'
    target.write_bytes(b'gguf')
    draft = root / 'Qwen3.5-27B-DFlash-F16.gguf'
    draft.write_bytes(b'draft')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [{
            'id': 'qwen-35',
            'model_id': 'qwen-35',
            'label': 'Qwen3.5 27B DFlash',
            'enabled': True,
            'profile': 'qwen-dflash',
            'target_path': str(target),
            'draft_path': str(draft),
            'port': 8082,
        }],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: cfg['servers'])
    monkeypatch.setattr('core.local_models._CATALOG_CACHE', None)
    monkeypatch.setattr('core.local_models._CATALOG_CACHE_AT', 0.0)
    monkeypatch.setattr('core.local_models.disk_scan_roots', lambda _cfg: [(root, 'library', 'custom', 'Test')])

    from core.hf_local_match import is_catalog_ready_to_load

    assert is_catalog_ready_to_load(
        'Alittlehammmer/Qwen3.5-27B-DFlash-GGUF-llama.cpp',
        title='Qwen3.5 27B DFlash',
        tags=['dflash'],
        cfg=cfg,
    ) is True
    assert is_catalog_ready_to_load(
        'Alittlehammmer/Qwen3.6-27B-DFlash-GGUF-llama.cpp',
        title='Qwen3.6 27B DFlash',
        tags=['dflash'],
        cfg=cfg,
    ) is False


def test_catalog_ready_rejects_dflash2_repo_when_stack_has_dflash1_draft(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    root.mkdir(parents=True)
    target = root / 'Qwen3.8-27B-Q6_K_L.gguf'
    target.write_bytes(b'gguf')
    draft = root / 'Qwen3.5-27B-DFlash-F16.gguf'
    draft.write_bytes(b'draft')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [{
            'id': 'qwen38',
            'model_id': 'qwen38',
            'label': 'Qwen 3.8 27B D-Flash',
            'enabled': True,
            'profile': 'qwen-dflash',
            'target_path': str(target),
            'draft_path': str(draft),
            'port': 8096,
        }],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: cfg['servers'])
    monkeypatch.setattr('core.local_models._CATALOG_CACHE', None)
    monkeypatch.setattr('core.local_models._CATALOG_CACHE_AT', 0.0)
    monkeypatch.setattr('core.local_models.disk_scan_roots', lambda _cfg: [(root, 'library', 'custom', 'Test')])

    from core.hf_local_match import find_repo_local_installs, is_catalog_ready_to_load

    assert find_repo_local_installs('incoai/Qwen3.8-27B-DFlash2-GGUF', cfg=cfg) == []
    assert is_catalog_ready_to_load(
        'incoai/Qwen3.8-27B-DFlash2-GGUF',
        title='Qwen3.8-27B-DFlash2-GGUF',
        tags=['dflash'],
        cfg=cfg,
    ) is False
    assert is_catalog_ready_to_load(
        'mrchuy/Qwen3.8-27B-DFlash-drafter-bootstrap-GGUF',
        title='Qwen3.8-27B-DFlash-drafter-bootstrap-GGUF',
        tags=['dflash'],
        cfg=cfg,
    ) is True


def test_imatrix_file_does_not_count_as_repo_install(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    imatrix = root / 'ISTA-DASLab' / 'Qwen3.8-27B-GSQ-RCO-GGUF' / 'imatrix-qwen3.8-27b.gguf'
    imatrix.parent.mkdir(parents=True)
    imatrix.write_bytes(b'gguf')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])
    monkeypatch.setattr(
        'core.hf_local_match.list_local_models',
        lambda **kwargs: {
            'models': [{
                'path': str(imatrix),
                'publisher': 'ISTA-DASLab',
                'loadable': True,
            }],
        },
    )

    from core.hf_local_match import annotate_models_local_installs, find_repo_local_installs

    assert find_repo_local_installs('ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF', cfg=cfg) == []
    assert len(find_repo_local_installs('ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF', cfg=cfg, weights_only=False)) == 1
    row = {'id': 'ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF', 'title': 'Qwen3.8-27B-GSQ-RCO-GGUF', 'tags': []}
    annotate_models_local_installs([row], cfg=cfg)
    assert row['local_ready'] is False
    assert row['local_auxiliary_only'] is True


def test_find_local_matches_fork_repo_by_filename(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    quant = root / 'ISTA-DASLab' / 'Qwen3.8-27B-GSQ-RCO-GGUF' / 'Qwen3.8-27B-GSQ-RCO-IQ3_XXS+MTP.gguf'
    quant.parent.mkdir(parents=True)
    quant.write_bytes(b'x' * 1024)
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])
    monkeypatch.setattr(
        'core.hf_local_match.list_local_models',
        lambda **kwargs: {
            'models': [{
                'path': str(quant),
                'publisher': 'ISTA-DASLab',
                'loadable': True,
            }],
        },
    )

    from core.hf_local_match import find_local_matches

    matches = find_local_matches(
        'cruizba/ISTA-DASLab-Qwen3.8-27B-GSQ-RCO-GGUF-Unsloth-MTP',
        'Qwen3.8-27B-GSQ-RCO-IQ3_XXS+MTP.gguf',
        cfg=cfg,
    )
    assert matches
    assert matches[0]['path'].endswith('Qwen3.8-27B-GSQ-RCO-IQ3_XXS+MTP.gguf')


def test_local_installs_for_files_skips_imatrix(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    imatrix = root / 'ISTA-DASLab' / 'Qwen3.8-27B-GSQ-RCO-GGUF' / 'imatrix-qwen3.8-27b.gguf'
    imatrix.parent.mkdir(parents=True)
    imatrix.write_bytes(b'gguf')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])
    monkeypatch.setattr(
        'core.hf_local_match.list_local_models',
        lambda **kwargs: {
            'models': [{
                'path': str(imatrix),
                'publisher': 'ISTA-DASLab',
                'loadable': True,
            }],
        },
    )

    from core.hf_local_match import local_installs_for_files

    installs = local_installs_for_files(
        'ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF',
        ['imatrix-qwen3.8-27b.gguf', 'model-q4_k_m.gguf'],
        cfg=cfg,
    )
    assert installs == {}


def test_generic_model_safetensors_does_not_basename_collide(tmp_path, monkeypatch):
    """Unrelated repo's model.safetensors must not mark another HF repo as installed."""
    root = tmp_path / 'models'
    breeze = root / 'BreezeBlue' / 'Breeze-TTS-2' / 'audio_tokenizer' / 'model.safetensors'
    breeze.parent.mkdir(parents=True)
    breeze.write_bytes(b'weights')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])
    monkeypatch.setattr(
        'core.hf_local_match.list_local_models',
        lambda **kwargs: {
            'models': [{
                'path': str(breeze),
                'publisher': 'BreezeBlue',
                'loadable': False,
            }],
        },
    )

    from core.hf_local_match import find_local_matches, is_generic_weight_filename

    assert is_generic_weight_filename('model.safetensors') is True
    matches = find_local_matches(
        'Aniemore/wav2vec2-xlsr-53-emotion-v1-crosslingual',
        'model.safetensors',
        cfg=cfg,
    )
    assert matches == []


def test_generic_model_safetensors_exact_hf_layout_still_matches(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    target = (
        root / 'Aniemore' / 'wav2vec2-xlsr-53-emotion-v1-crosslingual' / 'model.safetensors'
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b'weights')
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])
    monkeypatch.setattr(
        'core.hf_local_match.list_local_models',
        lambda **kwargs: {
            'models': [{
                'path': str(target),
                'publisher': 'Aniemore',
                'loadable': False,
            }],
        },
    )

    from core.hf_local_match import find_local_matches

    matches = find_local_matches(
        'Aniemore/wav2vec2-xlsr-53-emotion-v1-crosslingual',
        'model.safetensors',
        cfg=cfg,
    )
    assert len(matches) >= 1
    assert matches[0]['path'] == str(target.resolve())
    assert matches[0]['match_type'] in {'exact_path', 'hf_layout'}


def test_distinctive_gguf_basename_only_match_still_works(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    # Distinctive quant name under an unrelated path; repo id shares no tokens with the file.
    quant = root / 'downloads' / 'SuperUniqueDraft-27B-Q4_K_M.gguf'
    quant.parent.mkdir(parents=True)
    quant.write_bytes(b'x' * 1024)
    cfg = {
        'dflash_root': str(tmp_path),
        'model_libraries': [{
            'id': 'default',
            'label': 'Models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.hf_local_match.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.load_config', lambda: cfg)
    monkeypatch.setattr('core.local_models.list_servers', lambda _cfg: [])
    monkeypatch.setattr(
        'core.hf_local_match.list_local_models',
        lambda **kwargs: {
            'models': [{
                'path': str(quant),
                'publisher': '',
                'loadable': True,
            }],
        },
    )

    from core.hf_local_match import allows_basename_only_match, find_local_matches

    assert allows_basename_only_match('SuperUniqueDraft-27B-Q4_K_M.gguf') is True
    assert allows_basename_only_match('model.safetensors') is False
    matches = find_local_matches(
        'example-org/totally-different-catalog-entry',
        'SuperUniqueDraft-27B-Q4_K_M.gguf',
        cfg=cfg,
    )
    assert matches
    assert matches[0]['match_type'] == 'filename'
    assert matches[0]['path'].endswith('SuperUniqueDraft-27B-Q4_K_M.gguf')


def test_console_library_skips_generic_basename_without_repo(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    breeze = root / 'BreezeBlue' / 'Breeze-TTS-2' / 'audio_tokenizer' / 'model.safetensors'
    breeze.parent.mkdir(parents=True)
    breeze.write_bytes(b'weights')
    cfg = {
        'dflash_root': str(tmp_path),
        'models_root': str(root),
        'model_libraries': [{
            'id': 'default',
            'label': 'DFlash models',
            'path': str(root),
            'enabled': True,
            'preset': 'dflash',
            'download_default': True,
        }],
        'servers': [],
    }
    monkeypatch.setattr('core.library_import.load_config', lambda: cfg)
    monkeypatch.setattr('core.config.load_config', lambda: cfg)

    from core.library_import import find_existing_in_console_library

    assert find_existing_in_console_library('model.safetensors', cfg=cfg) == []
    assert find_existing_in_console_library(
        'model.safetensors',
        cfg=cfg,
        repo_id='Aniemore/wav2vec2-xlsr-53-emotion-v1-crosslingual',
    ) == []
    hits = find_existing_in_console_library(
        'model.safetensors',
        cfg=cfg,
        repo_id='BreezeBlue/Breeze-TTS-2',
    )
    assert len(hits) == 1
    assert hits[0]['path'] == str(breeze.resolve())
