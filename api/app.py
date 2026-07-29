"""DFlash Console — FastAPI backend."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.config import get_dflash_root, get_server, list_servers, load_config, normalize_hardware_settings, normalize_model_libraries, normalize_server, save_config
from core.version import APP_VERSION
from core.model_paths import allowed_model_roots
from core.gpu_devices import get_gpu_devices_payload
from core.local_models import list_local_models
from core.runtime import get_status_payload, stop_server, tcp_port_open, unload_model
from core.server_boot import eject_to_router_idle, load_server_checkpoint, note_boot_cycle_end, reload_server, start_router_listener, start_server

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / 'static'
ASSETS_DIR = ROOT / 'assets'

app = FastAPI(title='DFlash Console', version=APP_VERSION)
_BOOT_ID = uuid.uuid4().hex[:12]
_BOOT_AT = time.time()


@app.middleware('http')
async def log_console_api_requests(request: Request, call_next):
    from core.api_access_log import record_api_call

    path = request.url.path
    if not path.startswith('/api/'):
        return await call_next(request)

    start = time.perf_counter()
    client = request.client.host if request.client else ''
    query = request.url.query
    error = ''
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        record_api_call(
            method=request.method,
            path=path,
            query=query,
            status=status,
            duration_ms=(time.perf_counter() - start) * 1000,
            client=client,
            error=error,
        )


@app.on_event('startup')
def _restore_engines_on_startup() -> None:
    import logging
    import threading

    logger = logging.getLogger('uvicorn.error')

    def run() -> None:
        time.sleep(1.0)
        try:
            from core.engine_state import restore_engines

            results = restore_engines(cfg=load_config())
            for row in results:
                if row.get('action') not in ('skipped_engine_off',):
                    logger.info('engine restore %s', row)
        except Exception as exc:
            logger.exception('engine restore failed: %s', exc)

    threading.Thread(target=run, daemon=True, name='engine-restore').start()


@app.on_event('shutdown')
def _release_gpu_on_shutdown() -> None:
    """Console exit does not unload detached llama-server checkpoints."""
    return


def _ui_version() -> str:
    latest = 0.0
    for base in (STATIC_DIR, ASSETS_DIR):
        if not base.is_dir():
            continue
        for path in base.rglob('*'):
            if not path.is_file():
                continue
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
    return str(int(latest)) if latest else '0'


class ServerPatch(BaseModel):
    label: str | None = None
    profile: str | None = None
    port: int | None = None
    host: str | None = None
    api_url: str | None = None
    model_id: str | None = None
    gpu_device: str | None = None
    context_size: int | None = None
    idle_unload_minutes: int | None = None
    enabled: bool | None = None
    load_settings: dict[str, Any] | None = None
    inference_settings: dict[str, Any] | None = None


class ConfigPatch(BaseModel):
    ui_port: int | None = None
    dflash_root: str | None = None
    servers: list[dict[str, Any]] | None = None
    hardware_settings: dict[str, Any] | None = None
    model_libraries: list[dict[str, Any]] | None = None


class HardwarePatch(BaseModel):
    gpu_strategy: str | None = None
    enabled_gpu_indices: list[int] | None = None
    limit_offload_dedicated_vram: bool | None = None
    offload_kv_cache_to_gpu: bool | None = None


class HfDownloadRequest(BaseModel):
    repo_id: str = Field(..., min_length=3)
    filename: str = Field(..., min_length=4)
    library_id: str | None = None


class LibraryImportRequest(BaseModel):
    path: str = Field(..., min_length=1)
    preset: str = Field(default='custom')
    mode: str = Field(default='link', pattern='^(link|copy|move)$')


class PresetsImportBody(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)


class ServerLoadRequest(BaseModel):
    context_size: int | None = None
    load_settings: dict[str, Any] | None = None
    inference_settings: dict[str, Any] | None = None
    model_path: str | None = None
    model_id: str | None = None


def _persist_server_merge(cfg: dict[str, Any], server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    servers = cfg.get('servers') or []
    for idx, entry in enumerate(servers):
        if not isinstance(entry, dict) or str(entry.get('id') or '') != server_id:
            continue
        merged = normalize_server({**entry, **patch, 'id': server_id})
        servers[idx] = merged
        cfg['servers'] = servers
        save_config(cfg)
        return merged
    raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')

def _require_server(cfg: dict[str, Any], server_id: str) -> dict[str, Any]:
    server = get_server(cfg, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')
    return normalize_server(server)


@app.get('/api/health')
def health() -> dict[str, Any]:
    return {
        'success': True,
        'app': 'DFlash Console',
        'version': APP_VERSION,
        'boot_id': _BOOT_ID,
        'boot_at': _BOOT_AT,
        'ui_version': _ui_version(),
    }


@app.get('/api/gpu-devices')
def gpu_devices() -> dict[str, Any]:
    return get_gpu_devices_payload()


@app.get('/api/system-stats')
def system_stats() -> dict[str, Any]:
    from core.system_stats import get_system_stats_payload

    return get_system_stats_payload()


@app.get('/api/config')
def get_config() -> dict[str, Any]:
    return {'success': True, 'config': load_config()}


@app.put('/api/config')
def put_config(body: ConfigPatch) -> dict[str, Any]:
    cfg = load_config()
    data = body.model_dump(exclude_none=True)
    if 'servers' in data and isinstance(data['servers'], list):
        cfg['servers'] = [normalize_server(entry) for entry in data['servers'] if isinstance(entry, dict)]
        data.pop('servers')
    if 'hardware_settings' in data and isinstance(data['hardware_settings'], dict):
        cfg['hardware_settings'] = normalize_hardware_settings({
            **cfg.get('hardware_settings', {}),
            **data['hardware_settings'],
        })
        data.pop('hardware_settings')
    if 'model_libraries' in data and isinstance(data['model_libraries'], list):
        cfg['model_libraries'] = normalize_model_libraries(data['model_libraries'], cfg=cfg)
        data.pop('model_libraries')
    cfg.update(data)
    save_config(cfg)
    return {'success': True, 'config': cfg}


@app.get('/api/presets/export')
def export_presets() -> dict[str, Any]:
    presets_dir = ROOT / 'logs' / 'presets'
    presets_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for path in sorted(presets_dir.glob('*.ini')):
        files[path.name] = path.read_text(encoding='utf-8', errors='replace')
    return {'success': True, 'files': files, 'count': len(files), 'presets_dir': str(presets_dir)}


@app.post('/api/presets/import')
def import_presets(body: PresetsImportBody) -> dict[str, Any]:
    presets_dir = ROOT / 'logs' / 'presets'
    presets_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, content in (body.files or {}).items():
        safe_name = Path(str(name)).name
        if not safe_name.endswith('.ini'):
            continue
        (presets_dir / safe_name).write_text(str(content), encoding='utf-8')
        written += 1
    return {'success': True, 'written': written, 'presets_dir': str(presets_dir)}


@app.get('/api/model-libraries/scan')
def scan_model_libraries(
    preset: str = Query('custom'),
    q: str = Query('', max_length=200),
) -> dict[str, Any]:
    from core.model_discovery import scan_for_preset

    return scan_for_preset(preset, query=q, cfg=load_config())


@app.get('/api/model-libraries/preview')
def preview_model_library(
    preset: str = Query('custom'),
    path: str = Query(''),
) -> dict[str, Any]:
    from core.model_discovery import summarize_library_path
    from core.model_paths import normalize_model_library

    cfg = load_config()
    entry = normalize_model_library({'preset': preset, 'path': path}, cfg=cfg, index=0)
    if not entry:
        raise HTTPException(status_code=400, detail='invalid library')
    stats = summarize_library_path(entry['path'], preset)
    return {'success': True, 'library': {**entry, **stats}}


@app.get('/api/fs/browse')
def fs_browse(
    path: str = Query(''),
    preset: str = Query('custom'),
) -> dict[str, Any]:
    from core.fs_browse import browse_directory

    result = browse_directory(path, preset=preset, cfg=load_config())
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'browse failed')
    return result


@app.get('/api/model-libraries/import-plan')
def model_library_import_plan(
    path: str = Query(''),
    preset: str = Query('custom'),
    mode: str = Query('link'),
) -> dict[str, Any]:
    from core.library_import import import_plan

    if not str(path or '').strip():
        raise HTTPException(status_code=400, detail='path is required')
    try:
        return import_plan(path, preset=preset, mode=mode, cfg=load_config())
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/model-libraries/import')
def model_library_import(body: LibraryImportRequest) -> dict[str, Any]:
    from core.library_import import import_library_folder

    try:
        return import_library_folder(body.path, preset=body.preset, mode=body.mode, cfg=load_config())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get('/api/hardware')
def hardware_info() -> dict[str, Any]:
    from core.hardware_info import get_hardware_payload

    return get_hardware_payload()


@app.patch('/api/hardware')
def patch_hardware(body: HardwarePatch) -> dict[str, Any]:
    cfg = load_config()
    merged = normalize_hardware_settings({
        **cfg.get('hardware_settings', {}),
        **body.model_dump(exclude_none=True),
    })
    cfg['hardware_settings'] = merged
    save_config(cfg)
    return {'success': True, 'hardware_settings': merged}


@app.get('/api/runtime-recommendations')
def runtime_recommendations(
    server_id: str | None = Query(None, max_length=80),
    profile: str | None = Query(None, max_length=80),
    size_gb: float | None = Query(None, ge=0, le=512),
    context_max: int | None = Query(None, ge=2048, le=524288),
    gpu_layers_max: int | None = Query(None, ge=0, le=256),
) -> dict[str, Any]:
    from core.runtime_recommendations import get_runtime_recommendations_payload

    result = get_runtime_recommendations_payload(
        server_id=server_id,
        profile=profile,
        size_gb=size_gb,
        context_max=context_max,
        gpu_layers_max=gpu_layers_max,
    )
    if not result.get('success'):
        raise HTTPException(status_code=404, detail=str(result.get('error') or 'not found'))
    return result


@app.get('/api/hf/search')
def hf_search(
    q: str = Query('', max_length=200),
    limit: int = Query(25, ge=1, le=50),
    sort: str = Query('downloads'),
    category: str = Query('dflash'),
) -> dict[str, Any]:
    from core.huggingface import search_models

    return search_models(q, limit=limit, sort=sort, category=category)


@app.get('/api/hf/models/{repo_id:path}')
def hf_model_detail(
    repo_id: str,
    category: str = Query('dflash'),
) -> dict[str, Any]:
    from core.huggingface import get_model_detail

    result = get_model_detail(repo_id, category=category)
    if not result.get('success'):
        raise HTTPException(status_code=404, detail=result.get('error') or 'not found')
    return result


@app.get('/api/hf/local-match')
def hf_local_match(
    repo_id: str = Query(..., min_length=3),
    filename: str = Query(..., min_length=1),
) -> dict[str, Any]:
    from core.hf_local_match import find_local_matches

    matches = find_local_matches(repo_id, filename, cfg=load_config())
    return {'success': True, 'matches': matches, 'installed': bool(matches)}


@app.post('/api/hf/download')
def hf_download(body: HfDownloadRequest) -> dict[str, Any]:
    from core.huggingface import start_download

    result = start_download(body.repo_id, body.filename, library_id=body.library_id, cfg=load_config())
    if not result.get('success'):
        if result.get('already_installed'):
            raise HTTPException(status_code=409, detail=result)
        raise HTTPException(status_code=400, detail=result.get('error') or 'download failed')
    return result


@app.get('/api/hf/download/{job_id}')
def hf_download_status(job_id: str) -> dict[str, Any]:
    from core.huggingface import get_download_job

    result = get_download_job(job_id)
    if not result.get('success'):
        raise HTTPException(status_code=404, detail=result.get('error') or 'unknown job')
    return result


@app.get('/api/hf/downloads')
def hf_downloads(active: bool = Query(default=False)) -> dict[str, Any]:
    from core.huggingface import list_download_jobs

    return list_download_jobs(active_only=active)


@app.get('/api/models')
def models_catalog() -> dict[str, Any]:
    return list_local_models(cfg=load_config())


@app.get('/api/servers')
def servers_status() -> dict[str, Any]:
    cfg = load_config()
    enabled = [s for s in list_servers(cfg) if s.get('enabled', True)]
    payload = get_status_payload(enabled, cfg=cfg)
    payload['gpus'] = get_gpu_devices_payload().get('gpus') or []
    payload['all_servers'] = [normalize_server(s) for s in list_servers(cfg)]
    return payload


@app.get('/api/servers/{server_id}/status')
def server_status(server_id: str) -> dict[str, Any]:
    cfg = load_config()
    server = _require_server(cfg, server_id)
    from core.runtime import build_server_status

    cfg = load_config()
    return {'success': True, 'server': build_server_status(server, cfg=cfg)}


@app.patch('/api/servers/{server_id}')
def patch_server(server_id: str, body: ServerPatch) -> dict[str, Any]:
    cfg = load_config()
    servers = cfg.get('servers') or []
    found = False
    for idx, entry in enumerate(servers):
        if not isinstance(entry, dict) or str(entry.get('id') or '') != server_id:
            continue
        merged = normalize_server({**entry, **body.model_dump(exclude_none=True), 'id': server_id})
        servers[idx] = merged
        found = True
        break
    if not found:
        raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')
    cfg['servers'] = servers
    save_config(cfg)
    return {'success': True, 'server': merged}


@app.get('/api/docs/catalog')
def api_docs_catalog() -> dict[str, Any]:
    from core.api_catalog import get_api_catalog

    cfg = load_config()
    port = int(cfg.get('ui_port') or 8900)
    return get_api_catalog(console_base=f'http://127.0.0.1:{port}')


@app.get('/api/endpoints')
def api_endpoints() -> dict[str, Any]:
    from core.api_introspection import list_app_endpoints

    cfg = load_config()
    port = int(cfg.get('ui_port') or 8900)
    return list_app_endpoints(app, console_base=f'http://127.0.0.1:{port}')


@app.get('/api/installed')
def api_installed() -> dict[str, Any]:
    from core.api_introspection import get_installed_payload

    return get_installed_payload(cfg=load_config())


@app.get('/api/console/logs')
def console_logs(
    tail: int = Query(default=200, ge=1, le=5000),
    errors_only: bool = Query(default=False),
    include_engines: bool = Query(default=True),
    include_api_calls: bool = Query(default=True),
) -> dict[str, Any]:
    from core.api_introspection import get_console_logs_payload

    return get_console_logs_payload(
        cfg=load_config(),
        tail=tail,
        errors_only=errors_only,
        include_engines=include_engines,
        include_api_calls=include_api_calls,
    )


@app.post('/api/servers/{server_id}/listen')
@app.post('/api/servers/{server_id}/engine/start')
def server_listen(server_id: str) -> dict[str, Any]:
    from core.engine_state import note_engine_idle

    cfg = load_config()
    server = _require_server(cfg, server_id)
    result = start_router_listener(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'listen failed')
    note_engine_idle(server_id)
    return result


@app.post('/api/servers/{server_id}/load')
def server_load(server_id: str, body: ServerLoadRequest | None = None) -> dict[str, Any]:
    from core.engine_state import note_engine_loaded
    from core.memory_guardrails import assess_load

    cfg = load_config()
    server = _require_server(cfg, server_id)
    model_path = None
    model_id = None
    if body:
        patch = body.model_dump(exclude_none=True)
        model_path = patch.pop('model_path', None)
        model_id = patch.pop('model_id', None)
        if patch:
            server = _persist_server_merge(cfg, server_id, patch)
    if model_path:
        server = {**server, 'adhoc_model_path': model_path}
    check = assess_load(server, cfg=cfg)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))
    result = load_server_checkpoint(server, cfg=cfg, model_path=model_path, model_id=model_id)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'load failed')
    note_engine_loaded(server_id)
    if check.get('level') == 'warn' and check.get('message'):
        result['memory_warning'] = check['message']
    return result


@app.get('/api/servers/{server_id}/inference-stats')
def server_inference_stats(server_id: str) -> dict[str, Any]:
    from core.inference_stats import fetch_inference_stats
    from core.runtime import build_server_status

    cfg = load_config()
    server = _require_server(cfg, server_id)
    status = build_server_status(server, cfg=cfg)
    stats = status.get('inference_stats') or fetch_inference_stats(str(server.get('api_url') or ''), server_id=server_id)
    return {'success': True, 'server_id': server_id, 'status': status.get('status'), 'inference_stats': stats}


@app.post('/api/servers/{server_id}/v1/chat/completions')
async def proxy_chat_completions(server_id: str, request: Request) -> JSONResponse:
    import asyncio
    import json
    import urllib.error
    import urllib.request

    from core.inference_stats import mark_inference_end, mark_inference_start, note_completion_stats
    from core.runtime import api_base_url

    def _upstream_chat_completion() -> tuple[int, dict[str, Any]]:
        upstream = urllib.request.Request(url, data=raw, method='POST', headers={'Content-Type': content_type})
        try:
            with urllib.request.urlopen(upstream, timeout=600) as resp:
                payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
                return resp.status, payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            try:
                body = json.loads(detail)
            except json.JSONDecodeError:
                body = {'error': detail or str(exc)}
            return exc.code, body if isinstance(body, dict) else {'error': str(body)}

    cfg = load_config()
    server = _require_server(cfg, server_id)
    api_url = str(server.get('api_url') or '')
    base = api_base_url(api_url)
    if not base:
        raise HTTPException(status_code=400, detail='engine api_url not configured')
    raw = await request.body()
    content_type = request.headers.get('content-type') or 'application/json'
    url = f'{base}/v1/chat/completions'
    mark_inference_start(server_id)
    try:
        status_code, payload = await asyncio.to_thread(_upstream_chat_completion)
        if status_code >= 400:
            return JSONResponse(content=payload, status_code=status_code)
        note_completion_stats(server_id, payload)
        return JSONResponse(content=payload, status_code=status_code)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        mark_inference_end(server_id)


@app.post('/api/servers/{server_id}/start')
def server_start(server_id: str) -> dict[str, Any]:
    from core.engine_state import note_engine_loaded
    from core.memory_guardrails import assess_load

    cfg = load_config()
    server = _require_server(cfg, server_id)
    check = assess_load(server, cfg=cfg)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))
    result = start_server(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'start failed')
    note_engine_loaded(server_id)
    if check.get('level') == 'warn' and check.get('message'):
        result['memory_warning'] = check['message']
    return result


@app.post('/api/servers/{server_id}/stop')
@app.post('/api/servers/{server_id}/engine/stop')
def server_stop(server_id: str) -> dict[str, Any]:
    from core.engine_state import note_user_stopped
    from core.load_progress import append_log, stop_log_line

    cfg = load_config()
    server = _require_server(cfg, server_id)
    append_log(server_id, stop_log_line())
    result = stop_server(port=int(server['port']), host=str(server['host']), api_url=server.get('api_url'))
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'stop failed')
    note_user_stopped(server_id)
    return result


@app.post('/api/servers/{server_id}/unload')
def server_unload(server_id: str) -> dict[str, Any]:
    from core.engine_state import note_engine_idle
    from core.load_progress import append_log
    import time

    cfg = load_config()
    server = _require_server(cfg, server_id)
    host = str(server.get('host') or '127.0.0.1')
    port = int(server.get('port') or 0)
    if port <= 0 or not tcp_port_open(host, port):
        return {'success': True, 'unloaded': False, 'message': 'server not running'}

    model_id = str(server.get('model_id') or '').strip()
    result = unload_model(api_url=str(server.get('api_url') or ''), model_id=model_id)
    if result.get('success'):
        append_log(server_id, f"=== model unload {time.strftime('%Y-%m-%d %H:%M:%S')} model={model_id} ===")
        note_boot_cycle_end(port)
        note_engine_idle(server_id)
        return result

    http_status = int(result.get('http_status') or 0)
    if http_status in (404, 405) or 'not found' in str(result.get('error') or '').lower():
        append_log(server_id, f"=== legacy eject -> router idle {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        migrate = eject_to_router_idle(server, cfg=cfg)
        if migrate.get('success'):
            note_boot_cycle_end(port)
            return {
                'success': True,
                'unloaded': True,
                'model': model_id,
                'migrated_router': True,
                'message': 'Legacy server replaced with router (no model loaded).',
            }
        append_log(server_id, f"=== legacy eject failed {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        append_log(server_id, str(migrate.get('error') or 'unknown error'))
        raise HTTPException(status_code=400, detail=migrate.get('error') or 'eject failed')

    append_log(server_id, f"=== model unload failed {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    append_log(server_id, str(result.get('error') or 'unknown error'))
    raise HTTPException(status_code=400, detail=result.get('error') or 'model unload failed')


@app.post('/api/servers/{server_id}/reload')
def server_reload(server_id: str) -> dict[str, Any]:
    cfg = load_config()
    server = _require_server(cfg, server_id)
    result = reload_server(server)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'reload failed')
    return result


@app.get('/api/logs/{server_id}')
def server_logs(server_id: str, tail: int = 200) -> dict[str, Any]:
    from core.runtime import tcp_port_open

    cfg = load_config()
    server = _require_server(cfg, server_id)
    log_path = ROOT / 'logs' / f'{server_id}.log'
    if not log_path.is_file():
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        if port > 0 and tcp_port_open(host, port):
            return {
                'success': True,
                'lines': [
                    'Server is listening but was not started from DFlash Console.',
                    'Stop it here or restart from Load Model to capture developer logs.',
                ],
                'path': str(log_path),
            }
        return {'success': True, 'lines': [], 'path': str(log_path)}
    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    return {'success': True, 'lines': lines[-max(1, min(tail, 2000)):], 'path': str(log_path)}


@app.delete('/api/logs/{server_id}')
def clear_server_logs(server_id: str) -> dict[str, Any]:
    _require_server(load_config(), server_id)
    log_path = ROOT / 'logs' / f'{server_id}.log'
    LOG_DIR = log_path.parent
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path.write_text('', encoding='utf-8')
    return {'success': True, 'lines': [], 'path': str(log_path)}


def _allowed_model_roots(cfg: dict[str, Any]) -> list[Path]:
    return allowed_model_roots(cfg)


@app.delete('/api/models/file')
def delete_model_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    cfg = load_config()
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != '.gguf' or not target.is_file():
        raise HTTPException(status_code=400, detail='not a GGUF file')
    allowed = _allowed_model_roots(cfg)
    if not allowed or not any(target.is_relative_to(root) for root in allowed):
        raise HTTPException(status_code=403, detail='path not under allowed model directories')
    target.unlink()
    return {'success': True, 'path': str(target)}


if STATIC_DIR.is_dir():
    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')

if ASSETS_DIR.is_dir():
    app.mount('/assets', StaticFiles(directory=str(ASSETS_DIR)), name='assets')


@app.get('/')
def index() -> FileResponse:
    index_path = STATIC_DIR / 'index.html'
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail='UI not built')
    return FileResponse(index_path)
