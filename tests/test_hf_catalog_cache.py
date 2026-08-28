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
        'models': [
            {'id': f'cached/repo-{index}', 'size_gb': 1.0, 'size_label': '1 GB', 'has_gguf': True}
            for index in range(8)
        ],
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


def test_composed_supported_search_from_subcaches(monkeypatch):
    from core import hf_catalog_cache as cache

    monkeypatch.setattr(cache, '_CACHE_PATH', Path('/nonexistent/hf-catalog-cache.json'))
    monkeypatch.setattr(cache, '_memory', {})
    monkeypatch.setattr(cache, '_loaded', True)
    monkeypatch.setattr(cache, '_refreshing', set())

    now = time.time()
    for index, category in enumerate(('dflash', 'all-gguf')):
        cache._memory[f'{category}|downloads|25|'] = {
            'fetched_at': now,
            'payload': {
                'success': True,
                'models': [
                    {
                        'id': f'org/model-{category}-{row}',
                        'downloads': 1000 - row,
                        'has_gguf': True,
                        'gguf_count': 1,
                        'modality': 'llm',
                    }
                    for row in range(5)
                ],
            },
        }

    composed = cache._try_composed_supported_search(sort='downloads', limit=25)
    assert composed is not None
    assert composed['composed_from_subcaches'] is True
    assert len(composed['models']) == 10


def test_annotate_hf_models_fit_reuses_budget(monkeypatch):
    from core.hf_model_fit import annotate_hf_models_fit

    calls = {'count': 0}

    def fake_budget(cfg=None):
        calls['count'] += 1
        return {'fits_budget_gb': 10.0, 'vram_total_gb': 12.0, 'vram_free_gb': 8.0, 'gpu_count': 1}

    monkeypatch.setattr('core.hf_model_fit.machine_fit_budget_gb', fake_budget)
    rows = [
        {'id': 'org/a', 'has_gguf': True, 'size_gb': 1.0},
        {'id': 'org/b', 'has_gguf': True, 'size_gb': 2.0},
    ]
    annotate_hf_models_fit(rows, category='supported')
    assert calls['count'] == 1
    assert all(row.get('fits_machine') for row in rows)


def test_lookup_hf_repo_models_resolves_slug(monkeypatch):
    from core.huggingface import _lookup_hf_repo_models

    def fake_detail(repo_id, *, category='all'):
        if repo_id == 'deepseek-ai/DeepSeek-V4-Flash':
            return {'success': True, 'model': {'id': repo_id, 'downloads': 5000, 'runnable': True}}
        return {'success': False, 'error': 'missing'}

    def fake_request_json(url, timeout=20):
        if 'search=deepseek-v4-flash' in url or 'search=deepseek+v4+flash' in url:
            return [{'id': 'deepseek-ai/DeepSeek-V4-Flash', 'downloads': 5000}]
        return []

    monkeypatch.setattr('core.huggingface.get_model_detail', fake_detail)
    monkeypatch.setattr('core.huggingface._request_json', fake_request_json)
    models = _lookup_hf_repo_models('deepseek/deepseek-v4-flash', category='supported')
    assert models
    assert models[0]['id'] == 'deepseek-ai/DeepSeek-V4-Flash'


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
    assert not str(row['model_id']).startswith('stack-capable:')
    assert row['model_id'] == row['label']
