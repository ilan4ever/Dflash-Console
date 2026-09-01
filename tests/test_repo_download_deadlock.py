from __future__ import annotations

import threading
import time
from pathlib import Path

import core.huggingface as hf


def test_list_download_jobs_does_not_deadlock_during_repo_poll(tmp_path, monkeypatch):
    """Regression: repo progress poller must not nest _jobs_lock acquisitions."""
    dest = tmp_path / 'deepseek-ai' / 'DeepSeek-V4-Flash-0731'
    dest.mkdir(parents=True)
    (dest / 'model-00001-of-00048.safetensors').write_bytes(b'x' * 64)

    job_id = 'deadlock-test-job'
    hf._download_jobs.clear()
    hf._download_jobs[job_id] = {
        'id': job_id,
        'repo_id': 'deepseek-ai/DeepSeek-V4-Flash-0731',
        'filename': '',
        'status': 'downloading',
        'progress': 5.0,
        'bytes_read': 64,
        'bytes_total': 1024,
        'path': str(dest),
        'kind': 'repo',
        'started_at': time.time(),
    }

    stop = threading.Event()

    def poll_like_worker() -> None:
        from core.local_models import _weight_shard_status

        while not stop.wait(0.05):
            disk_bytes = hf._directory_download_bytes(dest)
            shard_status = _weight_shard_status(dest)
            refreshed_total = None
            with hf._jobs_lock:
                job = hf._download_jobs.get(job_id)
                if not job:
                    return
                total = int(job.get('bytes_total') or 0)
                need_total_refresh = total <= 0 or disk_bytes > total
            if need_total_refresh:
                refreshed_total = hf._repo_expected_bytes(
                    'deepseek-ai/DeepSeek-V4-Flash-0731',
                    fallback=total or None,
                )
            with hf._jobs_lock:
                job = hf._download_jobs.get(job_id)
                if not job:
                    return
                total = int(job.get('bytes_total') or 0)
                if refreshed_total and refreshed_total > total:
                    job['bytes_total'] = refreshed_total
                job['bytes_read'] = max(int(job.get('bytes_read') or 0), disk_bytes)
                hf._apply_shard_status_to_job(job, shard_status)
                hf._refresh_job_speed(job)

    worker = threading.Thread(target=poll_like_worker, daemon=True)
    worker.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            payload = hf.list_download_jobs()
            assert payload.get('success') is True
            time.sleep(0.05)
    finally:
        stop.set()
        worker.join(timeout=2.0)
        hf._download_jobs.clear()
