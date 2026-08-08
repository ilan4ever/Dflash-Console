from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def test_hf_catalog_cache_round_trip(tmp_path: Path, monkeypatch):
    from core import hf_catalog_cache as cache

    cache_path = tmp_path / 'hf-catalog-cache.json'
    monkeypatch.setattr(cache, '_CACHE_PATH', cache_path)
    monkeypatch.setattr(cache, '_memory', {})
    monkeypatch.setattr(cache, '_loaded', False)

    payload = {
        'success': True,
        'models': [{'id': 'z-lab/example', 'title': 'Example'}],
        'query': '',
        'sort': 'downloads',
        'category': 'dflash',
        'limit': 25,
    }
    cache.put_cached_search(
        query='',
        sort='downloads',
        category='dflash',
        limit=25,
        payload=payload,
    )

    hit = cache.get_cached_search(query='', sort='downloads', category='dflash', limit=25)
    assert hit is not None
    assert hit['payload']['models'][0]['id'] == 'z-lab/example'

    # Reload from disk in a fresh module state.
    monkeypatch.setattr(cache, '_memory', {})
    monkeypatch.setattr(cache, '_loaded', False)
    disk = json.loads(cache_path.read_text(encoding='utf-8'))
    assert 'dflash|downloads|25|' in disk['entries']

    hit2 = cache.get_cached_search(query='', sort='downloads', category='dflash', limit=25)
    assert hit2 is not None
    assert hit2['payload']['models'][0]['title'] == 'Example'


def test_search_with_cache_returns_stale_payload(monkeypatch):
    from core import hf_catalog_cache as cache

    monkeypatch.setattr(cache, '_CACHE_PATH', Path('/nonexistent/hf-catalog-cache.json'))
    monkeypatch.setattr(cache, '_memory', {})
    monkeypatch.setattr(cache, '_loaded', True)
    monkeypatch.setattr(cache, '_refreshing', set())

    stale_payload = {
        'success': True,
        'models': [{'id': f'cached/repo-{index}'} for index in range(8)],
        'query': '',
        'sort': 'downloads',
        'category': 'all-gguf',
        'limit': 25,
    }
    cache._memory['all-gguf|downloads|25|'] = {
        'fetched_at': time.time() - 9999,
        'payload': stale_payload,
    }

    scheduled = []

    def fake_schedule(key, fetcher):
        scheduled.append(key)

    monkeypatch.setattr(cache, '_schedule_refresh', fake_schedule)

    result = cache.search_with_cache(
        query='',
        sort='downloads',
        category='all-gguf',
        limit=25,
        fetcher=lambda: {'success': True, 'models': [{'id': 'live/repo'}]},
    )

    assert result['cached'] is True
    assert result['stale'] is True
    assert result['models'][0]['id'] == 'cached/repo-0'
    assert scheduled == ['all-gguf|downloads|25|']


def test_capable_stack_row_is_not_loadable(tmp_path: Path):
    from core.local_models import _capable_stack_row

    target = tmp_path / 'target.gguf'
    draft = tmp_path / 'draft-dflash.gguf'
    target.write_bytes(b'x' * 16)
    draft.write_bytes(b'y' * 8)

    row = _capable_stack_row({
        'path': str(target),
        'draft_path': str(draft),
        'draft_filename': draft.name,
    })
    assert row['loadable'] is False
    assert row['stack_status'] == 'unregistered'
