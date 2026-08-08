"""DFlash Console — FastAPI backend."""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.config import get_dflash_root, get_server, list_runtimes, list_servers, load_config, normalize_hardware_settings, normalize_model_libraries, normalize_runtime, normalize_server, normalize_ui_layout, save_config, suggest_server_port, update_server_runtime
from core.version import APP_VERSION
from core.model_paths import allowed_model_roots, disk_scan_roots, validate_model_path
from core.gpu_devices import get_gpu_devices_payload
from core.local_models import invalidate_model_catalog_cache, list_local_models, warm_model_catalog
from core.runtime import get_status_payload, stop_server, tcp_port_open, unload_model
from core.server_boot import load_server_checkpoint, note_boot_cycle_end, reload_server, start_router_listener, start_server

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / 'static'
ASSETS_DIR = ROOT / 'assets'

_BOOT_ID = uuid.uuid4().hex[:12]
_BOOT_AT = time.time()
_SERVERS_STATUS_LOCK = asyncio.Lock()
_SYSTEM_STATS_LOCK = asyncio.Lock()
_SYSTEM_STATS_CACHE: dict[str, Any] | None = None
_SYSTEM_STATS_CACHE_AT = 0.0
_SYSTEM_STATS_CACHE_TTL = 2.0


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _write_runtime_process_manifest()
    _start_background_tasks()
    try:
        yield
    finally:
        _release_gpu_on_shutdown()


def _write_runtime_process_manifest() -> None:
    """Write process-identity + per-runtime bundle manifests at boot."""
    try:
        from core.runtimes import write_bundle_manifests, write_process_tokens_manifest

        write_process_tokens_manifest()
        write_bundle_manifests()
    except Exception:
        # Manifests are an optimisation for cleanup/diagnostics; never block boot.
        pass


app = FastAPI(title='DFlash Console', version=APP_VERSION, lifespan=app_lifespan)


def _request_client_label(request: Request | None) -> str:
    if request is None:
        return 'DFlash Console'
    explicit = str(request.headers.get('x-dflash-client') or request.headers.get('X-DFlash-Client') or '').strip()
    if explicit:
        return explicit
    user_agent = str(request.headers.get('user-agent') or '').lower()
    if 'onevoice' in user_agent:
        return 'OneVoice'
    referer = str(request.headers.get('referer') or '').lower()
    if 'onevoice' in referer:
        return 'OneVoice'
    return 'DFlash Console'


