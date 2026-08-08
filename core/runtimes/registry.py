"""Runtime adapter registry.

Holds the set of installed runtime adapters and the shared process-identity
tokens used by the supervisor (server_boot.py, runtime.py) and server.ps1 to
recognise Console-managed child processes.

The built-in llama-server tokens are always present. Adapters register their
own ``process_identity_tokens`` so stop/adopt/kill-listener recognise their
children without hard-coding each engine name in every lifecycle function.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.runtimes.base import RUNTIME_LLAMA_SERVER, RuntimeAdapter

# Process identity tokens for the built-in llama-server path (always on).
_BASE_PROCESS_TOKENS: tuple[str, ...] = (
    'llama-server',
    'start_llama_server.ps1',
)

_ADAPTERS: dict[str, RuntimeAdapter] = {}
_REGISTRY_LOCK = threading.Lock()

# Manifest path read by server.ps1 Stop-ListenersOnPort at Console boot.
_PROCESS_TOKENS_MANIFEST = ROOT / 'runtimes' / 'process-tokens.json'


def register_runtime_adapter(adapter: RuntimeAdapter) -> RuntimeAdapter:
    """Register a runtime adapter. Later registrations override earlier ones."""
    with _REGISTRY_LOCK:
        _ADAPTERS[str(adapter.runtime_id)] = adapter
    return adapter


def get_runtime_adapter(runtime_id: str) -> RuntimeAdapter | None:
    if not runtime_id:
        return None
    with _REGISTRY_LOCK:
        return _ADAPTERS.get(str(runtime_id))


def list_runtime_adapters() -> list[RuntimeAdapter]:
    with _REGISTRY_LOCK:
        return list(_ADAPTERS.values())


def runtime_ids() -> set[str]:
    with _REGISTRY_LOCK:
        return set(_ADAPTERS)


def runtime_process_identity_tokens() -> tuple[str, ...]:
    """All tokens the supervisor treats as Console-managed processes."""
    tokens: list[str] = list(_BASE_PROCESS_TOKENS)
    with _REGISTRY_LOCK:
        for adapter in _ADAPTERS.values():
            for token in getattr(adapter, 'process_identity_tokens', ()) or ():
                text = str(token).strip()
                if text and text.lower() not in {t.lower() for t in tokens}:
                    tokens.append(text)
    return tuple(tokens)


def write_process_tokens_manifest(root: Path | None = None) -> Path:
    """Write the process-identity token manifest read by server.ps1.

    server.ps1 merges these tokens into its own managed-process check so
    stop/shutdown cleanup recognises new runtime children. Failures are
    swallowed — the manifest is an optimisation, not a hard dependency.
    """
    target = (root if root is not None else ROOT) / 'runtimes' / 'process-tokens.json'
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'tokens': list(runtime_process_identity_tokens()),
            'generated_by': 'core.runtimes.registry',
        }
        temporary = target.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        temporary.replace(target)
    except OSError:
        return target
    return target
