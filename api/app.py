"""DFlash Console — FastAPI backend."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.config import PACKAGE_ROOT, ROOT, get_dflash_root, get_server, is_embedding_server, list_runtimes, list_servers, load_config, normalize_download_settings, normalize_hardware_settings, normalize_inference_settings, normalize_load_settings, normalize_model_libraries, normalize_remote_nodes, normalize_runtime, normalize_server, normalize_ui_layout, save_config, suggest_server_port, update_server_runtime
from core.version import APP_VERSION
from core.model_paths import allowed_model_roots, disk_scan_roots, validate_model_path
from core.gpu_devices import get_gpu_devices_payload
from core.local_models import (
    friendly_model_dir_label,
    invalidate_model_catalog_cache,
    list_local_models,
    resolve_model_delete_dir,
    warm_model_catalog,
)
from core.runtime import get_status_payload, stop_server, tcp_port_open, unload_model
from core.server_boot import load_server_checkpoint, note_boot_cycle_end, reload_server, start_router_listener, start_server

STATIC_DIR = PACKAGE_ROOT / 'static' if (PACKAGE_ROOT / 'static').is_dir() else ROOT / 'static'
ASSETS_DIR = PACKAGE_ROOT / 'assets' if (PACKAGE_ROOT / 'assets').is_dir() else ROOT / 'assets'

_BOOT_ID = uuid.uuid4().hex[:12]
_BOOT_AT = time.time()
_SERVERS_STATUS_LOCK = asyncio.Lock()
_SYSTEM_STATS_LOCK = asyncio.Lock()
_SYSTEM_STATS_CACHE: dict[str, Any] | None = None
_SYSTEM_STATS_CACHE_AT = 0.0
_SYSTEM_STATS_CACHE_TTL = 2.0

# OpenAI gateway server (thread inside this process; see api/gateway.py).
_GATEWAY_SERVER: Any = None
_GATEWAY_THREAD: threading.Thread | None = None
_GATEWAY_ERROR = ''


def _parent_process_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == 'nt':
        # os.kill(pid, 0) is not signal-free on every Windows Python build (it
        # can deliver a deferred CTRL_C_EVENT to this console), so probe the
        # process handle directly instead.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _start_parent_watchdog() -> None:
    """Stop this API when the owning shell process dies (orphan guard).

    The Electron shell passes DFLASH_CONSOLE_PARENT_PID when it spawns the
    server. If the shell is force-killed (update helper, taskkill, crash), the
    graceful quit path never runs — without this guard the old server keeps
    listening and the next app start would reuse a stale backend.
    """
    try:
        parent_pid = int(str(os.environ.get('DFLASH_CONSOLE_PARENT_PID') or '0').strip())
    except (TypeError, ValueError):
        parent_pid = 0
    if parent_pid <= 0:
        return

    def watch() -> None:
        import logging

        logger = logging.getLogger('uvicorn.error')
        time.sleep(10.0)  # grace period while the shell finishes starting
        while True:
            time.sleep(2.0)
            if _parent_process_alive(parent_pid):
                continue
            logger.warning(
                'Console API parent process %s is gone — shutting down orphaned server',
                parent_pid,
            )
            try:
                _stop_gateway_server()
                _release_gpu_on_shutdown()
            except Exception:
                pass
            os._exit(0)

    threading.Thread(target=watch, daemon=True, name='console-parent-watchdog').start()


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    def write_manifests() -> None:
        try:
            _write_runtime_process_manifest()
        except Exception:
            pass

    threading.Thread(
        target=write_manifests,
        daemon=True,
        name='runtime-manifest',
    ).start()
    _start_background_tasks()
    _start_gateway_server()
    _start_parent_watchdog()
    try:
        yield
    finally:
        try:
            from core.huggingface import save_pending_downloads

            save_pending_downloads()
        except Exception:
            pass
        _stop_hf_engine_workers()
        _stop_gateway_server()
        _release_gpu_on_shutdown()


def _stop_hf_engine_workers() -> None:
    """Stop HF engine workers spawned by this API process (e.g. transformers)."""
    try:
        from core.runtimes import get_runtime_adapter

        for runtime_id in ('transformers',):
            adapter = get_runtime_adapter(runtime_id)
            stop_fn = getattr(adapter, 'stop', None) if adapter is not None else None
            if callable(stop_fn):
                stop_fn()
    except Exception:
        pass


def _start_gateway_server() -> None:
    """Spawn the OpenAI gateway on gateway_port (default 8001) in a daemon thread."""
    global _GATEWAY_SERVER, _GATEWAY_THREAD, _GATEWAY_ERROR
    _GATEWAY_ERROR = ''
    try:
        import uvicorn

        from api.gateway import gateway_app

        cfg = load_config()
        port = int(cfg.get('gateway_port') or 8001)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            _GATEWAY_ERROR = f'gateway port {port} is unavailable: {exc}'
            _GATEWAY_SERVER = None
            _GATEWAY_THREAD = None
            return
        finally:
            probe.close()
        server = uvicorn.Server(uvicorn.Config(gateway_app, host='127.0.0.1', port=port, log_level='warning'))
        _GATEWAY_SERVER = server
        def run_gateway() -> None:
            global _GATEWAY_ERROR
            try:
                server.run()
                if not getattr(server, 'started', False) and not server.should_exit:
                    _GATEWAY_ERROR = f'gateway stopped before becoming ready on port {port}'
            except Exception as exc:
                _GATEWAY_ERROR = str(exc)

        thread = threading.Thread(target=run_gateway, name='console-gateway', daemon=True)
        thread.start()
        _GATEWAY_THREAD = thread
    except Exception as exc:
        _GATEWAY_ERROR = str(exc)
        # The gateway is a convenience; never block console boot on it.
        _GATEWAY_SERVER = None
        _GATEWAY_THREAD = None


def _stop_gateway_server() -> None:
    server = _GATEWAY_SERVER
    if server is not None:
        try:
            server.should_exit = True
        except Exception:
            pass


def _write_runtime_process_manifest() -> None:
    """Write process-identity + per-runtime bundle manifests at boot."""
    try:
        from core.config import ensure_runtime_entry, load_config
        from core.runtimes import write_bundle_manifests, write_process_tokens_manifest

        # Fresh installs / first run need a persistent runtime entry for the
        # faster-whisper and vibevoice adapters so the Speech & runtimes panel
        # lists them and their settings survive restarts. Idempotent.
        ensure_runtime_entry(
            'faster-whisper',
            label='Faster-Whisper STT',
            cfg=load_config(),
        )
        ensure_runtime_entry(
            'vibevoice',
            label='VibeVoice TTS',
            cfg=load_config(),
        )
        ensure_runtime_entry(
            'transformers',
            label='Transformers (PyTorch)',
            cfg=load_config(),
        )
        ensure_runtime_entry(
            'vllm',
            label='vLLM',
            cfg=load_config(),
        )
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

    def autostart_hf_engines() -> None:
        """Bring up installed HF engine workers that have an idle server mode.

        After a fresh install the Engines tab must show Transformers on and
        ready — models still load into GPU memory only on explicit demand.
        """
        time.sleep(3.0)  # let restore_engines adopt/stop ports first
        try:
            from core.runtimes import get_runtime_adapter

            cfg = load_config()
            profiles = {str(r.get('runtime_id') or ''): r for r in list_runtimes(cfg)}
            for runtime_id in ('transformers',):
                profile = profiles.get(runtime_id) or {}
                if profile and profile.get('enabled', True) is False:
                    continue
                adapter = get_runtime_adapter(runtime_id)
                if adapter is None:
                    continue
                is_installed = getattr(adapter, 'is_installed', None)
                if not callable(is_installed) or not is_installed():
                    continue
                health = adapter.health() if callable(getattr(adapter, 'health', None)) else {}
                if isinstance(health, dict) and health.get('running') is True:
                    continue
                start_fn = getattr(adapter, 'start', None)
                if not callable(start_fn):
                    continue
                result = start_fn(profile)
                logger.info('hf engine autostart %s: success=%s', runtime_id, result.get('success'))
        except Exception as exc:
            logger.exception('hf engine autostart failed: %s', exc)

    threading.Thread(target=autostart_hf_engines, daemon=True, name='hf-engine-autostart').start()

    def warm_catalog() -> None:
        try:
            from core.local_models import start_model_catalog_refresh_loop

            cfg = load_config()
            start_model_catalog_refresh_loop()
            warm_model_catalog(cfg=cfg)
        except Exception as exc:
            logger.exception('model catalog warm failed: %s', exc)

    threading.Thread(target=warm_catalog, daemon=True, name='model-catalog-warm').start()

    def auto_register() -> None:
        try:
            from core.auto_register import auto_register_console_models

            result = auto_register_console_models()
            if result.get('registered'):
                logger.info(
                    'auto-registered console models: %s',
                    ', '.join(str(row.get('server_id')) for row in result['registered']),
                )
                invalidate_model_catalog_cache()
        except Exception as exc:
            logger.exception('auto-register console models failed: %s', exc)

    threading.Thread(target=auto_register, daemon=True, name='auto-register-models').start()

    def warm_hf_catalog() -> None:
        try:
            from core.hf_catalog_cache import preload_hf_catalog_cache, start_hf_catalog_refresh_loop, warm_hf_catalog_cache

            preload_hf_catalog_cache()
            start_hf_catalog_refresh_loop()
            warm_hf_catalog_cache()
        except Exception as exc:
            logger.exception('hf catalog warm failed: %s', exc)

    threading.Thread(target=warm_hf_catalog, daemon=True, name='hf-catalog-warm').start()

    def resume_downloads() -> None:
        time.sleep(1.5)
        try:
            from core.huggingface import resume_interrupted_downloads

            result = resume_interrupted_downloads(cfg=load_config())
            if result.get('count'):
                logger.info('resumed %s interrupted download(s): %s', result['count'], result.get('resumed'))
        except Exception as exc:
            logger.exception('download resume failed: %s', exc)

    threading.Thread(target=resume_downloads, daemon=True, name='hf-download-resume').start()


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
    context_max: int | None = None
    idle_unload_minutes: int | None = None
    enabled: bool | None = None
    load_settings: dict[str, Any] | None = None
    inference_settings: dict[str, Any] | None = None
    mmproj_path: str | None = None


class ConfigPatch(BaseModel):
    ui_port: int | None = None
    gateway_port: int | None = Field(default=None, ge=1, le=65535)
    gateway_server_id: str | None = None
    dflash_root: str | None = None
    servers: list[dict[str, Any]] | None = None
    runtimes: list[dict[str, Any]] | None = None
    runtime_stop_others_on_load: bool | None = None
    cpu_slow_warn: bool | None = None
    hardware_settings: dict[str, Any] | None = None
    model_libraries: list[dict[str, Any]] | None = None
    ui_layout: dict[str, Any] | None = None
    context_auto_grow: bool | None = None
    context_max: int | None = Field(default=None, ge=2048, le=1048576)
    download_settings: dict[str, Any] | None = None
    remote_nodes: list[dict[str, Any]] | None = None


class DownloadSettingsPatch(BaseModel):
    parallel_connections: int | None = Field(default=None, ge=1, le=8)


class DownloadBenchmarkRequest(BaseModel):
    connections: list[int] | None = None
    test_mib: int = Field(default=32, ge=8, le=128)


class RemoteNodeCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    base_url: str = Field(..., min_length=8, max_length=512)
    api_token: str | None = Field(default=None, max_length=512)
    enabled: bool | None = True


class RemoteNodePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=512)
    api_token: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None


class NodeConnectTest(BaseModel):
    base_url: str = Field(..., min_length=8, max_length=512)
    api_token: str | None = Field(default=None, max_length=512)


class NodeConnectSshCommand(BaseModel):
    scenario: str = Field(..., min_length=4, max_length=32)
    ssh_user: str = Field(default='user', min_length=1, max_length=120)
    ssh_host: str = Field(..., min_length=1, max_length=255)
    local_bind_port: int | None = Field(default=None, ge=1024, le=65535)
    remote_console_port: int | None = Field(default=None, ge=1, le=65535)


class HardwarePatch(BaseModel):
    gpu_strategy: str | None = None
    enabled_gpu_indices: list[int] | None = None
    limit_offload_dedicated_vram: bool | None = None
    offload_kv_cache_to_gpu: bool | None = None


class HfDownloadRequest(BaseModel):
    repo_id: str = Field(..., min_length=3)
    filename: str = Field(..., min_length=4)
    library_id: str | None = None


class HfInstallRequest(BaseModel):
    """Search Hugging Face, download model files, and optionally load into an engine."""
    query: str | None = Field(default=None, max_length=200)
    repo_id: str | None = Field(default=None, max_length=200)
    filename: str | None = Field(default=None, max_length=260)
    category: str = Field(default='supported', max_length=80)
    sort: str = Field(default='downloads', max_length=40)
    search_limit: int = Field(default=25, ge=1, le=50)
    result_index: int = Field(default=0, ge=0, le=49)
    library_id: str | None = None
    download_all_shards: bool = True
    wait: bool = True
    wait_timeout_seconds: int = Field(default=3600, ge=10, le=7200)
    load: bool = True
    server_id: str | None = None
    context_size: int | None = Field(default=None, ge=2048, le=1048576)
    load_settings: dict[str, Any] | None = None
    inference_settings: dict[str, Any] | None = None


class VisionSetupRequest(BaseModel):
    model_path: str = Field(..., min_length=1)
    server_id: str | None = None


class ModelLoadRequest(BaseModel):
    path: str = Field(..., min_length=1)
    server_id: str | None = None
    model_id: str | None = None
    runtime_id: str | None = None
    context_size: int | None = Field(default=None, ge=2048, le=1048576)
    load_settings: dict[str, Any] | None = None
    inference_settings: dict[str, Any] | None = None


class LibraryImportRequest(BaseModel):
    path: str = Field(..., min_length=1)
    preset: str = Field(default='custom')
    mode: str = Field(default='link', pattern='^(link|copy|move)$')
    overwrite: bool = Field(default=False)


class PresetsImportBody(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)


class SetupCompleteRequest(BaseModel):
    libraries: list[dict[str, Any]] = Field(default_factory=list)


class ServerLoadRequest(BaseModel):
    context_size: int | None = None
    load_settings: dict[str, Any] | None = None
    inference_settings: dict[str, Any] | None = None
    model_path: str | None = None
    model_id: str | None = None
    skip_draft: bool | None = None


class GpuProcessUnload(BaseModel):
    api_url: str | None = None
    model_id: str | None = None


class StackReplaceDraftRequest(BaseModel):
    draft_path: str = Field(..., min_length=1)


class ServerCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    target_path: str = Field(..., min_length=1)
    draft_path: str = Field(..., min_length=1)
    profile: str | None = Field(default=None, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    model_id: str | None = Field(default=None, max_length=120)
    id: str | None = Field(default=None, max_length=80)
    context_size: int | None = Field(default=None, ge=2048, le=262144)
    copy_to_console: bool = False
    copy_mode: str | None = Field(default=None, pattern='^(copy|move|none)$')
    overwrite: bool = Field(default=False)


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
    preset: str = Field(default='', max_length=40)
    load_settings: dict[str, Any] | None = None


class RuntimeInstallRequest(BaseModel):
    torch_variant: str = Field(default='auto', max_length=20)
    backend: str = Field(default='auto', max_length=20)


class RuntimeUnloadRequest(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)
    ollama_model: str | None = Field(default=None, max_length=200)


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


def _hf_engine_adapter(server_id: str) -> Any:
    """Runtime adapter behind a synthetic Engines row (vllm / transformers)."""
    sid = str(server_id or '').strip().lower()
    if sid not in ('vllm', 'transformers'):
        return None
    from core.runtimes import get_runtime_adapter

    return get_runtime_adapter(sid)


def _hf_engine_profile(cfg: dict[str, Any], runtime_id: str) -> dict[str, Any]:
    for entry in list_runtimes(cfg):
        if str(entry.get('runtime_id') or '') == runtime_id:
            return entry
    return {}


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
        'process_root': console_root,
        'console_root': configured_root,
        'config_path': str((ROOT / 'config.json').resolve()),
        'ui_url': f'http://127.0.0.1:{int(cfg.get("ui_port") or 8900)}/',
        'shell_version': os.environ.get('DFLASH_CONSOLE_SHELL_VERSION', ''),
        # Developer server = served from a git checkout AND started from a
        # terminal (the installed app's Electron shell always sets
        # DFLASH_CONSOLE_SHELL_VERSION, even when it runs the dev checkout root).
        'dev_server': bool(
            (ROOT / '.git').is_dir()
            and not os.environ.get('DFLASH_CONSOLE_SHELL_VERSION', '').strip()
        ),
        'boot_id': _BOOT_ID,
        'boot_at': _BOOT_AT,
        'ui_version': _ui_version(),
        'setup_complete': is_setup_complete(cfg),
    }


@app.post('/api/shutdown')
async def api_shutdown() -> dict[str, Any]:
    """Gracefully stop the Console API (used by the Electron shell on quit).

    Stops the OpenAI gateway, releases managed engines, then exits the process.
    The response is returned first; shutdown runs a moment later in a thread so
    the caller sees success before the port is released.
    """
    def _shutdown_worker() -> None:
        try:
            time.sleep(0.3)  # let the HTTP response flush first
            _stop_gateway_server()
            _release_gpu_on_shutdown()
        finally:
            os._exit(0)

    threading.Thread(target=_shutdown_worker, name='console-shutdown', daemon=True).start()
    return {'success': True, 'app': 'DFlash Console', 'message': 'shutting down'}


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


@app.get('/api/gateway')
def gateway_status() -> dict[str, Any]:
    """OpenAI gateway info: port, base URL, default engine, and live routes."""
    cfg = load_config()
    port = int(cfg.get('gateway_port') or 8001)
    from api.gateway import gateway_app

    routes = sorted({
        str(getattr(route, 'path', ''))
        for route in gateway_app.routes
        if str(getattr(route, 'path', '')).startswith('/')
    })
    return {
        'success': True,
        'port': port,
        'url': f'http://127.0.0.1:{port}/v1',
        'running': bool(_GATEWAY_THREAD is not None and _GATEWAY_THREAD.is_alive()),
        'default_server_id': str(cfg.get('gateway_server_id') or ''),
        'error': _GATEWAY_ERROR,
        'routes': routes,
    }


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
    if 'download_settings' in data and isinstance(data['download_settings'], dict):
        cfg['download_settings'] = normalize_download_settings({
            **cfg.get('download_settings', {}),
            **data['download_settings'],
        })
        data.pop('download_settings')
    if 'remote_nodes' in data and isinstance(data['remote_nodes'], list):
        cfg['remote_nodes'] = normalize_remote_nodes(data['remote_nodes'])
        data.pop('remote_nodes')
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


@app.get('/api/setup/scan')
def setup_scan() -> dict[str, Any]:
    from core.setup import build_setup_scan_payload

    return build_setup_scan_payload(cfg=load_config())


@app.post('/api/setup/complete')
def setup_complete(body: SetupCompleteRequest) -> dict[str, Any]:
    from core.setup import complete_setup

    cfg = complete_setup(body.libraries, cfg=load_config())
    _save_config_checked(cfg)
    invalidate_model_catalog_cache()
    _invalidate_status_cache()
    return {
        'success': True,
        'setup_complete': True,
        'model_libraries': cfg.get('model_libraries') or [],
    }


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
        return import_library_folder(
            body.path,
            preset=body.preset,
            mode=body.mode,
            overwrite=body.overwrite,
            cfg=load_config(),
        )
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
    from core.hardware_apply import hardware_reload_plan

    cfg = load_config()
    previous = normalize_hardware_settings(cfg.get('hardware_settings'))
    merged = normalize_hardware_settings({
        **previous,
        **body.model_dump(exclude_none=True),
    })
    settings_changed = merged != previous
    cfg['hardware_settings'] = merged
    _save_config_checked(cfg)
    # A running engine may have been adopted after a Console restart, or a
    # concurrent status poll may have recorded the new desired signature before
    # this plan runs. When hardware settings actually change, reload every
    # loaded engine instead of trusting that in-memory signature.
    plan = hardware_reload_plan(cfg, force_reload=settings_changed)
    return {'success': True, 'hardware_settings': merged, **plan}


@app.get('/api/download-settings')
def get_download_settings() -> dict[str, Any]:
    cfg = load_config()
    settings = normalize_download_settings(cfg.get('download_settings'))
    return {'success': True, 'download_settings': settings}


@app.patch('/api/download-settings')
def patch_download_settings(body: DownloadSettingsPatch) -> dict[str, Any]:
    cfg = load_config()
    merged = normalize_download_settings({
        **cfg.get('download_settings', {}),
        **body.model_dump(exclude_none=True),
    })
    cfg['download_settings'] = merged
    _save_config_checked(cfg)
    return {'success': True, 'download_settings': merged}


@app.post('/api/download-settings/benchmark')
def benchmark_download_settings(body: DownloadBenchmarkRequest) -> dict[str, Any]:
    from core.huggingface import benchmark_download_connections

    result = benchmark_download_connections(body.connections, test_mib=body.test_mib)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'benchmark failed')
    return result


@app.get('/api/nodes')
def list_remote_nodes(fresh: bool = Query(default=False)) -> dict[str, Any]:
    from core.remote_nodes import list_nodes_with_health

    nodes = list_nodes_with_health(fresh=fresh)
    return {'success': True, 'nodes': nodes, 'count': len(nodes)}


@app.get('/api/nodes/connect/wizard')
def node_connect_wizard() -> dict[str, Any]:
    from core.node_connect import build_connect_wizard

    return build_connect_wizard(cfg=load_config())


@app.post('/api/nodes/connect/test')
def node_connect_test(body: NodeConnectTest) -> dict[str, Any]:
    from core.node_connect import probe_console_url

    return probe_console_url(body.base_url, api_token=body.api_token or '')


@app.post('/api/nodes/connect/ssh-command')
def node_connect_ssh_command(body: NodeConnectSshCommand) -> dict[str, Any]:
    from core.node_connect import build_ssh_tunnel_commands

    scenario = str(body.scenario or '').strip().lower()
    if scenario not in {'reach_remote', 'share_local'}:
        raise HTTPException(status_code=400, detail='scenario must be reach_remote or share_local')
    cfg = load_config()
    ui_port = int(cfg.get('ui_port') or 8900)
    return {
        'success': True,
        **build_ssh_tunnel_commands(
            scenario=scenario,
            ui_port=ui_port,
            ssh_user=body.ssh_user,
            ssh_host=body.ssh_host,
            local_bind_port=body.local_bind_port,
            remote_console_port=body.remote_console_port,
        ),
    }


@app.post('/api/nodes')
def create_remote_node(body: RemoteNodeCreate) -> dict[str, Any]:
    from core.remote_nodes import add_remote_node, check_remote_node_health

    try:
        node = add_remote_node(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    health = check_remote_node_health(node)
    return {
        'success': True,
        'node': {
            'id': node.get('id'),
            'label': node.get('label'),
            'base_url': node.get('base_url'),
            'enabled': node.get('enabled') is not False,
            'has_token': bool(str(node.get('api_token') or '').strip()),
            **health,
        },
    }


@app.patch('/api/nodes/{node_id}')
def patch_remote_node(node_id: str, body: RemoteNodePatch) -> dict[str, Any]:
    from core.remote_nodes import check_remote_node_health, get_remote_node, update_remote_node

    updated = update_remote_node(node_id, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail='node not found')
    health = check_remote_node_health(updated)
    return {
        'success': True,
        'node': {
            'id': updated.get('id'),
            'label': updated.get('label'),
            'base_url': updated.get('base_url'),
            'enabled': updated.get('enabled') is not False,
            'has_token': bool(str(updated.get('api_token') or '').strip()),
            **health,
        },
    }


@app.delete('/api/nodes/{node_id}')
def delete_remote_node(node_id: str) -> dict[str, Any]:
    from core.remote_nodes import remove_remote_node

    if not remove_remote_node(node_id):
        raise HTTPException(status_code=404, detail='node not found')
    return {'success': True, 'id': node_id}


@app.post('/api/nodes/{node_id}/health')
def remote_node_health(node_id: str) -> dict[str, Any]:
    from core.remote_nodes import check_remote_node_health, get_remote_node

    node = get_remote_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail='node not found')
    health = check_remote_node_health(node)
    return {'success': True, 'node_id': node_id, **health}


@app.post('/api/nodes/{node_id}/v1/chat/completions')
async def proxy_remote_node_chat(node_id: str, request: Request):
    import json
    import urllib.error

    from core.chat_proxy import open_upstream_chat_stream, upstream_chat_completion, wants_stream
    from core.remote_nodes import _node_headers, get_remote_node, node_chat_url

    node = get_remote_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail='node not found')
    if node.get('enabled') is False:
        raise HTTPException(status_code=400, detail='node is disabled')

    raw = await request.body()
    content_type = request.headers.get('content-type') or 'application/json'
    url = node_chat_url(node)
    headers = _node_headers(node)

    if wants_stream(raw):
        try:
            media_type, chunks, close_upstream = await open_upstream_chat_stream(
                url,
                raw,
                content_type=content_type,
                server_id=f'node:{node_id}',
                extra_headers=headers,
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise HTTPException(status_code=exc.code, detail=detail) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        async def stream_body():
            try:
                async for chunk in chunks:
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                if close_upstream:
                    try:
                        close_upstream()
                    except Exception:
                        pass

        return StreamingResponse(
            stream_body(),
            media_type=media_type,
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            },
        )

    try:
        status, payload = upstream_chat_completion(
            url,
            raw,
            content_type=content_type,
            extra_headers=headers,
            server_id=f'node:{node_id}',
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if status >= 400:
        raise HTTPException(status_code=status, detail=payload)
    return JSONResponse(content=payload, status_code=status)


@app.get('/api/hardware/fit-budget')
def hardware_fit_budget() -> dict[str, Any]:
    """VRAM budget used by the Model catalog “fits this PC” badge and filter."""
    from core.hf_model_fit import machine_fit_budget_gb

    return {'success': True, **machine_fit_budget_gb(cfg=load_config())}


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
    from core.hf_catalog_cache import get_or_fetch_detail
    from core.huggingface import get_model_detail

    result = get_or_fetch_detail(
        repo_id=repo_id,
        category=category,
        fetcher=lambda: get_model_detail(repo_id, category=category),
    )
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
def hf_downloads(
    active: bool = Query(default=False),
    discover: bool = Query(default=False),
    console_only: bool = Query(default=True),
) -> dict[str, Any]:
    from core.huggingface import list_download_jobs

    return list_download_jobs(active_only=active, discover=discover, console_only=console_only)


@app.delete('/api/hf/downloads/{job_id}')
def hf_download_clear_one(job_id: str) -> dict[str, Any]:
    from core.huggingface import clear_download_job

    result = clear_download_job(job_id)
    if not result.get('success'):
        error = str(result.get('error') or 'unknown job')
        status = 409 if 'active' in error else 404
        raise HTTPException(status_code=status, detail=error)
    return result


@app.delete('/api/hf/downloads')
def hf_download_clear_history() -> dict[str, Any]:
    from core.huggingface import clear_download_history

    result = clear_download_history()
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error') or 'clear failed')
    return result


@app.post('/api/hf/install')
def hf_install(body: HfInstallRequest) -> dict[str, Any]:
    """Search Hugging Face, download the selected GGUF, and optionally load it."""
    from core.hf_install import execute_hf_install

    result = execute_hf_install(
        query=body.query,
        repo_id=body.repo_id,
        filename=body.filename,
        category=body.category,
        sort=body.sort,
        search_limit=body.search_limit,
        result_index=body.result_index,
        library_id=body.library_id,
        download_all_shards=body.download_all_shards,
        wait=body.wait,
        wait_timeout_seconds=body.wait_timeout_seconds,
        load=body.load,
        server_id=body.server_id,
        context_size=body.context_size,
        load_settings=body.load_settings,
        inference_settings=body.inference_settings,
        cfg=load_config(),
    )
    if body.load and result.get('load'):
        _invalidate_status_cache()
    return result


@app.get('/api/status/loaded')
def status_loaded() -> dict[str, Any]:
    """Currently loaded models across engines and non-llama runtimes."""
    from core.status_report import get_loaded_models_payload

    return get_loaded_models_payload(cfg=load_config())


@app.get('/api/status/report')
async def status_report(include_external: bool = Query(default=True)) -> dict[str, Any]:
    """Full machine report: CPU/RAM/VRAM, engines, runtimes, and loaded models."""
    from core.status_report import get_status_report_payload

    return await asyncio.to_thread(
        get_status_report_payload,
        cfg=load_config(),
        include_external=include_external,
    )


def _model_load_route(row: dict[str, Any]) -> dict[str, Any]:
    """How to load a catalog row through the API (used by the docs + UI)."""
    path = str(row.get('path') or '')
    modality = str(row.get('modality') or 'llm')
    runtime_id = str(row.get('runtime_id') or 'llama-server')
    body: dict[str, Any] = {'path': path}
    server_id = str(row.get('server_id') or '')
    if runtime_id == 'llama-server' and server_id:
        return {
            'method': 'POST',
            'path': f'/api/servers/{server_id}/load',
            'body': {'model_path': path},
            'hint': f'loads on server {server_id}; then use /api/servers/{server_id}/v1/chat/completions',
        }
    stt_route = 'faster-whisper' if runtime_id == 'faster-whisper' else 'stt'
    tts_route = 'vibevoice' if runtime_id == 'vibevoice' else 'piper'
    return {
        'method': 'POST',
        'path': '/api/models/load',
        'body': body,
        'hint': {
            'speech-to-text': f'then POST /api/runtimes/{stt_route}/v1/audio/transcriptions (multipart file=audio)',
            'text-to-speech': f'then POST /api/runtimes/{tts_route}/v1/audio/speech {{"input": "...", "voice": "..."}}',
            'embedding': 'then POST /api/servers/{server_id}/v1/embeddings {"input": [...]}',
            'llm': (
                f'then POST /api/servers/{runtime_id}/v1/chat/completions'
                if runtime_id in {'vllm', 'transformers'}
                else 'then POST /api/servers/{server_id}/v1/chat/completions'
            ),
        }.get(modality, ''),
    }


@app.get('/api/models')
def models_catalog(
    quick: bool = Query(default=False, description='Engine profiles only; skip the full disk library.'),
    refresh: bool = Query(default=False, description='Rescan model folders before listing.'),
    source: str = Query(
        default='',
        max_length=40,
        description='Filter by source: ollama, lmstudio, dflash, or library. Empty = full PC library.',
    ),
) -> dict[str, Any]:
    from core.local_models import invalidate_model_catalog_cache, model_matches_source

    cfg = load_config()
    if refresh:
        invalidate_model_catalog_cache()
        payload = list_local_models(cfg=cfg, scan_disk=not quick, force_refresh=True)
    else:
        payload = list_local_models(cfg=cfg, scan_disk=not quick)
    models = [row for row in (payload.get('models') or []) if isinstance(row, dict)]
    source_key = source.strip() if isinstance(source, str) else ''
    if source_key:
        models = [row for row in models if model_matches_source(row, source_key)]
        payload['source'] = source_key.lower()
    for row in models:
        row['load_route'] = _model_load_route(row)
    payload['models'] = models
    payload['total_count'] = len(models)
    return payload


@app.post('/api/models/load')
def model_load(body: ModelLoadRequest) -> dict[str, Any]:
    """Unified loader — load ANY catalog model by path.

    Dispatches by the catalog row's modality/runtime_id:
      - speech-to-text -> whisper runtime (runtimes/stt)
      - text-to-speech -> piper runtime (voice ready; synthesis via proxy)
      - llm / embedding / vision / ocr / translation -> a llama-server engine
    For llama-server loads you may pass ``server_id`` to pick the engine;
    otherwise the first enabled engine that matches the modality is used.
    """
    from core.catalog_load import execute_catalog_load

    result = execute_catalog_load(
        path=body.path,
        model_id=body.model_id,
        server_id=body.server_id,
        context_size=body.context_size,
        load_settings=body.load_settings,
        inference_settings=body.inference_settings,
        requested_runtime_id=body.runtime_id,
        loaded_by='api:/api/models/load',
        cfg=load_config(),
    )
    _invalidate_status_cache()
    return result


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
        mmproj = str(plan.get('mmproj_path') or '').strip()
        if body.server_id and mmproj:
            wired = wire_vision(
                model_path=body.model_path,
                mmproj_path=mmproj,
                server_id=body.server_id,
            )
            if wired.get('success'):
                return {**plan, **wired, 'wired': True}
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


def _adapter_engine_rows() -> list[dict[str, Any]]:
    """Synthetic Engines/Playground rows for vLLM and Transformers."""
    from pathlib import Path as _Path

    from core.runtimes import get_runtime_adapter

    rows: list[dict[str, Any]] = []
    for runtime_id, label in (('vllm', 'vLLM'), ('transformers', 'Transformers')):
        adapter = get_runtime_adapter(runtime_id)
        if adapter is None or not callable(getattr(adapter, 'health', None)):
            continue
        health = adapter.health()
        model = str(health.get('active_model') or '')
        name = _Path(model).name if model else ''
        running = health.get('running') is True
        rows.append({
            'id': runtime_id,
            'label': label,
            'status': 'loaded' if running and name else ('running' if running else 'stopped'),
            'runtime_id': runtime_id,
            'enabled': True,
            'engine_on': True,
            'running': running,
            'port': int(health.get('port') or 0),
            'host': str(health.get('host') or '127.0.0.1'),
            'api_url': str(health.get('api_url') or ''),
            'loaded_models': [name] if name else [],
            'active_model_id': name,
            'model_id': name,
            'loaded': bool(running and name),
        })
    return rows


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
        adapter_rows = _adapter_engine_rows()
        if adapter_rows:
            existing = {str(row.get('id') or '') for row in (payload.get('servers') or []) if isinstance(row, dict)}
            extra = [row for row in adapter_rows if str(row.get('id') or '') not in existing]
            payload['servers'] = list(payload.get('servers') or []) + extra
            payload['all_servers'] = list(payload.get('all_servers') or []) + extra
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
        adapter = get_runtime_adapter(runtime_id)
        health = adapter.health() if adapter is not None and callable(getattr(adapter, 'health', None)) else {}
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
            'adapter_installed': adapter is not None,
            # STT-specific settings (faster-whisper + whisper.cpp)
            'compute_type': runtime.get('compute_type') or 'auto',
            'language': runtime.get('language') or '',
            'task': runtime.get('task') or 'transcribe',
            'beam_size': runtime.get('beam_size') or 5,
            'vad_filter': runtime.get('vad_filter') is True,
            'temperature': runtime.get('temperature'),
            'cpu_threads': runtime.get('cpu_threads') or 0,
            'num_workers': runtime.get('num_workers') or 0,
            # Live adapter health (running state, active model, device)
            'running': health.get('running') is True,
            'active_model': health.get('active_model') or '',
            'active_device': health.get('device') or '',
            'active_compute_type': health.get('compute_type') or '',
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


@app.get('/api/components')
def components_hub() -> dict[str, Any]:
    """Engines and runtimes users can install or update (vLLM, Transformers, speech bundles)."""
    from core.components_hub import list_components_payload

    return list_components_payload()


@app.get('/api/runtimes/{runtime_id}/install')
def runtime_install_status(runtime_id: str) -> dict[str, Any]:
    rid = str(runtime_id or '').strip().lower()
    if rid == 'transformers':
        from core.transformers_runtime_install import install_status

        return {'success': True, 'runtime_id': rid, **install_status()}
    if rid == 'vllm':
        from core.vllm_runtime_install import install_status

        return {'success': True, 'runtime_id': rid, **install_status()}
    raise HTTPException(status_code=404, detail=f'no on-demand installer for runtime: {runtime_id}')


@app.post('/api/runtimes/{runtime_id}/install')
def runtime_install_start(runtime_id: str, body: RuntimeInstallRequest | None = None) -> dict[str, Any]:
    rid = str(runtime_id or '').strip().lower()
    payload = body or RuntimeInstallRequest()
    if rid == 'transformers':
        from core.transformers_runtime_install import start_install

        result = start_install(torch_variant=payload.torch_variant or 'auto')
        return {'success': bool(result.get('success')), 'runtime_id': rid, **result}
    if rid == 'vllm':
        from core.vllm_runtime_install import start_install

        result = start_install(backend=payload.backend or 'auto')
        return {'success': bool(result.get('success')), 'runtime_id': rid, **result}
    raise HTTPException(status_code=404, detail=f'no on-demand installer for runtime: {runtime_id}')


@app.post('/api/runtimes/{runtime_id}/uninstall')
def runtime_uninstall(runtime_id: str) -> dict[str, Any]:
    rid = str(runtime_id or '').strip().lower()
    if rid == 'transformers':
        from core.transformers_runtime_install import uninstall

        result = uninstall()
        return {'success': bool(result.get('success')), 'runtime_id': rid, **result}
    if rid == 'vllm':
        from core.vllm_runtime_install import uninstall

        result = uninstall()
        return {'success': bool(result.get('success')), 'runtime_id': rid, **result}
    raise HTTPException(status_code=404, detail=f'runtime cannot be removed on demand: {runtime_id}')


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
    payload: dict[str, Any] = {'id': body.voice, 'path': body.path}
    if body.preset:
        payload['preset'] = body.preset
    if body.load_settings:
        payload['load_settings'] = dict(body.load_settings)
    result = load_fn(payload)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'load failed')
    return {'success': True, 'runtime_id': runtime_id, **result}


@app.post('/api/runtimes/{runtime_id}/unload')
def runtime_unload(runtime_id: str, body: RuntimeUnloadRequest | None = None) -> dict[str, Any]:
    adapter = _require_runtime_adapter(runtime_id)
    unload_fn = getattr(adapter, 'unload', None)
    if not callable(unload_fn):
        raise HTTPException(status_code=400, detail='adapter does not support unload')
    payload: dict[str, Any] = {}
    if body is not None:
        if body.ollama_model:
            payload['ollama_model'] = body.ollama_model
        if body.model_id:
            payload['model_id'] = body.model_id
    if runtime_id == 'ollama':
        return {'success': True, 'runtime_id': runtime_id, **unload_fn(payload or None)}
    return {'success': True, 'runtime_id': runtime_id, **unload_fn()}


@app.post('/api/runtimes/{runtime_id}/start')
def runtime_start(runtime_id: str) -> dict[str, Any]:
    """Bring up a server-mode runtime process (whisper). CLI runtimes (piper)
    are always ready and report ``started: false`` with no error."""
    adapter = _require_runtime_adapter(runtime_id)
    start_fn = getattr(adapter, 'start', None)
    if not callable(start_fn):
        return {'success': True, 'runtime_id': runtime_id, 'started': False, 'message': 'adapter has no persistent process (CLI mode is always ready)'}
    profile: dict[str, Any] = {}
    for entry in list_runtimes(load_config()):
        if str(entry.get('runtime_id') or '') == runtime_id:
            profile = entry
            break
    result = start_fn(profile)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'start failed')
    return {'success': True, 'runtime_id': runtime_id, 'started': True, **result}


@app.post('/api/runtimes/{runtime_id}/stop')
def runtime_stop(runtime_id: str) -> dict[str, Any]:
    """Stop a server-mode runtime process (whisper). CLI runtimes (piper) have
    no persistent process and report ``stopped: true`` with no error."""
    adapter = _require_runtime_adapter(runtime_id)
    stop_fn = getattr(adapter, 'stop', None)
    if not callable(stop_fn):
        return {'success': True, 'runtime_id': runtime_id, 'stopped': True, 'message': 'adapter has no persistent process'}
    result = stop_fn()
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'stop failed')
    return {'success': True, 'runtime_id': runtime_id, 'stopped': True, **result}


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
    server = get_server(cfg, server_id)
    if not server:
        # Synthetic Engines rows (vllm / transformers) are not config servers —
        # start the runtime adapter worker instead of 404ing.
        adapter = _hf_engine_adapter(server_id)
        if adapter is None:
            raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')
        start_fn = getattr(adapter, 'start', None)
        if not callable(start_fn):
            raise HTTPException(status_code=400, detail='engine cannot be started')
        result = start_fn(_hf_engine_profile(cfg, str(server_id).strip().lower()))
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'listen failed')
        _invalidate_status_cache()
        return result
    server = normalize_server(server)
    embedding = is_embedding_server(server)
    result = start_embedding_server(server, cfg=cfg) if embedding else start_router_listener(server, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'listen failed')
    note_engine_idle(server_id)
    update_server_runtime(server_id, loaded_by=_request_client_label(request))
    _invalidate_status_cache()
    return result


def _loaded_per_slot_context(server: dict[str, Any]) -> int:
    """Return the live per-slot context (``n_ctx``) of a loaded engine, or 0."""
    import json as _json
    import urllib.request

    from core.runtime import api_base_url

    api_url = str(server.get('api_url') or '').strip()
    base = api_base_url(api_url)
    if not base:
        return 0
    try:
        with urllib.request.urlopen(base.rstrip('/') + '/models', timeout=2.5) as resp:
            payload = _json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except Exception:
        return 0
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return 0
    model_id = str(server.get('model_id') or '').strip()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if model_id and str(entry.get('id') or '') != model_id:
            continue
        meta = entry.get('meta')
        if isinstance(meta, dict):
            try:
                nctx = int(meta.get('n_ctx') or 0)
            except (TypeError, ValueError):
                nctx = 0
            if nctx > 0:
                return nctx
    return 0


def _grow_context_for_chat(
    server_id: str,
    server: dict[str, Any],
    cfg: dict[str, Any],
    required: int,
    *,
    client_label: str = 'DFlash Console',
) -> dict[str, Any]:
    """Reload a model with a larger per-slot context to fit ``required`` tokens.

    Implements the auto-grow rule: requests that need more context than the
    model is loaded with cause a reload to the larger context (capped by
    ``context_max``).  Parallel slots are preserved where VRAM allows, reducing
    them if the total context would exceed the cap.
    """
    import time

    from core.config import normalize_load_settings
    from core.engine_state import note_engine_loaded
    from core.runtime import build_server_status
    from core.server_boot import reload_server

    load = normalize_load_settings(server.get('load_settings'))
    parallel = max(1, int(load.get('parallel_slots') or 1))
    configured_ctx = max(2048, int(server.get('context_size') or 8192))
    # Per-server hard limit: API requests may never grow the context beyond
    # this.  Falls back to the global context_max, then the default.
    max_total = max(2048, int(server.get('context_max') or cfg.get('context_max') or 131072))
    current_per_slot = _loaded_per_slot_context(server)
    per_slot = max(required, current_per_slot, configured_ctx // parallel)
    total = per_slot * parallel
    if total > max_total:
        parallel = max(1, max_total // per_slot)
        total = per_slot * parallel
    if total > max_total:
        # Hard limit reached even at parallel=1: cap the per-slot context so
        # API requests can never grow the model beyond the limit.
        per_slot = max_total
        total = max_total
    if total <= configured_ctx and parallel <= int(load.get('parallel_slots') or 4):
        return build_server_status(server, cfg=cfg)

    _persist_server_merge(
        cfg,
        server_id,
        {'context_size': total, 'load_settings': {'parallel_slots': parallel}},
    )
    updated = _require_server(cfg, server_id)
    result = reload_server(updated, cfg=cfg)
    if not result.get('success'):
        raise HTTPException(
            status_code=503,
            detail={
                'error': 'context_grow_failed',
                'message': str(result.get('error') or 'failed to reload model with larger context'),
            },
        )
    note_engine_loaded(server_id, loaded_by=client_label)
    _invalidate_status_cache()
    deadline = time.time() + 180.0
    while time.time() < deadline:
        live = build_server_status(updated, cfg=cfg)
        if live.get('status') == 'loaded' and live.get('loaded_models'):
            return live
        if live.get('status') in {'booting', 'running'}:
            time.sleep(2.0)
            continue
        return live
    raise HTTPException(
        status_code=503,
        detail={'error': 'model_not_loaded', 'message': 'reload with larger context did not complete'},
    )


def _configured_per_slot(server: dict[str, Any]) -> int:
    """Per-slot context the server profile is configured for (ctx // parallel)."""
    from core.config import normalize_load_settings

    load = normalize_load_settings(server.get('load_settings'))
    parallel = max(1, int(load.get('parallel_slots') or 1))
    return max(2048, int(server.get('context_size') or 8192)) // parallel


def _ensure_server_ready_for_chat(
    server_id: str,
    server: dict[str, Any],
    cfg: dict[str, Any],
    *,
    client_label: str = 'DFlash Console',
    required_context: int | None = None,
) -> dict[str, Any]:
    """JIT-load configured checkpoint when chat arrives — only if engine is on.

    Implements context auto-grow: when a request needs more per-slot context
    than the loaded model provides, reload with a larger context.  Requests
    that fit share the already-loaded model (no reload).
    """
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
        # Already loaded.  Auto-grow if this request needs more context than
        # the loaded model provides; otherwise share the model as-is.
        if required_context and cfg.get('context_auto_grow') is not False:
            loaded_ctx = _loaded_per_slot_context(server)
            if loaded_ctx and required_context > loaded_ctx:
                return _grow_context_for_chat(server_id, server, cfg, required_context, client_label=client_label)
        return live

    if live.get('status') == 'booting':
        return _wait_until_loaded()

    # Not loaded (or the router is up without a loaded checkpoint).  If this
    # request needs more context than the configured per-slot context, grow it
    # now so the JIT load (or router restart) uses the larger size.
    if required_context and cfg.get('context_auto_grow') is not False:
        configured_per_slot = _configured_per_slot(server)
        if configured_per_slot and required_context > configured_per_slot:
            return _grow_context_for_chat(server_id, server, cfg, required_context, client_label=client_label)

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
    from core.server_boot import checkpoint_already_loaded, find_target_loaded_elsewhere

    cfg = load_config()
    server = _require_server(cfg, server_id)
    candidate = dict(server)
    if model_path.strip():
        candidate['adhoc_model_path'] = model_path.strip()
    if model_id.strip():
        candidate['model_id'] = model_id.strip()
    already = checkpoint_already_loaded(
        candidate,
        cfg=cfg,
        model_path=model_path.strip() or None,
        model_id=model_id.strip() or None,
    )
    if already:
        model_name = str(already.get('model') or candidate.get('label') or 'Model')
        return {
            'success': True,
            'server_id': server_id,
            'level': 'already_loaded',
            'already_loaded': True,
            'message': f'{model_name} is already loaded on this engine.',
            'model': already.get('model'),
            'port': already.get('port'),
        }
    elsewhere = find_target_loaded_elsewhere(
        candidate,
        cfg=cfg,
        model_path=model_path.strip() or None,
        exclude_server_id=server_id,
    )
    if elsewhere:
        host_label = str(elsewhere.get('label') or elsewhere.get('server_id') or 'another engine')
        port = int(elsewhere.get('port') or 0)
        port_text = f' (port {port})' if port else ''
        return {
            'success': True,
            'server_id': server_id,
            'level': 'already_loaded',
            'already_loaded': True,
            'already_loaded_elsewhere': True,
            'message': (
                f'This model is already loaded on {host_label}{port_text}. '
                'Unload it before loading a second copy.'
            ),
            **elsewhere,
        }
    return {
        'success': True,
        'server_id': server_id,
        **assess_load(candidate, cfg=cfg),
    }


@app.post('/api/servers/{server_id}/load')
def server_load(server_id: str, request: Request, body: ServerLoadRequest | None = None) -> dict[str, Any]:
    from core.engine_state import note_engine_loaded
    from core.memory_guardrails import assess_load
    from core.server_boot import checkpoint_already_loaded, find_target_loaded_elsewhere

    cfg = load_config()
    server = _require_server(cfg, server_id)
    model_path = None
    model_id = None
    skip_draft = False
    if body:
        patch = body.model_dump(exclude_none=True)
        model_path = patch.pop('model_path', None)
        model_id = patch.pop('model_id', None)
        skip_draft = bool(patch.pop('skip_draft', False))
        if patch:
            server = _persist_server_merge(cfg, server_id, patch)
    if model_path:
        server = {**server, 'adhoc_model_path': model_path}
    already = checkpoint_already_loaded(server, cfg=cfg, model_path=model_path, model_id=model_id)
    if already:
        note_engine_loaded(server_id, loaded_by=_request_client_label(request))
        _invalidate_status_cache()
        return already
    elsewhere = find_target_loaded_elsewhere(
        server,
        cfg=cfg,
        model_path=model_path,
        exclude_server_id=server_id,
    )
    if elsewhere:
        host_label = str(elsewhere.get('label') or elsewhere.get('server_id') or 'another engine')
        port = int(elsewhere.get('port') or 0)
        port_text = f' (port {port})' if port else ''
        raise HTTPException(
            status_code=409,
            detail={
                'error': 'model_already_loaded_elsewhere',
                'message': (
                    f'This model is already loaded on {host_label}{port_text}. '
                    'Unload it before loading a second copy.'
                ),
                **elsewhere,
            },
        )
    check = assess_load(server, cfg=cfg)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))
    if _auto_stop_other_servers(cfg, server_id):
        _invalidate_status_cache()
    result = load_server_checkpoint(
        server,
        cfg=cfg,
        model_path=model_path,
        model_id=model_id,
        skip_draft=skip_draft,
    )
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


