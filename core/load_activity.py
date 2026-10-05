"""In-memory record of a model load that has been accepted but not finished.

The Engines page reads this on every status response so a loading card can
appear the moment a load request arrives, before weights are on the GPU.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_ACTIVE: dict[str, dict[str, Any]] = {}


def _label_from_path(model_path: str, fallback: str) -> str:
    text = str(model_path or '').strip()
    if text:
        name = Path(text).name
        if name:
            return name
    return fallback or 'Model'


def begin_model_load(
    server_id: str,
    *,
    label: str = '',
    model_id: str = '',
    model_path: str = '',
    runtime_id: str = '',
    component_key: str = '',
    component_label: str = '',
    component_role: str = '',
) -> None:
    sid = str(server_id or '').strip()
    if not sid:
        return
    shown = str(label or '').strip() or _label_from_path(model_path, model_id or sid)
    with _LOCK:
        _ACTIVE[sid] = {
            'server_id': sid,
            'label': shown,
            'model_id': str(model_id or '').strip(),
            'model_path': str(model_path or '').strip(),
            'runtime_id': str(runtime_id or '').strip(),
            'component_key': str(component_key or '').strip(),
            'component_label': str(component_label or '').strip(),
            'component_role': str(component_role or '').strip(),
            'started_at': time.time(),
        }
    try:
        from core.runtime import invalidate_status_payload_cache

        invalidate_status_payload_cache()
    except Exception:
        pass


def end_model_load(server_id: str) -> None:
    sid = str(server_id or '').strip()
    if not sid:
        return
    with _LOCK:
        _ACTIVE.pop(sid, None)
    try:
        from core.runtime import invalidate_status_payload_cache

        invalidate_status_payload_cache()
    except Exception:
        pass


def active_model_loads() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(row) for row in _ACTIVE.values()]


class track_model_load:
    """Mark a server as loading for the duration of a blocking load call."""

    def __init__(
        self,
        server_id: str,
        *,
        label: str = '',
        model_id: str = '',
        model_path: str = '',
        runtime_id: str = '',
        component_key: str = '',
        component_label: str = '',
        component_role: str = '',
    ) -> None:
        self.server_id = str(server_id or '').strip()
        self.label = label
        self.model_id = model_id
        self.model_path = model_path
        self.runtime_id = runtime_id
        self.component_key = component_key
        self.component_label = component_label
        self.component_role = component_role

    def __enter__(self) -> 'track_model_load':
        begin_model_load(
            self.server_id,
            label=self.label,
            model_id=self.model_id,
            model_path=self.model_path,
            runtime_id=self.runtime_id,
            component_key=self.component_key,
            component_label=self.component_label,
            component_role=self.component_role,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        end_model_load(self.server_id)
        return False
