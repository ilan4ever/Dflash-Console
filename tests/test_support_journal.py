from __future__ import annotations

from pathlib import Path

from core.support_journal import (
    JOURNAL_PATH,
    META_PATH,
    ensure_support_meta,
    journal_event,
    read_journal_lines,
    record_model_event,
)


def test_support_meta_records_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr('core.support_journal.LOG_DIR', tmp_path)
    monkeypatch.setattr('core.support_journal.JOURNAL_PATH', tmp_path / 'support-journal.log')
    monkeypatch.setattr('core.support_journal.META_PATH', tmp_path / 'support-meta.json')

    first = ensure_support_meta(shell_version='1.0.0')
    second = ensure_support_meta(shell_version='1.0.1')

    assert first['first_run_at']
    assert first['session_count'] == 1
    assert second['session_count'] == 2
    assert second['shell_version'] == '1.0.1'


def test_journal_rotates_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr('core.support_journal.LOG_DIR', tmp_path)
    journal = tmp_path / 'support-journal.log'
    monkeypatch.setattr('core.support_journal.JOURNAL_PATH', journal)
    monkeypatch.setattr('core.support_journal.META_PATH', tmp_path / 'support-meta.json')
    monkeypatch.setattr('core.support_journal.MAX_JOURNAL_LINES', 5)

    for index in range(8):
        journal_event('test', f'line-{index}')

    lines = read_journal_lines(tail=10)
    assert len(lines) <= 5
    assert 'line-7' in lines[-1]


def test_record_model_event_writes_history(tmp_path, monkeypatch):
    monkeypatch.setattr('core.support_journal.LOG_DIR', tmp_path)
    monkeypatch.setattr('core.support_journal.JOURNAL_PATH', tmp_path / 'support-journal.log')
    meta = tmp_path / 'support-meta.json'
    monkeypatch.setattr('core.support_journal.META_PATH', meta)

    record_model_event(event='load', server_id='gemma', model_id='m1', client='OneVoice')
    payload = meta.read_text(encoding='utf-8')
    assert 'gemma' in payload
    assert 'OneVoice' in payload
