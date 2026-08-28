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
    RUNTIME_FASTER_WHISPER,
    RUNTIME_LLAMA_SERVER,
    RUNTIME_PIPER,
    RUNTIME_STT,
    RUNTIME_TRANSFORMERS,
    RUNTIME_VIBEVOICE,
    RUNTIME_VLLM,
    RuntimeAdapter,
)
from core.runtimes.faster_whisper import FasterWhisperRuntimeAdapter
from core.runtimes.noop import NoopRuntimeAdapter
from core.runtimes.piper import PiperRuntimeAdapter
from core.runtimes.registry import (
    get_runtime_adapter,
    list_runtime_adapters,
    register_runtime_adapter,
    runtime_ids,
    runtime_process_identity_tokens,
    write_bundle_manifests,
    write_process_tokens_manifest,
)
from core.runtimes.stt import SttRuntimeAdapter
from core.runtimes.transformers_hf import TransformersRuntimeAdapter
from core.runtimes.vibevoice import VibeVoiceRuntimeAdapter
from core.runtimes.vllm import VllmRuntimeAdapter

# The no-op adapter is always available so the UI can list installed adapters
# before any real inference runtime ships.
register_runtime_adapter(NoopRuntimeAdapter())
# Piper TTS adapter (CLI). Registered always; reports installed=false when the
# bundle is not present under runtimes/piper/.
register_runtime_adapter(PiperRuntimeAdapter())
# whisper.cpp whisper-server STT adapter (server mode). Registered always;
# reports installed=false until runtimes/stt/whisper-server.exe exists.
register_runtime_adapter(SttRuntimeAdapter())
# faster-whisper / CTranslate2 STT adapter (server mode). Registered always;
# reports installed=false until the venv under runtimes/faster-whisper/ exists.
register_runtime_adapter(FasterWhisperRuntimeAdapter())
# VibeVoice realtime TTS adapter (server mode). Registered always; reports
# installed=false until the venv under runtimes/vibevoice/ exists.
register_runtime_adapter(VibeVoiceRuntimeAdapter())
# Transformers / PyTorch LLM adapter (server mode). Registered always; reports
# installed=false until the venv under runtimes/transformers/ exists.
register_runtime_adapter(TransformersRuntimeAdapter())
# vLLM OpenAI server adapter (server mode). Registered always; reports
# installed=false until the on-demand bundle under runtimes/vllm/ exists.
register_runtime_adapter(VllmRuntimeAdapter())

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
    RUNTIME_FASTER_WHISPER,
    RUNTIME_LLAMA_SERVER,
    RUNTIME_PIPER,
    RUNTIME_STT,
    RUNTIME_TRANSFORMERS,
    RUNTIME_VIBEVOICE,
    RUNTIME_VLLM,
    RuntimeAdapter,
    'NoopRuntimeAdapter',
    'PiperRuntimeAdapter',
    'SttRuntimeAdapter',
    'FasterWhisperRuntimeAdapter',
    'TransformersRuntimeAdapter',
    'VibeVoiceRuntimeAdapter',
    'VllmRuntimeAdapter',
    'get_runtime_adapter',
    'list_runtime_adapters',
    'register_runtime_adapter',
    'runtime_ids',
    'runtime_process_identity_tokens',
    'write_bundle_manifests',
    'write_process_tokens_manifest',
]