async def _abort_upstream_when_client_disconnects(
    request: Request,
    *,
    server_id: str,
    api_url: str,
    model_id: str,
) -> None:
    """While a blocking proxy waits on llama, cancel upstream if the client goes away."""
    from core.chat_proxy import cancel_active_upstream_streams
    from core.inference_stats import abort_llama_processing_slots

    sid = str(server_id or '').strip()
    while True:
        if await request.is_disconnected():
            if sid:
                cancel_active_upstream_streams(sid)
                abort_llama_processing_slots(api_url, model_id=model_id)
            return
        await asyncio.sleep(0.05)


@app.post('/api/servers/{server_id}/cancel-inference')
def cancel_server_inference(server_id: str) -> dict[str, Any]:
    """Immediately abort all Console→llama chat streams for this engine."""
    from core.chat_proxy import cancel_active_upstream_streams
    from core.inference_stats import abort_llama_processing_slots, fetch_inference_stats, is_proxy_generating, mark_inference_end

    cfg = load_config()
    server = _require_server(cfg, server_id)
    closed = cancel_active_upstream_streams(server_id)
    from core.runtime import _SERVER_STATUS_CACHE

    cached = _SERVER_STATUS_CACHE.get(server_id) or {}
    model_id = str(
        cached.get('active_model_id')
        or server.get('model_id')
        or '',
    ).strip()
    api_url = str(server.get('api_url') or '')
    stats = fetch_inference_stats(api_url, server_id=server_id, model_id=model_id)
    still_generating = bool(stats.get('generating')) or any(
        row.get('generating') for row in (stats.get('slots') or []) if isinstance(row, dict)
    )
    erased_slots = 0
    if still_generating or closed <= 0:
        erased_slots = abort_llama_processing_slots(api_url, model_id=model_id)
    for _ in range(32):
        if not is_proxy_generating(server_id):
            break
        mark_inference_end(server_id)
    return {
        'success': True,
        'server_id': server_id,
        'closed_streams': closed,
        'erased_slots': erased_slots,
    }


