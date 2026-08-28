from __future__ import annotations

import core.huggingface as hf
from core.huggingface import (
    _connection_count,
    _download_parallel,
    _enrich_download_job,
    _estimate_parallel_bytes_written,
    _inspect_download_files,
    _is_transient_download_error,
    _looks_like_complete_download,
    _public_download_job,
    _range_appears_complete,
    _refresh_job_speed,
    _save_part_progress,
    _split_byte_ranges,
    clear_download_history,
    clear_download_job,
    list_download_jobs,
    resume_interrupted_downloads,
    save_pending_downloads,
)


def _reset_download_state(tmp_path):
    hf._download_jobs.clear()
    hf._cleared_ids.clear()
    hf._history_loaded = False
    hf._disk_scan_at = 0.0
    hf._HISTORY_PATH = tmp_path / 'hf-download-history.json'
    hf._PENDING_PATH = tmp_path / 'hf-download-pending.json'
    hf._discover_roots_override = [tmp_path]


def test_split_byte_ranges_covers_every_byte():
    ranges = _split_byte_ranges(1000, 4)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == 999
    assert sum(end - start + 1 for start, end in ranges) == 1000
    assert ranges == [(0, 249), (250, 499), (500, 749), (750, 999)]


def test_connection_count_stays_single_for_small_or_unranged():
    assert _connection_count(8 * 1024 * 1024, ranged=True) == 1
    assert _connection_count(80 * 1024 * 1024, ranged=False) == 1
    assert _connection_count(80 * 1024 * 1024, ranged=True) == 4
    assert _connection_count(512 * 1024 * 1024, ranged=True) == 4
    assert _connection_count(80 * 1024 * 1024, ranged=True, parallel_connections=6) == 6
    assert _connection_count(512 * 1024 * 1024, ranged=True, parallel_connections=2) == 2


def test_normalize_download_settings_clamps_parallel_connections():
    from core.config import normalize_download_settings

    assert normalize_download_settings(None) == {'parallel_connections': 4}
    assert normalize_download_settings({'parallel_connections': 6}) == {'parallel_connections': 6}
    assert normalize_download_settings({'parallel_connections': 99}) == {'parallel_connections': 8}
    assert normalize_download_settings({'parallel_connections': 0}) == {'parallel_connections': 1}


def test_add_job_bytes_leaves_progress_none_until_data_arrives():
    hf._download_jobs.clear()
    hf._download_jobs['live'] = {
        'id': 'live',
        'status': 'downloading',
        'bytes_read': 0,
        'bytes_total': None,
        'started_at': 1.0,
    }
    hf._add_job_bytes('live', 0, 1_000_000)
    job = hf._download_jobs['live']
    assert job['bytes_total'] == 1_000_000
    assert job['progress'] is None
    hf._add_job_bytes('live', 50_000, 1_000_000)
    assert job['bytes_read'] == 50_000
    assert job['progress'] == 5.0


def test_refresh_job_speed_computes_bytes_per_second():
    job = {
        'bytes_read': 20_000_000,
        'bytes_total': 100_000_000,
        'started_at': 100.0,
        '_speed_at': 100.0,
        '_speed_bytes': 0,
        'speed_bps': 0.0,
    }
    _refresh_job_speed(job, now=101.0)
    assert job['speed_bps'] == 20_000_000
    assert job['eta_seconds'] == 4


def test_public_download_job_hides_internal_speed_fields():
    row = _public_download_job({
        'id': '1',
        'speed_bps': 12.5,
        '_speed_at': 1,
        '_speed_bytes': 8,
        'post_action': {'type': 'wire_vision'},
    })
    assert row['speed_bps'] == 12.5
    assert '_speed_at' not in row
    assert '_speed_bytes' not in row
    assert 'post_action' not in row


def test_finished_downloads_persist_and_reload(tmp_path):
    _reset_download_state(tmp_path)
    hf._download_jobs['done-1'] = {
        'id': 'done-1',
        'repo_id': 'org/model',
        'filename': 'model.gguf',
        'status': 'downloading',
        'started_at': 10.0,
    }
    hf._mark_job_finished('done-1', 'done', path=str(tmp_path / 'model.gguf'))
    assert hf._HISTORY_PATH.is_file()

    hf._download_jobs.clear()
    hf._history_loaded = False
    listed = list_download_jobs()
    assert listed['count'] == 1
    assert listed['jobs'][0]['id'] == 'done-1'
    assert listed['jobs'][0]['status'] == 'done'
    assert listed['active_count'] == 0


