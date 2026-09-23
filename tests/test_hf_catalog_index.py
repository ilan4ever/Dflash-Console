from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.huggingface import _parse_link_next


@pytest.fixture
def catalog_index(tmp_path: Path):
    from core import hf_catalog_index as index

    original = index._INDEX_PATH
    index.reset_index_runtime(path=tmp_path / 'hf-catalog-index.sqlite')
    yield index
    index.reset_index_runtime(path=original)


def test_parse_link_next():
    header = (
        '<https://huggingface.co/api/models?cursor=abc&limit=100>; rel="next", '
        '<https://huggingface.co/api/models?cursor=zzz>; rel="last"'
    )
    assert 'cursor=abc' in (_parse_link_next(header) or '')
    assert _parse_link_next('') is None


def test_index_search_matches_qwen_dot_query(catalog_index):
    catalog_index.upsert_models([
        {
            'id': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF',
            'author': 'JonathanColetti',
            'title': 'Qwen3.8-27B-Uncensored-GGUF',
            'label': 'Qwen3.8-27B-Uncensored-GGUF',
            'downloads': 2_500_000,
            'likes': 1000,
            'last_modified': '2026-08-30T00:00:00Z',
            'has_gguf': True,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gguf', 'qwen'],
        },
        {
            'id': 'Qwen/Qwen2.5-7B-Instruct-GGUF',
            'author': 'Qwen',
            'title': 'Qwen2.5-7B-Instruct-GGUF',
            'label': 'Qwen2.5-7B-Instruct-GGUF',
            'downloads': 9_000_000,
            'likes': 4000,
            'has_gguf': True,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gguf'],
        },
    ])

    hit = catalog_index.search_local('qwen3.8', category='all', sort='downloads', limit=25)
    assert hit is not None
    ids = [row['id'] for row in hit['models']]
    assert ids[0] == 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF'
    assert hit['from_index'] is True


def test_index_search_prefers_exact_model_name(catalog_index):
    catalog_index.upsert_models([
        {
            'id': 'DavidAU/Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-Q5_K_M-GGUF',
            'title': 'Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-Q5_K_M-GGUF',
            'description': 'Qwen image-to-text model',
            'downloads': 2_000_000,
            'has_gguf': True,
            'tags': ['qwen', 'vision'],
        },
        {
            'id': 'Qwen/Qwen-Image-2.1',
            'title': 'Qwen-Image-2.1',
            'downloads': 16_200,
            'has_gguf': False,
            'has_files': True,
            'tags': ['qwen', 'image'],
        },
        {
            'id': 'Qwen/Qwen2.5-VL-7B-Instruct',
            'title': 'Qwen2.5-VL-7B-Instruct',
            'description': 'Qwen image understanding model',
            'downloads': 9_000_000,
            'has_gguf': False,
            'tags': ['qwen', 'vision'],
        },
    ])

    hit = catalog_index.search_local('Qwen Image 2.1', category='all', limit=25)

    assert hit is not None
    assert [row['id'] for row in hit['models']] == ['Qwen/Qwen-Image-2.1']


def test_index_search_miss_falls_through_to_live_search(catalog_index):
    catalog_index.upsert_models([
        {
            'id': 'Qwen/Qwen2.5-VL-7B-Instruct',
            'title': 'Qwen2.5-VL-7B-Instruct',
            'downloads': 9_000_000,
            'has_gguf': False,
            'tags': ['qwen'],
        },
    ])

    assert catalog_index.search_local('Qwen Image 2.1', category='all', limit=25) is None


def test_index_search_filters_gguf_category(catalog_index):
    catalog_index.upsert_models([
        {
            'id': 'org/speech-model',
            'author': 'org',
            'title': 'speech-model',
            'downloads': 100,
            'has_gguf': False,
            'pipeline_tag': 'automatic-speech-recognition',
            'modality': 'speech-to-text',
            'tags': [],
        },
        {
            'id': 'org/llm-gguf',
            'author': 'org',
            'title': 'llm-gguf',
            'downloads': 50,
            'has_gguf': True,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gguf'],
        },
    ])

    gguf = catalog_index.search_local('', category='all-gguf', sort='downloads', limit=25)
    assert gguf is not None
    assert [row['id'] for row in gguf['models']] == ['org/llm-gguf']