@app.post('/api/servers/{server_id}/v1/chat/completions')
async def proxy_chat_completions(server_id: str, request: Request):
    import asyncio
    import json
    import urllib.error

    from core.chat_proxy import (
        apply_reasoning_policy,
        chat_upstream_read_timeout,
        empty_completion_guard,
        estimate_request_context,
        extract_stream_completion_stats,
        is_reasoning_only_chunk,
        open_upstream_chat_stream,
        sse_had_content_delta,
        sse_stream_complete,
        sse_stream_error_chunk,
        SSE_KEEPALIVE_COMMENT,
        upstream_chat_completion,
        wants_stream,
    )
    from core.inference_stats import mark_inference_end, mark_inference_start, note_completion_stats
    from core.local_models import model_has_reasoning
    from core.runtime import api_base_url, build_server_status

    cfg = load_config()
    adapter_id = str(server_id or '').strip().lower()
    if adapter_id in {'vllm', 'transformers'}:
        adapter = _require_runtime_adapter(adapter_id)
        health = adapter.health() if callable(getattr(adapter, 'health', None)) else {}
        if not health.get('running') or not health.get('api_url'):
            raise HTTPException(
                status_code=400,
                detail=f'{adapter_id} is not running. Load a model on the Models tab first.',
            )
        from pathlib import Path as _Path

        model_path = str(health.get('active_model') or '')
        model_name = _Path(model_path).name if model_path else ''
        served_id = model_path or model_name
        server = {
            'id': adapter_id,
            'api_url': str(health.get('api_url') or ''),
            'host': str(health.get('host') or '127.0.0.1'),
            'port': int(health.get('port') or 0),
            'model_id': served_id,
            'engine_on': True,
        }
        live = {
            'status': 'loaded',
            'active_model_id': served_id,
            'loaded_models': [served_id] if served_id else [],
        }
        raw = await request.body()
        try:
            body_json = json.loads(raw.decode('utf-8', errors='replace'))
        except Exception:
            body_json = None
        required_context = 0
    else:
        server = _require_server(cfg, server_id)
        raw = await request.body()
        try:
            body_json = json.loads(raw.decode('utf-8', errors='replace'))
        except Exception:
            body_json = None
        required_context = (
            estimate_request_context(body_json) if isinstance(body_json, dict) else 0
        )
        live = _ensure_server_ready_for_chat(
            server_id,
            server,
            cfg,
            client_label=_request_client_label(request),
            required_context=required_context or None,
        )

    api_url = str(server.get('api_url') or '')
    base = api_base_url(api_url)
    if not base:
        raise HTTPException(status_code=400, detail='engine api_url not configured')
    # Non-reasoning models never negotiate reasoning: strip reasoning_effort and
    # thinking toggles so the API returns the regular chat behaviour.
    raw = apply_reasoning_policy(raw, reasoning=model_has_reasoning(server))
    content_type = request.headers.get('content-type') or 'application/json'

    # llama-server validates the model name against its loaded checkpoint id.
    # Clients may send the configured profile alias (e.g. gemma-4-12b-it-qat)
    # while the engine loaded it under a sanitized filename
    # (gemma-4-12b-it-q4-k-m), which yields "model '…' not found". Rewrite the
    # body's model field to the engine's actual loaded id so any client works.
    upstream_model_id = (
        str(live.get('active_model_id') or '')
        or (str((live.get('loaded_models') or [''])[0]) if live.get('loaded_models') else '')
        or str(server.get('model_id') or '')
    ).strip()
    if upstream_model_id:
        try:
            body_json = json.loads(raw.decode('utf-8'))
        except Exception:
            body_json = None
        if isinstance(body_json, dict) and 'model' in body_json:
            try:
                body_json['model'] = upstream_model_id
                raw = json.dumps(body_json).encode('utf-8')
            except Exception:
                pass

    url = f'{base}/v1/chat/completions'

    if wants_stream(raw):
        mark_inference_start(
            server_id,
            api_url=api_url,
            model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
        )
        close_upstream = None
        read_timeout = chat_upstream_read_timeout(cfg)
        try:
            media_type, chunks, close_upstream = await open_upstream_chat_stream(
                url,
                raw,
                content_type=content_type,
                server_id=server_id,
                read_timeout=read_timeout,
            )
        except urllib.error.HTTPError as exc:
            mark_inference_end(server_id)
            detail = exc.read().decode('utf-8', errors='replace')
            raise HTTPException(status_code=exc.code, detail=detail) from exc
        except Exception as exc:
            mark_inference_end(server_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        disable_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
        keepalive_interval = 15.0

        async def stream_body():
            import time

            buffer = bytearray()
            sent_to_client = bytearray()
            last_yield_at = time.monotonic()
            client_got_content = False
            try:
                async for chunk in chunks:
                    # Client gone → stop reading; finally closes llama-server.
                    if await request.is_disconnected():
                        break
                    if disable_reasoning and is_reasoning_only_chunk(chunk):
                        now = time.monotonic()
                        if now - last_yield_at >= keepalive_interval:
                            yield SSE_KEEPALIVE_COMMENT
                            last_yield_at = now
                        continue
                    buffer.extend(chunk)
                    sent_to_client.extend(chunk)
                    if not client_got_content and sse_had_content_delta(chunk):
                        client_got_content = True
                    yield chunk
                    last_yield_at = time.monotonic()
                if (
                    disable_reasoning
                    and sent_to_client
                    and not client_got_content
                ):
                    if not sse_stream_complete(bytes(buffer)):
                        yield sse_stream_error_chunk('Upstream chat stream closed before completion')
                    else:
                        yield sse_stream_error_chunk(
                            'Model produced no assistant content; increase max_tokens or send X-Disable-Reasoning: 0'
                        )
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
    active_model = str(live.get('active_model_id') or server.get('model_id') or '')
    disconnect_task = asyncio.create_task(
        _abort_upstream_when_client_disconnects(
            request,
            server_id=server_id,
            api_url=api_url,
            model_id=active_model,
        )
    )
    try:
        status_code, payload = await asyncio.to_thread(
            upstream_chat_completion,
            url,
            raw,
            content_type=content_type,
            server_id=server_id,
        )
        if status_code >= 400:
            return JSONResponse(content=payload, status_code=status_code)
        guard = empty_completion_guard(payload)
        if guard:
            return JSONResponse(
                status_code=422,
                content={'error': {'message': guard, 'type': 'empty_completion'}},
            )
        note_completion_stats(
            server_id,
            payload,
            api_url=api_url,
            model_id=active_model,
        )
        return JSONResponse(content=payload, status_code=status_code)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        disconnect_task.cancel()
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
    server = get_server(cfg, server_id)
    if not server:
        adapter = _hf_engine_adapter(server_id)
        if adapter is None:
            raise HTTPException(status_code=404, detail=f'unknown server: {server_id}')
        stop_fn = getattr(adapter, 'stop', None)
        result = stop_fn() if callable(stop_fn) else {'success': True}
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'stop failed')
        _invalidate_status_cache()
        return result
    server = normalize_server(server)
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
    _invalidate_status_cache()
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
        # A dead listener after Console restart does not mean the user stopped
        # the engine — preserve saved engine_on so boot can restore it.
        return {
            'success': True,
            'unloaded': False,
            'engine_stopped': True,
            'listener_ready': False,
            'message': 'Engine listener is not running; nothing to unload.',
        }
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
    result = reload_server(server, cfg=cfg)
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


