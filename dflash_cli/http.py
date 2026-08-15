"""HTTP client for the local DFlash Console server."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlencode


class ConsoleError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class ConsoleClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            clean = {key: value for key, value in query.items() if value is not None}
            if clean:
                url += ('&' if '?' in url else '?') + urlencode(clean, doseq=True)
        data = None
        headers = {'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return {'success': True}
                return json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = _read_error(exc)
            raise ConsoleError(_format_http_error(exc.code, detail), status=exc.code, payload=detail) from exc
        except urllib.error.URLError as exc:
            raise ConsoleError(
                f'Cannot reach DFlash Console at {self.base_url}\n'
                'Start it with:  dflash serve\n'
                'Or:             .\\server.ps1',
            ) from exc
        except TimeoutError as exc:
            raise ConsoleError(f'Request timed out: {method} {path}') from exc

    def get(self, path: str, **query: Any) -> Any:
        return self.request('GET', path, query=query or None)

    def post(self, path: str, body: Any | None = None, **query: Any) -> Any:
        return self.request('POST', path, query=query or None, body=body if body is not None else {})

    def delete(self, path: str, **query: Any) -> Any:
        return self.request('DELETE', path, query=query or None)


def resolve_base_url(url: str | None = None, port: int | None = None) -> str:
    if url:
        return str(url).rstrip('/')
    env = os.environ.get('DFLASH_URL') or os.environ.get('DFLASH_CONSOLE_URL')
    if env:
        return env.rstrip('/')
    env_port = os.environ.get('DFLASH_PORT') or os.environ.get('DFLASH_UI_PORT')
    chosen = port if port is not None else (int(env_port) if env_port else None)
    if chosen is None:
        try:
            from core.config import load_config

            chosen = int((load_config() or {}).get('ui_port') or 8900)
        except Exception:
            chosen = 8900
    return f'http://127.0.0.1:{int(chosen)}'


def _read_error(exc: urllib.error.HTTPError) -> Any:
    try:
        raw = exc.read().decode('utf-8')
        return json.loads(raw) if raw else {'error': exc.reason}
    except Exception:
        return {'error': str(exc.reason or exc)}


def _format_http_error(status: int, detail: Any) -> str:
    if isinstance(detail, dict):
        message = detail.get('detail') or detail.get('error') or detail.get('message')
        if isinstance(message, dict):
            message = message.get('error') or message.get('message') or json.dumps(message)
        if message:
            return f'{status}: {message}'
    return f'{status}: {detail}'
