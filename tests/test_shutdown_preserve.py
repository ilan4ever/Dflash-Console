from __future__ import annotations

import pytest


def test_shutdown_skips_engine_release_without_flag(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    monkeypatch.delenv('DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN', raising=False)
    monkeypatch.setattr(
        'core.engine_state.release_and_stop_all_managed_engines',
        lambda **kwargs: calls.append('release') or [],
    )

    from api.app import _release_gpu_on_shutdown

    _release_gpu_on_shutdown()
    assert calls == []


def test_shutdown_releases_engines_when_flag_set(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    monkeypatch.setenv('DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN', '1')
    monkeypatch.setattr(
        'core.engine_state.release_and_stop_all_managed_engines',
        lambda **kwargs: calls.append('release') or [{'server_id': 'demo'}],
    )

    from api.app import _release_gpu_on_shutdown

    _release_gpu_on_shutdown()
    assert calls == ['release']
