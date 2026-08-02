from unittest.mock import patch

from core.runtime import _cached_status_while_generating, _SERVER_STATUS_CACHE, get_status_payload


def test_cached_status_while_generating_uses_last_snapshot():
    server_id = 'gemma-31b-dflash'
    _SERVER_STATUS_CACHE[server_id] = {
        'id': server_id,
        'loaded_models': ['gemma-4-31b-it-dflash'],
        'status': 'loaded',
        'running': True,
        'api_url': 'http://127.0.0.1:8090',
    }
    server = {
        'id': server_id,
        'model_id': 'gemma-4-31b-it-dflash',
        'host': '127.0.0.1',
        'port': 8090,
        'api_url': 'http://127.0.0.1:8090',
    }
    with patch('core.inference_stats.is_proxy_generating', return_value=True), patch(
        'core.runtime._live_stats_during_generation',
        return_value={'generating': True, 'generating_tokens_per_second': 12.0},
    ):
        status = _cached_status_while_generating(
            server,
            server_id=server_id,
            host='127.0.0.1',
            port=8090,
            configured_model_id='gemma-4-31b-it-dflash',
        )
    assert status is not None
    assert status['loaded_models'] == ['gemma-4-31b-it-dflash']
    assert status['inference_stats']['generating'] is True


def test_cached_status_while_generating_falls_back_to_port_when_no_snapshot():
    server_id = 'gemma-12b-dflash'
    _SERVER_STATUS_CACHE.pop(server_id, None)
    server = {
        'id': server_id,
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191',
    }
    with patch('core.inference_stats.is_proxy_generating', return_value=True), patch(
        'core.runtime._live_stats_during_generation',
        return_value={'generating': True},
    ), patch('core.runtime.tcp_port_open', return_value=True):
        status = _cached_status_while_generating(
            server,
            server_id=server_id,
            host='127.0.0.1',
            port=8191,
            configured_model_id='gemma-4-12b-it-qat',
        )
    assert status is not None
    assert status['running'] is True
    assert status['loaded_models'] == ['gemma-4-12b-it-qat']


def test_get_status_payload_returns_stale_while_generating():
    servers = [{'id': 'gemma-31b-dflash', 'enabled': True, 'api_url': 'http://127.0.0.1:8090'}]
    from core import runtime as runtime_mod

    runtime_mod._STATUS_PAYLOAD_CACHE['payload'] = {
        'success': True,
        'servers': [{
            'id': 'gemma-31b-dflash',
            'status': 'loaded',
            'api_url': 'http://127.0.0.1:8090',
            'active_model_id': 'gemma-4-31b-it-dflash',
            'inference_stats': {'generating_tokens_per_second': 27.0},
        }],
        'primary_server_id': 'gemma-31b-dflash',
        'updated_at': 1.0,
    }
    runtime_mod._STATUS_PAYLOAD_CACHE['include_external'] = False
    runtime_mod._STATUS_PAYLOAD_CACHE['updated_at'] = 1.0

    with patch('core.runtime._any_proxy_generating', return_value=True), patch(
        'core.runtime._live_stats_during_generation',
        return_value={'generating': True, 'generating_tokens_per_second': 31.5},
    ):
        payload = get_status_payload(servers, include_external=False, allow_stale=True)

    assert payload['stale'] is True
    assert payload['servers'][0]['inference_stats']['generating_tokens_per_second'] == 31.5


def test_external_rows_survive_non_external_poll_during_generation():
    from core import runtime as runtime_mod

    runtime_mod._STATUS_EXTERNAL_CACHE[:] = [{
        'pid': 1234,
        'model_name': 'small.en',
        'app_label': 'OneVoice',
    }]
    runtime_mod._STATUS_PAYLOAD_CACHE['payload'] = {
        'success': True,
        'servers': [{
            'id': 'gemma-31b-dflash',
            'status': 'loaded',
            'api_url': 'http://127.0.0.1:8090',
            'active_model_id': 'gemma-4-31b-it-dflash',
        }],
        'primary_server_id': 'gemma-31b-dflash',
        'updated_at': 1.0,
    }
    runtime_mod._STATUS_PAYLOAD_CACHE['include_external'] = False
    runtime_mod._STATUS_PAYLOAD_CACHE['updated_at'] = 1.0

    servers = [{'id': 'gemma-31b-dflash', 'enabled': True, 'api_url': 'http://127.0.0.1:8090'}]
    try:
        with patch('core.runtime._any_proxy_generating', return_value=True), patch(
            'core.runtime._live_stats_during_generation',
            return_value={'generating': True, 'generating_tokens': 12},
        ):
            payload = get_status_payload(servers, include_external=True, allow_stale=True)
    finally:
        runtime_mod._STATUS_EXTERNAL_CACHE.clear()

    assert payload['external_gpu_loads'][0]['model_name'] == 'small.en'