def test_clear_history_leaves_active_downloads(tmp_path):
    _reset_download_state(tmp_path)
    hf._history_loaded = True
    hf._download_jobs['live'] = {
        'id': 'live',
        'status': 'downloading',
        'started_at': 1.0,
    }
    hf._download_jobs['old'] = {
        'id': 'old',
        'status': 'done',
        'started_at': 1.0,
        'finished_at': 2.0,
    }
    assert clear_download_job('live')['success'] is False
    assert clear_download_job('old')['success'] is True
    assert 'old' not in hf._download_jobs

    hf._download_jobs['kept'] = {
        'id': 'kept',
        'status': 'error',
        'started_at': 1.0,
        'finished_at': 2.0,
        'error': 'failed',
    }
    result = clear_download_history()
    assert result['success'] is True
    assert result['cleared'] == 1
    assert 'live' in hf._download_jobs
    assert 'kept' not in hf._download_jobs


def test_disk_models_fill_last_downloads(tmp_path):
    _reset_download_state(tmp_path)
    model = tmp_path / 'unsloth' / 'Qwen3-8B-GGUF' / 'qwen.gguf'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'0' * 20_000)
    listed = list_download_jobs(discover=True, console_only=False)
    assert listed['count'] == 1
    job = listed['jobs'][0]
    assert job['origin'] == 'disk'
    assert job['repo_id'] == 'unsloth/Qwen3-8B-GGUF'
    assert job['filename'] == 'qwen.gguf'
    assert clear_download_job(job['id'])['success'] is True
    hf._disk_scan_at = 0.0
    again = list_download_jobs(discover=True, console_only=False)
    assert again['count'] == 0


def test_console_only_downloads_exclude_disk_scan(tmp_path):
    _reset_download_state(tmp_path)
    model = tmp_path / 'unsloth' / 'Qwen3-8B-GGUF' / 'qwen.gguf'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'0' * 20_000)
    hf._download_jobs['console-1'] = {
        'id': 'console-1',
        'repo_id': 'org/console-model',
        'filename': 'model.gguf',
        'status': 'done',
        'finished_at': 100.0,
    }
    listed = list_download_jobs(discover=True, console_only=True)
    assert listed['count'] == 1
    assert listed['jobs'][0]['id'] == 'console-1'
    with_disk = list_download_jobs(discover=True, console_only=False)
    assert with_disk['count'] == 2


def test_pending_downloads_persist_for_resume(tmp_path, monkeypatch):
    _reset_download_state(tmp_path)
    hf._download_jobs['live'] = {
        'id': 'live',
        'repo_id': 'org/model',
        'filename': 'model.gguf',
        'status': 'downloading',
        'progress': 12.5,
        'bytes_read': 125,
        'bytes_total': 1000,
        'path': str(tmp_path / 'org' / 'model' / 'model.gguf'),
        'library_id': '',
        'started_at': 10.0,
    }
    save_pending_downloads()
    assert hf._PENDING_PATH.is_file()
    hf._download_jobs.clear()
    monkeypatch.setattr(hf, '_download_worker_once', lambda *args, **kwargs: None)
    listed = resume_interrupted_downloads(cfg={'model_libraries': [{'id': 'default', 'path': str(tmp_path), 'enabled': True}]})
    assert listed['count'] == 1
    assert listed['resumed'] == ['live']
    assert hf._download_jobs['live']['status'] == 'downloading'


def test_finished_download_clears_pending(tmp_path):
    _reset_download_state(tmp_path)
    hf._download_jobs['done-1'] = {
        'id': 'done-1',
        'repo_id': 'org/model',
        'filename': 'model.gguf',
        'status': 'downloading',
        'started_at': 10.0,
        'path': str(tmp_path / 'model.gguf'),
    }
    save_pending_downloads()
    hf._mark_job_finished('done-1', 'done', path=str(tmp_path / 'model.gguf'))
    assert not hf._PENDING_PATH.is_file() or hf._load_pending_downloads() == []


def test_transient_download_errors_are_retriable():
    import urllib.error

    assert _is_transient_download_error(TimeoutError())
    assert _is_transient_download_error(ConnectionResetError())
    assert _is_transient_download_error(urllib.error.URLError('network down'))
    assert _is_transient_download_error(OSError('range 0-99 ended early'))
    assert not _is_transient_download_error(urllib.error.HTTPError('url', 404, 'missing', {}, None))
    assert _is_transient_download_error(urllib.error.HTTPError('url', 503, 'busy', {}, None))


def test_preallocated_part_is_not_treated_as_complete(tmp_path):
    part = tmp_path / 'model.gguf.part'
    part.write_bytes(b'GGUF' + b'\x00' * 100)
    part.write_bytes(b'\x00' * 8192)  # extend via seek/truncate
    with part.open('r+b') as handle:
        handle.truncate(5000)
    assert not _looks_like_complete_download(part, total=5000)


