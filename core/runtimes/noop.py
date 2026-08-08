"""No-op runtime adapter.

Validates the adapter contract and lets the UI list installed adapters before
any real inference runtime exists. Never spawns a process and contributes no
process-identity tokens, so it cannot affect managed-process cleanup.
"""

from __future__ import annotations

from typing import Any

from core.runtimes.base import EXECUTION_MODE_CLI


class NoopRuntimeAdapter:
    runtime_id = 'noop'
    modalities = ('llm',)
    execution_mode = EXECUTION_MODE_CLI
    process_identity_tokens = ()

    def health(self) -> dict[str, Any]:
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'running': False,
            'execution_mode': self.execution_mode,
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        return {'success': True, 'loaded': True, 'runtime_id': self.runtime_id}

    def unload(self) -> dict[str, Any]:
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return []
