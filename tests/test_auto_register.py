"""Tests for auto-registration of models found in the Console library."""

from pathlib import Path

import pytest

from core import auto_register as ar


@pytest.fixture
def models_root(tmp_path: Path) -> Path:
    root = tmp_path / 'models'
    root.mkdir()
    return root


@pytest.fixture
def cfg(tmp_path: Path, models_root: Path, monkeypatch) -> dict:
    """Minimal config pointed at a temp models root; config/presets write to tmp."""
    monkeypatch.setattr('core.config.CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr('core.model_presets.PRESET_DIR', tmp_path / 'presets')
    # Keep the scan hermetic and fast: matching is unit-tested in test_stack_match.
    monkeypatch.setattr(
        'core.auto_register.find_local_accelerators',
        lambda _target, *, cfg=None, limit=12: [],
    )
    return {
        'dflash_root': str(tmp_path / 'dflash'),
        'models_root': str(models_root),
        'servers': [],
        'runtimes': [],
    }


def _write(root: Path, name: str) -> Path:
    path = root / name
    path.write_bytes(b'0' * 64)
    return path


def test_registers_plain_llm(models_root, cfg):
    _write(models_root, 'Qwen3.5-9B-Q4_K_M.gguf')

    result = ar.auto_register_console_models(cfg=cfg)

    assert len(result['registered']) == 1
    row = result['registered'][0]
    assert row['kind'] == 'llama-server'
    assert not row['draft_path']
    assert row['path'].endswith('Qwen3.5-9B-Q4_K_M.gguf')
    assert any(s.get('id') == row['server_id'] for s in cfg['servers'])


def test_registers_dflash_stack(models_root, cfg, monkeypatch):
    target = _write(models_root, 'gemma-4-12b-it-qat-q4_0.gguf')
    draft_dir = models_root / 'drafts'
    draft_dir.mkdir()
    draft = _write(draft_dir, 'gemma-4-12B-it-DFlash-Q4_K_M.gguf')
    monkeypatch.setattr(
        'core.auto_register.find_local_accelerators',
        lambda _target, *, cfg=None, limit=12: [{
            'path': str(draft),
            'filename': draft.name,
            'score': 7.5,
        }],
    )

    result = ar.auto_register_console_models(cfg=cfg)

    assert len(result['registered']) == 1
    row = result['registered'][0]
    assert row['kind'] == 'dflash-stack'
    assert row['path'] == str(target.resolve())
    assert row['draft_path'] == str(draft.resolve())
    server = next(s for s in cfg['servers'] if s.get('id') == row['server_id'])
    assert server.get('draft_path') == str(draft.resolve())


def test_skips_draft_accelerator(models_root, cfg):
    _write(models_root, 'gemma-4-12B-it-DFlash-Q4_K_M.gguf')

    result = ar.auto_register_console_models(cfg=cfg)

    assert result['registered'] == []
    assert any('accelerator' in str(r.get('reason') or '') for r in result['skipped'])


def test_skips_stt_and_mmproj(models_root, cfg):
    _write(models_root, 'whisper-large-v3-q8_0.gguf')
    _write(models_root, 'mmproj-gemma-4-12b-it-f16.gguf')

    result = ar.auto_register_console_models(cfg=cfg)

    assert result['registered'] == []
    # whisper is reported as skipped; mmproj is silently ignored (not a target).
    assert len(result['skipped']) == 1
    assert 'runtimes' in str(result['skipped'][0].get('reason') or '')


def test_skips_split_shard_continuations(models_root, cfg):
    _write(models_root, 'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf')
    _write(models_root, 'Laguna-S-2.1-UD-Q4_K_M-00002-of-00003.gguf')
    _write(models_root, 'Laguna-S-2.1-UD-Q4_K_M-00003-of-00003.gguf')

    result = ar.auto_register_console_models(cfg=cfg)

    paths = [r['path'] for r in result['registered']]
    assert not any('-00002-of-' in p for p in paths)
    assert not any('-00003-of-' in p for p in paths)
    assert any('-00001-of-' in p for p in paths)


def test_second_run_is_idempotent(models_root, cfg):
    _write(models_root, 'Qwen3.5-9B-Q4_K_M.gguf')

    first = ar.auto_register_console_models(cfg=cfg)
    second = ar.auto_register_console_models(cfg=cfg)

    assert len(first['registered']) == 1
    assert second['registered'] == []
    assert second['already_registered'] >= 1


def test_disabled_via_config(models_root, cfg):
    _write(models_root, 'Qwen3.5-9B-Q4_K_M.gguf')
    cfg['auto_register_models'] = False

    result = ar.auto_register_console_models(cfg=cfg)

    assert result['enabled'] is False
    assert result['registered'] == []
    assert cfg['servers'] == []


def test_missing_models_folder_reports_skip(tmp_path, cfg):
    cfg['models_root'] = str(tmp_path / 'does-not-exist')

    result = ar.auto_register_console_models(cfg=cfg)

    assert result['registered'] == []
    assert result['skipped'] and 'not found' in str(result['skipped'][0].get('reason') or '')
