"""Tests for engine module version checks and automatic updates."""

from __future__ import annotations

import importlib

import pytest

import core.component_versions as component_versions
import core.components_hub as components_hub


def test_component_versions_cover_every_on_demand_engine():
    assert set(component_versions.PYPI_PACKAGES) == {'vllm', 'transformers', 'freetoken'}


def test_version_parser_handles_ft_output_and_pypi_versions():
    assert component_versions._clean_version('freetoken version 0.1.2') == '0.1.2'
    assert component_versions._clean_version('0.28.0\n') == '0.28.0'
    assert component_versions.is_newer('0.32.0', '0.28.0') is True
    assert component_versions.is_newer('0.28.0', '0.32.0') is False
    assert component_versions.is_newer('0.28.0', '0.28.0') is False


def test_refresh_marks_updates_for_each_engine(monkeypatch):
    component_versions.invalidate()
    manifests = {
        'vllm': {'package_version': '0.28.0'},
        'transformers': {'package_version': '5.15.0'},
        'freetoken': {'package_version': '0.1.2'},
    }
    latest = {
        'vllm': '0.32.0',
        'transformers': '5.20.2',
        'freetoken': '0.2.0',
    }
    monkeypatch.setattr(component_versions, '_manifest', lambda runtime_id: manifests.get(runtime_id, {}))
    monkeypatch.setattr(component_versions, '_pypi_latest', lambda package: latest[package])

    snapshot = component_versions.refresh_versions(force=True)

    assert snapshot['versions']['vllm']['version_update_available'] is True
    assert snapshot['versions']['transformers']['version_update_available'] is True
    assert snapshot['versions']['freetoken']['installed_version'] == '0.1.2'
    assert snapshot['versions']['freetoken']['latest_version'] == '0.2.0'
    component_versions.invalidate()


def test_component_row_exposes_installed_and_latest_versions(monkeypatch):
    monkeypatch.setattr(components_hub, '_read_manifest', lambda runtime_id: {'bundle_revision': 1})
    monkeypatch.setattr(
        components_hub,
        '_on_demand_status',
        lambda runtime_id: {'installed': True, 'status': 'installed'},
    )
    entry = next(row for row in components_hub.CATALOG if row['id'] == 'freetoken')
    row = components_hub._component_row(entry, versions={
        'freetoken': {
            'installed_version': '0.1.2',
            'latest_version': '0.2.0',
            'version_update_available': True,
        },
    })
    assert row['installed_version'] == '0.1.2'
    assert row['latest_version'] == '0.2.0'
    assert row['version_update_available'] is True
    assert row['update_available'] is True
    assert row['status'] == 'update_available'


@pytest.mark.parametrize(
    ('module_name', 'worker_kwargs'),
    [
        ('core.vllm_runtime_install', {'backend': 'wsl'}),
        ('core.transformers_runtime_install', {'torch_variant': 'cuda'}),
        ('core.freetoken_runtime_install', {}),
    ],
)
def test_forced_update_stops_the_engine_before_replacing_its_files(
    monkeypatch, tmp_path, module_name, worker_kwargs,
):
    module = importlib.import_module(module_name)
    module._JOB.clear()
    stopped = []
    monkeypatch.setattr(module, '_SCRIPT', tmp_path / 'missing-installer.ps1')
    monkeypatch.setattr(module, '_stop_runtime', lambda: stopped.append(True))

    module._install_worker(force=True, **worker_kwargs)

    assert stopped == [True]
    assert module._JOB['status'] == 'error'
    module._JOB.clear()


def test_component_row_keeps_a_failed_update_visible(monkeypatch):
    monkeypatch.setattr(components_hub, '_read_manifest', lambda runtime_id: {'bundle_revision': 1})
    monkeypatch.setattr(
        components_hub,
        '_on_demand_status',
        lambda runtime_id: {
            'installed': True,
            'status': 'error',
            'error': 'python.exe is locked',
        },
    )
    entry = next(row for row in components_hub.CATALOG if row['id'] == 'transformers')
    row = components_hub._component_row(entry, versions={
        'transformers': {
            'installed_version': '5.15.0',
            'latest_version': '5.20.2',
            'version_update_available': True,
        },
    })
    assert row['status'] == 'error'
    assert row['install_error'] == 'python.exe is locked'
    assert row['installed_version'] == '5.15.0'