@app.middleware('http')
async def no_cache_static_assets(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith('/static/') and path.rsplit('.', 1)[-1] in ('js', 'css'):
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
    return response


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


def _start_background_tasks() -> None:
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

    def warm_catalog() -> None:
        try:
            warm_model_catalog(cfg=load_config())
        except Exception as exc:
            logger.exception('model catalog warm failed: %s', exc)

    threading.Thread(target=warm_catalog, daemon=True, name='model-catalog-warm').start()

    def warm_hf_catalog() -> None:
        try:
            from core.hf_catalog_cache import warm_hf_catalog_cache

            warm_hf_catalog_cache()
        except Exception as exc:
            logger.exception('hf catalog warm failed: %s', exc)

    threading.Thread(target=warm_hf_catalog, daemon=True, name='hf-catalog-warm').start()


def _release_gpu_on_shutdown() -> None:
    """Unload managed engines only on intentional Console shutdown (run.ps1 / restart script)."""
    import logging
    import os

    shutdown_logger = logging.getLogger('uvicorn.error')
    if os.environ.get('DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN', '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        shutdown_logger.info(
            'Console API exiting — preserving llama-server engines '
            '(set DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN=1 to stop them)'
        )
        return

    from core.engine_state import release_and_stop_all_managed_engines

    try:
        release_and_stop_all_managed_engines()
    except Exception as exc:
        shutdown_logger.exception('managed engine release on shutdown failed: %s', exc)


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
    runtimes: list[dict[str, Any]] | None = None
    runtime_stop_others_on_load: bool | None = None
    cpu_slow_warn: bool | None = None
    hardware_settings: dict[str, Any] | None = None
    model_libraries: list[dict[str, Any]] | None = None
    ui_layout: dict[str, Any] | None = None


class HardwarePatch(BaseModel):
    gpu_strategy: str | None = None
    enabled_gpu_indices: list[int] | None = None
    limit_offload_dedicated_vram: bool | None = None
    offload_kv_cache_to_gpu: bool | None = None


class HfDownloadRequest(BaseModel):
    repo_id: str = Field(..., min_length=3)
    filename: str = Field(..., min_length=4)
    library_id: str | None = None


class VisionSetupRequest(BaseModel):
    model_path: str = Field(..., min_length=1)
    server_id: str | None = None


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


class GpuProcessUnload(BaseModel):
    api_url: str | None = None
    model_id: str | None = None


class ServerCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    target_path: str = Field(..., min_length=1)
    draft_path: str = Field(..., min_length=1)
    profile: str | None = Field(default=None, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    model_id: str | None = Field(default=None, max_length=120)
    id: str | None = Field(default=None, max_length=80)
    context_size: int | None = Field(default=None, ge=2048, le=262144)


class AudioSpeechRequest(BaseModel):
    """OpenAI-shaped POST /v1/audio/speech body (Piper TTS)."""
    model: str = Field(default='tts-1', max_length=120)
    input: str = Field(..., min_length=1)
    voice: str = Field(default='', max_length=120)
    response_format: str = Field(default='wav', pattern='^(wav|mp3)$')
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


class RuntimeLoadRequest(BaseModel):
    voice: str = Field(default='', max_length=120)
    path: str = Field(default='', max_length=1024)


class EmbedBatchRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    model: str = Field(default='', max_length=120)
    export_jsonl: bool = Field(default=False)


def _persist_server_merge(cfg: dict[str, Any], server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    servers = cfg.get('servers') or []
    for idx, entry in enumerate(servers):
        if not isinstance(entry, dict) or str(entry.get('id') or '') != server_id:
            continue
        merged = normalize_server({**entry, **patch, 'id': server_id})
        servers[idx] = merged
        cfg['servers'] = servers
        _save_config_checked(cfg)
        return merged
    raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')

def _require_server(cfg: dict[str, Any], server_id: str) -> dict[str, Any]:
    server = get_server(cfg, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')
    return normalize_server(server)


def _invalidate_status_cache() -> None:
    from core.runtime import invalidate_status_payload_cache

    invalidate_status_payload_cache()


def _save_config_checked(cfg: dict[str, Any]) -> None:
    try:
        save_config(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/api/health')
async def health() -> dict[str, Any]:
    from core.setup import is_setup_complete

    cfg = load_config()
    console_root = str((ROOT).resolve())
    try:
        configured_root = str(Path(str(cfg.get('dflash_root') or console_root)).resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        configured_root = console_root
    return {
        'success': True,
        'app': 'DFlash Console',
        'version': APP_VERSION,
        'console_root': configured_root,
        'shell_version': os.environ.get('DFLASH_CONSOLE_SHELL_VERSION', ''),
        'boot_id': _BOOT_ID,
        'boot_at': _BOOT_AT,
        'ui_version': _ui_version(),
        'setup_complete': is_setup_complete(cfg),
    }


@app.get('/api/gpu-devices')
def gpu_devices() -> dict[str, Any]:
    return get_gpu_devices_payload()


@app.get('/api/system-stats')
async def system_stats() -> dict[str, Any]:
    global _SYSTEM_STATS_CACHE, _SYSTEM_STATS_CACHE_AT
    from core.system_stats import get_system_stats_payload

    now = time.monotonic()
    if _SYSTEM_STATS_CACHE is not None and now - _SYSTEM_STATS_CACHE_AT < _SYSTEM_STATS_CACHE_TTL:
        return dict(_SYSTEM_STATS_CACHE)

    # System stats invoke nvidia-smi and PowerShell on Windows. Keep those
    # blocking probes off the event loop and serialize them so a slow probe
    # cannot consume the entire request worker pool.
    async with _SYSTEM_STATS_LOCK:
        now = time.monotonic()
        if _SYSTEM_STATS_CACHE is not None and now - _SYSTEM_STATS_CACHE_AT < _SYSTEM_STATS_CACHE_TTL:
            return dict(_SYSTEM_STATS_CACHE)
        payload = await asyncio.to_thread(get_system_stats_payload)
        _SYSTEM_STATS_CACHE = dict(payload)
        _SYSTEM_STATS_CACHE_AT = time.monotonic()
        return payload


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
        invalidate_model_catalog_cache()
    if 'runtimes' in data and isinstance(data['runtimes'], list):
        cfg['runtimes'] = [
            normalize_runtime(entry)
            for entry in data['runtimes']
            if isinstance(entry, dict)
        ]
        data.pop('runtimes')
    if 'ui_layout' in data and isinstance(data['ui_layout'], dict):
        current = normalize_ui_layout(cfg.get('ui_layout'))
        incoming = normalize_ui_layout(data['ui_layout'])
        merged = { **current, **incoming }
        merged['table_columns'] = {
            **current.get('table_columns', {}),
            **incoming.get('table_columns', {}),
        }
        cfg['ui_layout'] = normalize_ui_layout(merged)
        data.pop('ui_layout')
    cfg.update(data)
    _save_config_checked(cfg)
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
    _save_config_checked(cfg)
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
    category: str = Query('all-gguf'),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    from core.hf_catalog_cache import search_with_cache
    from core.huggingface import search_models

    return search_with_cache(
        query=q,
        sort=sort,
        category=category,
        limit=limit,
        force_refresh=refresh,
        fetcher=lambda: search_models(q, limit=limit, sort=sort, category=category),
    )


@app.get('/api/hf/models/{repo_id:path}')
def hf_model_detail(
    repo_id: str,
    category: str = Query('all-gguf'),
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


@app.get('/api/hf/local-installs')
def hf_local_installs(repo_id: str = Query(..., min_length=3)) -> dict[str, Any]:
    from core.hf_local_match import find_repo_local_installs

    matches = find_repo_local_installs(repo_id, cfg=load_config())
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
def models_catalog(quick: bool = Query(default=False), refresh: bool = Query(default=False)) -> dict[str, Any]:
    from core.local_models import invalidate_model_catalog_cache

    cfg = load_config()
    if refresh:
        invalidate_model_catalog_cache()
        return list_local_models(cfg=cfg, scan_disk=not quick, force_refresh=True)
    return list_local_models(cfg=cfg, scan_disk=not quick)


@app.get('/api/models/vision/plan')
def models_vision_plan(
    path: str = Query(..., min_length=1),
    server_id: str | None = Query(default=None),
) -> dict[str, Any]:
    from core.vision_setup import vision_plan

    return vision_plan(model_path=path, server_id=server_id)


@app.post('/api/models/vision/setup')
def models_vision_setup(body: VisionSetupRequest) -> dict[str, Any]:
    from core.huggingface import start_download
    from core.vision_setup import vision_plan, wire_vision

    plan = vision_plan(model_path=body.model_path, server_id=body.server_id)
    if not plan.get('success'):
        raise HTTPException(status_code=400, detail=plan.get('error') or 'vision plan failed')
    if plan.get('ready'):
        return plan
    if not plan.get('needs_download'):
        wired = wire_vision(
            model_path=body.model_path,
            mmproj_path=str(plan.get('mmproj_path') or ''),
            server_id=body.server_id,
        )
        if not wired.get('success'):
            raise HTTPException(status_code=400, detail=wired.get('error') or 'vision wiring failed')
        return {**plan, **wired, 'wired': True}

    post_action = {
        'type': 'wire_vision',
        'model_path': body.model_path,
        'server_id': body.server_id or '',
        'dest_path': plan.get('dest_path') or '',
    }
    downloaded = start_download(
        str(plan.get('repo_id') or ''),
        str(plan.get('filename') or ''),
        dest_path=str(plan.get('dest_path') or ''),
        post_action=post_action,
        cfg=load_config(),
    )
    if not downloaded.get('success') and not downloaded.get('wired'):
        raise HTTPException(status_code=400, detail=downloaded.get('error') or 'vision download failed')
    return {**plan, **downloaded}


@app.get('/api/servers/profiles')
def server_profiles() -> dict[str, Any]:
    cfg = load_config()
    all_servers = [normalize_server(s) for s in list_servers(cfg)]
    enabled = [s for s in all_servers if s.get('enabled', True)]
    primary_id = enabled[0]['id'] if enabled else (all_servers[0]['id'] if all_servers else '')
    return {
        'success': True,
        'all_servers': all_servers,
        'servers': enabled,
        'primary_server_id': primary_id,
    }


@app.get('/api/servers')
async def servers_status(
    include_external: bool = Query(default=True),
    fresh: bool = Query(default=False),
) -> dict[str, Any]:
    from core.runtime import _cached_status_payload

    # External GPU discovery can legitimately take several seconds. Once a
    # snapshot exists, status refreshes should remain responsive while that
    # scan is in progress instead of queueing every UI poll behind it.
    if _SERVERS_STATUS_LOCK.locked() and not fresh:
        cached = _cached_status_payload(include_external)
        if cached is not None:
            cached['stale'] = True
            snapshot_at = float(cached.get('updated_at') or 0.0)
            if snapshot_at:
                cached['stale_age_ms'] = max(0, int((time.time() - snapshot_at) * 1000))
            return cached

    def _build_payload() -> dict[str, Any]:
        cfg = load_config()
        enabled = [s for s in list_servers(cfg) if s.get('enabled', True)]
        gpus = get_gpu_devices_payload().get('gpus') or []
        payload = get_status_payload(
            enabled,
            cfg=cfg,
            gpus=gpus,
            include_external=include_external,
            allow_stale=not fresh,
        )
        payload['gpus'] = gpus
        payload['all_servers'] = [normalize_server(s) for s in list_servers(cfg)]
        payload['external_scan_skipped'] = not include_external
        return payload

    # Several UI surfaces consume the same status snapshot. Only one expensive
    # status build may run at a time; other callers wait without occupying an
    # anyio worker thread and then receive the fresh cached snapshot.
    async with _SERVERS_STATUS_LOCK:
        return await asyncio.to_thread(_build_payload)


@app.get('/api/runtimes')
def runtimes_status() -> dict[str, Any]:
    """Dual-read unified runtime list.

    ``servers[]`` entries are synthesised with ``runtime_id: llama-server``;
    non-llama adapters live in ``runtimes[]``. Read-only — this endpoint never
    rewrites the Engines/stack APIs and never migrates servers[] onto runtimes[].
    """
    from core.runtimes import (
        get_runtime_adapter,
        list_runtime_adapters,
        runtime_process_identity_tokens,
    )

    cfg = load_config()
    merged: list[dict[str, Any]] = []
    for server in list_servers(cfg):
        merged.append({
            'id': str(server.get('id') or ''),
            'kind': 'server',
            'runtime_id': 'llama-server',
            'label': str(server.get('label') or server.get('id') or ''),
            'port': int(server.get('port') or 0),
            'host': str(server.get('host') or '127.0.0.1'),
            'api_url': str(server.get('api_url') or ''),
            'device_policy': str(server.get('gpu_device') or 'auto'),
            'enabled': server.get('enabled', True) is not False,
            # llama-server is a built-in adapter (not in the registry), so it is
            # always "installed".
            'adapter_installed': True,
        })
    for runtime in list_runtimes(cfg):
        runtime_id = str(runtime.get('runtime_id') or '')
        merged.append({
            'id': str(runtime.get('id') or ''),
            'kind': 'runtime',
            'runtime_id': runtime_id,
            'label': str(runtime.get('label') or runtime.get('id') or ''),
            'port': int(runtime.get('port') or 0),
            'host': str(runtime.get('host') or '127.0.0.1'),
            'api_url': str(runtime.get('api_url') or ''),
            'device_policy': str(runtime.get('device_policy') or 'auto'),
            'default_voice': str(runtime.get('default_voice') or ''),
            'default_model': str(runtime.get('default_model') or ''),
            'vram_budget_mb': runtime.get('vram_budget_mb'),
            'allow_cpu_fallback': runtime.get('allow_cpu_fallback'),
            'enabled': runtime.get('enabled', True) is not False,
            'adapter_installed': get_runtime_adapter(runtime_id) is not None,
        })
    adapters = [{
        'runtime_id': adapter.runtime_id,
        'modalities': list(adapter.modalities),
        'execution_mode': adapter.execution_mode,
        'process_identity_tokens': list(adapter.process_identity_tokens),
        'openai_routes': list(adapter.openai_routes()),
    } for adapter in list_runtime_adapters()]
    return {
        'success': True,
        'runtimes': merged,
        'adapters': adapters,
        'process_identity_tokens': list(runtime_process_identity_tokens()),
    }


@app.get('/api/gpu/contention')
def gpu_contention() -> dict[str, Any]:
    """Phase 0 scaffold: which Console runtimes / external apps hold GPU memory."""
    from core.runtimes.contention import gpu_contention_report

    return gpu_contention_report(cfg=load_config())


def _require_runtime_adapter(runtime_id: str):
    from core.runtimes import get_runtime_adapter

    adapter = get_runtime_adapter(str(runtime_id or ''))
    if adapter is None:
        raise HTTPException(status_code=404, detail=f'runtime adapter not found: {runtime_id}')
    return adapter


@app.get('/api/runtimes/manifests')
def runtime_manifests() -> dict[str, Any]:
    """Aggregate installed runtime plugin manifests + the process-token manifest.

    Registered before the ``/api/runtimes/{runtime_id}`` route so the literal
    path ``manifests`` is not captured by the runtime_id path parameter.
    """
    import json as _json

    from core.runtimes import list_runtime_adapters

    manifests: list[dict[str, Any]] = []
    bundle_root = ROOT / 'runtimes'
    for adapter in list_runtime_adapters():
        runtime_id = str(adapter.runtime_id or '')
        manifest_path = bundle_root / runtime_id / 'manifest.json'
        if manifest_path.is_file():
            try:
                manifests.append({
                    'runtime_id': runtime_id,
                    'manifest': _json.loads(manifest_path.read_text(encoding='utf-8')),
                })
            except (OSError, ValueError):
                pass
    tokens: dict[str, Any] = {}
    tokens_path = bundle_root / 'process-tokens.json'
    if tokens_path.is_file():
        try:
            tokens = _json.loads(tokens_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            tokens = {}
    return {'success': True, 'manifests': manifests, 'process_tokens': tokens}


@app.get('/api/runtimes/{runtime_id}')
def runtime_status(runtime_id: str) -> dict[str, Any]:
    adapter = _require_runtime_adapter(runtime_id)
    health = adapter.health() if callable(getattr(adapter, 'health', None)) else {}
    return {'success': True, 'runtime_id': runtime_id, **health}


@app.get('/api/runtimes/{runtime_id}/voices')
def runtime_voices(runtime_id: str) -> dict[str, Any]:
    adapter = _require_runtime_adapter(runtime_id)
    list_fn = getattr(adapter, 'list_voices', None)
    voices = list_fn() if callable(list_fn) else []
    return {'success': True, 'runtime_id': runtime_id, 'voices': voices}


@app.post('/api/runtimes/{runtime_id}/load')
def runtime_load(runtime_id: str, body: RuntimeLoadRequest) -> dict[str, Any]:
    adapter = _require_runtime_adapter(runtime_id)
    load_fn = getattr(adapter, 'load', None)
    if not callable(load_fn):
        raise HTTPException(status_code=400, detail='adapter does not support load')
    result = load_fn({'id': body.voice, 'path': body.path})
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'load failed')
    return {'success': True, 'runtime_id': runtime_id, **result}


@app.post('/api/runtimes/{runtime_id}/unload')
def runtime_unload(runtime_id: str) -> dict[str, Any]:
    adapter = _require_runtime_adapter(runtime_id)
    unload_fn = getattr(adapter, 'unload', None)
    if not callable(unload_fn):
        raise HTTPException(status_code=400, detail='adapter does not support unload')
    return {'success': True, 'runtime_id': runtime_id, **unload_fn()}


@app.post('/api/runtimes/{runtime_id}/v1/audio/speech')
def runtime_audio_speech(runtime_id: str, body: AudioSpeechRequest) -> Response:
    """Console-proxied OpenAI audio/speech route backed by Piper (CLI)."""
    adapter = _require_runtime_adapter(runtime_id)
    synthesize = getattr(adapter, 'synthesize', None)
    if not callable(synthesize):
        raise HTTPException(status_code=400, detail='adapter does not support speech synthesis')
    text = str(body.input or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail='input text is required')
    result = synthesize(text, voice=body.voice, speed=body.speed)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error') or 'synthesis failed')
    audio = result.get('audio') or b''
    media_type = str(result.get('media_type') or 'audio/wav')
    return Response(
        content=audio,
        media_type=media_type,
        headers={
            'Content-Disposition': 'inline; filename="speech.wav"',
            'X-DFlash-Runtime': runtime_id,
            'X-DFlash-Voice': str(result.get('voice') or ''),
        },
    )


@app.post('/api/runtimes/{runtime_id}/v1/audio/transcriptions')
async def runtime_audio_transcriptions(
    runtime_id: str,
    file: UploadFile = File(...),
    model: str = Form(default='whisper-1'),
    language: str = Form(default=''),
    response_format: str = Form(default='json'),
) -> Any:
    """Console-proxied OpenAI audio/transcriptions route (whisper-server)."""
    adapter = _require_runtime_adapter(runtime_id)
    transcribe = getattr(adapter, 'transcribe', None)
    if not callable(transcribe):
        raise HTTPException(status_code=400, detail='adapter does not support transcription')
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail='empty audio file')
    result = transcribe(
        audio,
        filename=file.filename or 'audio.wav',
        language=language,
        response_format=response_format,
    )
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error') or 'transcription failed')
    text = str(result.get('text') or '')
    if response_format == 'text':
        return Response(content=text, media_type='text/plain')
    return {'success': True, 'text': text}


@app.get('/api/runtimes/{runtime_id}/logs')
def runtime_logs(
    runtime_id: str,
    lines: int = Query(default=120, ge=1, le=2000),
) -> dict[str, Any]:
    """Tail the per-runtime log file (logs/runtimes/<runtime_id>.log)."""
    log_path = ROOT / 'logs' / 'runtimes' / f'{str(runtime_id).strip()}.log'
    rows: list[str] = []
    if log_path.is_file():
        try:
            content = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
            rows = content[-int(lines):]
        except OSError:
            rows = []
    return {'success': True, 'runtime_id': runtime_id, 'lines': rows, 'log_file': str(log_path)}


@app.get('/api/servers/{server_id}/status')
async def server_status(server_id: str) -> dict[str, Any]:
    import asyncio

    from core.runtime import build_server_status

    cfg = load_config()
    server = _require_server(cfg, server_id)

    def _build_one() -> dict[str, Any]:
        return {'success': True, 'server': build_server_status(server, cfg=cfg)}

    return await asyncio.to_thread(_build_one)


@app.get('/api/servers/{server_id}/chat-ready')
def server_chat_ready(server_id: str) -> dict[str, Any]:
    from core.chat_ready import assess_server_chat_ready

    cfg = load_config()
    server = _require_server(cfg, server_id)
    result = assess_server_chat_ready(server, cfg=cfg)
    return {'success': True, 'server_id': server_id, **result}


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
    _save_config_checked(cfg)
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
def server_listen(server_id: str, request: Request) -> dict[str, Any]:
    from core.config import is_embedding_server
    from core.embedding_server import start_embedding_server
    from core.engine_state import note_engine_idle

    cfg = load_config()
    server = _require_server(cfg, server_id)
    embedding = is_embedding_server(server)
    result = start_embedding_server(server, cfg=cfg) if embedding else start_router_listener(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'listen failed')
    note_engine_idle(server_id)
    update_server_runtime(server_id, loaded_by=_request_client_label(request))
    _invalidate_status_cache()
    return result


def _ensure_server_ready_for_chat(server_id: str, server: dict[str, Any], cfg: dict[str, Any], *, client_label: str = 'DFlash Console') -> dict[str, Any]:
    """JIT-load configured checkpoint when chat arrives — only if engine is on."""
    import time

    from core.chat_ready import assess_server_chat_ready, chat_ready_http_error_detail
    from core.engine_state import note_engine_loaded
    from core.memory_guardrails import assess_load
    from core.runtime import build_server_status

    gate = assess_server_chat_ready(server, cfg=cfg)
    if not gate.get('ready'):
        raise HTTPException(status_code=503, detail=chat_ready_http_error_detail(gate))

    def _wait_until_loaded(*, timeout_seconds: float = 180.0) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            live_status = build_server_status(server, cfg=cfg)
            if live_status.get('status') == 'loaded' and live_status.get('loaded_models'):
                return live_status
            if live_status.get('status') in {'booting', 'running'}:
                time.sleep(2.0)
                continue
            return live_status
        live_status = build_server_status(server, cfg=cfg)
        if live_status.get('status') == 'loaded' and live_status.get('loaded_models'):
            return live_status
        raise HTTPException(
            status_code=503,
            detail={
                'error': 'model_not_loaded',
                'message': 'Checkpoint load did not complete — retry chat shortly.',
                'status': live_status.get('status'),
                'model_id': live_status.get('model_id'),
                'loaded_models': live_status.get('loaded_models') or [],
            },
        )

    live = build_server_status(server, cfg=cfg)
    if live.get('status') == 'loaded' and live.get('loaded_models'):
        return live

    if live.get('status') == 'booting':
        return _wait_until_loaded()

    check = assess_load(server, cfg=cfg)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))

    result = load_server_checkpoint(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(
            status_code=503,
            detail={
                'error': 'model_load_failed',
                'message': str(result.get('error') or 'model load failed'),
                'status': live.get('status'),
                'model_id': live.get('model_id'),
            },
        )

    note_engine_loaded(server_id, loaded_by=client_label)
    _invalidate_status_cache()
    return _wait_until_loaded()


def _auto_stop_other_servers(cfg: dict[str, Any], target_server_id: str) -> list[str]:
    """When ``runtime_stop_others_on_load`` is enabled, unload other running
    Console engines first so the target can load without VRAM contention.

    Only acts on a ``stop-others`` GPU-contention recommendation and only on
    Console-owned (llama) servers — never embedding engines, never external
    apps. Returns the ids that were unloaded.
    """
    if cfg.get('runtime_stop_others_on_load') is not True:
        return []
    from core.config import is_embedding_server
    from core.runtimes.contention import gpu_contention_report

    try:
        report = gpu_contention_report(cfg=cfg)
    except Exception:
        return []
    if report.get('recommendation') != 'stop-others':
        return []
    stopped: list[str] = []
    for row in report.get('console_runtimes') or []:
        other_id = str(row.get('id') or '')
        if not other_id or other_id == target_server_id or not row.get('running'):
            continue
        server = _require_server(cfg, other_id)
        if is_embedding_server(server):
            continue
        try:
            result = server_unload(other_id)
        except HTTPException:
            continue
        if result.get('success'):
            stopped.append(other_id)
    return stopped


@app.get('/api/servers/{server_id}/load-plan')
def server_load_plan(
    server_id: str,
    model_path: str = Query(default=''),
    model_id: str = Query(default=''),
) -> dict[str, Any]:
    from core.memory_guardrails import assess_load

    cfg = load_config()
    server = _require_server(cfg, server_id)
    candidate = dict(server)
    if model_path.strip():
        candidate['adhoc_model_path'] = model_path.strip()
    if model_id.strip():
        candidate['model_id'] = model_id.strip()
    return {
        'success': True,
        'server_id': server_id,
        **assess_load(candidate, cfg=cfg),
    }


@app.post('/api/servers/{server_id}/load')
def server_load(server_id: str, request: Request, body: ServerLoadRequest | None = None) -> dict[str, Any]:
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
    if _auto_stop_other_servers(cfg, server_id):
        _invalidate_status_cache()
    result = load_server_checkpoint(server, cfg=cfg, model_path=model_path, model_id=model_id)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'load failed')
    note_engine_loaded(server_id, loaded_by=_request_client_label(request))
    _invalidate_status_cache()
    if check.get('level') == 'warn' and check.get('message'):
        result['memory_warning'] = check['message']
    return result


@app.get('/api/servers/{server_id}/inference-stats')
def server_inference_stats(server_id: str) -> dict[str, Any]:
    from core.inference_stats import fetch_inference_stats
    from core.runtime import _SERVER_STATUS_CACHE

    cfg = load_config()
    server = _require_server(cfg, server_id)
    # Always hit /slots directly. Never route through build_server_status — that
    # path returns a frozen cache while proxy inference is active.
    cached = _SERVER_STATUS_CACHE.get(server_id) or {}
    stats = fetch_inference_stats(
        str(server.get('api_url') or ''),
        server_id=server_id,
        model_id=str(
            cached.get('active_model_id')
            or server.get('model_id')
            or '',
        ),
    )
    status_label = str(cached.get('status') or ('loaded' if cached.get('loaded_models') else 'unknown'))
    return {
        'success': True,
        'server_id': server_id,
        'status': status_label,
        'inference_stats': stats,
    }


@app.post('/api/servers/{server_id}/cancel-inference')
def cancel_server_inference(server_id: str) -> dict[str, Any]:
    """Immediately abort all Console→llama chat streams for this engine."""
    from core.chat_proxy import cancel_active_upstream_streams
    from core.inference_stats import mark_inference_end

    _require_server(load_config(), server_id)
    closed = cancel_active_upstream_streams(server_id)
    # Clear generating badge even if some streams already ended.
    from core.inference_stats import is_proxy_generating

    for _ in range(32):
        if not is_proxy_generating(server_id):
            break
        mark_inference_end(server_id)
    return {'success': True, 'server_id': server_id, 'closed_streams': closed}


@app.post('/api/servers/{server_id}/v1/chat/completions')
async def proxy_chat_completions(server_id: str, request: Request):
    import asyncio
    import json
    import urllib.error

    from core.chat_proxy import extract_stream_completion_stats, open_upstream_chat_stream, upstream_chat_completion, wants_stream
    from core.inference_stats import mark_inference_end, mark_inference_start, note_completion_stats
    from core.runtime import api_base_url, build_server_status

    cfg = load_config()
    server = _require_server(cfg, server_id)
    live = _ensure_server_ready_for_chat(server_id, server, cfg, client_label=_request_client_label(request))

    api_url = str(server.get('api_url') or '')
    base = api_base_url(api_url)
    if not base:
        raise HTTPException(status_code=400, detail='engine api_url not configured')
    raw = await request.body()
    content_type = request.headers.get('content-type') or 'application/json'
    url = f'{base}/v1/chat/completions'

    if wants_stream(raw):
        mark_inference_start(
            server_id,
            api_url=api_url,
            model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
        )
        close_upstream = None
        try:
            media_type, chunks, close_upstream = await open_upstream_chat_stream(
                url,
                raw,
                content_type=content_type,
                server_id=server_id,
            )
        except urllib.error.HTTPError as exc:
            mark_inference_end(server_id)
            detail = exc.read().decode('utf-8', errors='replace')
            raise HTTPException(status_code=exc.code, detail=detail) from exc
        except Exception as exc:
            mark_inference_end(server_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        async def stream_body():
            buffer = bytearray()
            try:
                async for chunk in chunks:
                    # Client gone → stop reading; finally closes llama-server.
                    if await request.is_disconnected():
                        break
                    buffer.extend(chunk)
                    yield chunk
            finally:
                if close_upstream:
                    try:
                        close_upstream()
                    except Exception:
                        pass
                payload = extract_stream_completion_stats(bytes(buffer))
                if payload:
                    note_completion_stats(
                        server_id,
                        payload,
                        api_url=api_url,
                        model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
                    )
                mark_inference_end(server_id)

        return StreamingResponse(
            stream_body(),
            media_type=media_type,
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            },
        )

    mark_inference_start(
        server_id,
        api_url=api_url,
        model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
    )
    try:
        status_code, payload = await asyncio.to_thread(
            upstream_chat_completion,
            url,
            raw,
            content_type=content_type,
        )
        if status_code >= 400:
            return JSONResponse(content=payload, status_code=status_code)
        note_completion_stats(
            server_id,
            payload,
            api_url=api_url,
            model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
        )
        return JSONResponse(content=payload, status_code=status_code)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        mark_inference_end(server_id)


def _ensure_server_ready_for_embed(server_id: str, server: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """JIT-load an embedding engine (no chat-ready gate)."""
    import time

    from core.runtime import build_server_status

    def _wait_until_loaded(*, timeout_seconds: float = 180.0) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            live_status = build_server_status(server, cfg=cfg)
            if live_status.get('status') == 'loaded' and live_status.get('loaded_models'):
                return live_status
            if live_status.get('status') in {'booting', 'running'}:
                time.sleep(2.0)
                continue
            return live_status
        live_status = build_server_status(server, cfg=cfg)
        if live_status.get('status') == 'loaded' and live_status.get('loaded_models'):
            return live_status
        raise HTTPException(
            status_code=503,
            detail={
                'error': 'model_not_loaded',
                'message': 'Embedding engine load did not complete — retry shortly.',
                'status': live_status.get('status'),
                'loaded_models': live_status.get('loaded_models') or [],
            },
        )

    live = build_server_status(server, cfg=cfg)
    if live.get('status') == 'loaded' and live.get('loaded_models'):
        return live
    if live.get('status') == 'booting':
        return _wait_until_loaded()
    result = load_server_checkpoint(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(
            status_code=503,
            detail={
                'error': 'model_load_failed',
                'message': str(result.get('error') or 'embedding engine load failed'),
                'status': live.get('status'),
                'model_id': live.get('model_id'),
            },
        )
    return _wait_until_loaded()


@app.post('/api/servers/{server_id}/v1/embeddings')
async def server_embeddings_proxy(server_id: str, request: Request):
    """Console-proxied OpenAI embeddings route (embedding llama-server profile)."""
    import asyncio
    import json
    import urllib.error
    import urllib.request

    from core.runtime import api_base_url

    cfg = load_config()
    server = _require_server(cfg, server_id)
    _ensure_server_ready_for_embed(server_id, server, cfg)

    api_url = str(server.get('api_url') or '')
    base = api_base_url(api_url)
    if not base:
        raise HTTPException(status_code=400, detail='engine api_url not configured')
    raw = await request.body()
    try:
        body = json.loads(raw.decode('utf-8', errors='replace') or '{}')
    except (ValueError, AttributeError):
        body = {}
    input_value = body.get('input')
    if isinstance(input_value, str):
        texts = [input_value]
    elif isinstance(input_value, list):
        texts = [str(t) for t in input_value]
    else:
        raise HTTPException(status_code=400, detail='input must be a string or list of strings')

    from core.embedding_server import embed_batch

    def _run():
        items = [{'text': t} for t in texts]
        return embed_batch(server, items=items, model_id=str(body.get('model') or ''), cfg=cfg)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not result.get('success'):
        raise HTTPException(status_code=502, detail=result.get('error') or 'embedding failed')

    rows = result.get('rows') or []
    data = [
        {'object': 'embedding', 'index': row.get('index'), 'embedding': row.get('embedding')}
        for row in rows
    ]
    return {
        'object': 'list',
        'data': data,
        'model': str(result.get('model') or server.get('model_id') or ''),
        'usage': result.get('usage') or {},
    }


@app.post('/api/servers/{server_id}/embed/batch')
async def server_embed_batch(server_id: str, body: EmbedBatchRequest) -> dict[str, Any]:
    """Embed a list of text items and optionally export rows as .jsonl."""
    import asyncio

    from core.embedding_server import embed_batch, embed_rows_to_jsonl

    cfg = load_config()
    server = _require_server(cfg, server_id)
    _ensure_server_ready_for_embed(server_id, server, cfg)

    def _run():
        return embed_batch(server, items=body.items, model_id=body.model, cfg=cfg)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not result.get('success'):
        raise HTTPException(status_code=502, detail=result.get('error') or 'embedding failed')

    payload: dict[str, Any] = {
        'success': True,
        'rows': result.get('rows') or [],
        'model': result.get('model') or '',
        'usage': result.get('usage') or {},
        'count': len(result.get('rows') or []),
    }
    if body.export_jsonl:
        payload['jsonl'] = embed_rows_to_jsonl(result.get('rows') or [])
    return payload


@app.post('/api/servers/{server_id}/start')
def server_start(server_id: str, request: Request) -> dict[str, Any]:
    from core.engine_state import note_engine_loaded
    from core.memory_guardrails import assess_load

    cfg = load_config()
    server = _require_server(cfg, server_id)
    check = assess_load(server, cfg=cfg)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))
    if _auto_stop_other_servers(cfg, server_id):
        _invalidate_status_cache()
    result = start_server(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'start failed')
    note_engine_loaded(server_id, loaded_by=_request_client_label(request))
    _invalidate_status_cache()
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
    _invalidate_status_cache()
    return result


@app.post('/api/gpu/processes/{pid}/unload')
def gpu_process_unload(pid: int, body: GpuProcessUnload | None = None) -> dict[str, Any]:
    from core.gpu_processes import unload_external_gpu_process

    payload = body.model_dump(exclude_none=True) if body else {}
    result = unload_external_gpu_process(
        int(pid),
        api_url=str(payload.get('api_url') or ''),
        model_id=str(payload.get('model_id') or ''),
    )
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'unload failed')
    return result


@app.post('/api/servers/{server_id}/unload')
def server_unload(server_id: str) -> dict[str, Any]:
    from core.load_progress import append_log
    from core.config import is_embedding_server
    from core.server_boot import eject_to_router_idle
    import time

    cfg = load_config()
    server = _require_server(cfg, server_id)
    _invalidate_status_cache()
    host = str(server.get('host') or '127.0.0.1')
    port = int(server.get('port') or 0)
    api_url = str(server.get('api_url') or '')
    if port <= 0 or not tcp_port_open(host, port):
        from core.engine_state import note_user_stopped

        note_user_stopped(server_id)
        return {'success': True, 'unloaded': False, 'engine_stopped': True, 'message': 'engine already stopped'}
    if is_embedding_server(server):
        raise HTTPException(
            status_code=409,
            detail='Embedding engines must stay loaded while running. Use Stop instead of Unload.',
        )

    from core.runtime import probe_models

    loaded_ids = probe_models(api_url)
    model_id = str((loaded_ids[0] if loaded_ids else server.get('model_id')) or '').strip()
    if not model_id:
        from core.engine_state import note_engine_idle

        note_engine_idle(server_id)
        return {
            'success': True,
            'unloaded': False,
            'engine_stopped': False,
            'listener_ready': True,
            'message': 'No model was loaded; engine listener remains ready.',
        }
    result = unload_model(api_url=api_url, model_id=model_id)
    if result.get('success'):
        append_log(server_id, f"=== model unload {time.strftime('%Y-%m-%d %H:%M:%S')} model={model_id} ===")
        note_boot_cycle_end(port)
        from core.engine_state import note_engine_idle

        listener_ready = tcp_port_open(host, port)
        if not listener_ready:
            restart = start_router_listener(server, cfg=cfg)
            listener_ready = bool(restart.get('success')) and tcp_port_open(host, port)
            if not listener_ready:
                raise HTTPException(
                    status_code=502,
                    detail=restart.get('error') or 'model unloaded but idle listener restart failed',
                )
        note_engine_idle(server_id)
        return {
            **result,
            'engine_stopped': False,
            'listener_ready': listener_ready,
            'message': 'Model unloaded; engine listener remains ready.',
        }

    http_status = int(result.get('http_status') or 0)
    if http_status in (404, 405) or 'not found' in str(result.get('error') or '').lower():
        append_log(server_id, f"=== legacy unload -> idle router {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        note_boot_cycle_end(port)
        idle_result = eject_to_router_idle(server, cfg=cfg)
        if idle_result.get('success'):
            from core.engine_state import note_engine_idle

            note_engine_idle(server_id)
            return {
                'success': True,
                'unloaded': True,
                'model': model_id,
                'engine_stopped': False,
                'listener_ready': True,
                'message': 'Legacy model ejected; engine listener remains ready.',
            }
        append_log(server_id, f"=== idle router transition failed {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        append_log(server_id, str(idle_result.get('error') or 'unknown error'))
        raise HTTPException(status_code=400, detail=idle_result.get('error') or 'idle router transition failed')

    append_log(server_id, f"=== model unload failed {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    append_log(server_id, str(result.get('error') or 'unknown error'))
    raise HTTPException(status_code=400, detail=result.get('error') or 'model unload failed')


@app.post('/api/servers/{server_id}/reload')
def server_reload(server_id: str) -> dict[str, Any]:
    from core.engine_state import note_engine_loaded

    cfg = load_config()
    server = _require_server(cfg, server_id)
    result = reload_server(server)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'reload failed')
    note_engine_loaded(server_id)
    _invalidate_status_cache()
    return result


@app.get('/api/logs/{server_id}')
def server_logs(server_id: str, tail: int = 200) -> dict[str, Any]:
    from core.runtime import tcp_port_open
    from core.log_utils import read_tail_lines

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
    lines, truncated = read_tail_lines(log_path, max_lines=max(1, min(tail, 2000)))
    return {'success': True, 'lines': lines, 'truncated': truncated, 'path': str(log_path)}


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


def _stack_model_roots(cfg: dict[str, Any]) -> list[Path]:
    """Return configured and recognized browse roots usable as stack targets."""
    roots: list[Path] = []
    seen: set[str] = set()
    try:
        scan_roots = disk_scan_roots(cfg)
    except OSError:
        scan_roots = []
    candidates = [*_allowed_model_roots(cfg), *(path for path, _source in scan_roots)]
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots


def _validate_gguf_under_allowed_roots(
    path_text: str,
    cfg: dict[str, Any],
    *,
    roots: list[Path] | None = None,
) -> Path:
    allowed = roots if roots is not None else _allowed_model_roots(cfg)
    try:
        return validate_model_path(
            path_text,
            cfg=cfg,
            allowed_extensions=('.gguf',),
            allowed_dirs=allowed,
            require_file=True,
        )
    except ValueError as exc:
        message = str(exc)
        status = 403 if 'under allowed' in message else 400
        raise HTTPException(status_code=status, detail=message) from exc


@app.get('/api/stacks/capable-targets')
def stacks_capable_targets() -> dict[str, Any]:
    from core.stack_match import list_capable_targets

    return list_capable_targets(cfg=load_config())


@app.get('/api/stacks/preflight')
def stacks_preflight(target_path: str = Query(..., min_length=1)) -> dict[str, Any]:
    from core.stack_match import preflight_stack_target

    cfg = load_config()
    _validate_gguf_under_allowed_roots(target_path, cfg, roots=_stack_model_roots(cfg))
    return preflight_stack_target(target_path, cfg=cfg)


@app.get('/api/stacks/match')
def stacks_match(target_path: str = Query(..., min_length=1)) -> dict[str, Any]:
    from core.stack_match import match_stack_for_target

    cfg = load_config()
    _validate_gguf_under_allowed_roots(target_path, cfg, roots=_stack_model_roots(cfg))
    return match_stack_for_target(target_path, cfg=cfg)


@app.get('/api/stacks/suggest-port')
def stacks_suggest_port() -> dict[str, Any]:
    cfg = load_config()
    return {'success': True, 'port': suggest_server_port(cfg=cfg)}


@app.post('/api/servers')
def create_server(body: ServerCreateRequest) -> dict[str, Any]:
    from core.config import validate_config
    from core.model_presets import model_id_from_path, write_server_preset
    from core.stack_match import (
        infer_dflash_profile,
        is_accelerator_path,
        is_target_candidate,
        is_viable_stack_pair,
        score_accelerator_pair,
        suggest_server_id,
    )

    cfg = load_config()
    stack_roots = _stack_model_roots(cfg)
    target = _validate_gguf_under_allowed_roots(body.target_path, cfg, roots=stack_roots)
    draft = _validate_gguf_under_allowed_roots(body.draft_path, cfg, roots=stack_roots)
    if not is_target_candidate(target):
        raise HTTPException(
            status_code=400,
            detail='Choose a full target GGUF, not a DFlash or DSpark accelerator.',
        )
    if not is_accelerator_path(draft):
        raise HTTPException(
            status_code=400,
            detail='The accelerator file must include DFlash or DSpark in its filename.',
        )
    pair_score = score_accelerator_pair(target, draft)
    if not is_viable_stack_pair(target, draft, pair_score):
        raise HTTPException(
            status_code=400,
            detail='These two files do not look like a compatible target and DFlash accelerator pair.',
        )
    server_id = str(body.id or suggest_server_id(target, cfg=cfg)).strip()
    if not server_id:
        raise HTTPException(status_code=400, detail='server id required')
    if get_server(cfg, server_id):
        raise HTTPException(status_code=409, detail=f'server already exists: {server_id}')

    profile = str(body.profile or infer_dflash_profile(target, draft)).strip()
    port = int(body.port or suggest_server_port(cfg=cfg))
    model_id = str(body.model_id or model_id_from_path(target)).strip()
    if not model_id:
        raise HTTPException(status_code=400, detail='target model has no usable identifier')
    entry = normalize_server({
        'id': server_id,
        'label': body.label.strip(),
        'profile': profile,
        'port': port,
        'model_id': model_id,
        'target_path': str(target),
        'draft_path': str(draft),
        'context_size': body.context_size or 32768,
        'enabled': True,
        'engine_on': False,
    })
    servers = cfg.get('servers') or []
    if not isinstance(servers, list):
        servers = []
    servers.append(entry)
    cfg['servers'] = servers
    try:
        validate_config(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        write_server_preset(entry, cfg=cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_config_checked(cfg)
    invalidate_model_catalog_cache()
    return {'success': True, 'server': entry}


@app.post('/api/fs/reveal')
def fs_reveal(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    from core.fs_reveal import reveal_path

    cfg = load_config()
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail='path not found')
    allowed = _allowed_model_roots(cfg)
    if not allowed or not any(target.is_relative_to(root) for root in allowed):
        raise HTTPException(status_code=403, detail='path not under allowed model directories')
    result = reveal_path(target)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error') or 'reveal failed')
    return result


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
def index() -> HTMLResponse:
    index_path = STATIC_DIR / 'index.html'
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail='UI not built')
    html = index_path.read_text(encoding='utf-8')
    version = _ui_version()
    html = re.sub(
        r'((?:/static/|/assets/)[^"\']+?)(?=["\'])',
        rf'\1?v={version}',
        html,
    )
    return HTMLResponse(html, headers={'Cache-Control': 'no-store, must-revalidate'})
