"""Unified catalog of downloadable Console components (engines and runtimes)."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.version import APP_VERSION
from core import component_versions

# Bump when install scripts or bundled requirements change — surfaces "Update available".
BUNDLE_REVISIONS: dict[str, int] = {
    'vllm': 1,
    'transformers': 1,
    'freetoken': 1,
    'piper': 1,
    'stt': 1,
    'faster-whisper': 1,
    'vibevoice': 1,
}

CATALOG: list[dict[str, Any]] = [
    {
        'id': 'dflash-gguf',
        'runtime_id': '',
        'category': 'llm_engine',
        'label': 'DFlash / GGUF engines',
        'short_label': 'GGUF',
        'description': 'llama-server profiles for local GGUF models. Included with the Console installer — no extra download.',
        'install_mode': 'bundled',
        'keywords': ['gguf', 'llama', 'dflash', 'engine', 'llama-server'],
    },
    {
        'id': 'vllm',
        'runtime_id': 'vllm',
        'category': 'llm_engine',
        'label': 'vLLM engine',
        'short_label': 'vLLM',
        'description': 'Fast GPU engine for Hugging Face SafeTensors models. NVIDIA GPU required. On Windows may install through WSL.',
        'install_mode': 'on_demand',
        'keywords': ['vllm', 'safetensors', 'huggingface', 'gpu', 'engine'],
    },
    {
        'id': 'transformers',
        'runtime_id': 'transformers',
        'category': 'llm_engine',
        'label': 'Transformers / PyTorch runtime',
        'short_label': 'Transformers',
        'description': 'Works on more PCs (CPU or GPU). Slower than vLLM. Installs torch + transformers on demand.',
        'install_mode': 'on_demand',
        'keywords': ['transformers', 'pytorch', 'safetensors', 'huggingface', 'cpu'],
    },
    {
        'id': 'freetoken',
        'runtime_id': 'freetoken',
        'category': 'llm_engine',
        'label': 'FreeToken WSL engine',
        'short_label': 'FreeToken',
        'description': 'Optional Linux/CUDA MoE engine for supported Hugging Face models. Requires WSL2 Ubuntu and an NVIDIA driver with CUDA support; large dependencies stay inside WSL.',
        'install_mode': 'on_demand',
        'keywords': ['freetoken', 'moe', 'wsl', 'cuda', 'nvidia', 'safetensors'],
    },
    {
        'id': 'piper',
        'runtime_id': 'piper',
        'category': 'speech',
        'label': 'Piper TTS',
        'short_label': 'Piper',
        'description': 'Local text-to-speech for Playground Speak. Included with the installer.',
        'install_mode': 'bundled',
        'keywords': ['piper', 'tts', 'speech', 'voice'],
    },
    {
        'id': 'stt',
        'runtime_id': 'stt',
        'category': 'speech',
        'label': 'Whisper STT',
        'short_label': 'Whisper',
        'description': 'Speech-to-text for Playground Transcribe. Ships with the installer (whisper-server binary) — not a separate download.',
        'install_mode': 'bundled',
        'keywords': ['whisper', 'stt', 'speech', 'transcribe'],
    },
    {
        'id': 'faster-whisper',
        'runtime_id': 'faster-whisper',
        'category': 'speech',
        'label': 'faster-whisper STT',
        'short_label': 'faster-whisper',
        'description': 'Alternative GPU speech-to-text. Included with the installer.',
        'install_mode': 'bundled',
        'keywords': ['faster-whisper', 'whisper', 'stt', 'speech'],
    },
    {
        'id': 'vibevoice',
        'runtime_id': 'vibevoice',
        'category': 'speech',
        'label': 'VibeVoice TTS',
        'short_label': 'VibeVoice',
        'description': 'Diffusion TTS runtime. Included with the installer.',
        'install_mode': 'bundled',
        'keywords': ['vibevoice', 'tts', 'speech', 'voice'],
    },
]

_CATEGORY_LABELS = {
    'llm_engine': 'LLM engines',
    'speech': 'Speech runtimes',
}


def _read_manifest(runtime_id: str) -> dict[str, Any]:
    path = ROOT / 'runtimes' / runtime_id / 'manifest.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _adapter_installed(runtime_id: str) -> bool:
    if not runtime_id:
        return True
    try:
        from core.runtimes import get_runtime_adapter

        adapter = get_runtime_adapter(runtime_id)
        if adapter is None:
            return False
        health = adapter.health() if callable(getattr(adapter, 'health', None)) else {}
        if isinstance(health, dict) and 'installed' in health:
            return health.get('installed') is True
        checker = getattr(adapter, 'is_installed', None)
        if callable(checker):
            return bool(checker())
    except Exception:
        pass
    return False


def _on_demand_status(runtime_id: str) -> dict[str, Any]:
    if runtime_id == 'vllm':
        from core.vllm_runtime_install import install_status

        return install_status()
    if runtime_id == 'transformers':
        from core.transformers_runtime_install import install_status

        return install_status()
    if runtime_id == 'freetoken':
        from core.freetoken_runtime_install import install_status

        return install_status()
    return {}


def _component_row(entry: dict[str, Any], *, versions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    runtime_id = str(entry.get('runtime_id') or '')
    install_mode = str(entry.get('install_mode') or 'bundled')
    manifest = _read_manifest(runtime_id) if runtime_id else {}
    expected_revision = int(BUNDLE_REVISIONS.get(runtime_id or entry.get('id') or '', 0) or 0)
    installed_revision = int(manifest.get('bundle_revision') or 0) if manifest else 0
    has_revision_marker = 'bundle_revision' in manifest if manifest else False

    on_demand = _on_demand_status(runtime_id) if install_mode == 'on_demand' and runtime_id else {}
    install_state = str(on_demand.get('status') or '')
    installing = install_state == 'installing'
    install_error = str(on_demand.get('error') or '')

    if install_mode == 'on_demand' and runtime_id:
        installed = bool(on_demand.get('installed'))
    elif runtime_id:
        installed = _adapter_installed(runtime_id)
    else:
        installed = True

    version_row = dict((versions or {}).get(runtime_id) or {})
    if install_mode != 'on_demand':
        version_row = {
            'installed_version': APP_VERSION,
            'latest_version': APP_VERSION,
            'version_update_available': False,
            'version_source': 'bundled',
        }
    installed_version = str(version_row.get('installed_version') or '').strip()
    latest_version = str(version_row.get('latest_version') or '').strip()
    version_update = bool(version_row.get('version_update_available'))

    revision_update = (
        installed
        and expected_revision > 0
        and installed_revision < expected_revision
        and (install_mode == 'on_demand' or has_revision_marker)
    )
    update_available = bool(revision_update or version_update)
    needs_install = install_mode == 'on_demand' and not installed and not installing

    status = 'installed'
    if installing:
        status = 'installing'
    elif installed:
        if install_state == 'error':
            status = 'error'
        else:
            status = 'update_available' if update_available else 'installed'
            install_error = ''
    elif install_state == 'error':
        status = 'error'
    elif update_available:
        status = 'update_available'
    elif needs_install:
        status = 'not_installed'
    elif not installed and runtime_id:
        status = 'not_installed'

    return {
        'id': entry['id'],
        'runtime_id': runtime_id,
        'category': entry.get('category') or 'other',
        'category_label': _CATEGORY_LABELS.get(entry.get('category') or '', 'Components'),
        'label': entry.get('label') or entry['id'],
        'short_label': entry.get('short_label') or entry['id'],
        'description': entry.get('description') or '',
        'install_mode': install_mode,
        'keywords': list(entry.get('keywords') or []),
        'installed': installed,
        'status': status,
        'installing': installing,
        'install_progress': on_demand.get('progress'),
        'install_message': str(on_demand.get('message') or on_demand.get('log_line') or ''),
        'install_error': install_error,
        'update_available': update_available,
        'version_update_available': version_update,
        'installed_version': installed_version,
        'latest_version': latest_version,
        'version_source': str(version_row.get('version_source') or ('PyPI' if install_mode == 'on_demand' else 'bundled')),
        'version_error': str(version_row.get('latest_error') or version_row.get('installed_error') or ''),
        'expected_revision': expected_revision,
        'installed_revision': installed_revision,
        'manifest': manifest or None,
        'needs_attention': bool(needs_install or update_available or status == 'error'),
    }


def list_components_payload(*, refresh_versions: bool = False) -> dict[str, Any]:
    from core.config import load_config

    if refresh_versions:
        component_versions.start_refresh_if_stale()
    version_state = component_versions.version_snapshot()
    versions = version_state.get('versions') or {}
    components = [_component_row(entry, versions=versions) for entry in CATALOG]
    attention_count = sum(1 for row in components if row.get('needs_attention'))
    update_count = sum(1 for row in components if row.get('update_available'))
    missing_count = sum(1 for row in components if row.get('status') == 'not_installed')

    downloads: dict[str, Any] = {'jobs': [], 'active_count': 0}
    try:
        from core.huggingface import list_download_jobs

        downloads = list_download_jobs(active_only=True, fast=True)
    except Exception:
        pass

    try:
        auto_update = load_config().get('auto_update_components') is True
    except Exception:
        auto_update = False

    return {
        'success': True,
        'components': components,
        'attention_count': attention_count,
        'update_count': update_count,
        'missing_count': missing_count,
        'active_downloads': downloads.get('jobs') or [],
        'active_download_count': int(downloads.get('active_count') or 0),
        'bundle_revisions': dict(BUNDLE_REVISIONS),
        'auto_update_components': auto_update,
        'version_checked_at': version_state.get('checked_at'),
        'version_check_pending': bool(version_state.get('pending')),
        'version_check_error': str(version_state.get('error') or ''),
    }


_AUTO_LOCK = threading.Lock()
_AUTO_THREAD: threading.Thread | None = None
_AUTO_LAST_ATTEMPT: dict[str, float] = {}
_AUTO_LOGGER = logging.getLogger('uvicorn.error')


def _runtime_is_running(runtime_id: str) -> bool:
    try:
        from core.runtimes import get_runtime_adapter

        adapter = get_runtime_adapter(runtime_id)
        if adapter is None:
            return False
        health = adapter.health() if callable(getattr(adapter, 'health', None)) else {}
        return bool(isinstance(health, dict) and health.get('running'))
    except Exception:
        return False


def _start_component_update(row: dict[str, Any]) -> dict[str, Any]:
    runtime_id = str(row.get('runtime_id') or '').strip().lower()
    manifest = row.get('manifest') if isinstance(row.get('manifest'), dict) else {}
    if runtime_id == 'vllm':
        from core.vllm_runtime_install import start_install

        backend = str(manifest.get('backend') or 'auto').strip().lower()
        return start_install(backend=backend or 'auto', force=True)
    if runtime_id == 'transformers':
        from core.transformers_runtime_install import start_install

        torch_variant = str(manifest.get('torch_variant') or 'auto').strip().lower()
        return start_install(torch_variant=torch_variant or 'auto', force=True)
    if runtime_id == 'freetoken':
        from core.freetoken_runtime_install import start_install

        return start_install(backend='wsl', force=True)
    return {'success': False, 'error': f'no automatic installer for {runtime_id or "component"}'}


def auto_update_tick(*, now: float | None = None) -> dict[str, Any]:
    """Start one pending engine update when automatic updates are enabled.

    Updates are serialized: one installer at a time.  A runtime that is
    currently loaded is skipped so an update cannot pull the venv from under a
    running model.
    """
    from core.config import load_config

    if load_config().get('auto_update_components') is not True:
        return {'enabled': False, 'started': False, 'skipped': 'automatic updates are off'}

    payload = list_components_payload()
    if any(row.get('status') == 'installing' for row in payload.get('components') or []):
        return {'enabled': True, 'started': False, 'skipped': 'an engine install is already running'}

    clock = time.time() if now is None else float(now)
    candidates = [
        row for row in payload.get('components') or []
        if row.get('install_mode') == 'on_demand'
        and row.get('update_available')
        and not row.get('installing')
    ]
    skipped: list[str] = []
    for row in candidates:
        runtime_id = str(row.get('runtime_id') or '')
        last = float(_AUTO_LAST_ATTEMPT.get(runtime_id) or 0.0)
        if clock - last < 300.0:
            skipped.append(f'{runtime_id}: tried recently')
            continue
        if _runtime_is_running(runtime_id):
            skipped.append(f'{runtime_id}: still running')
            continue
        result = _start_component_update(row)
        _AUTO_LAST_ATTEMPT[runtime_id] = clock
        if result.get('success'):
            _AUTO_LOGGER.info(
                'automatic engine update started: %s %s -> %s',
                runtime_id,
                row.get('installed_version') or '?',
                row.get('latest_version') or '?',
            )
            return {
                'enabled': True,
                'started': True,
                'runtime_id': runtime_id,
                'installed_version': row.get('installed_version') or '',
                'latest_version': row.get('latest_version') or '',
                'result': result,
                'skipped': skipped,
            }
        skipped.append(f'{runtime_id}: {result.get("error") or "install did not start"}')

    return {'enabled': True, 'started': False, 'skipped': skipped}


def check_components_now() -> dict[str, Any]:
    """Force a real version check, then let the automatic updater act."""
    component_versions.refresh_versions(force=True)
    auto = auto_update_tick()
    payload = list_components_payload()
    payload['auto_update'] = auto
    return payload


def start_auto_update_loop(*, interval_seconds: float = 1800.0) -> bool:
    """Start the periodic check used by the running Console API."""
    global _AUTO_THREAD
    with _AUTO_LOCK:
        if _AUTO_THREAD is not None and _AUTO_THREAD.is_alive():
            return False
        thread = threading.Thread(
            target=_auto_update_loop,
            kwargs={'interval_seconds': interval_seconds},
            daemon=True,
            name='component-auto-update',
        )
        _AUTO_THREAD = thread
        thread.start()
        return True


def _auto_update_loop(*, interval_seconds: float) -> None:
    time.sleep(15.0)
    while True:
        try:
            check_components_now()
        except Exception as exc:
            _AUTO_LOGGER.exception('automatic engine update check failed: %s', exc)
        time.sleep(max(60.0, float(interval_seconds)))
