"""Multi-modal runtime adapters for DFlash Console.

Package layout:
  base.py        — RuntimeAdapter protocol + modality/runtime constants
  registry.py    — adapter registry + shared process-identity tokens + manifest
  noop.py        — no-op adapter that validates the contract without inference
  contention.py  — GPU contention / stop-others scaffolding (import lazily)

``servers[]`` stays the persistent shape for llama-server / DFlash / GGUF
embeddings / vision. ``runtimes[]`` (config) is for non-llama adapters only.
"""

from __future__ import annotations

from core.runtimes.base import (
    EXECUTION_MODE_CLI,
    EXECUTION_MODE_SERVER,
    MODALITY_EMBEDDING,
    MODALITY_LLM,
    MODALITY_LABELS,
    MODALITY_OCR,
    MODALITY_SPEECH_TO_TEXT,
    MODALITY_TASKS,
    MODALITY_TEXT_TO_SPEECH,
    MODALITY_VISION,
    RUNTIME_LLAMA_SERVER,
    RUNTIME_PIPER,
    RUNTIME_STT,
    RuntimeAdapter,
)
from core.runtimes.noop import NoopRuntimeAdapter
from core.runtimes.registry import (
    get_runtime_adapter,
    list_runtime_adapters,
    register_runtime_adapter,
    runtime_ids,
    runtime_process_identity_tokens,
    write_process_tokens_manifest,
)

# The no-op adapter is always available so the UI can list installed adapters
# before any real inference runtime ships.
register_runtime_adapter(NoopRuntimeAdapter())

__all__ = [
    'EXECUTION_MODE_CLI',
    'EXECUTION_MODE_SERVER',
    'MODALITY_EMBEDDING',
    'MODALITY_LLM',
    'MODALITY_LABELS',
    'MODALITY_OCR',
    'MODALITY_SPEECH_TO_TEXT',
    'MODALITY_TASKS',
    'MODALITY_TEXT_TO_SPEECH',
    'MODALITY_VISION',
    'RUNTIME_LLAMA_SERVER',
    'RUNTIME_PIPER',
    'RUNTIME_STT',
    'RuntimeAdapter',
    'NoopRuntimeAdapter',
    'get_runtime_adapter',
    'list_runtime_adapters',
    'register_runtime_adapter',
    'runtime_ids',
    'runtime_process_identity_tokens',
    'write_process_tokens_manifest',
]