def test_bootstrap_from_search_cache(catalog_index, tmp_path: Path, monkeypatch):
    cache_path = tmp_path / 'hf-catalog-cache.json'
    cache_path.write_text(
        json.dumps({
            'version': 9,
            'entries': {
                'all|downloads|25|qwen3.8': {
                    'payload': {
                        'models': [
                            {
                                'id': 'acme/Qwen3.8-cached-GGUF',
                                'title': 'Qwen3.8-cached-GGUF',
                                'label': 'Qwen3.8-cached-GGUF',
                                'downloads': 99,
                                'has_gguf': True,
                                'tags': ['gguf'],
                            },
                        ],
                    },
                },
            },
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(catalog_index, '_SEARCH_CACHE_PATH', cache_path)
    assert catalog_index.bootstrap_from_search_cache() >= 1
    hit = catalog_index.search_local('qwen3.8', category='all', limit=25)
    assert hit is not None
    assert hit['models'][0]['id'] == 'acme/Qwen3.8-cached-GGUF'


def test_search_with_cache_uses_index_without_live_fetch(catalog_index, tmp_path: Path, monkeypatch):
    from core import hf_catalog_cache as cache

    catalog_index.upsert_models([
        {
            'id': 'acme/qwen3.8-mini-gguf',
            'author': 'acme',
            'title': 'qwen3.8-mini-gguf',
            'label': 'qwen3.8-mini-gguf',
            'downloads': 1234,
            'has_gguf': True,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gguf'],
        },
    ])
    monkeypatch.setattr(cache, '_CACHE_PATH', tmp_path / 'hf-catalog-cache.json')
    monkeypatch.setattr(cache, '_memory', {})
    monkeypatch.setattr(cache, '_loaded', True)
    monkeypatch.setattr(cache, '_refreshing', set())

    called = {'count': 0}

    def boom():
        called['count'] += 1
        raise AssertionError('live Hugging Face search should not run')

    result = cache.search_with_cache(
        query='qwen3.8',
        sort='downloads',
        category='all',
        limit=25,
        fetcher=boom,
        enrich_sizes=False,
    )
    assert called['count'] == 0
    assert result['from_index'] is True
    assert result['models'][0]['id'] == 'acme/qwen3.8-mini-gguf'


def test_resolve_repo_sizes_uses_index_then_hub(catalog_index, monkeypatch):
    catalog_index.upsert_models([
        {
            'id': 'google/gemma-3-1b-it',
            'title': 'gemma-3-1b-it',
            'downloads': 100,
            'size_label': '—',
            'tags': ['transformers'],
        },
        {
            'id': 'google/gemma-3-270m',
            'title': 'gemma-3-270m',
            'downloads': 50,
            'size_gb': 0.55,
            'size_label': '0.55 GB',
            'tags': ['transformers'],
        },
    ])

    def fake_fetch(repo_id, timeout=8.0):
        assert repo_id == 'google/gemma-3-1b-it'
        return {'id': repo_id, 'size_gb': 2.4, 'size_label': '2.4 GB', 'size_bytes': int(2.4 * 1024 ** 3)}

    monkeypatch.setattr('core.huggingface.fetch_used_storage_size', fake_fetch)
    sizes = catalog_index.resolve_repo_sizes(['google/gemma-3-1b-it', 'google/gemma-3-270m'])
    assert sizes['google/gemma-3-270m']['size_label'] == '0.55 GB'
    assert sizes['google/gemma-3-1b-it']['size_label'] == '2.4 GB'
    stored = catalog_index.search_local('gemma-3-1b-it', category='all', limit=5)
    assert stored['models'][0]['size_label'] == '2.4 GB'


def test_expand_query_tokens_splits_family_and_size():
    from core.hf_catalog_index import _expand_query_tokens

    assert _expand_query_tokens('gemma4 2b') == ['gemma', '4', '2b']
    assert _expand_query_tokens('gemma 4 e2b') == ['gemma', '4', '2b']
    assert _expand_query_tokens('qwen3.8') == ['qwen', '3', '8']


def test_numeric_query_does_not_match_inside_larger_number():
    from core.hf_catalog_index import _token_in_text

    assert not _token_in_text('2', 'Qwen-Image-2511', 'qwenimage2511')


def test_index_search_matches_gemma4_2b_keywords(catalog_index):
    catalog_index.upsert_models([
        {
            'id': 'google/gemma-4-E2B-it',
            'author': 'google',
            'title': 'gemma-4-E2B-it',
            'label': 'gemma-4-E2B-it',
            'downloads': 800_000,
            'has_gguf': False,
            'pipeline_tag': 'image-text-to-text',
            'modality': 'vision',
            'tags': ['gemma', 'e2b'],
        },
        {
            'id': 'google/gemma-4-12B-it',
            'author': 'google',
            'title': 'gemma-4-12B-it',
            'label': 'gemma-4-12B-it',
            'downloads': 5_000_000,
            'has_gguf': False,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gemma'],
        },
        {
            'id': 'google/gemma-4-31B-it',
            'author': 'google',
            'title': 'gemma-4-31B-it',
            'label': 'gemma-4-31B-it',
            'downloads': 9_000_000,
            'has_gguf': False,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gemma'],
        },
        {
            'id': 'google/gemma-4-E4B-it',
            'author': 'google',
            'title': 'gemma-4-E4B-it',
            'label': 'gemma-4-E4B-it',
            'downloads': 1_200_000,
            'has_gguf': False,
            'pipeline_tag': 'text-generation',
            'modality': 'llm',
            'tags': ['gemma', 'e4b'],
        },
    ])

    hit = catalog_index.search_local('gemma4 2b', category='all', sort='downloads', limit=25)
    assert hit is not None
    ids = [row['id'] for row in hit['models']]
    assert ids
    assert ids[0] == 'google/gemma-4-E2B-it'
    assert 'google/gemma-4-12B-it' not in ids
    assert 'google/gemma-4-31B-it' not in ids
    assert 'google/gemma-4-E4B-it' not in ids
