"""Focused tests for the optional FreeToken WSL adapter."""

from __future__ import annotations

import json
from pathlib import Path

from core.config import normalize_freetoken_settings
from core.runtimes.freetoken import (
    FreeTokenRuntimeAdapter,
    is_freetoken_model_dir,
    parse_freetoken_log_progress,
)


def test_freetoken_settings_are_allow_listed_and_bounded():
    settings = normalize_freetoken_settings({
        'moe_backend': 'not-a-backend',
        'memory_ratio': 4,
        'max_running_requests': 999,
        'moe_cpu_layers': 'x' * 200,
        'unknown_shell_command': 'rm -rf /',
    })
    assert settings['moe_backend'] == 'auto'
    assert settings['memory_ratio'] == 0.99
    assert settings['max_running_requests'] == 64
    assert len(settings['moe_cpu_layers']) == 80
    assert 'unknown_shell_command' not in settings


def test_freetoken_model_dir_requires_safetensors(tmp_path: Path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'qwen3_moe'}), encoding='utf-8')
    assert is_freetoken_model_dir(model_dir) is False
    (model_dir / 'model.safetensors').write_text('weights', encoding='utf-8')
    assert is_freetoken_model_dir(model_dir) is True


def test_freetoken_manifest_with_utf8_bom_is_readable(tmp_path: Path, monkeypatch):
    import core.runtimes.freetoken as freetoken_mod

    manifest = tmp_path / 'manifest.json'
    manifest.write_bytes(
        b'\xef\xbb\xbf{"backend":"wsl","wsl_distro":"Ubuntu",'
        b'"wsl_python":"/root/.dflash-console/freetoken-venv/bin/python",'
        b'"wsl_ft":"/root/.dflash-console/freetoken-venv/bin/ft"}'
    )
    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_MANIFEST', manifest)
    assert freetoken_mod.FreeTokenRuntimeAdapter.is_installed() is True


def test_freetoken_health_reports_missing_manifest(monkeypatch, tmp_path: Path):
    import core.runtimes.freetoken as freetoken_mod

    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_MANIFEST', tmp_path / 'manifest.json')
    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_PROCESS_STATE', tmp_path / 'process.json')
    health = FreeTokenRuntimeAdapter().health()
    assert health['installed'] is False
    assert health['running'] is False


def test_freetoken_log_progress_parses_expert_banks():
    text = (
        'Loading DSV4 FP4 experts:  42%|████      | 18/43 [08:11<11:50, 28.42s/it]\n'
        'expert banks: slow path (serial build)\n'
    )
    progress = parse_freetoken_log_progress(text)
    assert progress['phase'] == 'experts'
    assert progress['expert_present'] == 18
    assert progress['expert_total'] == 43
    assert progress['expert_pct'] == 41.9
    assert progress['eta_seconds'] == 710
    assert progress['elapsed_seconds'] == 491


def test_freetoken_load_without_install_returns_install_hint(monkeypatch):
    monkeypatch.setattr(FreeTokenRuntimeAdapter, 'is_installed', staticmethod(lambda: False))
    result = FreeTokenRuntimeAdapter().load({'path': 'C:/models/qwen'})
    assert result['success'] is False
    assert result['requires_install'] is True
    assert 'WSL' in result['error']


def test_freetoken_load_reports_warming_when_port_opens_before_inference(monkeypatch, tmp_path: Path):
    import core.runtimes.freetoken as freetoken_mod

    model_dir = tmp_path / 'deepseek'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'model.safetensors').write_text('weights', encoding='utf-8')
    monkeypatch.setattr(FreeTokenRuntimeAdapter, 'is_installed', staticmethod(lambda: True))
    monkeypatch.setattr(freetoken_mod, 'probe_freetoken_inference_ready', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        FreeTokenRuntimeAdapter,
        '_start_server',
        lambda self, *_args, **_kwargs: {'success': True, 'pid': 99, 'port': 8911, 'warming': True},
    )
    monkeypatch.setattr(FreeTokenRuntimeAdapter, '_start_warmup_watch', lambda self, *_args, **_kwargs: None)
    result = FreeTokenRuntimeAdapter().load({'path': str(model_dir)})
    assert result['success'] is True
    assert result['warming'] is True
    assert result['loaded'] is False
    assert result['load_progress']['phase'] == 'starting'


def test_freetoken_command_uses_loopback_and_wsl_path(monkeypatch, tmp_path: Path):
    import core.runtimes.freetoken as freetoken_mod

    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    manifest = bundle / 'manifest.json'
    manifest.write_text(json.dumps({
        'backend': 'wsl',
        'wsl_distro': 'Ubuntu',
        'wsl_python': '/home/test/.dflash-console/freetoken-venv/bin/python',
        'wsl_ft': '/home/test/.dflash-console/freetoken-venv/bin/ft',
    }), encoding='utf-8')
    model_dir = tmp_path / 'models' / 'qwen'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'model.safetensors').write_text('weights', encoding='utf-8')
    log_path = tmp_path / 'freetoken.log'
    state_path = bundle / 'process.json'
    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_BUNDLE', bundle)
    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_MANIFEST', manifest)
    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_PROCESS_STATE', state_path)
    monkeypatch.setattr(freetoken_mod, 'FREETOKEN_LOG', log_path)
    monkeypatch.setattr(freetoken_mod, 'LOG_DIR', tmp_path)
    monkeypatch.setattr(freetoken_mod, '_tcp_open', lambda *args, **kwargs: True)
    monkeypatch.setattr(freetoken_mod, '_PROCESS', None)
    monkeypatch.setattr(freetoken_mod, '_PORT', 0)
    monkeypatch.setattr(freetoken_mod, '_ACTIVE_MODEL', '')

    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        return FakeProcess()

    monkeypatch.setattr(freetoken_mod.subprocess, 'Popen', fake_popen)
    result = FreeTokenRuntimeAdapter()._start_server(
        model_dir,
        profile={'port': 1919, 'gpu_device': '1'},
        settings=normalize_freetoken_settings({'moe_backend': 'offload'}),
    )
    assert result['success'] is True
    assert calls[0][:9] == [
        'wsl',
        '-d',
        'Ubuntu',
        '--',
        'env',
        'DFLASH_CONSOLE_RUNTIME=dflash-console-freetoken',
        '/home/test/.dflash-console/freetoken-venv/bin/ft',
        'serve',
        '--model',
    ]
    assert calls[0][9].startswith('/mnt/')
    assert '--host' in calls[0]
    assert calls[0][calls[0].index('--host') + 1] == '127.0.0.1'
    assert '--gpu' in calls[0]
