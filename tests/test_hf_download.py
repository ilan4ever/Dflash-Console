from __future__ import annotations

import core.huggingface as hf
from core.huggingface import (
    _connection_count,
    _public_download_job,
    _refresh_job_speed,
    _split_byte_ranges,
    clear_download_history,
    clear_download_job,
    list_download_jobs,
)


def _reset_download_state(tmp_path):
    hf._download_jobs.clear()
    hf._cleared_ids.clear()
    hf._history_loaded = False
    hf._disk_scan_at = 0.0
    hf._HISTORY_PATH = tmp_path / 'hf-download-history.json'
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
    assert _connection_count(512 * 1024 * 1024, ranged=True) == 6


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
    listed = list_download_jobs(discover=True)
    assert listed['count'] == 1
    job = listed['jobs'][0]
    assert job['origin'] == 'disk'
    assert job['repo_id'] == 'unsloth/Qwen3-8B-GGUF'
    assert job['filename'] == 'qwen.gguf'
    assert clear_download_job(job['id'])['success'] is True
    hf._disk_scan_at = 0.0
    again = list_download_jobs(discover=True)
    assert again['count'] == 0