def _delete_allowed_roots(cfg: dict[str, Any]) -> list[Path]:
    """Library roots plus scanned folders that the Models tab can delete from."""
    roots: list[Path] = []
    seen: set[str] = set()
    candidates = [*_allowed_model_roots(cfg), *(path for path, _source in disk_scan_roots(cfg))]
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


_PROTECTED_DELETE_NAMES = frozenset({
    'hub', 'huggingface', 'models', 'snapshots', 'blobs', 'refs',
})


def _assert_deletable_dir(folder: Path, allowed: list[Path]) -> None:
    if not any(folder == root or folder.is_relative_to(root) for root in allowed):
        raise HTTPException(status_code=403, detail='path not under allowed model directories')
    if any(folder == root for root in allowed) and not folder.name.startswith('models--'):
        raise HTTPException(status_code=403, detail='refusing to delete a library root')
    if folder.name.lower() in _PROTECTED_DELETE_NAMES:
        raise HTTPException(status_code=403, detail='refusing to delete a cache root')


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
def stacks_match(
    target_path: str = Query(..., min_length=1),
    current_draft: str = Query(default=''),
    dflash_generation: str = Query(default='auto'),
) -> dict[str, Any]:
    from core.stack_match import match_stack_for_target

    cfg = load_config()
    _validate_gguf_under_allowed_roots(target_path, cfg, roots=_stack_model_roots(cfg))
    current = str(current_draft or '').strip() or None
    if current:
        _validate_gguf_under_allowed_roots(current, cfg, roots=_stack_model_roots(cfg))
    return match_stack_for_target(
        target_path,
        cfg=cfg,
        current_draft_path=current,
        dflash_generation=dflash_generation,
    )


