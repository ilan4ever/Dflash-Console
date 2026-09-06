from __future__ import annotations

import pytest


def test_shutdown_releases_engines_by_default(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    monkeypatch.delenv('DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN', raising=False)
    monkeypatch.setattr('core.config.load_config', lambda: {'keep_models_loaded_on_exit': False})
    monkeypatch.setattr(
        'core.engine_state.release_and_stop_all_managed_engines',
        lambda **kwargs: calls.append('release') or [],
    )

    from api.app import _release_gpu_on_shutdown

    _release_gpu_on_shutdown()
    assert calls == ['release']


def test_shutdown_skips_engine_release_when_keep_flag_set(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    monkeypatch.setattr('core.config.load_config', lambda: {'keep_models_loaded_on_exit': True})
    monkeypatch.setattr(
        'core.engine_state.release_and_stop_all_managed_engines',
        lambda **kwargs: calls.append('release') or [],
    )

    from api.app import _release_gpu_on_shutdown

    _release_gpu_on_shutdown()
    assert calls == []
