"""Who may call which route (sign-in, Phase 1).

Rules, in order:

1. **Local requests pass, exactly as before.** A request is local when it
   comes from a loopback address (127.0.0.0/8 or ::1) *and* carries no proxy
   forwarding header. A reverse proxy on this PC (``tailscale serve``,
   cloudflared, nginx) relays other computers' requests from loopback; its
   ``X-Forwarded-For`` marks them as remote.
2. **Local-only routes** (shutdown, file browsing, config import/export, engine
   install/uninstall, anything that runs a program) answer 403 to every other
   computer, even with a valid session or key.
3. **Machine keys** (``Authorization: Bearer dfk_...``) work only on the
   GPU-sharing routes: status, load, unload, and inference. Downloads need the
   key's "May download models here" setting. A key never opens a browser
   session.
4. **Browser sessions** (the owner, signed in) may use everything else. Every
   POST/PUT/PATCH/DELETE also needs the CSRF token and a matching ``Origin``.
5. Anything else from another computer gets 401.

Every route is listed explicitly in a table below; a test fails when a new
route is not classified. An unlisted route is treated as local-only, so a
forgotten entry fails closed.
"""

from __future__ import annotations

import hmac
import ipaddress
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Match, Mount

from core import auth

SESSION_COOKIE = 'dflash_session'
CSRF_COOKIE = 'dflash_csrf'
CSRF_HEADER = 'x-csrf-token'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
AUTH_STATE_KEY = 'dflash_auth'

# A proxy on this PC adds one of these when it relays another computer.
_FORWARDING_HEADERS = frozenset({
    b'forwarded',
    b'x-forwarded-for',
    b'x-forwarded-host',
    b'x-forwarded-proto',
    b'x-real-ip',
    b'x-client-ip',
    b'cf-connecting-ip',
    b'true-client-ip',
    b'fastly-client-ip',
})

# Route classes.
PUBLIC = 'public'              # sign-in page and sign-in API: no credentials needed
KEY = 'key'                    # GPU sharing: machine keys and owner sessions
KEY_DOWNLOAD = 'key_download'  # keys only with "May download models here"; owner sessions always
SESSION = 'session'            # owner sessions only
LOCAL_ONLY = 'local'           # never from another computer

ROUTE_CLASSES = (PUBLIC, KEY, KEY_DOWNLOAD, SESSION, LOCAL_ONLY)