def test_auto_update_starts_one_engine_when_enabled(monkeypatch):
    components_hub._AUTO_LAST_ATTEMPT.clear()
    monkeypatch.setattr('core.config.load_config', lambda: {'auto_update_components': True})
    monkeypatch.setattr(
        components_hub,
        'list_components_payload',
        lambda: {
            'components': [{
                'id': 'freetoken',
                'runtime_id': 'freetoken',
                'install_mode': 'on_demand',
                'status': 'update_available',
                'update_available': True,
                'installed_version': '0.1.2',
                'latest_version': '0.2.0',
                'manifest': {'backend': 'wsl'},
            }],
        },
    )
    monkeypatch.setattr(components_hub, '_runtime_is_running', lambda runtime_id: False)
    monkeypatch.setattr(components_hub, '_start_component_update', lambda row: {'success': True})

    result = components_hub.auto_update_tick(now=1000.0)

    assert result['enabled'] is True
    assert result['started'] is True
    assert result['runtime_id'] == 'freetoken'


def test_auto_update_skips_an_engine_that_is_running(monkeypatch):
    components_hub._AUTO_LAST_ATTEMPT.clear()
    monkeypatch.setattr('core.config.load_config', lambda: {'auto_update_components': True})
    monkeypatch.setattr(
        components_hub,
        'list_components_payload',
        lambda: {
            'components': [{
                'id': 'vllm',
                'runtime_id': 'vllm',
                'install_mode': 'on_demand',
                'status': 'update_available',
                'update_available': True,
                'installed_version': '0.28.0',
                'latest_version': '0.32.0',
                'manifest': {'backend': 'wsl'},
            }],
        },
    )
    monkeypatch.setattr(components_hub, '_runtime_is_running', lambda runtime_id: True)

    result = components_hub.auto_update_tick(now=1000.0)

    assert result['started'] is False
    assert any('still running' in item for item in result['skipped'])


def test_auto_update_is_off_by_default(monkeypatch):
    components_hub._AUTO_LAST_ATTEMPT.clear()
    monkeypatch.setattr('core.config.load_config', lambda: {})
    result = components_hub.auto_update_tick(now=1000.0)
    assert result['enabled'] is False
    assert result['started'] is False


@pytest.mark.parametrize(
    'module_name',
    [
        'core.vllm_runtime_install',
        'core.transformers_runtime_install',
        'core.freetoken_runtime_install',
    ],
)
def test_install_status_does_not_hide_a_failed_update(monkeypatch, module_name):
    module = importlib.import_module(module_name)
    module._JOB.clear()
    module._JOB.update({'status': 'error', 'error': 'python.exe is locked'})
    monkeypatch.setattr(module, 'is_installed', lambda: True)

    status = module.install_status()

    assert status['status'] == 'error'
    assert status['error'] == 'python.exe is locked'
    module._JOB.clear()


@pytest.mark.parametrize(
    ('module_name', 'kwargs'),
    [
        ('core.vllm_runtime_install', {'backend': 'wsl'}),
        ('core.transformers_runtime_install', {'torch_variant': 'cuda'}),
        ('core.freetoken_runtime_install', {'backend': 'wsl'}),
    ],
)
def test_force_update_starts_an_install_for_each_engine(monkeypatch, module_name, kwargs):
    module = importlib.import_module(module_name)
    module._JOB.clear()
    monkeypatch.setattr(module, 'is_installed', lambda: True)

    class FakeThread:
        def __init__(self, *args, **thread_kwargs):
            self.args = args
            self.kwargs = thread_kwargs

        def start(self):
            return None

    monkeypatch.setattr(module.threading, 'Thread', FakeThread)
    result = module.start_install(force=True, **kwargs)

    assert result['success'] is True
    assert result['started'] is True
    assert result.get('already_installed') is not True
    module._JOB.clear()
