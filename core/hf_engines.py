"""Shared helpers for Hugging Face SafeTensors LLM engines."""

from __future__ import annotations

HF_LLM_ENGINES = ('vllm', 'transformers', 'freetoken')


def preferred_hf_runtime() -> str:
    """Prefer vLLM when it is installed; otherwise Transformers."""
    try:
        from core.runtimes.vllm import VllmRuntimeAdapter

        if VllmRuntimeAdapter.is_installed():
            return 'vllm'
    except Exception:
        pass
    return 'transformers'


def annotate_hf_llm_row(row: dict) -> None:
    row['engines'] = list(HF_LLM_ENGINES)
    row.setdefault('runtime_id', preferred_hf_runtime())
    row['kind'] = 'dir'
    row['plain_gguf'] = False
