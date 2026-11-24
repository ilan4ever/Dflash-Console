"""Shared pytest setup.

Tests must never touch the real Console data root. Before this fixture existed
a download-state test wrote ``logs/hf-download-pending.json`` and wiped the
resume entry of a live download, so the model was forgotten after a restart.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from core import auth
from core import huggingface as hf

# The Console only listens on 127.0.0.1, so a test client acts as a program on
# this PC by default. Starlette's own default address ("testclient") is not an
# IP address and would count as another computer. Tests of sign-in and machine
# keys pass client=("192.168.1.50", 50000) or similar to act as one.
_testclient_init = TestClient.__init__


def _local_testclient_init(self, app, *args, client=('127.0.0.1', 50000), **kwargs):
    _testclient_init(self, app, *args, client=client, **kwargs)


TestClient.__init__ = _local_testclient_init


@pytest.fixture(autouse=True)
def _isolate_auth_state(tmp_path, monkeypatch):
    """Sign-in data goes to a per-test auth.json, never the real data root.

    scrypt also runs at a test-sized cost; the production cost is checked in
    test_auth_store.py.
    """
    monkeypatch.setattr(auth, 'AUTH_PATH', tmp_path / 'auth.json')
    monkeypatch.setattr(auth, 'SCRYPT_N', 2 ** 10)
    auth.reset_cache()
    auth.LOGIN_THROTTLE.reset()
    yield
    auth.reset_cache()
    auth.LOGIN_THROTTLE.reset()


@pytest.fixture(autouse=True)
def _isolate_hf_download_state(tmp_path, monkeypatch):
    """Point every HF download state file at a per-test temp directory."""
    monkeypatch.setattr(hf, '_HISTORY_PATH', tmp_path / 'hf-download-history.json')
    monkeypatch.setattr(hf, '_PENDING_PATH', tmp_path / 'hf-download-pending.json')
    monkeypatch.setattr(hf, '_discover_roots_override', [tmp_path])
    monkeypatch.setattr(hf, '_supplemental_history_paths', lambda: [])
    with hf._jobs_lock:
        hf._download_jobs.clear()
    hf._cleared_ids.clear()
    hf._history_loaded = False
    hf._disk_scan_at = 0.0
    hf._last_pending_save_at.clear()
    hf._last_pending_save_bytes.clear()
    yield
    with hf._jobs_lock:
        hf._download_jobs.clear()
    hf._cleared_ids.clear()
