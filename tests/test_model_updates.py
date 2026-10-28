"""Model update detection and replacement transaction tests."""

from __future__ import annotations

import json
import time

from core import huggingface as hf
from core import model_updates as updates


def _row(path, repo='org/model'):
    return {
        'path': str(path),
        'hf_repo': repo,
        'filename': path.name,
        'label': path.name,
        'server_id': 'server-1',
    }


def test_manual_source_never_becomes_update(tmp_path, monkeypatch):
    cache_path = tmp_path / 'cache.json'
    monkeypatch.setattr(updates, '_CACHE_PATH', cache_path)
    row = _row(tmp_path / 'copied-model.gguf')
    updates.annotate_models([row])

    assert row['local_changed'] == ''
    assert row['hf_source_verified'] is False
    assert row['hf_update_available'] is False
    assert row['hf_update_status'] == 'unknown'


def test_known_download_baselines_remote_revision(tmp_path, monkeypatch):
    cache_path = tmp_path / 'cache.json'
    monkeypatch.setattr(updates, '_CACHE_PATH', cache_path)
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'old')
    row = _row(model)
    monkeypatch.setattr(
        updates,
        '_history_rows',
        lambda: [{'repo_id': 'org/model', 'path': str(model), 'finished_at': 100}],
    )
    monkeypatch.setattr(
        updates,
        '_fetch_remote',
        lambda _repo: {
            'success': True,
            'revision': 'revision-a',
            'last_modified': '1970-01-01T00:01:00Z',
            'files': [{'filename': 'model.gguf', 'size_bytes': 3}],
        },
    )

    result = updates.refresh_model_updates([row], force=True)

    assert result['success'] is True
    assert row['hf_update_status'] == 'current'
    assert row['hf_update_available'] is False
    payload = json.loads(cache_path.read_text(encoding='utf-8'))
    assert payload['models']
    assert next(iter(payload['models'].values()))['installed_revision'] == 'revision-a'


def test_changed_remote_revision_is_update_ready(tmp_path, monkeypatch):
    cache_path = tmp_path / 'cache.json'
    monkeypatch.setattr(updates, '_CACHE_PATH', cache_path)
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'old')
    row = _row(model)
    monkeypatch.setattr(
        updates,
        '_history_rows',
        lambda: [{'repo_id': 'org/model', 'path': str(model), 'finished_at': 100}],
    )
    remote = {
        'success': True,
        'revision': 'revision-a',
        'last_modified': '1970-01-01T00:01:00Z',
        'files': [{'filename': 'model.gguf', 'size_bytes': 3}],
    }
    monkeypatch.setattr(updates, '_fetch_remote', lambda _repo: remote)
    updates.refresh_model_updates([row], force=True)

    remote['revision'] = 'revision-b'
    remote['last_modified'] = '1970-01-01T00:03:00Z'
    updates.refresh_model_updates([row], force=True)

    assert row['hf_update_available'] is True
    assert row['hf_update_status'] == 'update-ready'
    details = updates.get_model_update_details(row)
    assert details['success'] is True
    assert details['details']['old_revision'] == 'revision-a'
    assert details['details']['new_revision'] == 'revision-b'
    assert details['details']['files'][0]['filename'] == 'model.gguf'


def test_file_replacement_can_roll_back(tmp_path):
    old = tmp_path / 'model.gguf'
    stage = tmp_path / '.stage'
    backup = tmp_path / '.backup'
    old.write_bytes(b'old')
    stage.mkdir()
    (stage / old.name).write_bytes(b'new')

    updates._swap_in(
        old,
        stage,
        is_directory=False,
        local_paths=[old],
        selected_files=[{'filename': old.name, 'size_bytes': 3}],
        backup=backup,
    )
    assert old.read_bytes() == b'new'

    updates._rollback(old, backup, is_directory=False, local_paths=[old])
    assert old.read_bytes() == b'old'


def test_staged_update_replaces_file_without_changing_profile(tmp_path, monkeypatch):
    cache_path = tmp_path / 'cache.json'
    monkeypatch.setattr(updates, '_CACHE_PATH', cache_path)
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'old')
    row = _row(model)
    row['server_id'] = ''
    monkeypatch.setattr(
        updates,
        '_history_rows',
        lambda: [{'repo_id': 'org/model', 'path': str(model), 'finished_at': 100}],
    )
    remote = {
        'success': True,
        'revision': 'revision-a',
        'last_modified': '1970-01-01T00:01:00Z',
        'files': [{'filename': 'model.gguf', 'size_bytes': 3}],
    }
    monkeypatch.setattr(updates, '_fetch_remote', lambda _repo: remote)
    updates.refresh_model_updates([row], force=True)
    remote['revision'] = 'revision-b'
    remote['last_modified'] = '1970-01-01T00:03:00Z'
    updates.refresh_model_updates([row], force=True)

    def fake_download(_repo, _filename, *, dest_path, **_kwargs):
        from pathlib import Path

        Path(dest_path).write_bytes(b'new')
        return {'success': True, 'job_id': 'download-1'}

    monkeypatch.setattr(hf, 'start_download', fake_download)
    monkeypatch.setattr(
        hf,
        'get_download_job',
        lambda _job_id: {'success': True, 'job': {'status': 'done', 'progress': 100}},
    )
    result = updates.start_model_update(row, cfg={'servers': []})
    assert result['success'] is True

    for _ in range(50):
        status = updates.get_update_job(result['job_id'])['job']
        if status['status'] in {'done', 'error'}:
            break
        time.sleep(0.01)

    assert status['status'] == 'done'
    assert model.read_bytes() == b'new'
    assert not (tmp_path / f'.dflash-backup-{result["job_id"]}').exists()