def _routes(cls: str, rows: Iterable[str]) -> dict[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    for row in rows:
        method, path = row.split(' ', 1)
        table[(method, path)] = cls
    return table


# ------------------------------------------------------------------ Console :8900

CONSOLE_PUBLIC_ROUTES = (
    'GET /login',
    'GET /api/auth/status',
    'POST /api/auth/login',
)

# GPU sharing for other Consoles: status, load, unload, inference.
CONSOLE_KEY_ROUTES = (
    # status
    'GET /api/health',
    'GET /api/gpu-devices',
    'GET /api/system-stats',
    'GET /api/servers',
    'GET /api/servers/{server_id}/status',
    'GET /api/servers/{server_id}/chat-ready',
    'GET /api/servers/{server_id}/inference-stats',
    'GET /api/status/loaded',
    'GET /api/models',
    'GET /api/runtimes',
    'GET /api/runtimes/{runtime_id}',
    'GET /api/hf/download/{job_id}',
    # load
    'POST /api/models/load',
    'POST /api/servers/{server_id}/load',
    'POST /api/runtimes/{runtime_id}/load',
    # unload
    'POST /api/servers/{server_id}/unload',
    'POST /api/runtimes/{runtime_id}/unload',
    # inference
    'POST /api/servers/{server_id}/v1/chat/completions',
    'POST /api/servers/{server_id}/v1/embeddings',
    'POST /api/servers/{server_id}/embed/batch',
    'POST /api/servers/{server_id}/cancel-inference',
    'POST /api/runtimes/{runtime_id}/v1/audio/speech',
    'POST /api/runtimes/{runtime_id}/v1/audio/transcriptions',
)

# Hugging Face catalog download. The route itself also checks, for keys, that
# the file is in a Hugging Face repository and fits the key's disk limit.
CONSOLE_KEY_DOWNLOAD_ROUTES = (
    'POST /api/hf/download',
)

# Dangerous: only on this PC, even with a valid session or key.
CONSOLE_LOCAL_ONLY_ROUTES = (
    # stops the Console
    'POST /api/shutdown',
    # file browsing and file operations outside the model libraries
    'GET /api/fs/browse',
    'POST /api/fs/reveal',  # also starts the file manager
    'GET /api/setup/scan',
    'GET /api/model-libraries/scan',
    'GET /api/model-libraries/preview',
    'GET /api/model-libraries/import-plan',
    'POST /api/model-libraries/import',
    'POST /api/models/import-into-console',
    # config import and export (the libraries list is the file sandbox)
    'GET /api/config/export',
    'POST /api/setup/complete',
    'GET /api/presets/export',
    'POST /api/presets/import',
    'GET /api/diagnostics/bundle',
    'POST /api/diagnostics/report',
    # engine install and uninstall (check-updates may install updates)
    'POST /api/runtimes/{runtime_id}/install',
    'POST /api/runtimes/{runtime_id}/uninstall',
    'POST /api/components/check-updates',
    # registering a program to run
    'POST /api/external/launchers',
    'POST /api/external/launchers/colibri',
)

# The owner, signed in from a browser. PUT /api/config, POST /api/nodes, and
# PATCH /api/nodes/{node_id} also refuse some fields from other computers; see
# LOCAL_ONLY_CONFIG_FIELDS and LOCAL_ONLY_NODE_FIELDS.
CONSOLE_SESSION_ROUTES = (
    'GET /',
    'GET /openapi.json',
    'GET /docs',
    'GET /docs/oauth2-redirect',
    'GET /redoc',
    # sign-out and Settings -> Security
    'POST /api/auth/logout',
    'GET /api/security',
    'PUT /api/security/password',
    'POST /api/security/password/remove',
    'DELETE /api/security/sessions',
    'DELETE /api/security/sessions/{session_id}',
    'POST /api/security/keys',
    'PATCH /api/security/keys/{key_id}',
    'DELETE /api/security/keys/{key_id}',
    # app info and settings
    'GET /api/release-notice',
    'GET /api/config',
    'PUT /api/config',
    'GET /api/gateway',
    'GET /api/endpoints',
    'GET /api/docs/catalog',
    'GET /api/installed',
    'GET /api/hardware',
    'PATCH /api/hardware',
    'GET /api/hardware/fit-budget',
    'GET /api/download-settings',
    'PATCH /api/download-settings',
    'POST /api/download-settings/benchmark',
    'GET /api/runtime-recommendations',
    'GET /api/ui-state',
    'PUT /api/ui-state/pins',
    'POST /api/ui-state/pins/toggle',
    'PUT /api/ui-state/prefs',
    'GET /api/model-notes',
    'PUT /api/model-notes',
    'DELETE /api/model-notes',
    'GET /api/console/logs',
    'GET /api/logs/{server_id}',
    'DELETE /api/logs/{server_id}',
    'GET /api/status/report',
    # cloud API providers
    'GET /api/api-providers',
    'PUT /api/api-providers',
    'DELETE /api/api-providers/{provider_id}',
    'POST /api/api-providers/fetch-models',
    'POST /api/api-providers/{provider_id}/test',
    'POST /api/providers/{provider_id}/v1/chat/completions',
    # nodes (this Console using other Consoles)
    'GET /api/nodes',
    'GET /api/nodes/connect/wizard',
    'POST /api/nodes/connect/test',
    'POST /api/nodes/connect/ssh-command',
    'POST /api/nodes',
    'PATCH /api/nodes/{node_id}',
    'DELETE /api/nodes/{node_id}',
    'POST /api/nodes/{node_id}/health',
    'POST /api/nodes/{node_id}/v1/chat/completions',
    'GET /api/remote-gpus',
    'POST /api/remote-gpus/load',
    # Hugging Face catalog and downloads
    'GET /api/hf/search',
    'GET /api/hf/repo-sizes',
    'GET /api/hf/readme',
    'GET /api/hf/files',
    'GET /api/hf/resolve-local',
    'GET /api/hf/models/{repo_id:path}',
    'GET /api/hf/local-match',
    'GET /api/hf/local-installs',
    'GET /api/hf/downloads',
    'POST /api/hf/downloads/resume',
    'POST /api/hf/downloads/{job_id:path}/resume',
    'DELETE /api/hf/downloads/{job_id:path}',
    'DELETE /api/hf/downloads',
    'POST /api/hf/install',
    # model library (files stay inside the configured libraries)
    'GET /api/models/update/details',
    'POST /api/models/updates/refresh',
    'POST /api/models/update',
    'GET /api/models/update/{job_id}',
    'GET /api/models/vision/plan',
    'POST /api/models/vision/setup',
    'PATCH /api/models/slots',
    'DELETE /api/models/file',
    'POST /api/models/cleanup',
    'GET /api/models/import-progress/{progress_id}',
    'POST /api/models/auto-register',
    'POST /api/models/auto-setup',
    # engines and runtimes the owner configured on this PC
    'GET /api/servers/profiles',
    'PATCH /api/servers/{server_id}',
    'POST /api/servers',
    'GET /api/servers/{server_id}/load-plan',
    'POST /api/servers/{server_id}/listen',
    'POST /api/servers/{server_id}/engine/start',
    'POST /api/servers/{server_id}/start',
    'POST /api/servers/{server_id}/stop',
    'POST /api/servers/{server_id}/engine/stop',
    'POST /api/servers/{server_id}/reload',
    'GET /api/runtimes/manifests',
    'GET /api/runtimes/{runtime_id}/install',
    'GET /api/runtimes/{runtime_id}/voices',
    'POST /api/runtimes/{runtime_id}/start',
    'POST /api/runtimes/{runtime_id}/stop',
    'GET /api/runtimes/{runtime_id}/logs',
    'DELETE /api/runtimes/{runtime_id}/logs',
    'GET /api/components',
    'GET /api/gpu/contention',
    'POST /api/gpu/ownership/check',
    'GET /api/gpu/budget',
    'POST /api/gpu/processes/{pid}/unload',
    # external servers registered on this PC (start and stop only)
    'GET /api/external/launchers',
    'POST /api/external/launchers/{launcher_id}/load',
    'POST /api/external/launchers/{launcher_id}/unload',
    'PATCH /api/external/launchers/{launcher_id}',
    # DFlash stacks (paths are checked against the model libraries)
    'GET /api/stacks/capable-targets',
    'GET /api/stacks/preflight',
    'POST /api/stacks/find-draft',
    'POST /api/stacks/find-and-attach-draft',
    'GET /api/stacks/match',
    'POST /api/stacks/{server_id}/replace-draft',
    'GET /api/stacks/suggest-port',
    'POST /api/stacks/ensure',
)

CONSOLE_MOUNTS = {
    '/static': SESSION,
    '/assets': SESSION,
}

# PUT /api/config fields that only this PC may change: engine binaries
# (dflash_root), engine and runtime definitions, the model-library sandbox,
# SSH node settings, cloud API keys, listening ports, and automatic engine
# installs. "Import config" sends all of them.
LOCAL_ONLY_CONFIG_FIELDS = frozenset({
    'dflash_root',
    'ui_port',
    'gateway_port',
    'servers',
    'runtimes',
    'model_libraries',
    'remote_nodes',
    'api_providers',
    'auto_update_components',
})

# Node fields that make this Console start ``ssh`` to a host.
LOCAL_ONLY_NODE_FIELDS = frozenset({'ssh_host', 'ssh_local_port', 'ssh_remote_port'})


# ------------------------------------------------------------------ gateway :8001

GATEWAY_KEY_ROUTES = (
    'GET /',
    'GET /health',
    'GET /v1/models',
    'POST /v1/chat/completions',
    'POST /v1/embeddings',
    'POST /v1/audio/speech',
    'POST /v1/audio/transcriptions',
)

GATEWAY_SESSION_ROUTES = (
    'GET /openapi.json',
    'GET /docs',
    'GET /docs/oauth2-redirect',
    'GET /redoc',
)


@dataclass(frozen=True)
class AccessPolicy:
    name: str
    routes: Mapping[tuple[str, str], str]
    mounts: Mapping[str, str] = field(default_factory=dict)
    # 'console' -> FastAPI {"detail": {...}}; 'openai' -> {"error": {...}}
    error_style: str = 'console'
    # Browser page navigations without a session get the sign-in page.
    sign_in_page: Callable[[], str] | None = None


def console_route_table() -> dict[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    table.update(_routes(PUBLIC, CONSOLE_PUBLIC_ROUTES))
    table.update(_routes(KEY, CONSOLE_KEY_ROUTES))
    table.update(_routes(KEY_DOWNLOAD, CONSOLE_KEY_DOWNLOAD_ROUTES))
    table.update(_routes(LOCAL_ONLY, CONSOLE_LOCAL_ONLY_ROUTES))
    table.update(_routes(SESSION, CONSOLE_SESSION_ROUTES))
    return table


def gateway_route_table() -> dict[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    table.update(_routes(KEY, GATEWAY_KEY_ROUTES))
    table.update(_routes(SESSION, GATEWAY_SESSION_ROUTES))
    return table


# ------------------------------------------------------------------ requests

@dataclass(frozen=True)
class AuthContext:
    """Who made a request; handlers read it with :func:`auth_context`."""

    kind: str  # 'local' | 'session' | 'key' | 'anonymous'
    client_ip: str = ''
    session_id: str = ''
    key_id: str = ''
    key_name: str = ''
    may_download: bool = False

    @property
    def local(self) -> bool:
        return self.kind == 'local'


LOCAL_CONTEXT = AuthContext(kind='local')


def client_host(scope: Mapping[str, Any]) -> str:
    client = scope.get('client')
    if not client:
        return ''
    return str(client[0] or '')


def is_loopback_address(host: str) -> bool:
    text = str(host or '').strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    mapped = getattr(address, 'ipv4_mapped', None)
    if mapped is not None:
        address = mapped
    return address.is_loopback


def has_forwarding_headers(scope: Mapping[str, Any]) -> bool:
    for name, _value in scope.get('headers') or ():
        if bytes(name).lower() in _FORWARDING_HEADERS:
            return True
    return False


def request_is_local(scope: Mapping[str, Any]) -> bool:
    """True for a request made on this PC (loopback, not relayed by a proxy)."""
    return is_loopback_address(client_host(scope)) and not has_forwarding_headers(scope)


def classify_route(
    scope: Mapping[str, Any],
    policy: AccessPolicy,
    routes: Iterable[Any],
) -> tuple[str, str | None]:
    """Return (route class, matched route path) for a request.

    Uses the router's own matching, so the class always belongs to the route
    that will answer. A path no route matches is SESSION, so a signed-in owner
    gets the normal 404 and everyone else 401. A route missing from the table
    is LOCAL_ONLY.
    """
    method = str(scope.get('method') or 'GET').upper()
    lookup_method = 'GET' if method == 'HEAD' else method
    partial: Any = None
    for route in routes:
        try:
            match, _child = route.matches(scope)
        except Exception:
            continue
        if match == Match.FULL:
            if isinstance(route, Mount):
                return policy.mounts.get(route.path, LOCAL_ONLY), route.path
            path = str(getattr(route, 'path', ''))
            return policy.routes.get((lookup_method, path), LOCAL_ONLY), path
        if match == Match.PARTIAL and partial is None:
            partial = route
    if partial is not None:
        # The path exists but not for this method: the router answers 405.
        return SESSION, str(getattr(partial, 'path', ''))
    return SESSION, None


def bearer_token(headers: Headers) -> str | None:
    """The Bearer credential, '' for an empty one, None when there is none."""
    value = headers.get('authorization')
    if value is None:
        return None
    scheme, _sep, token = value.strip().partition(' ')
    if scheme.lower() != 'bearer':
        return None
    return token.strip()


def request_cookies(headers: Headers) -> dict[str, str]:
    raw = headers.get('cookie')
    if not raw:
        return {}
    try:
        return cookie_parser(raw)
    except Exception:
        return {}


def _origin_tuple(scheme: str, netloc: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(f'{scheme}://{netloc}')
        host = (parts.hostname or '').lower()
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    if port is None:
        port = 443 if scheme == 'https' else 80
    return scheme, host, port


def origin_matches(scope: Mapping[str, Any], headers: Headers) -> bool:
    """True when the browser's Origin header names this Console itself."""
    origin = (headers.get('origin') or '').strip()
    host = (headers.get('host') or '').strip()
    if not origin or origin == 'null' or not host:
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ('http', 'https') or parts.path not in ('', '/'):
        return False
    scheme = str(scope.get('scheme') or 'http').lower()
    seen = _origin_tuple(parts.scheme.lower(), parts.netloc)
    expected = _origin_tuple(scheme, host)
    return seen is not None and seen == expected


@dataclass(frozen=True)
class Decision:
    allowed: bool
    context: AuthContext
    status: int = 200
    error: str = ''
    message: str = ''
    clear_session: bool = False


_MESSAGES = {
    'auth_required': (
        'Sign in first. Other computers must sign in as the owner (browser) '
        'or send a machine key (another DFlash Console).'
    ),
    'invalid_key': (
        'This machine key is not valid on this Console. It may have been revoked. '
        'Create a new key in Settings → Sign-in & keys on the Console you are connecting to.'
    ),
    'session_expired': 'Your sign-in expired or was revoked. Sign in again.',
    'local_only': (
        'This only works on the Console’s own PC. Shutdown, file browsing, config import and export, '
        'engine install and uninstall, and anything that runs a program are never available '
        'from another computer.'
    ),
    'key_not_allowed': (
        'A machine key can only check status, load, unload, and run models on this GPU. '
        'Sign in as the owner for everything else.'
    ),
    'download_not_allowed': (
        'This machine key may not download models here. The owner can turn on '
        '“May download models here” for it in Settings → Sign-in & keys.'
    ),
    'csrf_failed': 'Blocked: the security token is missing or wrong. Reload the page and try again.',
    'origin_mismatch': 'Blocked: this request did not come from this Console’s own page.',
}


def _deny(status: int, error: str, context: AuthContext, *, clear_session: bool = False) -> Decision:
    return Decision(
        allowed=False,
        context=context,
        status=status,
        error=error,
        message=_MESSAGES.get(error, error),
        clear_session=clear_session,
    )


def evaluate_request(
    scope: Mapping[str, Any],
    policy: AccessPolicy,
    routes: Iterable[Any],
) -> tuple[Decision, str]:
    """Decide whether a request may continue. Returns (decision, route class)."""
    if request_is_local(scope):
        return Decision(allowed=True, context=LOCAL_CONTEXT), ''
    ip = client_host(scope)
    anonymous = AuthContext(kind='anonymous', client_ip=ip)
    route_class, _path = classify_route(scope, policy, routes)
    method = str(scope.get('method') or 'GET').upper()
    headers = Headers(scope=scope)

    if route_class == LOCAL_ONLY:
        return _deny(403, 'local_only', anonymous), route_class

    bearer = bearer_token(headers)
    if bearer is not None:
        key = auth.validate_key(bearer, ip=ip)
        if key is None:
            if route_class == PUBLIC:
                return Decision(allowed=True, context=anonymous), route_class
            return _deny(401, 'invalid_key', anonymous), route_class
        context = AuthContext(
            kind='key',
            client_ip=ip,
            key_id=str(key.get('id') or ''),
            key_name=str(key.get('name') or ''),
            may_download=key.get('may_download') is True,
        )
        if route_class in (PUBLIC, KEY):
            return Decision(allowed=True, context=context), route_class
        if route_class == KEY_DOWNLOAD:
            if not context.may_download:
                return _deny(403, 'download_not_allowed', context), route_class
            return Decision(allowed=True, context=context), route_class
        return _deny(403, 'key_not_allowed', context), route_class

    cookie = request_cookies(headers).get(SESSION_COOKIE)
    if cookie:
        session = auth.validate_session(cookie, ip=ip)
        if session is None:
            if route_class == PUBLIC:
                return Decision(allowed=True, context=anonymous, clear_session=True), route_class
            return _deny(401, 'session_expired', anonymous, clear_session=True), route_class
        context = AuthContext(kind='session', client_ip=ip, session_id=str(session.get('id') or ''))
        if method not in SAFE_METHODS and route_class != PUBLIC:
            if not origin_matches(scope, headers):
                return _deny(403, 'origin_mismatch', context), route_class
            sent = headers.get(CSRF_HEADER) or ''
            # Compare bytes: compare_digest refuses non-ASCII str, and a header
            # can carry any latin-1 byte.
            expected = auth.csrf_token_for(cookie).encode('utf-8')
            if not sent or not hmac.compare_digest(sent.encode('utf-8'), expected):
                return _deny(403, 'csrf_failed', context), route_class
        return Decision(allowed=True, context=context), route_class

    if route_class == PUBLIC:
        return Decision(allowed=True, context=anonymous), route_class
    return _deny(401, 'auth_required', anonymous), route_class


def _wants_html(scope: Mapping[str, Any], headers: Headers) -> bool:
    path = str(scope.get('path') or '')
    if str(scope.get('method') or 'GET').upper() not in ('GET', 'HEAD') or path.startswith('/api/'):
        return False
    return 'text/html' in (headers.get('accept') or '').lower()


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path='/', httponly=True, samesite='strict')
    response.delete_cookie(CSRF_COOKIE, path='/', samesite='strict')


def denial_response(decision: Decision, scope: Mapping[str, Any], policy: AccessPolicy) -> Response:
    headers = Headers(scope=scope)
    extra = {'Cache-Control': 'no-store'}
    if decision.status == 401:
        extra['WWW-Authenticate'] = 'Bearer realm="DFlash Console"'
    response: Response
    if decision.status == 401 and policy.sign_in_page is not None and _wants_html(scope, headers):
        response = HTMLResponse(policy.sign_in_page(), status_code=401, headers=extra)
    elif policy.error_style == 'openai':
        response = JSONResponse(
            {
                'error': {
                    'message': decision.message,
                    'type': 'authentication_error' if decision.status == 401 else 'permission_error',
                    'code': decision.status,
                    'reason': decision.error,
                    'retryable': False,
                },
            },
            status_code=decision.status,
            headers=extra,
        )
    else:
        response = JSONResponse(
            {'detail': {'error': decision.error, 'message': decision.message, 'retryable': False}},
            status_code=decision.status,
            headers=extra,
        )
    if decision.clear_session:
        clear_session_cookies(response)
    return response


def _set_context(scope: dict[str, Any], context: AuthContext) -> None:
    state = scope.setdefault('state', {})
    if isinstance(state, dict):
        state[AUTH_STATE_KEY] = context


class AccessControlMiddleware:
    """ASGI middleware enforcing :func:`evaluate_request` (Console and gateway)."""

    def __init__(self, app: Any, *, policy: AccessPolicy, routes: Any) -> None:
        self.app = app
        self.policy = policy
        # Anything iterable at request time: the router's live route list.
        self.routes = routes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope.get('type')
        if kind not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return
        if request_is_local(scope):
            _set_context(scope, LOCAL_CONTEXT)
            await self.app(scope, receive, send)
            return
        if kind == 'websocket':
            # Nothing is shared over WebSockets yet.
            await send({'type': 'websocket.close', 'code': 1008})
            return
        from starlette.concurrency import run_in_threadpool

        # Validating a session or key may write auth.json (last-seen times).
        decision, _route_class = await run_in_threadpool(evaluate_request, scope, self.policy, self.routes)
        if not decision.allowed:
            response = denial_response(decision, scope, self.policy)
            await response(scope, receive, send)
            return
        _set_context(scope, decision.context)
        if not decision.clear_session:
            await self.app(scope, receive, send)
            return

        async def send_clearing_cookie(message: dict[str, Any]) -> None:
            if message.get('type') == 'http.response.start':
                cleared = Response()
                clear_session_cookies(cleared)
                raw = [(k, v) for k, v in cleared.raw_headers if k == b'set-cookie']
                message = {**message, 'headers': list(message.get('headers') or []) + raw}
            await send(message)

        await self.app(scope, receive, send_clearing_cookie)


# ------------------------------------------------------------------ route helpers

def auth_context(request: Any) -> AuthContext:
    """The caller of a route. A direct Python call (no request) counts as local."""
    if request is None:
        return LOCAL_CONTEXT
    scope = getattr(request, 'scope', None)
    if not isinstance(scope, dict):
        return LOCAL_CONTEXT
    state = scope.get('state')
    context = state.get(AUTH_STATE_KEY) if isinstance(state, dict) else None
    if isinstance(context, AuthContext):
        return context
    # The middleware did not run (a sub-application or a test calling the
    # route directly): decide from the address alone.
    if request_is_local(scope):
        return LOCAL_CONTEXT
    return AuthContext(kind='anonymous', client_ip=client_host(scope))


def local_only_detail(what: str) -> dict[str, Any]:
    return {
        'error': 'local_only',
        'message': f'{what} can only be changed on the Console’s own PC, not from another computer.',
        'retryable': False,
    }


def require_local_for_fields(request: Any, data: Mapping[str, Any], fields: Iterable[str], what: str) -> None:
    """Raise 403 when another computer sends any of ``fields``."""
    blocked = sorted(name for name in fields if name in data)
    if not blocked or auth_context(request).local:
        return
    from fastapi import HTTPException

    detail = local_only_detail(what)
    detail['fields'] = blocked
    raise HTTPException(status_code=403, detail=detail)


def redact_config_for_remote(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Config as shown to another computer: no keys for other Consoles, no report token."""
    out = dict(cfg)
    nodes = []
    for row in cfg.get('remote_nodes') or []:
        if not isinstance(row, dict):
            continue
        node = dict(row)
        node['has_token'] = bool(str(node.get('api_token') or '').strip())
        node['api_token'] = ''
        nodes.append(node)
    out['remote_nodes'] = nodes
    out.pop('support_report_token', None)
    return out