@app.post('/api/stacks/{server_id}/replace-draft')
def stacks_replace_draft(server_id: str, body: StackReplaceDraftRequest) -> dict[str, Any]:
    from core.stack_match import replace_stack_draft

    cfg = load_config()
    _validate_gguf_under_allowed_roots(body.draft_path, cfg, roots=_stack_model_roots(cfg))
    result = replace_stack_draft(server_id, body.draft_path, cfg=cfg)
    if not result.get('success'):
        error = str(result.get('error') or 'replace failed')
        status = 404 if 'unknown server' in error else 400
        raise HTTPException(status_code=status, detail=error)
    _invalidate_status_cache()
    return result


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

    # When the user converts an external model to a DFlash stack, bring both the
    # target GGUF and its accelerator into the Console's own models folder so the
    # pair registers under DFlash Console. copy_mode controls whether the
    # originals are kept (copy), removed (move), or left untouched (none).
    copied = None
    copy_mode = (body.copy_mode or ('copy' if body.copy_to_console else 'none')).strip().lower()
    if copy_mode in ('copy', 'move'):
        from core.library_import import import_stack_pair

        try:
            copied = import_stack_pair(
                str(target),
                str(draft),
                label=body.label.strip() or server_id,
                mode=copy_mode,
                overwrite=body.overwrite,
                cfg=cfg,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if copied.get('exists'):
            return copied
        target = Path(str(copied['target_path'])).expanduser().resolve()
        draft = Path(str(copied['draft_path'])).expanduser().resolve()

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
    result = {'success': True, 'server': entry}
    if copied:
        result['copied_to_console'] = {
            'target_path': str(target),
            'draft_path': str(draft),
            'library_path': copied.get('library_path'),
        }
    return result


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


def _servers_referencing_model(path: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Configured server profiles whose stack target/draft path equals ``path``."""
    try:
        from core.local_models import _normalize_path_key, _resolve_stack_pair
    except Exception:
        return []
    tkey = _normalize_path_key(str(path))
    servers = list(cfg.get('servers') or [])
    matching: list[dict[str, Any]] = []
    for server in servers:
        pair = _resolve_stack_pair(server, cfg=cfg)
        matched = False
        for row in (pair or []):
            if not row:
                continue
            model_path = str(row.get('path') or '')
            if model_path and _normalize_path_key(model_path) == tkey:
                matched = True
                break
        if matched:
            matching.append(server)
    return matching


def _remove_servers_from_config(cfg: dict[str, Any], servers: list[dict[str, Any]]) -> list[str]:
    """Remove the given server profiles from config and persist. Returns removed ids."""
    if not servers:
        return []
    removed_ids = [str(s.get('id') or s.get('model_id') or 'unknown') for s in servers]
    drop = {str(s.get('id') or ''): True for s in servers if s.get('id')}
    all_servers = list(cfg.get('servers') or [])
    kept = [s for s in all_servers if str(s.get('id') or '') not in drop]
    if len(kept) == len(all_servers):
        return []
    cfg['servers'] = kept
    try:
        from core.config import save_config
        save_config(cfg)
    except Exception:
        return []
    return removed_ids


def _collect_model_delete_targets(
    cfg: dict[str, Any],
    *,
    path: str = '',
    server_id: str = '',
) -> tuple[list[dict[str, Any]], list[Path], list[Path]]:
    """Resolve server profiles, GGUF files, and model folders to remove."""
    from core.local_models import _normalize_path_key, _resolve_stack_pair

    matching_servers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    files: dict[str, Path] = {}
    folders: dict[str, Path] = {}

    if path:
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() == '.gguf':
            files[_normalize_path_key(str(target))] = target
            for server in _servers_referencing_model(target, cfg):
                sid = str(server.get('id') or '')
                if sid and sid not in seen_ids:
                    seen_ids.add(sid)
                    matching_servers.append(server)
        else:
            folder = resolve_model_delete_dir(target)
            if folder is not None:
                folders[_normalize_path_key(str(folder))] = folder

    sid = str(server_id or '').strip()
    if sid and sid not in seen_ids:
        server = get_server(cfg, sid)
        if server:
            seen_ids.add(sid)
            matching_servers.append(server)

    for server in list(matching_servers):
        for row in _resolve_stack_pair(server, cfg=cfg):
            if not row:
                continue
            model_path = str(row.get('path') or '').strip()
            if not model_path:
                continue
            candidate = Path(model_path).expanduser().resolve()
            if candidate.suffix.lower() != '.gguf':
                continue
            files[_normalize_path_key(str(candidate))] = candidate

    return matching_servers, list(files.values()), list(folders.values())


@app.delete('/api/models/file')
def delete_model_file(
    path: str = Query(default=''),
    server_id: str = Query(default=''),
    source: str = Query(default=''),
    model_id: str = Query(default=''),
) -> dict[str, Any]:
    cfg = load_config()
    # Ollama models are blobs, not .gguf files, and live outside the Console's
    # model roots — delete them through Ollama (daemon or manifest + blobs).
    if str(source or '').strip().lower() == 'ollama':
        if not path:
            raise HTTPException(status_code=400, detail='path required')
        from core.local_models import _delete_ollama_model

        result = _delete_ollama_model(path, model_id=model_id)
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'delete failed')
        invalidate_model_catalog_cache()
        return result

    matching, file_paths, dir_paths = _collect_model_delete_targets(cfg, path=path, server_id=server_id)
    if not file_paths and not matching and not dir_paths:
        raise HTTPException(status_code=400, detail='nothing to delete')

    allowed = _delete_allowed_roots(cfg)
    if file_paths and (not allowed or not all(
        any(candidate.is_relative_to(root) for root in allowed)
        for candidate in file_paths
    )):
        raise HTTPException(status_code=403, detail='path not under allowed model directories')
    for folder in dir_paths:
        _assert_deletable_dir(folder, allowed)

    for server in matching:
        sid = str(server.get('id') or '')
        if not sid:
            continue
        try:
            server_unload(sid)
        except HTTPException:
            pass

    deleted_files: list[str] = []
    for candidate in file_paths:
        if not candidate.is_file():
            continue
        candidate.unlink()
        deleted_files.append(str(candidate))

    deleted_dirs: list[str] = []
    for folder in dir_paths:
        if not folder.is_dir():
            continue
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f'could not delete folder: {exc}') from exc
        deleted_dirs.append(str(folder))

    if not deleted_files and not deleted_dirs and not matching:
        raise HTTPException(status_code=400, detail='nothing to delete')

    removed = _remove_servers_from_config(cfg, matching)
    invalidate_model_catalog_cache()
    result: dict[str, Any] = {'success': True}
    if deleted_files:
        result['path'] = deleted_files[0]
        result['deleted_files'] = deleted_files
    if deleted_dirs:
        result['path'] = result.get('path') or deleted_dirs[0]
        result['deleted_dirs'] = deleted_dirs
        result['model'] = friendly_model_dir_label(deleted_dirs[0])
    if removed:
        result['removed_profiles'] = removed
    return result


class ModelImportIntoConsoleRequest(BaseModel):
    path: str = Field(..., min_length=1)
    mode: str = Field(default='copy', pattern='^(copy|move)$')
    overwrite: bool = Field(default=False)
    progress_id: str | None = Field(default=None, max_length=64)


@app.get('/api/models/import-progress/{progress_id}')
def model_import_progress(progress_id: str) -> dict[str, Any]:
    from core.library_import import get_import_progress

    row = get_import_progress(progress_id)
    if not row:
        return {'success': False, 'status': 'unknown'}
    return {'success': True, **row}


@app.post('/api/models/auto-register')
def models_auto_register() -> dict[str, Any]:
    """Register any model files found under the Console's own models folder.

    Idempotent: files already covered by a server profile are skipped, existing
    profiles are never modified, and only the Console's own library is scanned.
    Run automatically at server startup; this endpoint allows an on-demand run.
    """
    from core.auto_register import auto_register_console_models

    cfg = load_config()
    result = auto_register_console_models(cfg=cfg)
    if result.get('registered'):
        invalidate_model_catalog_cache()
    return result


@app.post('/api/models/import-into-console')
def model_import_into_console(body: ModelImportIntoConsoleRequest) -> dict[str, Any]:
    """Copy or move a single external model into the Console's own library.

    Accepts either a single ``.gguf`` file (managed by llama-server) or a
    faster-whisper **model directory** (contains ``model.bin``; managed by the
    faster-whisper STT runtime). ``mode`` is ``copy`` (default, keeps the
    original) or ``move`` (removes the original from its current location).

    When a model with the same name already exists in the Console library and
    ``overwrite`` is false, returns ``{'success': False, 'exists': True,
    'existing_path': ...}`` (HTTP 200) so the UI can ask the user whether to
    overwrite or abort instead of silently creating a duplicate.
    """
    from core.config import ensure_runtime_entry
    from core.library_import import clear_import_progress, import_single_model_file, is_faster_whisper_dir

    cfg = load_config()
    source = Path(body.path).expanduser().resolve()
    is_fw_dir = is_faster_whisper_dir(source)
    if not ((source.suffix.lower() == '.gguf' and source.is_file()) or is_fw_dir):
        raise HTTPException(status_code=400, detail='not a GGUF file or a faster-whisper model directory')
    try:
        result = import_single_model_file(
            str(source),
            mode=body.mode,
            overwrite=body.overwrite,
            progress_id=body.progress_id,
            cfg=cfg,
        )
    except ValueError as exc:
        if body.progress_id:
            clear_import_progress(body.progress_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get('exists'):
        if body.progress_id:
            clear_import_progress(body.progress_id)
        return result
    if result.get('runtime_id') == 'faster-whisper':
        ensure_runtime_entry('faster-whisper', label='Faster-Whisper STT', cfg=cfg)
    invalidate_model_catalog_cache()
    if body.progress_id:
        clear_import_progress(body.progress_id)
    return result


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
