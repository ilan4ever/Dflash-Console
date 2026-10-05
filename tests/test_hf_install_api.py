from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException


def test_resolve_download_filenames_single_file():
    from core.hf_install import resolve_download_filenames

    files = [{'filename': 'model-q4_k_m.gguf', 'size_bytes': 10_000_000}]
    assert resolve_download_filenames(files, None) == ['model-q4_k_m.gguf']


def test_resolve_download_filenames_shard_group():
    from core.hf_install import resolve_download_filenames

    files = [
        {'filename': 'model-00001-of-00003.gguf', 'size_bytes': 5_000_000},
        {'filename': 'model-00002-of-00003.gguf', 'size_bytes': 50_000_000},
        {'filename': 'model-00003-of-00003.gguf', 'size_bytes': 50_000_000},
    ]
    names = resolve_download_filenames(files, 'model-00001-of-00003.gguf')
    assert names == [
        'model-00001-of-00003.gguf',
        'model-00002-of-00003.gguf',
        'model-00003-of-00003.gguf',
    ]


def test_execute_hf_install_already_installed_then_load():
    from core.hf_install import execute_hf_install

    with patch('core.hf_install.get_model_detail') as detail, patch(
        'core.hf_install.start_download'
    ) as start, patch('core.hf_install.execute_catalog_load') as load:
        detail.return_value = {
            'success': True,
            'model': {
                'id': 'org/example',
                'gguf_files': [{'filename': 'example-q4.gguf', 'size_bytes': 1_000}],
            },
        }
        start.return_value = {
            'success': False,
            'already_installed': True,
            'path': 'C:/models/org/example/example-q4.gguf',
        }
        load.return_value = {'success': True, 'loaded': True, 'server_id': 'demo'}

        result = execute_hf_install(
            repo_id='org/example',
            filename='example-q4.gguf',
            cfg={'servers': []},
        )

    assert result['phase'] == 'ready'
    assert result['path'].endswith('example-q4.gguf')
    load.assert_called_once()


def test_execute_hf_install_requires_query_or_repo():
    from core.hf_install import execute_hf_install

    with pytest.raises(HTTPException) as exc:
        execute_hf_install(cfg={'servers': []})
    assert exc.value.status_code == 400


def test_get_loaded_models_payload_shape():
    from core.status_report import get_loaded_models_payload

    with patch('core.status_report.get_status_payload') as status, patch(
        'core.status_report._runtime_rows'
    ) as runtimes, patch('core.status_report.get_gpu_devices_payload') as gpus:
        gpus.return_value = {'gpus': []}
        status.return_value = {
            'servers': [{
                'id': 'demo',
                'label': 'Demo',
                'status': 'loaded',
                'loaded_models': ['demo-model'],
                'active_model_id': 'demo-model',
                'api_url': 'http://127.0.0.1:8090/v1',
                'ready_for_chat': True,
                'visible_cards': [{'path': 'C:/models/demo.gguf'}],
            }],
        }
        runtimes.return_value = [{
            'id': 'piper',
            'runtime_id': 'piper',
            'label': 'Piper',
            'active_model': '',
            'running': False,
        }]

        payload = get_loaded_models_payload(cfg={'servers': []})

    assert payload['success'] is True
    assert payload['count'] == 1
    assert payload['loaded'][0]['server_id'] == 'demo'


def test_runtime_status_report_includes_component_and_vram_attribution():
    from core.status_report import _runtime_rows

    class _Adapter:
        def health(self):
            return {
                'running': True,
                'port': 9010,
                'host': '127.0.0.1',
                'active_model': r'C:\models\TeleOCR',
            }

    with patch('core.status_report.get_runtime_adapter', return_value=_Adapter()), patch(
        'core.gpu_processes.process_attribution_for_port',
        return_value={
            'pids': [4242],
            'vram_by_gpu': {'0': 2.65},
            'vram_total_gb': 2.65,
            'vram_source': 'nvidia-smi',
        },
    ):
        rows = _runtime_rows({
            'runtimes': [{
                'id': 'runtime-vllm',
                'runtime_id': 'vllm',
                'label': 'vLLM',
                'enabled': True,
            }],
        })

    assert rows[0]['component_label'] == 'OneVoice vLLM'
    assert rows[0]['active_model_id'] == 'TeleOCR'
    assert rows[0]['vram_total_gb'] == 2.65
    assert rows[0]['vram_source'] == 'nvidia-smi'
    assert rows[0]['pids'] == [4242]


def test_get_status_report_payload_shape():
    from core.status_report import get_status_report_payload

    with patch('core.status_report.get_system_stats_payload') as system, patch(
        'core.status_report.get_gpu_devices_payload'
    ) as gpus, patch('core.status_report.get_status_payload') as engines, patch(
        'core.status_report._runtime_rows'
    ) as runtimes, patch('core.status_report.runtime_ids') as adapters, patch(
        'core.status_report.get_loaded_models_payload'
    ) as loaded:
        system.return_value = {'success': True, 'cpu_percent': 10}
        gpus.return_value = {'success': True, 'gpus': []}
        engines.return_value = {'servers': [], 'primary_server_id': ''}
        runtimes.return_value = []
        adapters.return_value = {'piper', 'stt'}
        loaded.return_value = {'success': True, 'count': 0, 'loaded': []}

        payload = get_status_report_payload(cfg={'servers': []})

    assert payload['success'] is True
    assert payload['system']['cpu_percent'] == 10
    assert 'engines' in payload
    assert 'loaded' in payload
