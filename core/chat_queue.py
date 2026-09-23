"""Per-engine chat gates so concurrent OpenAI clients share one loaded model safely.

Steady-state overlapping ``/v1/chat/completions`` (e.g. DeepSeek Harness main turn +
session title) must not re-enter JIT load and raise HTTP 409. Callers hold the
gate through ready-check + ``mark_inference_start`` (and opening an upstream
stream); llama-server then multiplexes within ``parallel_slots``.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

_GATE_GUARD = threading.Lock()
_GATES: dict[str, asyncio.Lock] = {}


def chat_server_gate(server_id: str) -> asyncio.Lock:
    """Return the asyncio lock for this engine id (created on first use)."""
    sid = str(server_id or '').strip() or '_'
    with _GATE_GUARD:
        lock = _GATES.get(sid)
        if lock is None:
            lock = asyncio.Lock()
            _GATES[sid] = lock
        return lock


@asynccontextmanager
async def hold_chat_server_gate(server_id: str) -> AsyncIterator[None]:
    """Acquire the per-engine chat gate for the critical ready/start section."""
    lock = chat_server_gate(server_id)
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def reset_chat_server_gates_for_tests() -> None:
    """Drop all gates (unit tests only)."""
    with _GATE_GUARD:
        _GATES.clear()


def chat_gate_snapshot() -> dict[str, Any]:
    """Debug helper: which engines currently hold a gate."""
    with _GATE_GUARD:
        return {
            sid: {'locked': lock.locked()}
            for sid, lock in _GATES.items()
        }
