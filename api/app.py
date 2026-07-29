"""DFlash Studio — FastAPI backend."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.config import get_dflash_root, get_server, list_servers, load_config, normalize_hardware_settings, normalize_model_libraries, normalize_server, save_config
from core.model_paths import allowed_model_roots
from core.gpu_devices import get_gpu_devices_payload
from core.local_models import list_local_models
from core.runtime import get_status_payload, stop_server, tcp_port_open, unload_model
from core.server_boot import eject_to_router_idle, note_boot_cycle_end, reload_server, start_server

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / 'static'

app = FastAPI(title='DFlash Studio', version='0.1.0')
_BOOT_ID = uuid.uuid4().hex[:12]
_BOOT_AT = time.time()


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


def _require_server(cfg: dict[str, Any], server_id: str) -> dict[str, Any]:
    server = get_server(cfg, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')
    return normalize_server(server)


@app.get('/api/health')
def health() -> dict[str, Any]:
    return {
        'success': True,
        'app': 'DFlash Studio',
        'boot_id': _BOOT_ID,
        'boot_at': _BOOT_AT,
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


@app.post('/api/hf/download')
def hf_download(body: HfDownloadRequest) -> dict[str, Any]:
    from core.huggingface import start_download

    result = start_download(body.repo_id, body.filename, library_id=body.library_id, cfg=load_config())
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'download failed')
    return result


@app.get('/api/hf/download/{job_id}')
def hf_download_status(job_id: str) -> dict[str, Any]:
    from core.huggingface import get_download_job

    result = get_download_job(job_id)
    if not result.get('success'):
        raise HTTPException(status_code=404, detail=result.get('error') or 'unknown job')
    return result


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


@app.post('/api/servers/{server_id}/start')
def server_start(server_id: str) -> dict[str, Any]:
    from core.memory_guardrails import assess_load

    cfg = load_config()
    server = _require_server(cfg, server_id)
    check = assess_load(server, cfg=cfg)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))
    result = start_server(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'start failed')
    if check.get('level') == 'warn' and check.get('message'):
        result['memory_warning'] = check['message']
    return result


@app.post('/api/servers/{server_id}/stop')
def server_stop(server_id: str) -> dict[str, Any]:
    from core.load_progress import append_log, stop_log_line

    cfg = load_config()
    server = _require_server(cfg, server_id)
    append_log(server_id, stop_log_line())
    result = stop_server(port=int(server['port']), host=str(server['host']), api_url=server.get('api_url'))
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'stop failed')
    return result


@app.post('/api/servers/{server_id}/unload')
def server_unload(server_id: str) -> dict[str, Any]:
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
                    'Server is listening but was not started from DFlash Studio.',
                    'Stop it here or restart from Load Model to capture developer logs.',
                ],
                'path': str(log_path),
            }
        return {'success': True, 'lines': [], 'path': str(log_path)}
    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    return {'success': True, 'lines': lines[-max(1, min(tail, 2000)):], 'path': str(log_path)}


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


@app.get('/')
def index() -> FileResponse:
    index_path = STATIC_DIR / 'index.html'
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail='UI not built')
    return FileResponse(index_path)
