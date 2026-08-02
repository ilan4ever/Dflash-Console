from __future__ import annotations

from core.api_access_log import is_error_line, record_api_call
from core.api_introspection import get_installed_payload, list_app_endpoints


def test_record_api_call_ring_buffer():
    record_api_call(method='GET', path='/api/health', status=200, duration_ms=1.2)
    record_api_call(method='GET', path='/api/missing', status=404, duration_ms=2.5, error='not found')
    from core.api_access_log import list_api_calls

    rows = list_api_calls(tail=10)
    assert len(rows) >= 2
    assert rows[-1]['status'] == 404


def test_is_error_line():
    assert is_error_line('Traceback (most recent call last)')
    assert is_error_line('[ERROR] engine restore failed')
    assert not is_error_line('GET /api/health -> 200')


def test_list_app_endpoints_shape():
    class _Route:
        methods = {'GET'}
        path = '/api/health'
        name = 'health'
        summary = 'Console liveness.'
        tags = []
        endpoint = None

    class _App:
        class _BlankEndpointRoute:
            methods = {'GET'}
            path = '/api/blank'
            name = 'blank'
            summary = ''
            tags = []
            endpoint = lambda: None

        routes = [_Route(), _BlankEndpointRoute()]

    payload = list_app_endpoints(_App(), console_base='http://127.0.0.1:8900')
    assert payload['success'] is True
    assert payload['count'] == 2
    assert {row['path'] for row in payload['endpoints']} == {'/api/health', '/api/blank'}


def test_installed_payload_has_providers():
    payload = get_installed_payload()
    assert payload['success'] is True
    assert 'models' in payload
    assert 'providers' in payload
    assert isinstance(payload['providers'], list)
