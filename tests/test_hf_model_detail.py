from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def catalog_index(tmp_path: Path):
    from core import hf_catalog_index as index

    original = index._INDEX_PATH
    index.reset_index_runtime(path=tmp_path / 'hf-catalog-index.sqlite')
    yield index
    index.reset_index_runtime(path=original)


def _stub_local_match(monkeypatch):
    monkeypatch.setattr('core.hf_local_match.list_local_models', lambda **k: {'models': []})
    monkeypatch.setattr('core.hf_local_match.find_repo_local_installs', lambda *a, **k: [])
    monkeypatch.setattr('core.hf_local_match.local_installs_for_files', lambda *a, **k: {})
    monkeypatch.setattr('core.hf_local_match.is_catalog_ready_to_load', lambda *a, **k: False)


def test_get_model_detail_uses_siblings_without_tree_walk(monkeypatch):
    from core.huggingface import get_model_detail

    _stub_local_match(monkeypatch)
    raw = {
        'id': 'google/gemma-4-26B-A4B-it',
        'downloads': 1000,
        'likes': 10,
        'tags': ['transformers', 'image-text-to-text'],
        'pipeline_tag': 'image-text-to-text',
        'siblings': [
            {'rfilename': 'model.safetensors'},
            {'rfilename': 'config.json'},
        ],
    }
    tree_calls: list[str] = []
    monkeypatch.setattr('core.huggingface._request_json', lambda url, timeout=20: raw)
    monkeypatch.setattr('core.huggingface._fetch_readme_head', lambda *a, **k: '# Gemma 4')
    monkeypatch.setattr(
        'core.huggingface._resolve_repo_tree',
        lambda *a, **k: tree_calls.append('tree') or [],
    )

    result = get_model_detail('google/gemma-4-26B-A4B-it', category='all')
    assert result['success'] is True
    assert tree_calls == []
    files = result['model']['download_files']
    assert any(item.get('filename') == 'model.safetensors' for item in files)
    assert result['model']['readme'].startswith('# Gemma')


def test_get_model_detail_returns_local_when_hub_hangs(monkeypatch, catalog_index):
    from core.huggingface import get_model_detail

    _stub_local_match(monkeypatch)
    catalog_index.upsert_models([
        {
            'id': 'google/gemma-4-26B-A4B-it',
            'title': 'gemma-4-26B-A4B-it',
            'label': 'gemma-4-26B-A4B-it',
            'downloads': 100,
            'has_gguf': False,
            'tags': ['gemma'],
        },
    ])

    def hang(url, timeout=20):
        time.sleep(5)
        return {}

    monkeypatch.setattr('core.huggingface._fetch_readme_head', lambda *a, **k: '')
    monkeypatch.setattr('core.huggingface._request_json', hang)
    started = time.perf_counter()
    result = get_model_detail(
        'google/gemma-4-26B-A4B-it',
        category='all',
        hub_timeout=0.2,
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5
    assert result['success'] is True
    assert result.get('partial') is True
    assert result['model']['id'] == 'google/gemma-4-26B-A4B-it'
    assert result['model'].get('detail_partial') is True


def test_get_model_detail_keeps_readme_when_hub_info_hangs(monkeypatch, catalog_index):
    from core.huggingface import get_model_detail

    _stub_local_match(monkeypatch)
    catalog_index.upsert_models([
        {
            'id': 'google/gemma-4-E2B-it',
            'title': 'gemma-4-E2B-it',
            'label': 'gemma-4-E2B-it',
            'downloads': 100,
            'has_gguf': False,
            'tags': ['gemma'],
        },
    ])

    def hang(url, timeout=20):
        time.sleep(5)
        return {}

    monkeypatch.setattr('core.huggingface._request_json', hang)
    monkeypatch.setattr('core.huggingface._fetch_readme_head', lambda *a, **k: '# Gemma 4 E2B\n\nA Google Gemma model.')
    result = get_model_detail('google/gemma-4-E2B-it', category='all', hub_timeout=0.2)
    assert result.get('partial') is True
    assert result['model']['readme'].startswith('# Gemma 4 E2B')
    assert result['model'].get('readme_pending') is False


def test_get_model_readme_reports_pending_on_timeout(monkeypatch):
    from core.huggingface import get_model_readme

    def boom(*_a, **_k):
        raise TimeoutError('readme fetch timed out')

    monkeypatch.setattr('core.huggingface._fetch_readme_head', boom)
    result = get_model_readme('google/gemma-4-E2B-it', timeout=0.2)
    assert result['success'] is True
    assert result['readme'] == ''
    assert result['pending'] is True


def test_get_model_files_builds_download_options(monkeypatch):
    from core import huggingface as hf

    monkeypatch.setattr(hf, '_files_memory', {})
    monkeypatch.setattr(hf, '_files_disk_loaded', True)
    monkeypatch.setattr(hf, '_schedule_siblings_background', lambda *_a, **_k: None)
    monkeypatch.setattr(
        hf,
        '_siblings_from_hub',
        lambda repo: [
            {'rfilename': 'model.safetensors', 'size': 4_000_000_000},
            {'rfilename': 'config.json'},
        ],
    )
    result = hf.get_model_files('google/gemma-4-E2B-it', category='all')
    assert result['success'] is True
    assert result['pending'] is False
    assert any(item.get('filename') == 'model.safetensors' for item in result['download_files'])
    assert result['download_options']


def test_get_model_files_reports_pending_on_timeout(monkeypatch):
    from core import huggingface as hf

    monkeypatch.setattr(hf, '_files_memory', {})
    monkeypatch.setattr(hf, '_files_disk_loaded', True)
    scheduled: list[str] = []
    monkeypatch.setattr(hf, '_schedule_siblings_background', lambda repo: scheduled.append(repo))

    def hang(*_a, **_k):
        time.sleep(5)
        return []

    monkeypatch.setattr(hf, '_siblings_from_hub', hang)
    started = time.perf_counter()
    result = hf.get_model_files('google/gemma-4-E2B-it', category='all', timeout=0.2)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5
    assert result['success'] is True
    assert result['pending'] is True
    assert result['download_options'] == []
    assert scheduled == ['google/gemma-4-E2B-it']


def test_parse_curl_response_splits_headers_and_body():
    from core.huggingface import _parse_curl_response

    raw = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}'
    body, hdrs = _parse_curl_response(raw)
    assert body == b'{"ok":true}'
    assert hdrs.get('Content-Type') == 'application/json'


def test_get_or_fetch_detail_does_not_cache_partial(monkeypatch, tmp_path: Path):
    from core import hf_catalog_cache as cache

    monkeypatch.setattr(cache, '_CACHE_PATH', tmp_path / 'hf-catalog-cache.json')
    monkeypatch.setattr(cache, '_memory', {})
    monkeypatch.setattr(cache, '_detail_memory', {})
    monkeypatch.setattr(cache, '_loaded', True)

    payload = cache.get_or_fetch_detail(
        repo_id='google/gemma-4-26B-A4B-it',
        category='all',
        fetcher=lambda: {
            'success': True,
            'partial': True,
            'model': {'id': 'google/gemma-4-26B-A4B-it'},
        },
    )
    assert payload.get('partial') is True
    assert cache.get_cached_detail(repo_id='google/gemma-4-26B-A4B-it', category='all') is None
