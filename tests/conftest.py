"""Shared pytest setup.

Tests must never touch the real Console data root. Before this fixture existed
a download-state test wrote ``logs/hf-download-pending.json`` and wiped the
resume entry of a live download, so the model was forgotten after a restart.
"""

from __future__ import annotations

import pytest

from core import huggingface as hf


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