def test_complete_gguf_tail_is_detected(tmp_path):
    path = tmp_path / 'model.gguf'
    with path.open('wb') as handle:
        handle.write(b'GGUF' + b'\x01' * 100)
        handle.seek(9000)
        handle.write(b'\x02' * 1000)
    assert _looks_like_complete_download(path)


def test_preallocated_parallel_part_resumes_completed_ranges(tmp_path, monkeypatch):
    _reset_download_state(tmp_path)
    part = tmp_path / 'model.gguf.part'
    total = 1000
    with part.open('wb') as handle:
        handle.truncate(total)
        handle.seek(0)
        handle.write(b'GGUF' + b'\x01' * 246)
        handle.seek(750)
        handle.write(b'\x02' * 250)
    ranges = _split_byte_ranges(total, 4)
    _save_part_progress(
        part,
        total=total,
        completed_ranges=[f'{ranges[0][0]}-{ranges[0][1]}', f'{ranges[3][0]}-{ranges[3][1]}'],
        bytes_read=500,
    )
    hf._download_jobs['job'] = {
        'id': 'job',
        'status': 'downloading',
        'bytes_read': 500,
        'bytes_total': total,
        'started_at': 1.0,
    }
    downloaded: list[tuple[int, int]] = []

    def fake_range(job_id, url, headers, dest, start, end, total_bytes):
        downloaded.append((start, end))

    monkeypatch.setattr(hf, '_download_range', fake_range)
    _download_parallel('job', 'http://example.test/file', {}, part, total, 4, resume=True)
    assert set(downloaded) == {ranges[1], ranges[2]}


def test_resume_interrupted_downloads_starts_worker_without_network(tmp_path, monkeypatch):
    _reset_download_state(tmp_path)
    dest = tmp_path / 'org' / 'model' / 'model.gguf'
    dest.parent.mkdir(parents=True)
    hf._download_jobs['live'] = {
        'id': 'live',
        'repo_id': 'org/model',
        'filename': 'model.gguf',
        'status': 'downloading',
        'progress': 12.5,
        'bytes_read': 125,
        'bytes_total': 1000,
        'path': str(dest),
        'library_id': '',
        'started_at': 10.0,
    }
    save_pending_downloads()
    hf._download_jobs.clear()
    called: list[bool] = []

    def fake_worker_once(job_id, repo_id, filename, dest_path, *, resume=False):
        called.append(resume)

    monkeypatch.setattr(hf, '_download_worker_once', fake_worker_once)
    listed = resume_interrupted_downloads(cfg={'model_libraries': [{'id': 'default', 'path': str(tmp_path), 'enabled': True}]})
    import time
    time.sleep(0.2)
    assert listed['count'] == 1
    assert listed['resumed'] == ['live']
    assert called == [True]


def test_range_appears_complete_checks_tail_not_file_size(tmp_path):
    part = tmp_path / 'model.gguf.part'
    with part.open('wb') as handle:
        handle.truncate(1000)
        handle.seek(900)
        handle.write(b'\xff' * 100)
    assert _range_appears_complete(part, 750, 999)
    assert not _range_appears_complete(part, 250, 499)


def test_inspect_download_files_reports_partial_parallel_part(tmp_path, monkeypatch):
    monkeypatch.setattr(hf, 'get_download_parallel_connections', lambda: 4)
    monkeypatch.setattr(hf, '_connection_count', lambda total, ranged=True, parallel_connections=4: 4)
    dest = tmp_path / 'model.gguf'
    part = dest.with_suffix(dest.suffix + '.part')
    total = 1000
    with part.open('wb') as handle:
        handle.truncate(total)
        handle.seek(750)
        handle.write(b'\x02' * 250)
    inspect = _inspect_download_files(dest, total=total)
    assert inspect['part_exists'] is True
    assert inspect['complete'] is False
    assert inspect['disk_bytes'] == 250
    row = _enrich_download_job({
        'id': 'job',
        'repo_id': 'org/model',
        'filename': 'model.gguf',
        'status': 'done',
        'bytes_read': 0,
        'bytes_total': total,
        'path': str(dest),
    })
    assert row['status'] == 'incomplete'
    assert row['disk_bytes'] == 250
    assert row['progress'] == 25.0


def test_estimate_parallel_bytes_written_uses_completed_ranges(tmp_path):
    part = tmp_path / 'model.gguf.part'
    total = 1000
    with part.open('wb') as handle:
        handle.truncate(total)
        handle.seek(750)
        handle.write(b'\x02' * 250)
    assert _estimate_parallel_bytes_written(part, total, 4) == 250
