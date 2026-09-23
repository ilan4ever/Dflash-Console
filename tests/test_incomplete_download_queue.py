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


def test_same_repo_downloads_use_parallel_destination_jobs(tmp_path, monkeypatch):
    from core import huggingface as hf

    with hf._jobs_lock:
        hf._download_jobs.clear()
        hf._cleared_ids.clear()
    monkeypatch.setattr(hf, '_is_under_allowed_model_root', lambda path, cfg: True)
    monkeypatch.setattr(hf, '_save_pending_downloads', lambda: None)
    monkeypatch.setattr(hf, '_repo_download_worker', lambda *args: None)

    first_path = tmp_path / 'first'
    second_path = tmp_path / 'second'
    first = hf.start_repo_download('org/model', dest_path=str(first_path), cfg={})
    second = hf.start_repo_download('org/model', dest_path=str(second_path), cfg={})

    assert first['success'] is True
    assert second['success'] is True
    assert second.get('already_running') is not True
    assert first['job_id'] != second['job_id']

    same_target = hf.start_repo_download('org/model', dest_path=str(first_path), cfg={})
    assert same_target['success'] is True
    assert same_target['already_running'] is True
    assert same_target['job_id'] == first['job_id']


def test_component_resume_recovers_parent_repo_id(tmp_path, monkeypatch):
    from core import huggingface as hf

    model_root = tmp_path / 'Qwen' / 'Qwen-Image-2.1'
    component = model_root / 'text_encoder'
    component.mkdir(parents=True)
    (model_root / 'config.json').write_text('{}', encoding='utf-8')
    with hf._jobs_lock:
        hf._download_jobs.clear()
        hf._cleared_ids.clear()
    monkeypatch.setattr(hf, 'allowed_model_roots', lambda cfg: [tmp_path])
    monkeypatch.setattr(hf, '_is_under_allowed_model_root', lambda path, cfg: True)
    monkeypatch.setattr(hf, '_save_pending_downloads', lambda: None)
    monkeypatch.setattr(hf, '_repo_download_worker', lambda *args: None)

    result = hf.start_repo_download(
        'Qwen-Image-2.1/text_encoder',
        dest_path=str(component),
        cfg={},
    )

    assert result['success'] is True
    assert hf._download_jobs[result['job_id']]['repo_id'] == 'Qwen/Qwen-Image-2.1'


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
