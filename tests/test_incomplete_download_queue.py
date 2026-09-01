from __future__ import annotations

from pathlib import Path

from core.huggingface import _discover_incomplete_repo_jobs, list_download_jobs


def test_discover_incomplete_repo_jobs_finds_partial_deepseek(tmp_path, monkeypatch):
    model_dir = tmp_path / 'deepseek-ai' / 'DeepSeek-V4-Flash-0731'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{"model_type":"deepseek"}', encoding='utf-8')
    (model_dir / 'model-00001-of-00048.safetensors').write_bytes(b'x' * 2048)

    monkeypatch.setattr(
        'core.model_paths.disk_scan_roots',
        lambda cfg=None: [(tmp_path, 'dflash', '', 'Console')],
    )
    monkeypatch.setattr('core.local_models._catalog_repo_size_gb', lambda repo_id: 155.0)

    found = _discover_incomplete_repo_jobs({})
    assert len(found) == 1
    row = found[0]
    assert row['status'] == 'incomplete'
    assert row['resumable'] is True
    assert row['shard_present'] == 1
    assert row['shard_total'] == 48
    assert 'deepseek-ai/DeepSeek-V4-Flash-0731' in row['repo_id']
    assert row['id'] == 'incomplete::deepseek-ai--deepseek-v4-flash-0731'
    assert '/' not in row['id'].split('::', 1)[-1]


def test_resume_download_job_accepts_legacy_slash_id(monkeypatch):
    from core import huggingface as hf

    calls = {}

    def fake_start_repo_download(repo_id, **kwargs):
        calls['repo_id'] = repo_id
        calls['kwargs'] = kwargs
        return {'success': True, 'job_id': 'job-1', 'path': kwargs.get('dest_path')}

    with hf._jobs_lock:
        hf._download_jobs.clear()
        hf._cleared_ids.clear()
        hf._download_jobs['incomplete::org--model'] = {
            'id': 'incomplete::org--model',
            'repo_id': 'org/model',
            'filename': '',
            'status': 'incomplete',
            'path': 'C:/models/org/model',
            'kind': 'repo',
            'resumable': True,
            'incomplete': True,
        }

    monkeypatch.setattr(hf, '_ensure_download_history_loaded', lambda: None)
    monkeypatch.setattr(hf, '_merge_incomplete_repo_jobs', lambda cfg=None: None)
    monkeypatch.setattr(hf, 'start_repo_download', fake_start_repo_download)

    # Legacy slash form used by older UI/API clients.
    result = hf.resume_download_job('incomplete::org/model')
    assert result['success'] is True
    assert calls['repo_id'] == 'org/model'
    assert calls['kwargs'].get('allow_incomplete_resume') is True


def test_list_download_jobs_includes_incomplete(monkeypatch):
    monkeypatch.setattr(
        'core.huggingface._discover_incomplete_repo_jobs',
        lambda cfg=None: [{
            'id': 'incomplete::org--model',
            'repo_id': 'org/model',
            'filename': '',
            'status': 'incomplete',
            'path': 'C:/models/org/model',
            'kind': 'repo',
            'resumable': True,
            'incomplete': True,
            'shard_present': 1,
            'shard_total': 3,
            'bytes_read': 1000,
            'bytes_total': 3000,
            'started_at': 1,
        }],
    )
    monkeypatch.setattr('core.huggingface._ensure_download_history_loaded', lambda: None)
    monkeypatch.setattr('core.huggingface._merge_disk_download_history', lambda force=False: None)
    from core import huggingface as hf

    with hf._jobs_lock:
        hf._download_jobs.clear()
        hf._cleared_ids.clear()

    payload = list_download_jobs(active_only=True)
    assert payload['success'] is True
    assert payload['active_count'] >= 1
    assert any(job.get('status') == 'incomplete' for job in payload['jobs'])
