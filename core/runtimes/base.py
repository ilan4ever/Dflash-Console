"""Runtime adapter contract shared by every DFlash Console runtime adapter.

An adapter wraps one external model runtime (llama-server, Piper, a future
whisper engine, ...) so the Console can start, stop, health-check, load,
unload, and proxy OpenAI-shaped routes for many model families.

Design intent (see docs/MULTI-MODAL-RUNTIME-PLAN.md):
- One catalog, many runtimes: catalog rows carry ``runtime_id`` so the UI and
  supervisor can pick the right adapter without hard-coding GGUF.
- ``servers[]`` stays the ONLY persistent shape for llama-server / DFlash /
  GGUF embeddings / vision. ``runtimes[]`` is for non-llama adapters only.
- Child loopback ports stay internal; the Console proxies OpenAI-shaped routes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Canonical modality identifiers used in catalog rows and the UI.
MODALITY_LLM = 'llm'
MODALITY_EMBEDDING = 'embedding'
MODALITY_SPEECH_TO_TEXT = 'speech-to-text'
MODALITY_TEXT_TO_SPEECH = 'text-to-speech'
MODALITY_VISION = 'vision'
MODALITY_OCR = 'ocr'

MODALITY_LABELS: dict[str, str] = {
    MODALITY_LLM: 'LLM',
    MODALITY_EMBEDDING: 'Embed',
    MODALITY_SPEECH_TO_TEXT: 'STT',
    MODALITY_TEXT_TO_SPEECH: 'TTS',
    MODALITY_VISION: 'Vision',
    MODALITY_OCR: 'OCR',
}

# Well-known runtime ids. ``llama-server`` is built in; new adapters register
# their own ids through the registry.
RUNTIME_LLAMA_SERVER = 'llama-server'
RUNTIME_PIPER = 'piper'
RUNTIME_STT = 'stt'  # whisper.cpp whisper-server STT adapter
RUNTIME_FASTER_WHISPER = 'faster-whisper'  # faster-whisper / CTranslate2 STT adapter
RUNTIME_VIBEVOICE = 'vibevoice'  # Microsoft VibeVoice realtime TTS adapter
RUNTIME_TRANSFORMERS = 'transformers'  # Hugging Face Transformers / PyTorch LLM adapter
RUNTIME_VLLM = 'vllm'  # vLLM high-throughput OpenAI server (installed on demand)
RUNTIME_FREETOKEN = 'freetoken'  # FreeToken edge-native MoE server (WSL/Linux)
RUNTIME_OLLAMA = 'ollama'  # Ollama local model library (native HTTP API)

# execution_mode values
EXECUTION_MODE_SERVER = 'server'
EXECUTION_MODE_CLI = 'cli'

# Generic task labels derived from a modality (where easy).
MODALITY_TASKS: dict[str, str] = {
    MODALITY_LLM: 'chat',
    MODALITY_EMBEDDING: 'embed',
    MODALITY_SPEECH_TO_TEXT: 'transcribe',
    MODALITY_TEXT_TO_SPEECH: 'speech',
    MODALITY_VISION: 'vision',
    MODALITY_OCR: 'ocr',
}


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Contract implemented by each runtime adapter.

    ``process_identity_tokens`` are substrings matched (case-insensitively)
    against a Windows process name and command line. The supervisor uses them
    to recognise, adopt, and clean up child processes, so every adapter that
    spawns a child process MUST contribute at least one unique token.
    """

    runtime_id: str
    modalities: tuple[str, ...]
    execution_mode: str  # EXECUTION_MODE_SERVER | EXECUTION_MODE_CLI
    process_identity_tokens: tuple[str, ...]

    def health(self) -> dict[str, Any]:
        """Return a status dict; include ``running`` bool when meaningful."""
        ...

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Bring up the runtime process(es) for a config profile."""
        ...

    def stop(self) -> dict[str, Any]:
        """Stop the runtime process(es) and free resources."""
        ...

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        """Load a model (or voice) into the running runtime."""
        ...

    def unload(self) -> dict[str, Any]:
        """Unload the active model and free GPU memory where applicable."""
        ...

    def openai_routes(self) -> list[str]:
        """OpenAI-shaped routes this adapter proxies, e.g. ['/v1/audio/speech']."""
        ...
