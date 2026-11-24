"""Sign-in, machine keys, and local-only routes (core/access_control.py).

Another computer is faked by giving the test client a non-loopback address;
the default test client acts as a program on this PC (see conftest.py).
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from api import app as app_module
from api.app import app
from api.gateway import gateway_app
from core import access_control as ac
from core import auth

CONSOLE_URL = 'http://192.168.1.10:8900'
ORIGIN = {'origin': CONSOLE_URL}
PASSWORD = 'owner-password-1'


def remote_client(ip: str = '192.168.1.50', *, base_url: str = CONSOLE_URL, target=app) -> TestClient:
    return TestClient(target, client=(ip, 50000), base_url=base_url)


def local_client(target=app, ip: str = '127.0.0.1') -> TestClient:
    return TestClient(target, client=(ip, 50000))


def signed_in_client(ip: str = '192.168.1.50') -> TestClient:
    if not auth.password_is_set():
        auth.set_owner_password(PASSWORD, require_current=False)
    client = remote_client(ip)
    resp = client.post('/api/auth/login', json={'password': PASSWORD}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    return client


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {'x-csrf-token': client.cookies.get(ac.CSRF_COOKIE) or '', **ORIGIN}


def new_key(**options) -> str:
    raw, _record = auth.create_key('Other Console', **options)
    return raw


def bearer(raw: str) -> dict[str, str]:
    return {'authorization': f'Bearer {raw}'}


def _sample_path(template: str) -> str:
    samples = {'runtime_id': 'piper', 'server_id': 'srv1', 'pid': '1234'}

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        return samples.get(name, f'sample-{name}')

    return re.sub(r'\{([a-z_]+)(?::path)?\}', fill, template)


def _scope(method: str, path: str, *, ip: str = '192.168.1.50', headers: dict[str, str] | None = None) -> dict:
    raw = [(key.lower().encode('latin-1'), value.encode('latin-1')) for key, value in (headers or {}).items()]
    raw.append((b'host', b'192.168.1.10:8900'))
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'raw_path': path.encode(),
        'root_path': '',
        'scheme': 'http',
        'query_string': b'',
        'headers': raw,
        'client': (ip, 50000),
        'server': ('192.168.1.10', 8900),
    }


CONSOLE_POLICY = ac.AccessPolicy(name='console', routes=ac.console_route_table(), mounts=ac.CONSOLE_MOUNTS)


# ---------------------------------------------------------------- the route table

def _live_routes(target) -> set[tuple[str, str]]:
    live: set[tuple[str, str]] = set()
    for route in target.routes:
        if isinstance(route, Mount):
            continue
        # Anything else (an included-router wrapper, for example) could not be classified.
        assert isinstance(route, (APIRoute, Route)), f'unclassifiable route object: {route!r}'
        for method in sorted(route.methods or ()):
            if method != 'HEAD':
                live.add((method, route.path))
    return live


def test_every_console_route_is_classified_exactly():
    table = ac.console_route_table()
    live = _live_routes(app)
    assert sorted(live - set(table)) == [], 'classify new routes in core/access_control.py'
    assert sorted(set(table) - live) == [], 'remove stale rows from core/access_control.py'
    for route in app.routes:
        if isinstance(route, Mount):
            assert route.path in ac.CONSOLE_MOUNTS


def test_every_gateway_route_is_classified_exactly():
    table = ac.gateway_route_table()
    live = _live_routes(gateway_app)
    assert sorted(live - set(table)) == []
    assert sorted(set(table) - live) == []


def test_route_lists_do_not_overlap():
    groups = [
        ac.CONSOLE_PUBLIC_ROUTES,
        ac.CONSOLE_KEY_ROUTES,
        ac.CONSOLE_KEY_DOWNLOAD_ROUTES,
        ac.CONSOLE_LOCAL_ONLY_ROUTES,
        ac.CONSOLE_SESSION_ROUTES,
    ]
    rows = [row for group in groups for row in group]
    assert len(rows) == len(set(rows))


@pytest.mark.parametrize('row', ac.CONSOLE_KEY_ROUTES + ac.CONSOLE_LOCAL_ONLY_ROUTES + ac.CONSOLE_PUBLIC_ROUTES)
def test_router_matches_each_listed_route_to_itself(row):
    method, template = row.split(' ', 1)
    cls, matched = ac.classify_route(_scope(method, _sample_path(template)), CONSOLE_POLICY, app.routes)
    assert matched == template
    assert cls == ac.console_route_table()[(method, template)]


def test_dangerous_routes_are_local_only():
    local_only = set(ac.CONSOLE_LOCAL_ONLY_ROUTES)
    for row in (
        'POST /api/shutdown',
        'GET /api/fs/browse',
        'POST /api/fs/reveal',
        'GET /api/config/export',
        'GET /api/presets/export',
        'POST /api/presets/import',
        'GET /api/diagnostics/bundle',
        'POST /api/runtimes/{runtime_id}/install',
        'POST /api/runtimes/{runtime_id}/uninstall',
        'POST /api/components/check-updates',
        'POST /api/external/launchers',
        'POST /api/external/launchers/colibri',
        'POST /api/model-libraries/import',
        'POST /api/models/import-into-console',
        'POST /api/setup/complete',
    ):
        assert row in local_only, row
    assert {'dflash_root', 'servers', 'runtimes', 'model_libraries', 'remote_nodes'} <= ac.LOCAL_ONLY_CONFIG_FIELDS
    assert ac.LOCAL_ONLY_NODE_FIELDS == {'ssh_host', 'ssh_local_port', 'ssh_remote_port'}


# ---------------------------------------------------------------- who counts as local

@pytest.mark.parametrize('host', ['127.0.0.1', '127.0.0.53', '::1', '::ffff:127.0.0.1'])
def test_loopback_addresses_are_local(host):
    assert ac.request_is_local({'client': (host, 1), 'headers': []})


@pytest.mark.parametrize('host', ['192.168.1.50', '10.0.0.2', '100.73.13.79', '8.8.8.8', 'testclient', ''])
def test_other_addresses_are_not_local(host):
    assert not ac.request_is_local({'client': (host, 1) if host else None, 'headers': []})


def test_proxied_loopback_request_is_not_local():
    scope = {'client': ('127.0.0.1', 1), 'headers': [(b'x-forwarded-for', b'203.0.113.7')]}
    assert not ac.request_is_local(scope)
    resp = local_client().get('/api/health', headers={'X-Forwarded-For': '203.0.113.7'})
    assert resp.status_code == 401


# ---------------------------------------------------------------- middleware

@pytest.mark.parametrize('ip', ['127.0.0.1', '::1'])
def test_loopback_passes_without_sign_in(ip):
    client = local_client(ip=ip)
    assert client.get('/api/health').status_code == 200
    # Local OpenAI clients often send a dummy key; it is ignored.
    assert client.get('/api/health', headers=bearer('sk-not-a-console-key')).status_code == 200
    resp = client.get('/api/auth/status')
    assert resp.json()['local'] is True
    assert 'set-cookie' not in resp.headers


def test_loopback_needs_no_csrf_and_gets_no_cookies():
    client = local_client()
    resp = client.post('/api/security/keys', json={'name': 'Local test'})
    assert resp.status_code == 200
    assert 'set-cookie' not in resp.headers


def test_remote_without_credentials_gets_401():
    client = remote_client()
    resp = client.get('/api/health')
    assert resp.status_code == 401
    assert resp.json()['detail']['error'] == 'auth_required'
    assert resp.headers['www-authenticate'].startswith('Bearer')
    assert client.get('/api/models').status_code == 401
    assert client.get('/static/js/console-api.js').status_code == 401


def test_remote_browser_gets_the_sign_in_page():
    client = remote_client()
    resp = client.get('/', headers={'accept': 'text/html'})
    assert resp.status_code == 401
    assert 'text/html' in resp.headers['content-type']
    assert '/api/auth/login' in resp.text
    login = client.get('/login')
    assert login.status_code == 200
    assert '/api/auth/login' in login.text


def test_wrong_and_revoked_keys_get_401():
    client = remote_client()
    resp = client.get('/api/health', headers=bearer('dfk_' + 'A' * 43))
    assert resp.status_code == 401
    assert resp.json()['detail']['error'] == 'invalid_key'

    raw, record = auth.create_key('OMAN')
    assert client.get('/api/health', headers=bearer(raw)).status_code == 200
    auth.revoke_key(record['id'])
    revoked = client.get('/api/health', headers=bearer(raw))
    assert revoked.status_code == 401
    assert revoked.json()['detail']['error'] == 'invalid_key'


def test_key_usage_is_recorded():
    raw, record = auth.create_key('OMAN')
    remote_client('192.168.1.77').get('/api/health', headers=bearer(raw))
    assert auth.get_key(record['id'])['last_used_ip'] == '192.168.1.77'


@pytest.mark.parametrize('row', ac.CONSOLE_KEY_ROUTES)
def test_machine_key_reaches_gpu_sharing_routes(row):
    method, template = row.split(' ', 1)
    raw = new_key()
    decision, cls = ac.evaluate_request(_scope(method, _sample_path(template), headers=bearer(raw)), CONSOLE_POLICY, app.routes)
    assert decision.allowed, (row, decision.error)
    assert decision.context.kind == 'key'
    assert cls == ac.KEY


@pytest.mark.parametrize('row', [
    'GET /',
    'GET /api/config',
    'PUT /api/config',
    'GET /api/security',
    'POST /api/security/keys',
    'POST /api/remote-gpus/load',
    'POST /api/nodes',
    'POST /api/hf/install',
    'DELETE /api/models/file',
    'POST /api/servers/{server_id}/start',
])
def test_machine_key_cannot_reach_owner_routes(row):
    method, template = row.split(' ', 1)
    raw = new_key(may_download=True)
    decision, _cls = ac.evaluate_request(_scope(method, _sample_path(template), headers=bearer(raw)), CONSOLE_POLICY, app.routes)
    assert not decision.allowed
    assert (decision.status, decision.error) == (403, 'key_not_allowed')


def test_machine_key_cannot_open_a_browser_session():
    auth.set_owner_password(PASSWORD, require_current=False)
    raw = new_key()
    client = remote_client()
    resp = client.post('/api/auth/login', json={'password': 'anything'}, headers={**bearer(raw), **ORIGIN})
    assert resp.status_code == 403
    assert resp.json()['detail']['error'] == 'key_cannot_sign_in'
    assert ac.SESSION_COOKIE not in resp.cookies
    # Not even with the right password.
    resp = client.post('/api/auth/login', json={'password': PASSWORD}, headers={**bearer(raw), **ORIGIN})
    assert resp.status_code == 403
    assert ac.SESSION_COOKIE not in resp.cookies
    assert client.get('/', headers={**bearer(raw), 'accept': 'text/html'}).status_code == 403
    assert client.get('/api/security', headers=bearer(raw)).status_code == 403


@pytest.mark.parametrize('row', ac.CONSOLE_LOCAL_ONLY_ROUTES)
def test_dangerous_routes_refuse_remote_session_and_key(row):
    """Evaluated without running the handlers (one of them stops the process)."""
    method, template = row.split(' ', 1)
    path = _sample_path(template)
    auth.set_owner_password(PASSWORD, require_current=False)
    token, _session = auth.create_session(ip='192.168.1.50')
    session_headers = {
        'cookie': f'{ac.SESSION_COOKIE}={token}',
        'x-csrf-token': auth.csrf_token_for(token),
        **ORIGIN,
    }
    for headers in (session_headers, bearer(new_key(may_download=True)), {}):
        decision, cls = ac.evaluate_request(_scope(method, path, headers=headers), CONSOLE_POLICY, app.routes)
        assert cls == ac.LOCAL_ONLY
        assert (decision.allowed, decision.status, decision.error) == (False, 403, 'local_only')
    # The same request from this PC passes the access check.
    local, _cls = ac.evaluate_request(_scope(method, path, ip='127.0.0.1'), CONSOLE_POLICY, app.routes)
    assert local.allowed


def test_dangerous_routes_over_http_with_a_valid_session():
    client = signed_in_client()
    headers = csrf_headers(client)
    for method, path in (
        ('GET', '/api/fs/browse?path=C:/'),
        ('GET', '/api/config/export'),
        ('GET', '/api/presets/export'),
        ('POST', '/api/runtimes/vllm/install'),
        ('POST', '/api/external/launchers'),
    ):
        resp = client.request(method, path, headers=headers, json={} if method == 'POST' else None)
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()['detail']['error'] == 'local_only'


# ---------------------------------------------------------------- browser sessions and CSRF

def test_sign_in_sets_httponly_strict_cookie():
    auth.set_owner_password(PASSWORD, require_current=False)
    client = remote_client()
    resp = client.post('/api/auth/login', json={'password': PASSWORD}, headers=ORIGIN)
    assert resp.status_code == 200
    cookies = [value for key, value in resp.headers.multi_items() if key == 'set-cookie']
    session_cookie = next(value for value in cookies if value.startswith(f'{ac.SESSION_COOKIE}='))
    csrf_cookie = next(value for value in cookies if value.startswith(f'{ac.CSRF_COOKIE}='))
    assert 'HttpOnly' in session_cookie
    assert 'SameSite=strict' in session_cookie or 'SameSite=Strict' in session_cookie
    assert 'HttpOnly' not in csrf_cookie
    assert client.get('/api/health').status_code == 200
    status = client.get('/api/auth/status').json()
    assert status['signed_in'] is True and status['via'] == 'session'


def test_remote_session_post_needs_csrf_token_and_origin():
    client = signed_in_client()
    token = client.cookies.get(ac.CSRF_COOKIE)

    missing = client.delete('/api/security/sessions', headers=ORIGIN)
    assert missing.status_code == 403
    assert missing.json()['detail']['error'] == 'csrf_failed'

    wrong = client.delete('/api/security/sessions', headers={'x-csrf-token': 'x' * 64, **ORIGIN})
    assert wrong.status_code == 403

    foreign = client.delete('/api/security/sessions', headers={'x-csrf-token': token, 'origin': 'http://evil.example'})
    assert foreign.status_code == 403
    assert foreign.json()['detail']['error'] == 'origin_mismatch'

    no_origin = client.delete('/api/security/sessions', headers={'x-csrf-token': token})
    assert no_origin.status_code == 403

    ok = client.delete('/api/security/sessions', headers={'x-csrf-token': token, **ORIGIN})
    assert ok.status_code == 200


def test_odd_header_bytes_are_refused_not_crashed():
    auth.set_owner_password(PASSWORD, require_current=False)
    token, _session = auth.create_session(ip='192.168.1.50')
    scope = _scope('DELETE', '/api/security/sessions', headers={'cookie': f'{ac.SESSION_COOKIE}={token}', **ORIGIN})
    scope['headers'].append((b'x-csrf-token', 'caf\xe9'.encode('latin-1')))
    decision, _cls = ac.evaluate_request(scope, CONSOLE_POLICY, app.routes)
    assert (decision.status, decision.error) == (403, 'csrf_failed')

    odd_key = _scope('GET', '/api/health')
    odd_key['headers'].append((b'authorization', 'Bearer dfk_\xe9\xff'.encode('latin-1')))
    decision, _cls = ac.evaluate_request(odd_key, CONSOLE_POLICY, app.routes)
    assert (decision.status, decision.error) == (401, 'invalid_key')


def test_revoked_session_gets_401_and_cookie_cleared():
    client = signed_in_client()
    session_id = client.get('/api/auth/status').json()['session_id']
    auth.revoke_session(session_id)
    resp = client.get('/api/health')
    assert resp.status_code == 401
    assert resp.json()['detail']['error'] == 'session_expired'
    assert any(
        value.startswith(f'{ac.SESSION_COOKIE}=') for key, value in resp.headers.multi_items() if key == 'set-cookie'
    )


def test_logout_revokes_the_session():
    client = signed_in_client()
    token = client.cookies.get(ac.SESSION_COOKIE)
    resp = client.post('/api/auth/logout', headers=csrf_headers(client))
    assert resp.status_code == 200
    assert auth.validate_session(token) is None


def test_sign_in_errors():
    client = remote_client()
    no_password = client.post('/api/auth/login', json={'password': 'whatever'}, headers=ORIGIN)
    assert no_password.status_code == 409
    auth.set_owner_password(PASSWORD, require_current=False)
    wrong = client.post('/api/auth/login', json={'password': 'not-it'}, headers=ORIGIN)
    assert wrong.status_code == 401
    assert wrong.json()['detail']['error'] == 'wrong_password'
    foreign = client.post('/api/auth/login', json={'password': PASSWORD}, headers={'origin': 'http://evil.example'})
    assert foreign.status_code == 403
    assert ac.SESSION_COOKIE not in foreign.cookies


def test_sign_in_rate_limit(monkeypatch):
    monkeypatch.setattr(auth.LOGIN_THROTTLE, 'MAX_DELAY_SECONDS', 0.0)
    auth.set_owner_password(PASSWORD, require_current=False)
    client = remote_client('192.168.1.66')
    codes = [
        client.post('/api/auth/login', json={'password': f'guess-{n}'}, headers=ORIGIN).status_code
        for n in range(auth.LoginThrottle.MAX_FAILURES)
    ]
    assert codes == [401] * (auth.LoginThrottle.MAX_FAILURES - 1) + [429]
    locked = client.post('/api/auth/login', json={'password': PASSWORD}, headers=ORIGIN)
    assert locked.status_code == 429
    assert int(locked.headers['retry-after']) > 0
    assert locked.json()['detail']['error'] == 'sign_in_locked'
    # A different computer is not locked out by this one.
    other = remote_client('192.168.1.67').post('/api/auth/login', json={'password': PASSWORD}, headers=ORIGIN)
    assert other.status_code == 200


def test_remote_password_change_needs_current_password():
    client = signed_in_client()
    headers = csrf_headers(client)
    refused = client.put('/api/security/password', json={'new_password': 'brand-new-pass'}, headers=headers)
    assert refused.status_code == 403
    ok = client.put(
        '/api/security/password',
        json={'new_password': 'brand-new-pass', 'current_password': PASSWORD},
        headers=headers,
    )
    assert ok.status_code == 200
    # The browser that changed it stays signed in.
    assert client.get('/api/security').status_code == 200


# ---------------------------------------------------------------- per-field rules

@pytest.fixture
def fake_config(monkeypatch):
    cfg = {
        'ui_port': 8900,
        'gateway_port': 8001,
        'servers': [],
        'remote_nodes': [{
            'id': 'lab',
            'label': 'Lab',
            'base_url': 'http://192.168.1.80:8900',
            'api_token': 'dfk_secret_for_the_lab',
            'enabled': True,
            'ssh_host': '',
            'ssh_local_port': 0,
            'ssh_remote_port': 0,
            'share_gpu': True,
        }],
        'support_report_token': 'report-secret',
    }
    monkeypatch.setattr(app_module, 'load_config', lambda: dict(cfg))
    monkeypatch.setattr(app_module, '_save_config_checked', lambda _cfg: None)
    return cfg


def test_config_seen_from_another_computer_hides_secrets(fake_config):
    local = local_client().get('/api/config').json()['config']
    assert local['remote_nodes'][0]['api_token'] == 'dfk_secret_for_the_lab'

    client = signed_in_client()
    remote = client.get('/api/config').json()['config']
    assert remote['remote_nodes'][0]['api_token'] == ''
    assert remote['remote_nodes'][0]['has_token'] is True
    assert 'support_report_token' not in remote


def test_remote_owner_can_save_settings_but_not_local_only_fields(fake_config):
    client = signed_in_client()
    headers = csrf_headers(client)
    ok = client.put('/api/config', json={'cpu_slow_warn': True}, headers=headers)
    assert ok.status_code == 200, ok.text
    for body in ({'dflash_root': 'C:/evil'}, {'model_libraries': []}, {'servers': []}, {'ui_port': 9000}):
        refused = client.put('/api/config', json=body, headers=headers)
        assert refused.status_code == 403, body
        assert refused.json()['detail']['fields'] == sorted(body)


def test_remote_owner_cannot_add_an_ssh_node(monkeypatch, fake_config):
    import core.remote_nodes as remote_nodes

    monkeypatch.setattr(remote_nodes, 'add_remote_node', lambda payload, **_kw: {**payload, 'id': 'n1'})
    monkeypatch.setattr(remote_nodes, 'check_remote_node_health', lambda node, **_kw: {'status': 'online', 'online': True})
    client = signed_in_client()
    headers = csrf_headers(client)
    refused = client.post(
        '/api/nodes',
        json={'label': 'Prod', 'base_url': 'http://127.0.0.1:8990', 'ssh_host': 'production'},
        headers=headers,
    )
    assert refused.status_code == 403
    assert refused.json()['detail']['fields'] == ['ssh_host']
    ok = client.post('/api/nodes', json={'label': 'Lab', 'base_url': 'http://192.168.1.80:8900'}, headers=headers)
    assert ok.status_code == 200, ok.text
    # The same SSH node is fine on this PC.
    local = local_client().post(
        '/api/nodes',
        json={'label': 'Prod', 'base_url': 'http://127.0.0.1:8990', 'ssh_host': 'production'},
    )
    assert local.status_code == 200


def test_key_sees_only_this_pcs_gpus(monkeypatch):
    import core.remote_gpu as remote_gpu

    monkeypatch.setattr(app_module, 'get_gpu_devices_payload', lambda: {'success': True, 'gpus': [{'index': 0, 'name': 'RTX 2060'}]})
    monkeypatch.setattr(
        remote_gpu,
        'with_shared_gpus',
        lambda rows: list(rows) + [{'index': 'remote:lab:0', 'name': 'TITAN', 'remote': True}],
    )
    local = local_client().get('/api/gpu-devices').json()
    assert local['count'] == 2
    keyed = remote_client().get('/api/gpu-devices', headers=bearer(new_key())).json()
    assert [row['index'] for row in keyed['gpus']] == [0]


# ---------------------------------------------------------------- downloads

GIB = 1024 ** 3


@pytest.fixture
def fake_hf(monkeypatch):
    import core.huggingface as hf

    started: list[tuple[str, str]] = []

    def start_download(repo, filename, **_kw):
        started.append((repo, filename))
        return {'success': True, 'job_id': f'job-{len(started)}', 'path': f'C:/models/{filename}'}

    def start_repo_download(repo, **_kw):
        started.append((repo, '*'))
        return {'success': True, 'job_id': 'repo-job', 'path': 'C:/models/repo'}

    listing = {
        'model-Q4_K_M.gguf': 4 * GIB,
        'model-Q8_0.gguf': 8 * GIB,
        'README.md': 2048,
    }
    lookups: list[str] = []

    def repo_file_sizes(repo, **_kw):
        lookups.append(repo)
        return dict(listing) if repo == 'bartowski/Model-GGUF' else {}

    monkeypatch.setattr(hf, 'start_download', start_download)
    monkeypatch.setattr(hf, 'start_repo_download', start_repo_download)
    monkeypatch.setattr(hf, 'repo_file_sizes', repo_file_sizes)
    return {'started': started, 'lookups': lookups}


def _download(client, headers, **body):
    payload = {'repo_id': 'bartowski/Model-GGUF', 'filename': 'model-Q4_K_M.gguf', **body}
    return client.post('/api/hf/download', json=payload, headers=headers)


def test_key_cannot_download_unless_allowed(fake_hf):
    raw = new_key()
    resp = _download(remote_client(), bearer(raw))
    assert resp.status_code == 403
    assert resp.json()['detail']['error'] == 'download_not_allowed'
    assert fake_hf['started'] == []


def test_key_download_only_from_the_hugging_face_catalog(fake_hf):
    raw = new_key(may_download=True)
    client = remote_client()
    for body in (
        {'repo_id': 'https://evil.example/model'},
        {'repo_id': '../../Windows/System32'},
        {'filename': '../../evil.gguf'},
        {'filename': 'C:/Windows/evil.gguf'},
        {'filename': 'not-in-the-repo.gguf'},
        {'repo_id': 'someone/missing-repo'},
    ):
        resp = _download(client, bearer(raw), **body)
        assert resp.status_code == 403, body
        assert resp.json()['detail']['error'] == 'not_catalog', body
    assert fake_hf['started'] == []


def test_key_download_within_and_over_the_disk_limit(fake_hf):
    raw, record = auth.create_key('Lab', may_download=True, download_limit_gb=10)
    client = remote_client()
    first = _download(client, bearer(raw))
    assert first.status_code == 200, first.text
    assert fake_hf['started'] == [('bartowski/Model-GGUF', 'model-Q4_K_M.gguf')]
    assert auth.get_key(record['id'])['downloaded_bytes'] == 4 * GIB

    over = _download(client, bearer(raw), filename='model-Q8_0.gguf')
    assert over.status_code == 403
    assert over.json()['detail']['error'] == 'download_limit'
    assert len(fake_hf['started']) == 1

    whole = _download(client, bearer(raw), whole_repo=True)
    assert whole.status_code == 403
    assert whole.json()['detail']['error'] == 'download_limit'


def test_key_download_that_does_not_start_counts_nothing(fake_hf, monkeypatch):
    import core.huggingface as hf

    monkeypatch.setattr(
        hf,
        'start_download',
        lambda repo, filename, **_kw: {'success': False, 'already_installed': True, 'error': 'already installed'},
    )
    raw, record = auth.create_key('Lab', may_download=True, download_limit_gb=10)
    resp = _download(remote_client(), bearer(raw))
    assert resp.status_code == 409
    assert auth.get_key(record['id'])['downloaded_bytes'] == 0


def test_key_download_group_counts_only_started_files(fake_hf, monkeypatch):
    import core.huggingface as hf

    def start_download(repo, filename, **_kw):
        if filename == 'model-Q8_0.gguf':
            return {'success': False, 'already_installed': True, 'path': f'C:/models/{filename}'}
        return {'success': True, 'job_id': 'job-1', 'path': f'C:/models/{filename}'}

    monkeypatch.setattr(hf, 'start_download', start_download)
    raw, record = auth.create_key('Lab', may_download=True, download_limit_gb=20)
    resp = _download(remote_client(), bearer(raw), files=['model-Q4_K_M.gguf', 'model-Q8_0.gguf'])
    assert resp.status_code == 200, resp.text
    assert auth.get_key(record['id'])['downloaded_bytes'] == 4 * GIB


def test_owner_signed_in_remotely_can_start_a_catalog_download(fake_hf):
    client = signed_in_client()
    resp = _download(client, csrf_headers(client))
    assert resp.status_code == 200, resp.text
    assert fake_hf['started'] == [('bartowski/Model-GGUF', 'model-Q4_K_M.gguf')]
    # No machine-key checks for the owner.
    assert fake_hf['lookups'] == []


# ---------------------------------------------------------------- OpenAI gateway :8001

def test_gateway_same_rules():
    assert local_client(gateway_app).get('/').status_code == 200

    remote = remote_client(base_url='http://192.168.1.10:8001', target=gateway_app)
    resp = remote.get('/v1/models')
    assert resp.status_code == 401
    error = resp.json()['error']
    assert error['type'] == 'authentication_error' and error['code'] == 401

    bad = remote.get('/', headers=bearer('dfk_' + 'B' * 43))
    assert bad.status_code == 401
    assert bad.json()['error']['reason'] == 'invalid_key'

    assert remote.get('/', headers=bearer(new_key())).status_code == 200
    docs = remote.get('/openapi.json', headers=bearer(new_key()))
    assert docs.status_code == 403
    assert docs.json()['error']['type'] == 'permission_error'


def test_gateway_session_post_needs_csrf():
    auth.set_owner_password(PASSWORD, require_current=False)
    token, _session = auth.create_session(ip='192.168.1.50')
    remote = remote_client(base_url='http://192.168.1.10:8001', target=gateway_app)
    remote.cookies.set(ac.SESSION_COOKIE, token)
    assert remote.get('/').status_code == 200
    resp = remote.post('/v1/chat/completions', json={'messages': []}, headers={'origin': 'http://192.168.1.10:8001'})
    assert resp.status_code == 403
    assert resp.json()['error']['reason'] == 'csrf_failed'


# ---------------------------------------------------------------- client side (Nodes)

def test_node_errors_explain_what_to_do():
    from core.remote_nodes import describe_remote_refusal

    body = '{"detail": {"error": "auth_required", "message": "x"}}'
    assert 'machine key' in describe_remote_refusal(401, body, {'label': 'OMAN'})
    assert 'OMAN' in describe_remote_refusal(401, body, {'label': 'OMAN'})
    revoked = '{"detail": {"error": "invalid_key", "message": "x"}}'
    assert 'revoked' in describe_remote_refusal(401, revoked)
    downloads = '{"detail": {"error": "download_not_allowed", "message": "x"}}'
    assert 'May download models here' in describe_remote_refusal(403, downloads)
    assert describe_remote_refusal(500, body) == ''
    assert describe_remote_refusal(401, 'not json') == ''
