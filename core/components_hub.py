"""Unified catalog of downloadable Console components (engines and runtimes)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config import ROOT

# Bump when install scripts or bundled requirements change — surfaces "Update available".
BUNDLE_REVISIONS: dict[str, int] = {
    'vllm': 1,
    'transformers': 1,
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
        data = json.loads(path.read_text(encoding='utf-8'))
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
    return {}


def _component_row(entry: dict[str, Any]) -> dict[str, Any]:
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

    update_available = (
        installed
        and expected_revision > 0
        and installed_revision < expected_revision
        and (install_mode == 'on_demand' or has_revision_marker)
    )
    needs_install = install_mode == 'on_demand' and not installed and not installing

    status = 'installed'
    if installing:
        status = 'installing'
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
        'install_error': install_error,
        'update_available': update_available,
        'expected_revision': expected_revision,
        'installed_revision': installed_revision,
        'manifest': manifest or None,
        'needs_attention': bool(needs_install or update_available or status == 'error'),
    }


def list_components_payload() -> dict[str, Any]:
    components = [_component_row(entry) for entry in CATALOG]
    attention_count = sum(1 for row in components if row.get('needs_attention'))
    update_count = sum(1 for row in components if row.get('update_available'))
    missing_count = sum(1 for row in components if row.get('status') == 'not_installed')

    downloads: dict[str, Any] = {'jobs': [], 'active_count': 0}
    try:
        from core.huggingface import list_download_jobs

        downloads = list_download_jobs(active_only=True)
    except Exception:
        pass

    return {
        'success': True,
        'components': components,
        'attention_count': attention_count,
        'update_count': update_count,
        'missing_count': missing_count,
        'active_downloads': downloads.get('jobs') or [],
        'active_download_count': int(downloads.get('active_count') or 0),
        'bundle_revisions': dict(BUNDLE_REVISIONS),
    }
