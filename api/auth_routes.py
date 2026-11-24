"""Sign-in page, sign-in API, and Settings -> Security routes (Phase 1).

Who may reach each route is decided by ``core.access_control``; these
handlers check the caller again (defense in depth) and do the work.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers

from core import auth
from core.access_control import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthContext,
    auth_context,
    bearer_token,
    clear_session_cookies,
    origin_matches,
)
from core.config import PACKAGE_ROOT, ROOT

# Routes are added flat to the app (``register_auth_routes``), not through
# ``include_router``: FastAPI nests included routers, and the access-control
# table classifies each route by its own path.
_ROUTES: list[tuple[str, str, Callable[..., Any], dict[str, Any]]] = []


def _route(method: str, path: str, **options: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(endpoint: Callable[..., Any]) -> Callable[..., Any]:
        _ROUTES.append((method, path, endpoint, options))
        return endpoint

    return register


def register_auth_routes(app: FastAPI) -> None:
    for method, path, endpoint, options in _ROUTES:
        app.add_api_route(path, endpoint, methods=[method], **options)


_STATIC_DIR = PACKAGE_ROOT / 'static' if (PACKAGE_ROOT / 'static').is_dir() else ROOT / 'static'
_SIGN_IN_CACHE: dict[str, Any] = {'mtime': None, 'html': ''}

_FALLBACK_SIGN_IN = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Sign in · DFlash Console</title></head>'
    '<body><p>Sign in to DFlash Console: POST your password to /api/auth/login.</p></body></html>'
)


def sign_in_page_html() -> str:
    """The self-contained sign-in page (static/login.html), cached by mtime."""
    path: Path = _STATIC_DIR / 'login.html'
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _FALLBACK_SIGN_IN
    if _SIGN_IN_CACHE['mtime'] != mtime:
        try:
            _SIGN_IN_CACHE['html'] = path.read_text(encoding='utf-8')
            _SIGN_IN_CACHE['mtime'] = mtime
        except OSError:
            return _FALLBACK_SIGN_IN
    return str(_SIGN_IN_CACHE['html'] or _FALLBACK_SIGN_IN)


def _auth_http_error(exc: auth.AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={**exc.detail(), 'retryable': False})


def _require_owner(request: Request | None) -> AuthContext:
    """The owner: on this PC, or signed in from a browser. Never a machine key."""
    context = auth_context(request)
    if context.kind in ('local', 'session'):
        return context
    if context.kind == 'key':
        raise HTTPException(
            status_code=403,
            detail={
                'error': 'key_not_allowed',
                'message': 'A machine key cannot manage sign-in or keys. Sign in as the owner.',
                'retryable': False,
            },
        )
    raise HTTPException(
        status_code=401,
        detail={'error': 'auth_required', 'message': 'Sign in first.', 'retryable': False},
    )


def _set_session_cookies(response: JSONResponse, request: Request, token: str) -> None:
    secure = request.url.scheme == 'https'
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(auth.SESSION_TTL_SECONDS),
        path='/',
        httponly=True,
        samesite='strict',
        secure=secure,
    )
    # Readable by the page so it can send X-CSRF-Token; useless without the
    # HttpOnly session cookie.
    response.set_cookie(
        CSRF_COOKIE,
        auth.csrf_token_for(token),
        max_age=int(auth.SESSION_TTL_SECONDS),
        path='/',
        httponly=False,
        samesite='strict',
        secure=secure,
    )


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=auth.PASSWORD_MAX_LENGTH)


class PasswordSetRequest(BaseModel):
    new_password: str = Field(..., max_length=auth.PASSWORD_MAX_LENGTH)
    current_password: str | None = Field(default=None, max_length=auth.PASSWORD_MAX_LENGTH)


class PasswordRemoveRequest(BaseModel):
    current_password: str | None = Field(default=None, max_length=auth.PASSWORD_MAX_LENGTH)


class KeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=auth.KEY_NAME_MAX)
    may_download: bool = False
    download_limit_gb: float | None = Field(default=None, ge=0, le=auth.DOWNLOAD_LIMIT_MAX_GB)


class KeyPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=auth.KEY_NAME_MAX)
    may_download: bool | None = None
    download_limit_gb: float | None = Field(default=None, ge=0, le=auth.DOWNLOAD_LIMIT_MAX_GB)
    reset_usage: bool = False


# ---------------------------------------------------------------- sign-in

@_route('GET', '/login', include_in_schema=False)
def sign_in_page() -> HTMLResponse:
    return HTMLResponse(sign_in_page_html(), headers={'Cache-Control': 'no-store'})


@_route('GET', '/api/auth/status')
def auth_status(request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    """Whether this caller must sign in. Open to everyone (the sign-in page uses it)."""
    context = auth_context(request)
    return {
        'success': True,
        'local': context.local,
        'via': context.kind,
        'signed_in': context.kind == 'session',
        'password_set': auth.password_is_set(),
        'session_id': context.session_id,
        'locked_for': round(auth.LOGIN_THROTTLE.locked_for(context.client_ip), 1) if not context.local else 0.0,
    }


@_route('POST', '/api/auth/login')
async def auth_login(body: LoginRequest, request: Request) -> JSONResponse:
    headers = Headers(scope=request.scope)
    if bearer_token(headers) is not None:
        raise HTTPException(
            status_code=403,
            detail={
                'error': 'key_cannot_sign_in',
                'message': 'A machine key cannot open a browser session. Sign in with the owner password.',
                'retryable': False,
            },
        )
    # A browser always sends Origin on POST; it must be this Console's own page.
    if headers.get('origin') is not None and not origin_matches(request.scope, headers):
        raise HTTPException(
            status_code=403,
            detail={
                'error': 'origin_mismatch',
                'message': 'Blocked: sign in from this Console’s own sign-in page.',
                'retryable': False,
            },
        )
    context = auth_context(request)
    ip = context.client_ip or (request.client.host if request.client else '')
    attempt = await run_in_threadpool(auth.attempt_sign_in, ip, body.password)
    result = attempt['result']
    if attempt.get('delay'):
        # Slow down wrong guesses without holding a worker thread.
        await asyncio.sleep(float(attempt['delay']))
    if result == 'no_password':
        raise HTTPException(
            status_code=409,
            detail={
                'error': 'no_password',
                'message': 'Signing in from another computer is off: this Console has no owner password. '
                           'Set one in Settings → Sign-in & keys on the Console’s own PC.',
                'retryable': False,
            },
        )
    if result == 'locked':
        retry_after = max(1, int(round(float(attempt.get('retry_after') or 0))))
        raise HTTPException(
            status_code=429,
            detail={
                'error': 'sign_in_locked',
                'message': f'Too many wrong passwords. Try again in {max(1, round(retry_after / 60))} minute(s).',
                'retry_after': retry_after,
                'retryable': True,
            },
            headers={'Retry-After': str(retry_after)},
        )
    if result != 'ok':
        raise HTTPException(
            status_code=401,
            detail={'error': 'wrong_password', 'message': 'Wrong password.', 'retryable': True},
        )
    try:
        token, session = auth.create_session(ip=ip, user_agent=request.headers.get('user-agent') or '')
    except auth.AuthError as exc:
        raise _auth_http_error(exc) from exc
    response = JSONResponse(
        {'success': True, 'signed_in': True, 'session': session},
        headers={'Cache-Control': 'no-store'},
    )
    _set_session_cookies(response, request, token)
    return response


@_route('POST', '/api/auth/logout')
def auth_logout(request: Request = None) -> JSONResponse:  # type: ignore[assignment]
    context = auth_context(request)
    if context.session_id:
        auth.revoke_session(context.session_id)
    response = JSONResponse({'success': True, 'signed_in': False}, headers={'Cache-Control': 'no-store'})
    clear_session_cookies(response)
    return response


# ---------------------------------------------------------------- Settings -> Security

@_route('GET', '/api/security')
def security_overview(request: Request = None) -> JSONResponse:  # type: ignore[assignment]
    context = _require_owner(request)
    return JSONResponse(
        {
            'success': True,
            'local': context.local,
            'via': context.kind,
            'client_ip': context.client_ip,
            'current_session_id': context.session_id,
            'password': auth.password_info(),
            'password_min_length': auth.PASSWORD_MIN_LENGTH,
            'sessions': auth.list_sessions(current_id=context.session_id),
            'keys': auth.list_keys(),
            'download_limit_default_gb': auth.DOWNLOAD_LIMIT_DEFAULT_GB,
        },
        headers={'Cache-Control': 'no-store'},
    )


@_route('PUT', '/api/security/password')
async def security_set_password(body: PasswordSetRequest, request: Request) -> dict[str, Any]:
    context = _require_owner(request)
    try:
        info = await run_in_threadpool(
            lambda: auth.set_owner_password(
                body.new_password,
                current_password=body.current_password,
                require_current=not context.local,
                keep_session_id=context.session_id,
            )
        )
    except auth.AuthError as exc:
        raise _auth_http_error(exc) from exc
    return {'success': True, 'password': info, 'sessions': auth.list_sessions(current_id=context.session_id)}


@_route('POST', '/api/security/password/remove')
async def security_remove_password(body: PasswordRemoveRequest, request: Request) -> JSONResponse:
    context = _require_owner(request)
    try:
        info = await run_in_threadpool(
            lambda: auth.remove_owner_password(
                current_password=body.current_password,
                require_current=not context.local,
            )
        )
    except auth.AuthError as exc:
        raise _auth_http_error(exc) from exc
    response = JSONResponse({'success': True, 'password': info, 'sessions': []})
    if context.session_id:
        clear_session_cookies(response)
    return response


@_route('DELETE', '/api/security/sessions')
def security_revoke_other_sessions(request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    """Sign out every other browser (keeps the one making the request)."""
    context = _require_owner(request)
    removed = auth.revoke_all_sessions(except_id=context.session_id)
    return {'success': True, 'revoked': removed, 'sessions': auth.list_sessions(current_id=context.session_id)}


@_route('DELETE', '/api/security/sessions/{session_id}')
def security_revoke_session(session_id: str, request: Request = None) -> JSONResponse:  # type: ignore[assignment]
    context = _require_owner(request)
    if not auth.revoke_session(session_id):
        raise HTTPException(
            status_code=404,
            detail={'error': 'session_not_found', 'message': 'That browser is already signed out.', 'retryable': False},
        )
    response = JSONResponse({'success': True, 'sessions': auth.list_sessions(current_id=context.session_id)})
    if session_id == context.session_id:
        clear_session_cookies(response)
    return response


@_route('POST', '/api/security/keys')
def security_create_key(body: KeyCreateRequest, request: Request = None) -> JSONResponse:  # type: ignore[assignment]
    _require_owner(request)
    try:
        raw, record = auth.create_key(
            body.name,
            may_download=body.may_download,
            download_limit_gb=body.download_limit_gb,
        )
    except auth.AuthError as exc:
        raise _auth_http_error(exc) from exc
    # The full key is shown once, here; only its hash is stored.
    return JSONResponse(
        {'success': True, 'key': raw, 'record': record},
        headers={'Cache-Control': 'no-store'},
    )


@_route('PATCH', '/api/security/keys/{key_id}')
def security_update_key(key_id: str, body: KeyPatchRequest, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    _require_owner(request)
    try:
        record = auth.update_key(
            key_id,
            name=body.name,
            may_download=body.may_download,
            download_limit_gb=body.download_limit_gb,
            reset_usage=body.reset_usage,
        )
    except auth.AuthError as exc:
        raise _auth_http_error(exc) from exc
    return {'success': True, 'record': record}


@_route('DELETE', '/api/security/keys/{key_id}')
def security_revoke_key(key_id: str, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    _require_owner(request)
    if not auth.revoke_key(key_id):
        raise HTTPException(
            status_code=404,
            detail={'error': 'key_not_found', 'message': 'That machine key does not exist.', 'retryable': False},
        )
    return {'success': True, 'keys': auth.list_keys()}
