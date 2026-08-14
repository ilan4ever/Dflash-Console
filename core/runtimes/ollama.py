"""Ollama LLM runtime adapter.

Loads models installed in the local Ollama library into GPU/CPU memory through
Ollama's native HTTP API (default ``127.0.0.1:11434``). The Console does not
spawn Ollama — the user must have the Ollama app or service running.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_LLM, RUNTIME_OLLAMA

_DEFAULT_HOST = '127.0.0.1'
_DEFAULT_PORT = 11434
_LOAD_TIMEOUT_SECONDS = 600.0

_STATE_LOCK = threading.Lock()
_ACTIVE_MODEL = ''


def _ollama_host() -> str:
    raw = str(os.environ.get('OLLAMA_HOST') or '').strip()
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw.rstrip('/')
    if raw:
        return f'http://{raw}'
    return f'http://{_DEFAULT_HOST}:{_DEFAULT_PORT}'


def _api_request(
    path: str,
    *,
    method: str = 'GET',
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | list[Any] | str]:
    url = f'{_ollama_host()}{path}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={'Content-Type': 'application/json'} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8', errors='replace')
            if not raw:
                return response.status, {}
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        try:
            parsed = json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            parsed = detail
        return exc.code, parsed if isinstance(parsed, (dict, list)) else {'error': str(parsed)}


def _daemon_reachable() -> bool:
    status, _payload = _api_request('/api/tags', timeout=2.5)
    return status == 200


def _installed_model_names() -> set[str]:
    status, payload = _api_request('/api/tags', timeout=5.0)
    if status != 200 or not isinstance(payload, dict):
        return set()
    names: set[str] = set()
    for entry in payload.get('models') or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or entry.get('model') or '').strip()
        if name:
            names.add(name)
    return names


def _model_installed(model_name: str) -> bool:
    target = str(model_name or '').strip()
    if not target:
        return False
    installed = _installed_model_names()
    if target in installed:
        return True
    base, _, tag = target.partition(':')
    if tag:
        return f'{base}:latest' in installed or base in installed
    return f'{base}:latest' in installed or base in installed


def _running_model_names() -> set[str]:
    status, payload = _api_request('/api/ps', timeout=2.5)
    if status != 200 or not isinstance(payload, dict):
        return set()
    names: set[str] = set()
    for entry in payload.get('models') or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or entry.get('model') or '').strip()
        if name:
            names.add(name)
    return names


def _preload_model(model_name: str, *, keep_alive: int | str = -1) -> dict[str, Any]:
    status, payload = _api_request(
        '/api/generate',
        method='POST',
        body={
            'model': model_name,
            'prompt': ' ',
            'stream': False,
            'keep_alive': keep_alive,
            'options': {'num_predict': 1},
        },
        timeout=_LOAD_TIMEOUT_SECONDS,
    )
    if status == 200:
        return {'success': True, 'response': payload}
    detail = ''
    if isinstance(payload, dict):
        detail = str(payload.get('error') or payload)
    elif isinstance(payload, str):
        detail = payload
    return {'success': False, 'error': detail or f'Ollama load failed (HTTP {status})'}


def _unload_model(model_name: str) -> dict[str, Any]:
    from core.gpu_processes import _unload_ollama_model

    return _unload_ollama_model(api_url=_ollama_host(), model_id=model_name)


class OllamaRuntimeAdapter:
    runtime_id = RUNTIME_OLLAMA
    modalities = (MODALITY_LLM,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = ('ollama',)

    @staticmethod
    def is_installed() -> bool:
        return _daemon_reachable()

    def health(self) -> dict[str, Any]:
        with _STATE_LOCK:
            active = _ACTIVE_MODEL
        reachable = _daemon_reachable()
        return {
            'ok': reachable,
            'runtime_id': self.runtime_id,
            'installed': reachable,
            'execution_mode': self.execution_mode,
            'running': reachable,
            'active_model': active,
            'api_url': _ollama_host(),
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not _daemon_reachable():
            return {
                'success': False,
                'error': 'Ollama is not running — start the Ollama app (port 11434)',
            }
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        return {'success': True, 'stopped': False, 'message': 'Ollama is managed outside the Console'}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        global _ACTIVE_MODEL
        model_name = str(
            (model or {}).get('ollama_model')
            or (model or {}).get('label')
            or (model or {}).get('id')
            or ''
        ).strip()
        if model_name.startswith('ollama:'):
            model_name = model_name.split(':', 1)[-1]
        if not model_name:
            return {'success': False, 'error': 'Ollama model name is required'}

        if not _daemon_reachable():
            return {
                'success': False,
                'error': 'Ollama is not running — start the Ollama app first (port 11434)',
            }
        if not _model_installed(model_name):
            return {
                'success': False,
                'error': f'{model_name} is not installed in Ollama',
            }

        result = _preload_model(model_name)
        if not result.get('success'):
            return result

        with _STATE_LOCK:
            _ACTIVE_MODEL = model_name

        return {
            'success': True,
            'loaded': True,
            'model': model_name,
            'api_url': f'{_ollama_host()}/v1',
            'device': 'ollama',
        }

    def unload(self, model: dict[str, Any] | None = None) -> dict[str, Any]:
        global _ACTIVE_MODEL
        requested = ''
        if isinstance(model, dict):
            requested = str(model.get('ollama_model') or model.get('model') or model.get('label') or '').strip()
        with _STATE_LOCK:
            active = requested or _ACTIVE_MODEL
        if not active:
            return {'success': True, 'unloaded': False, 'message': 'no Ollama model is loaded'}
        if not _daemon_reachable():
            with _STATE_LOCK:
                _ACTIVE_MODEL = ''
            return {'success': True, 'unloaded': False, 'message': 'Ollama is not running'}
        result = _unload_model(active)
        if result.get('success'):
            with _STATE_LOCK:
                if _ACTIVE_MODEL == active:
                    _ACTIVE_MODEL = ''
        return result

    def openai_routes(self) -> list[str]:
        return ['/v1/chat/completions', '/v1/models']


ollama_adapter = OllamaRuntimeAdapter()
