"""Port probes, model discovery, and server stop helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.client_identity import display_loaded_by_label, resolve_active_clients, resolve_engine_client_label
from core.net_listeners import pid_listening_on_port

_SERVER_STATUS_CACHE: dict[str, dict[str, Any]] = {}
_STATUS_PAYLOAD_CACHE: dict[str, Any] = {
    'payload': None,
    'updated_at': 0.0,
    'include_external': None,
}
_STATUS_PAYLOAD_REVISION = 0
_STATUS_EXTERNAL_CACHE: list[dict[str, Any]] = []
_STATUS_PAYLOAD_LOCK = threading.Lock()
_ROUTER_UNLOAD_CACHE: dict[str, tuple[bool, float]] = {}
_ROUTER_UNLOAD_CACHE_TTL = 300.0

from core.gpu_devices import format_gpu_assignment, query_gpu_devices, resolve_role_gpu_launch_params
from core.load_progress import (
    clear_vram_progress_baseline,
    estimate_vram_load_progress,
    is_active_boot,
    is_active_model_load,
    merge_load_progress,
    model_load_failure_message,
    parse_load_progress,
    read_log_tail,
)
from core.model_presets import gpu_layers_max_for, preset_path_for, profile_requires_draft
from core.model_stack import resolve_model_stack
from core.server_boot import (
    adopt_running_engine,
    clear_server_tracking,
    dflash_live_launch_state,
    get_started_launch,
    managed_process_identity,
    terminate_started_process,
    wait_for_port_closed,
)


def _kill_listener_on_port(port: int, host: str = '127.0.0.1') -> bool:
    import socket as _socket

    try:
        with _socket.create_connection((host, int(port)), timeout=0.4):
            pass
    except OSError:
        return False

    if terminate_started_process(port):
        return wait_for_port_closed(host, port)
    if not tcp_port_open(host, port):
        return True

    pid = None
    try:
        pid = pid_listening_on_port(int(port), host)
    except Exception:
        pid = None

    if pid is None:
        return False
    if not managed_process_identity(pid):
        return False

    try:
        if sys.platform == 'win32':
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            os.kill(pid, 9)
        return wait_for_port_closed(host, port)
    except Exception:
        return False


def tcp_port_open(host: str, port: int, *, timeout: float = 0.25) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _append_status_trace(
    trace: list[dict[str, Any]] | None,
    *,
    step: str,
    started_at: float,
    detail: str,
) -> None:
    if trace is None:
        return
    trace.append({
        'step': step,
        'ms': max(0, int((time.time() - started_at) * 1000)),
        'detail': detail,
    })


def _write_engine_status_log(trace: list[dict[str, Any]], build_ms: int) -> None:
    try:
        from core.config import ROOT

        log_path = ROOT / 'logs' / 'engine-status.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        lines = [f'[{stamp}] engine status build {build_ms} ms']
        for row in trace:
            lines.append(
                f"  - {row.get('step')}: {int(row.get('ms') or 0)} ms — {row.get('detail')}"
            )
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
    except OSError:
        pass


def _fetch_models_payload(api_url: str) -> list[dict[str, Any]]:
    url = f"{str(api_url or '').strip().rstrip('/')}/models"
    try:
        with urllib.request.urlopen(url, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError):
        return []
    models = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    return [entry for entry in models if isinstance(entry, dict)]


def _model_state(entry: dict[str, Any]) -> str:
    status = entry.get('status')
    if isinstance(status, dict):
        return str(status.get('value') or '').lower()
    return ''


def router_unload_available(api_url: str) -> bool:
    url = f'{api_base_url(api_url)}/models/unload'
    request = urllib.request.Request(
        url,
        data=b'{}',
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.5):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code != 404
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError):
        return False


def _loaded_model_ids(entries: list[dict[str, Any]], *, router: bool) -> list[str]:
    ids: list[str] = []
    for entry in entries:
        model_id = str(entry.get('id') or entry.get('model') or '').strip()
        if not model_id or model_id == 'default':
            continue
        if router:
            state = _model_state(entry)
            if state:
                if state not in ('loaded', 'running'):
                    continue
            elif not (isinstance(entry.get('meta'), dict) and entry['meta'].get('n_ctx')):
                continue
        ids.append(model_id)
    return ids


def _loading_model_ids(entries: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for entry in entries:
        if _model_state(entry) != 'loading':
            continue
        model_id = str(entry.get('id') or entry.get('model') or '').strip()
        if model_id and model_id != 'default':
            ids.append(model_id)
    return ids


def _loading_progress_from_models(entries: list[dict[str, Any]]) -> float | None:
    best: float | None = None
    for entry in entries:
        if _model_state(entry) != 'loading':
            continue
        status = entry.get('status')
        if not isinstance(status, dict):
            continue
        value = status.get('progress')
        if not isinstance(value, (int, float)):
            continue
        pct = float(value) * 100.0 if float(value) <= 1.0 else float(value)
        pct = min(100.0, max(0.0, pct))
        if best is None or pct > best:
            best = pct
    return best


def probe_models(api_url: str) -> list[str]:
    entries = _fetch_models_payload(api_url)
    router = router_unload_available(api_url)
    return _loaded_model_ids(entries, router=router)


def probe_loading_models(api_url: str) -> list[str]:
    return _loading_model_ids(_fetch_models_payload(api_url))


def _router_unload_cached(api_url: str) -> bool:
    key = api_base_url(api_url)
    if not key:
        return False
    now = time.time()
    cached = _ROUTER_UNLOAD_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]
    value = router_unload_available(api_url)
    _ROUTER_UNLOAD_CACHE[key] = (value, now + _ROUTER_UNLOAD_CACHE_TTL)
    return value


def probe_runtime_state(api_url: str) -> tuple[list[str], list[str], bool, float | None]:
    """Single /models fetch for status polling — avoids duplicate HTTP calls."""
    entries = _fetch_models_payload(api_url)
    router = _router_unload_cached(api_url)
    return (
        _loaded_model_ids(entries, router=router),
        _loading_model_ids(entries),
        router,
        _loading_progress_from_models(entries),
    )


def api_base_url(api_url: str) -> str:
    parsed = urlparse(str(api_url or '').strip().rstrip('/'))
    path = parsed.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    base = f'{parsed.scheme}://{parsed.netloc}{path}'.rstrip('/')
    return base or f'{parsed.scheme}://{parsed.netloc}'


def unload_model(*, api_url: str, model_id: str) -> dict[str, Any]:
    model = str(model_id or '').strip()
    if not model:
        return {'success': False, 'error': 'model_id required'}
    url = f'{api_base_url(api_url)}/models/unload'
    body = json.dumps({'model': model}).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        # Unload is intentionally idempotent. A prior stop, crash, or router
        # cleanup may already have removed the checkpoint, so a second unload
        # must not turn an already-clean state into a false failure.
        if 'model is not running' in detail.lower():
            return {
                'success': True,
                'unloaded': False,
                'already_unloaded': True,
                'model': model,
                'response': detail,
            }
        return {'success': False, 'error': detail or str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError) as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'unloaded': True, 'model': model, 'response': payload}


def load_model(*, api_url: str, model_id: str) -> dict[str, Any]:
    model = str(model_id or '').strip()
    if not model:
        return {'success': False, 'error': 'model_id required'}
    url = f'{api_base_url(api_url)}/models/load'
    body = json.dumps({'model': model}).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        return {'success': False, 'error': detail or str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError) as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'loaded': True, 'model': model, 'response': payload}


def stop_server(*, port: int | None = None, api_url: str | None = None, host: str = '127.0.0.1') -> dict[str, Any]:
    resolved_port = int(port or 0)
    if not resolved_port and api_url:
        parsed = urlparse(str(api_url))
        resolved_port = int(parsed.port or 0)
        host = str(parsed.hostname or host)
    if resolved_port <= 0:
        return {'success': False, 'error': 'port or api_url required'}

    killed = _kill_listener_on_port(resolved_port, host)
    clear_server_tracking(resolved_port)
    stopped = killed or not tcp_port_open(host, resolved_port)
    return {
        'success': stopped,
        'port': resolved_port,
        'host': host,
        'stopped': stopped,
        'error': None if stopped else f'Listener on {host}:{resolved_port} is still running',
    }


_GENERIC_LOADED_IDS = frozenset({'default', 'model', 'browse', ''})


def _normalize_loaded_model_ids(
    loaded_models: list[str],
    *,
    configured_model_id: str = '',
    model_path: 'Path | None' = None,
    allow_fallback: bool = True,
) -> list[str]:
    fallback = ''
    if model_path is not None:
        fallback = model_path.name
    elif configured_model_id and configured_model_id.lower() not in _GENERIC_LOADED_IDS:
        fallback = configured_model_id
    normalized: list[str] = []
    for raw in loaded_models:
        token = str(raw or '').strip()
        if not token or token.lower() in _GENERIC_LOADED_IDS:
            continue
        if token not in normalized:
            normalized.append(token)
    if not normalized and fallback and allow_fallback:
        normalized.append(fallback)
    return normalized


def _apply_engine_runtime_flags(
    server: dict[str, Any],
    *,
    port_open: bool,
    booting: bool,
    status: str,
) -> tuple[bool, str]:
    """Honor saved engine_on — user stop must not flip back to running on poll."""
    engine_on = server.get('engine_on') is True
    if engine_on:
        return port_open, status
    if booting:
        return port_open, status
    return False, 'stopped'


def _annotate_model_stack(
    stack: list[dict[str, Any]],
    *,
    booting: bool,
    loaded_models: list[str],
    progress: float | None,
) -> list[dict[str, Any]]:
    alias_ready = bool(loaded_models)
    result: list[dict[str, Any]] = []
    for entry in stack:
        row = dict(entry)
        role = str(entry.get('role') or '')
        if alias_ready:
            row['card_state'] = 'ready'
            row['progress'] = None
            row['ejectable'] = role == 'alias'
        elif booting:
            row['card_state'] = 'loading'
            row['progress'] = progress if role in ('alias', 'target') else None
            row['ejectable'] = role == 'alias'
        else:
            row['card_state'] = 'idle'
            row['progress'] = None
            row['ejectable'] = False
        result.append(row)
    return result


def _stack_detail(entry: dict[str, Any]) -> dict[str, Any]:
    path = str(entry.get('path') or '')
    filename = path.replace('\\', '/').split('/')[-1] if path else str(entry.get('id') or entry.get('label') or '')
    return {
        'role': entry.get('role'),
        'label': entry.get('label') or entry.get('role'),
        'name': filename or str(entry.get('id') or ''),
        'path': path,
        'size_gb': entry.get('size_gb'),
        'source': entry.get('source'),
    }


def _stack_size_gb(parts: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    for row in parts:
        val = row.get('size_gb')
        if val is None:
            continue
        try:
            total += float(val)
            found = True
        except (TypeError, ValueError):
            continue
    return round(total, 2) if found else None


def _stack_has_dflash_draft(parts: list[dict[str, Any]]) -> bool:
    return any(str(row.get('role') or '').startswith('draft') for row in parts)


def _preset_has_dflash_draft(server_id: str) -> bool | None:
    if not str(server_id or '').strip():
        return None
    path = preset_path_for(server_id)
    if not path.is_file():
        return None
    try:
        return any(
            line.strip().lower().startswith('model-draft')
            for line in path.read_text(encoding='utf-8').splitlines()
        )
    except OSError:
        return None


def _acceleration_metadata(
    server: dict[str, Any],
    stack: list[dict[str, Any]],
    *,
    card: dict[str, Any] | None = None,
    live_draft: bool | None = None,
    live: bool = False,
) -> dict[str, Any]:
    """Describe configured speculative decoding without inferring it from names."""
    if card and (card.get('is_adhoc') or card.get('plain_llm')):
        return {
            'acceleration_mode': 'autoregressive',
            'acceleration_expected': False,
            'acceleration_label': '',
            'draft_loaded': False,
            'draft_status': 'not_applicable',
        }
    parts = [row for row in stack if str(row.get('role') or '') != 'alias']
    draft_configured = any(
        str(row.get('role') or '').startswith('draft')
        and bool(str(row.get('path') or '').strip())
        and not row.get('path_missing')
        for row in parts
    )
    preset_has_draft = _preset_has_dflash_draft(str(server.get('id') or ''))
    if preset_has_draft is False:
        draft_configured = False
    draft_loaded = (
        bool(live_draft)
        if live_draft is not None
        else draft_configured
    )
    profile = str(server.get('profile') or '').strip().lower()
    acceleration_expected = profile_requires_draft(profile)
    if draft_loaded:
        label = 'DFlash active'
        mode = 'dflash'
        draft_status = 'active'
    elif acceleration_expected:
        needs_repair = not draft_configured or (live and live_draft is not True)
        label = 'Draft required · repair' if needs_repair else 'DFlash stack ready'
        mode = 'autoregressive'
        draft_status = 'repair_required' if needs_repair else 'ready'
    else:
        label = ''
        mode = 'autoregressive'
        draft_status = 'not_applicable'
    return {
        'acceleration_mode': mode,
        'acceleration_expected': acceleration_expected,
        'acceleration_label': label,
        'draft_loaded': draft_loaded,
        'draft_status': draft_status,
    }


def _fallback_loaded_card(
    model_id: str,
    *,
    booting: bool,
    progress: float | None,
) -> dict[str, Any]:
    """Keep a loaded model visible even when no configured stack resolves."""
    token = str(model_id or '').strip()
    normalized = token.replace('\\', '/')
    title = normalized.rsplit('/', 1)[-1] if '/' in normalized else token.replace('-', ' ')
    return {
        'role': 'loaded-model',
        'label': title or 'Loaded model',
        'id': token or 'loaded-model',
        'title': title or 'Loaded model',
        'subtitle': f'API: {token}' if token else '',
        'path': token if normalized.lower().endswith('.gguf') else '',
        'stack_details': [],
        'size_gb': None,
        'card_state': 'loading' if booting else 'ready',
        'progress': progress if booting else None,
        'ejectable': True,
        'dflash_stack': False,
        'plain_llm': True,
        'is_adhoc': True,
    }


def _build_visible_cards(
    model_stack: list[dict[str, Any]],
    *,
    server_label: str,
    display_name: str = '',
    booting: bool,
    loaded_models: list[str],
    progress: float | None,
) -> list[dict[str, Any]]:
    card_title = str(display_name or server_label or '').strip()
    alias_rows = [row for row in model_stack if row.get('role') == 'alias']
    alias = alias_rows[0] if alias_rows else None
    alias_ready = bool(loaded_models)
    loaded_id = str(loaded_models[0] or '') if loaded_models else ''
    alias_id = str(alias.get('id') or '') if alias else ''
    if loaded_id.lower() in _GENERIC_LOADED_IDS:
        loaded_id = alias_id or loaded_id
    adhoc = bool(
        alias_ready
        and alias
        and loaded_id
        and alias_id
        and alias_id.lower() not in _GENERIC_LOADED_IDS
        and loaded_id != alias_id
        and loaded_id.lower() not in _GENERIC_LOADED_IDS
    )

    if alias_ready and alias:
        if adhoc:
            composite = {
                **alias,
                'id': loaded_id,
                'title': loaded_id.replace('-', ' '),
                'subtitle': f"API: {loaded_id}",
                'stack_details': [{
                    'role': 'target',
                    'label': 'target',
                    'name': loaded_id,
                    'source': 'adhoc',
                }],
                'size_gb': None,
                'card_state': 'ready',
                'progress': None,
                'ejectable': True,
                'dflash_stack': False,
                'plain_llm': True,
                'is_adhoc': True,
            }
            extras = [
                _fallback_loaded_card(model_id, booting=False, progress=None)
                for model_id in loaded_models[1:]
                if str(model_id or '').strip() and str(model_id) != loaded_id
            ]
            return [composite, *extras]
        parts = [row for row in model_stack if row.get('role') != 'alias']
        composite = {
            **alias,
            'title': card_title or str(alias.get('id') or 'Loaded model'),
            'subtitle': f"API: {alias.get('id')}",
            'stack_details': [_stack_detail(row) for row in parts],
            'size_gb': _stack_size_gb(parts),
            'card_state': 'ready',
            'progress': None,
            'ejectable': True,
            'dflash_stack': _stack_has_dflash_draft(parts),
        }
        extras = [
            _fallback_loaded_card(model_id, booting=False, progress=None)
            for model_id in loaded_models[1:]
            if str(model_id or '').strip() and str(model_id) != loaded_id
        ]
        return [composite, *extras]

    if booting and alias:
        parts = [row for row in model_stack if row.get('role') != 'alias']
        composite = {
            **alias,
            'title': card_title or str(alias.get('id') or 'Loading model'),
            'subtitle': 'Loading…',
            'stack_details': [_stack_detail(row) for row in parts],
            'size_gb': _stack_size_gb(parts),
            'card_state': 'loading',
            'progress': progress,
            'ejectable': True,
            'dflash_stack': _stack_has_dflash_draft(parts),
        }
        return [composite]

    if loaded_models:
        return [
            _fallback_loaded_card(model_id, booting=booting, progress=progress)
            for model_id in loaded_models
            if str(model_id or '').strip()
        ]

    return [row for row in model_stack if row.get('card_state') in ('ready', 'loading')]


def _build_embedding_server_status(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    gpus: list[dict[str, Any]] | None = None,
    vram_map: dict[int, float] | None = None,
) -> dict[str, Any]:
    from core.config import normalize_server
    from core.display_names import build_engine_client_metadata
    from core.embedding_server import embedding_metadata, probe_embedding_health, probe_embedding_models, resolve_embedding_model_path
    from core.gpu_processes import _model_kind_fields, vram_gb_for_port

    entry = normalize_server(server)
    host = str(entry.get('host') or '127.0.0.1')
    port = int(entry.get('port') or 0)
    api_url = str(entry.get('api_url') or '')
    server_id = str(entry.get('id') or '')
    if gpus is None:
        gpus = query_gpu_devices()
    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=entry.get('model_id'),
        gpus=gpus,
        hardware=(cfg or {}).get('hardware_settings'),
        context_size=entry.get('context_size'),
    )
    gpu_display = format_gpu_assignment(str(entry.get('gpu_device') or 'auto'), launch, gpus)
    configured_model_id = str(entry.get('model_id') or '').strip()
    port_open = tcp_port_open(host, port) if port > 0 else False
    engine_on = entry.get('engine_on') is True
    running = port_open
    healthy = False
    loaded_models: list[str] = []
    if running:
        loaded_models = probe_embedding_models(api_url)
        healthy = probe_embedding_health(host, port) or bool(loaded_models)
        if healthy and not get_started_launch(port) and engine_on:
            adopt_running_engine(entry, cfg=cfg)
    log_lines = read_log_tail(server_id) if server_id else []
    from core.load_progress import boot_failure_message

    boot_error = boot_failure_message(log_lines)
    active_boot = is_active_boot(log_lines)
    load_progress = parse_load_progress(log_lines) if active_boot else None
    booting = running and not healthy and active_boot and not boot_error
    status = 'stopped'
    if boot_error:
        status = 'error'
    elif running and healthy and loaded_models:
        status = 'loaded'
    elif booting:
        status = 'booting'
    elif running:
        status = 'running'

    running, status = _apply_engine_runtime_flags(entry, port_open=running, booting=booting, status=status)
    if not running:
        loaded_models = []
        healthy = False
        booting = False

    try:
        model_path = resolve_embedding_model_path(entry, cfg=cfg)
        meta = embedding_metadata(model_path)
    except ValueError:
        model_path = None
        meta = {
            'model_kind': 'embedding',
            'architecture': 'nomic-bert',
            'model_family': 'nomic-embed-text',
            'model_version': 'v1.5',
            'pooling': str(entry.get('pooling') or 'mean'),
            'embedding_dimensions': int((entry.get('embedding_settings') or {}).get('dimensions') or 768),
            'parameters': str((entry.get('embedding_settings') or {}).get('parameters') or '137M'),
            'quantization': 'Q8_0',
            'context_tokens': int(entry.get('context_size') or 2048),
            'api_path': '/v1/embeddings',
        }

    loaded_models = _normalize_loaded_model_ids(
        loaded_models,
        configured_model_id=configured_model_id,
        model_path=model_path,
        # /models may list an embedding alias while the router reports it as
        # unloaded; only an explicit loaded/running state counts.
        allow_fallback=False,
    )

    stack = resolve_model_stack(entry, cfg=cfg)
    model_stack = _annotate_model_stack(
        stack,
        booting=booting,
        loaded_models=loaded_models,
        progress=load_progress,
    )
    client_meta = build_engine_client_metadata(entry, model_stack)
    card_title = str(
        client_meta.get('display_name_full')
        or client_meta.get('display_name')
        or entry.get('label')
        or '',
    ).strip()
    visible_cards = _build_visible_cards(
        model_stack,
        server_label=str(entry.get('label') or ''),
        display_name=card_title,
        booting=booting,
        loaded_models=loaded_models,
        progress=load_progress,
    )
    listener_vram_gb = vram_gb_for_port(port, host, vram_map=vram_map) if running and port > 0 else None
    embed_settings = dict(entry.get('embedding_settings') or {})
    embed_file_name = model_path.name if model_path else ''
    embed_display = (
        embed_file_name
        or card_title
        or configured_model_id
        or str(entry.get('label') or '')
        or 'Embedding model'
    )
    loaded_by = resolve_engine_client_label(str(entry.get('id') or ''), entry.get('loaded_by'))
    active_clients = resolve_active_clients(str(entry.get('id') or ''), entry.get('loaded_by'))
    embedding_dimensions = meta.get('embedding_dimensions') or meta.get('dimensions')
    card_detail = ' · '.join(
        str(part)
        for part in (
            meta.get('parameters'),
            f'{embedding_dimensions}d' if embedding_dimensions else '',
            meta.get('quantization'),
        )
        if str(part or '').strip()
    )
    for card in visible_cards:
        card['gpu_display'] = gpu_display
        card['title'] = embed_display
        card['display_name'] = embed_display
        card['display_name_full'] = embed_display
        card['path'] = str(model_path) if model_path else card.get('path')
        card['app_label'] = loaded_by
        card['loaded_by'] = loaded_by
        card['active_clients'] = active_clients
        card['app_source'] = 'dflash'
        card['external'] = False
        card['card_detail'] = card_detail
        card['model_kind'] = 'embedding'
        card['embedding_settings'] = {**embed_settings, **meta}
        card.update(
            _model_kind_fields(
                model_name=str(card.get('title') or card.get('id') or ''),
                model_path=str(card.get('path') or (str(model_path) if model_path else '')),
                role=str(card.get('role') or 'target'),
            )
        )
        if listener_vram_gb is not None:
            card['vram_gb'] = listener_vram_gb
        if entry.get('context_size'):
            card['context_size'] = int(entry.get('context_size'))
        if card.get('size_gb') is None and model_path is not None:
            try:
                card['size_gb'] = round(model_path.stat().st_size / (1024 ** 3), 2)
            except OSError:
                pass

    active_model_id = loaded_models[0] if loaded_models else configured_model_id
    started = get_started_launch(port)

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    from core.inference_stats import fetch_inference_stats, get_cached_inference_stats

    inference_stats: dict[str, Any] = {}
    if healthy and api_url:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                fetch_inference_stats,
                api_url,
                server_id=server_id,
                model_id=active_model_id,
            )
            try:
                inference_stats = future.result(timeout=2.5)
            except FuturesTimeoutError:
                inference_stats = get_cached_inference_stats(server_id)

    for card in visible_cards:
        card['inference_stats'] = inference_stats

    return {
        **entry,
        'model_id': configured_model_id,
        **client_meta,
        'running': running,
        'status': status,
        'booting': booting,
        'load_progress': load_progress,
        'loaded_models': loaded_models,
        'active_model_id': active_model_id,
        'ready_for_chat': bool(healthy and loaded_models),
        'ready_for_embedding': bool(healthy and loaded_models),
        'model_stack': model_stack,
        'visible_cards': visible_cards,
        'gpu_display': gpu_display,
        'model_kind': 'embedding',
        'card_detail': card_detail,
        'embedding_settings': {**embed_settings, **meta},
        'pooling': str(entry.get('pooling') or meta.get('pooling') or 'mean'),
        'launch': started or {
            'context': entry.get('context_size'),
            'main_gpu': launch.get('main_gpu'),
            'split_mode': launch.get('split_mode'),
            'tensor_split': launch.get('tensor_split'),
            'engine_mode': 'embedding',
            'gpu_layers': (entry.get('load_settings') or {}).get('gpu_layers'),
        },
        'active_gpu_index': started.get('main_gpu') if started else launch.get('main_gpu'),
        'reachable_url': f'http://{host}:{port}' if port > 0 else '',
        'gpu_layers_max': gpu_layers_max_for(entry, cfg=cfg),
        'inference_stats': inference_stats,
        'active_clients': active_clients,
        'loaded_by': loaded_by,
        'boot_error': boot_error,
    }


def _live_stats_during_generation(
    *,
    server_id: str,
    api_url: str,
    configured_model_id: str,
) -> dict[str, Any]:
    """Refresh slot metrics only — never run nvidia-smi / full probes mid-generation."""
    from core.inference_stats import fetch_inference_stats, get_cached_inference_stats

    try:
        stats = fetch_inference_stats(
            api_url,
            server_id=server_id,
            model_id=configured_model_id,
        )
        if isinstance(stats, dict) and stats:
            return stats
    except Exception:
        pass
    return get_cached_inference_stats(server_id)


def _cached_status_while_generating(
    server: dict[str, Any],
    *,
    server_id: str,
    host: str,
    port: int,
    configured_model_id: str,
) -> dict[str, Any] | None:
    """Return a lightweight status snapshot without probing llama-server during inference."""
    from core.inference_stats import is_proxy_generating

    if not is_proxy_generating(server_id):
        return None

    api_url = str(server.get('api_url') or '').strip()
    if not api_url and port > 0:
        api_url = f'http://{host}:{port}'

    cached = _SERVER_STATUS_CACHE.get(server_id)
    if isinstance(cached, dict) and cached.get('loaded_models'):
        refreshed = dict(cached)
        stats = _live_stats_during_generation(
            server_id=server_id,
            api_url=api_url,
            configured_model_id=configured_model_id or str(refreshed.get('active_model_id') or ''),
        )
        if stats:
            refreshed['inference_stats'] = stats
        refreshed['ready_for_chat'] = bool(refreshed.get('loaded_models'))
        refreshed['status'] = refreshed.get('status') or 'loaded'
        refreshed['running'] = True
        _SERVER_STATUS_CACHE[server_id] = dict(refreshed)
        return refreshed

    port_open = tcp_port_open(host, port) if port > 0 else False
    if not port_open:
        return None

    stats = _live_stats_during_generation(
        server_id=server_id,
        api_url=api_url,
        configured_model_id=configured_model_id,
    )
    loaded = [configured_model_id] if configured_model_id else []
    result = {
        **server,
        'running': True,
        'status': 'loaded' if loaded else 'running',
        'booting': False,
        'load_progress': None,
        'loaded_models': loaded,
        'active_model_id': loaded[0] if loaded else '',
        'ready_for_chat': bool(loaded),
        'model_stack': server.get('model_stack') or [],
        'visible_cards': server.get('visible_cards') or [],
        'inference_stats': stats,
        'boot_error': None,
    }
    _SERVER_STATUS_CACHE[server_id] = dict(result)
    return result


def build_server_status(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    gpus: list[dict[str, Any]] | None = None,
    vram_map: dict[int, float] | None = None,
    open_ports: set[int] | None = None,
) -> dict[str, Any]:
    from core.config import is_embedding_server

    if is_embedding_server(server):
        status = _build_embedding_server_status(server, cfg=cfg, gpus=gpus, vram_map=vram_map)
        server_id = str(server.get('id') or '')
        if server_id:
            _SERVER_STATUS_CACHE[server_id] = dict(status)
        return status

    host = str(server.get('host') or '127.0.0.1')
    port = int(server.get('port') or 0)
    api_url = str(server.get('api_url') or '')
    server_id = str(server.get('id') or '')
    configured_model_id = str(server.get('model_id') or '').strip()
    cached_generating = _cached_status_while_generating(
        server,
        server_id=server_id,
        host=host,
        port=port,
        configured_model_id=configured_model_id,
    )
    if cached_generating is not None:
        return cached_generating
    if gpus is None:
        gpus = query_gpu_devices()
    launch = resolve_role_gpu_launch_params(
        server.get('gpu_device'),
        model_id=server.get('model_id'),
        gpus=gpus,
        hardware=(cfg or {}).get('hardware_settings'),
        context_size=server.get('context_size'),
    )
    engine_on = server.get('engine_on') is True
    if open_ports is not None:
        port_open = port > 0 and port in open_ports
    else:
        port_open = tcp_port_open(host, port) if port > 0 else False
    running = port_open
    if running and not get_started_launch(port) and engine_on:
        adopt_running_engine(server, cfg=cfg)
    started_launch = get_started_launch(port) if port > 0 else {}
    display_launch = {
        'main_gpu': started_launch.get('main_gpu', launch.get('main_gpu')),
        'split_mode': started_launch.get('split_mode', launch.get('split_mode')),
        'tensor_split': started_launch.get('tensor_split', launch.get('tensor_split')),
    } if started_launch else launch
    gpu_display = format_gpu_assignment(str(server.get('gpu_device') or 'auto'), display_launch, gpus)
    if running and api_url:
        loaded_models, loading_models, router_ready, api_load_progress = probe_runtime_state(api_url)
    else:
        loaded_models, loading_models, router_ready, api_load_progress = [], [], False, None
    loaded_models = _normalize_loaded_model_ids(
        loaded_models,
        configured_model_id=configured_model_id,
        # A router can advertise configured model aliases while its weights
        # are explicitly unloaded. Never turn that advertisement into a
        # false "loaded" state.
        allow_fallback=running and not router_ready,
    )
    log_lines = read_log_tail(server_id) if server_id else []
    from core.load_progress import boot_failure_message

    boot_error = boot_failure_message(log_lines)
    load_error = None if boot_error else model_load_failure_message(log_lines)
    status_error = boot_error or load_error
    if status_error and loaded_models:
        status_error = None
    active_boot = is_active_boot(log_lines)
    active_model_load = is_active_model_load(log_lines)
    log_load_progress = parse_load_progress(log_lines) if (active_boot or active_model_load) and not load_error else None
    alias_ready = bool(loaded_models)
    booting = running and not alias_ready and not status_error and (
        bool(loading_models) or active_model_load or (active_boot and not router_ready)
    )
    listener_vram_gb = None
    if running and port > 0:
        from core.gpu_processes import vram_gb_for_port

        listener_vram_gb = vram_gb_for_port(port, host, vram_map=vram_map)
    stack = resolve_model_stack(server, cfg=cfg)
    model_size_gb = _stack_size_gb([row for row in stack if str(row.get('role') or '') != 'alias'])
    vram_load_progress = estimate_vram_load_progress(
        server_id,
        listener_vram_gb,
        model_size_gb,
        active=booting,
    )
    load_progress = merge_load_progress(log_load_progress, api_load_progress, vram_load_progress)
    started = get_started_launch(port)
    status = 'stopped'
    if status_error:
        status = 'error'
    elif running and loaded_models:
        status = 'loaded'
    elif booting:
        status = 'booting'
    elif running:
        status = 'running'

    running, status = _apply_engine_runtime_flags(server, port_open=running, booting=booting, status=status)
    if not running:
        loaded_models = []
        loading_models = []
        booting = False
    if not booting:
        clear_vram_progress_baseline(server_id)

    model_stack = _annotate_model_stack(
        stack,
        booting=booting,
        loaded_models=loaded_models,
        progress=load_progress,
    )
    from core.display_names import build_engine_client_metadata

    client_meta = build_engine_client_metadata(server, model_stack)
    card_title = str(
        client_meta.get('display_name_full')
        or client_meta.get('display_name')
        or server.get('label')
        or '',
    ).strip()
    visible_cards = _build_visible_cards(
        model_stack,
        server_label=str(server.get('label') or ''),
        display_name=card_title,
        booting=booting,
        loaded_models=loaded_models,
        progress=load_progress,
    )
    live_draft_state = dflash_live_launch_state(server) if running else None
    acceleration = _acceleration_metadata(
        server,
        model_stack,
        live_draft=(live_draft_state is True and status == 'loaded'),
        live=status in {'loaded', 'error'},
    )
    for card in visible_cards:
        card_acceleration = _acceleration_metadata(
            server,
            card.get('stack_details') or model_stack,
            card=card,
            live_draft=(live_draft_state is True and status == 'loaded'),
            live=status in {'loaded', 'error'},
        )
        card.update(card_acceleration)
    if visible_cards and visible_cards[0].get('is_adhoc'):
        acceleration = {
            key: visible_cards[0].get(key)
            for key in (
                'acceleration_mode',
                'acceleration_expected',
                'acceleration_label',
                'draft_loaded',
                'draft_status',
            )
            if key in visible_cards[0]
        }
    from core.gpu_processes import _model_kind_fields

    loaded_by = resolve_engine_client_label(server_id, server.get('loaded_by'))
    active_clients = resolve_active_clients(server_id, server.get('loaded_by'))

    for card in visible_cards:
        card['gpu_display'] = gpu_display
        if not card.get('is_adhoc'):
            card['display_name'] = client_meta.get('display_name')
            card['display_name_full'] = client_meta.get('display_name_full')
        card['app_label'] = loaded_by
        card['loaded_by'] = loaded_by
        card['active_clients'] = active_clients
        card['app_source'] = 'dflash'
        card['external'] = False
        card.update(
            _model_kind_fields(
                model_name=str(card.get('title') or card.get('id') or ''),
                model_path=str(card.get('path') or ''),
                role=str(card.get('role') or ''),
            )
        )
        if listener_vram_gb is not None:
            card['vram_gb'] = listener_vram_gb
        if server.get('context_size'):
            card['context_size'] = int(server.get('context_size'))
        if card.get('size_gb') is None:
            card['size_gb'] = card.get('size_gb') or _stack_size_gb([card])

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    from core.inference_stats import fetch_inference_stats, get_cached_inference_stats, is_proxy_generating

    inference_stats: dict[str, Any] = {}
    if running and loaded_models:
        active_model = loaded_models[0]
        server_id = str(server.get('id') or '')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                fetch_inference_stats,
                api_url,
                server_id=server_id,
                model_id=active_model,
            )
            try:
                inference_stats = future.result(timeout=2.5)
            except FuturesTimeoutError:
                inference_stats = get_cached_inference_stats(server_id)
                if not is_proxy_generating(server_id):
                    inference_stats = {
                        **inference_stats,
                        'generating': False,
                        'generating_tokens': None,
                        'generating_tokens_per_second': None,
                        'generating_seconds': None,
                    }

    active_model_id = loaded_models[0] if loaded_models else ''

    from core.vision_setup import resolve_mmproj_path, server_supports_vision_chat

    mmproj_path = resolve_mmproj_path(server, cfg=cfg)
    target_path = str(server.get('target_path') or '').strip()
    supports_vision = server_supports_vision_chat(server, cfg=cfg)

    result = {
        **server,
        'model_id': configured_model_id,
        **client_meta,
        'running': running,
        'status': status,
        'booting': booting,
        'load_progress': load_progress,
        'loaded_models': loaded_models,
        'active_model_id': active_model_id,
        'ready_for_chat': bool(loaded_models),
        'model_stack': model_stack,
        'visible_cards': visible_cards,
        **acceleration,
        'gpu_display': gpu_display,
        'mmproj_path': mmproj_path or str(server.get('mmproj_path') or '').strip(),
        'supports_vision': supports_vision,
        'capabilities': ['vision'] if supports_vision else [],
        'launch': started or {
            'context': server.get('context_size'),
            'main_gpu': launch.get('main_gpu'),
            'split_mode': launch.get('split_mode'),
            'tensor_split': launch.get('tensor_split'),
            'idle_unload_seconds': int(server.get('idle_unload_minutes') or 0) * 60,
        },
        'active_gpu_index': started.get('main_gpu') if started else launch.get('main_gpu'),
        'reachable_url': f'http://{host}:{port}' if port > 0 else '',
        'gpu_layers_max': gpu_layers_max_for(server, cfg=cfg),
        'inference_stats': inference_stats,
        'active_clients': active_clients,
        'loaded_by': loaded_by,
        'boot_error': status_error,
        'load_error': load_error,
    }
    if server_id:
        _SERVER_STATUS_CACHE[server_id] = dict(result)
    return result


def _any_proxy_generating(servers: list[dict[str, Any]]) -> bool:
    from core.inference_stats import is_proxy_generating

    for entry in servers:
        sid = str((entry or {}).get('id') or '')
        if sid and is_proxy_generating(sid):
            return True
    return False


def _cached_external_gpu_loads() -> list[dict[str, Any]]:
    with _STATUS_PAYLOAD_LOCK:
        return [dict(row) for row in _STATUS_EXTERNAL_CACHE]


def _cached_status_payload(include_external: bool) -> dict[str, Any] | None:
    with _STATUS_PAYLOAD_LOCK:
        cached = _STATUS_PAYLOAD_CACHE.get('payload')
        if not isinstance(cached, dict):
            return None
        # Prefer an exact match; otherwise allow a previous external=0 snapshot.
        if _STATUS_PAYLOAD_CACHE.get('include_external') == include_external:
            return dict(cached)
        if include_external is False:
            return dict(cached)
        # Status polling without external scans is frequent. Reuse its server
        # snapshot while preserving the last known external GPU rows.
        fallback = dict(cached)
        fallback['external_gpu_loads'] = [dict(row) for row in _STATUS_EXTERNAL_CACHE]
        return fallback
    return None


def invalidate_status_payload_cache() -> None:
    """Force aggregate status to observe a server lifecycle/model change."""
    with _STATUS_PAYLOAD_LOCK:
        _STATUS_PAYLOAD_CACHE['payload'] = None
        _STATUS_PAYLOAD_CACHE['updated_at'] = 0.0
        _STATUS_PAYLOAD_CACHE['include_external'] = None


def _store_status_payload(payload: dict[str, Any], *, include_external: bool) -> None:
    global _STATUS_PAYLOAD_REVISION
    with _STATUS_PAYLOAD_LOCK:
        _STATUS_PAYLOAD_REVISION += 1
        payload['snapshot_revision'] = _STATUS_PAYLOAD_REVISION
        snapshot = dict(payload)
        if include_external:
            rows = snapshot.get('external_gpu_loads')
            _STATUS_EXTERNAL_CACHE.clear()
            if isinstance(rows, list):
                _STATUS_EXTERNAL_CACHE.extend(
                    dict(row) for row in rows if isinstance(row, dict)
                )
        _STATUS_PAYLOAD_CACHE['payload'] = snapshot
        _STATUS_PAYLOAD_CACHE['updated_at'] = float(payload.get('updated_at') or time.time())
        _STATUS_PAYLOAD_CACHE['include_external'] = include_external


def get_status_payload(
    servers: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
    gpus: list[dict[str, Any]] | None = None,
    include_external: bool = True,
    allow_stale: bool = True,
    max_stale_seconds: float = 0.75,
    fast_external: bool = False,
    status_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from core.gpu_processes import get_external_gpu_loads, loopback_listening_ports, query_compute_vram_map

    enabled = [s for s in servers if s.get('enabled', True)]
    primary_id = enabled[0]['id'] if enabled else (servers[0]['id'] if servers else '')

    # Under active chat, return the last good snapshot immediately so the Engines
    # page never blocks on nvidia-smi / netstat while llama-server is busy.
    if allow_stale and _any_proxy_generating(servers):
        stale = _cached_status_payload(include_external)
        if stale is not None:
            stale = dict(stale)
            stale['stale'] = True
            snapshot_at = float(stale.get('updated_at') or 0.0)
            if snapshot_at:
                stale['stale_age_ms'] = max(0, int((time.time() - snapshot_at) * 1000))
            # Still refresh per-server inference_stats for generating engines.
            refreshed_servers = []
            for entry in list(stale.get('servers') or []):
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                sid = str(row.get('id') or '')
                if sid and _any_proxy_generating([row]):
                    row['inference_stats'] = _live_stats_during_generation(
                        server_id=sid,
                        api_url=str(row.get('api_url') or ''),
                        configured_model_id=str(row.get('active_model_id') or row.get('model_id') or ''),
                    )
                refreshed_servers.append(row)
            stale['servers'] = refreshed_servers
            if include_external:
                try:
                    rows = get_external_gpu_loads(
                        servers=servers,
                        gpus=stale.get('gpus') if isinstance(stale.get('gpus'), list) else None,
                        cfg=cfg,
                        fast=True,
                    )
                    if rows:
                        stale['external_gpu_loads'] = rows
                        with _STATUS_PAYLOAD_LOCK:
                            _STATUS_EXTERNAL_CACHE.clear()
                            _STATUS_EXTERNAL_CACHE.extend(
                                dict(row) for row in rows if isinstance(row, dict)
                            )
                    else:
                        stale['external_gpu_loads'] = _cached_external_gpu_loads()
                except Exception:
                    stale['external_gpu_loads'] = _cached_external_gpu_loads()
            return stale

    with _STATUS_PAYLOAD_LOCK:
        cached_at = float(_STATUS_PAYLOAD_CACHE.get('updated_at') or 0.0)
        cached_payload = _STATUS_PAYLOAD_CACHE.get('payload')
        cache_matches = (
            isinstance(cached_payload, dict)
            and _STATUS_PAYLOAD_CACHE.get('include_external') == include_external
            and (time.time() - cached_at) <= float(max_stale_seconds)
        )
    if allow_stale and cache_matches:
        out = dict(cached_payload)
        out['stale'] = True
        if cached_at:
            out['stale_age_ms'] = max(0, int((time.time() - cached_at) * 1000))
        return out

    resolved_gpus = gpus if gpus is not None else query_gpu_devices()
    if gpus is None:
        _append_status_trace(
            status_trace,
            step='gpus',
            started_at=time.time() - 0.001,
            detail=f'{len(resolved_gpus)} GPU(s) detected',
        )
    else:
        _append_status_trace(
            status_trace,
            step='gpus',
            started_at=time.time(),
            detail=f'{len(resolved_gpus)} GPU(s) supplied',
        )

    # Skip expensive VRAM process scans while any engine is generating.
    vram_started = time.time()
    if _any_proxy_generating(servers):
        vram_map = {}
        _append_status_trace(
            status_trace,
            step='vram_map',
            started_at=vram_started,
            detail='skipped while an engine is generating',
        )
    else:
        vram_map = query_compute_vram_map()
        _append_status_trace(
            status_trace,
            step='vram_map',
            started_at=vram_started,
            detail=f'{len(vram_map)} GPU process(es) from nvidia-smi',
        )

    listen_started = time.time()
    open_ports = loopback_listening_ports()
    running_ports = sum(
        1 for server in servers
        if int(server.get('port') or 0) in open_ports
    )
    _append_status_trace(
        status_trace,
        step='listen_ports',
        started_at=listen_started,
        detail=(
            f'{len(servers)} engine profile(s); {running_ports} listener(s) up '
            f'(loopback listener snapshot, not per-port TCP probes)'
        ),
    )

    servers_started = time.time()

    def _build_one(entry: dict[str, Any]) -> dict[str, Any]:
        sid = str(entry.get('id') or '')
        try:
            return build_server_status(
                entry,
                cfg=cfg,
                gpus=resolved_gpus,
                vram_map=vram_map,
                open_ports=open_ports,
            )
        except Exception:
            cached = _SERVER_STATUS_CACHE.get(sid)
            if isinstance(cached, dict):
                return dict(cached)
            raise

    if len(servers) <= 1:
        built = [_build_one(server) for server in servers]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(8, len(servers))) as pool:
            built = list(pool.map(_build_one, servers))

    loaded_count = sum(1 for row in built if str(row.get('status') or '') == 'loaded')
    _append_status_trace(
        status_trace,
        step='server_status',
        started_at=servers_started,
        detail=(
            f'{len(built)} profile(s) checked; {running_ports} listening; '
            f'{loaded_count} loaded'
        ),
    )

    payload = {
        'success': True,
        'servers': built,
        'primary_server_id': primary_id,
        'updated_at': time.time(),
        'stale': False,
    }
    if include_external:
        external_started = time.time()
        try:
            payload['external_gpu_loads'] = get_external_gpu_loads(
                servers=servers,
                gpus=resolved_gpus,
                cfg=cfg,
                fast=fast_external or _any_proxy_generating(servers),
            )
            _append_status_trace(
                status_trace,
                step='external_scan',
                started_at=external_started,
                detail=(
                    f'{len(payload["external_gpu_loads"])} external GPU card(s)'
                    + (' (fast scan)' if fast_external else '')
                ),
            )
        except Exception as exc:
            payload['external_gpu_loads'] = _cached_external_gpu_loads()
            payload['external_scan_error'] = str(exc)[:240]
            _append_status_trace(
                status_trace,
                step='external_scan',
                started_at=external_started,
                detail=f'external scan failed — using cached cards ({exc})',
            )
    else:
        payload['external_gpu_loads'] = []
        _append_status_trace(
            status_trace,
            step='external_scan',
            started_at=time.time(),
            detail='skipped (include_external=0)',
        )
    _store_status_payload(payload, include_external=include_external)
    return payload
