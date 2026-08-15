"""Persistent Hugging Face catalog cache (disk + memory)."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.config import ROOT

logger = logging.getLogger(__name__)

_CACHE_VERSION = 9
_MIN_CACHED_MODELS = {
    'supported': 8,
    'dflash': 8,
    'all-gguf': 8,
    'text-generation': 8,
}
_CACHE_PATH = ROOT / 'logs' / 'hf-catalog-cache.json'
_REFRESH_SECONDS = 10 * 60
_DETAIL_REFRESH_SECONDS = 30 * 60
_SUPPORTED_SOURCE_CATEGORIES = (
    'dflash',
    'all-gguf',
    'automatic-speech-recognition',
    'text-to-speech',
    'image-to-text',
    'feature-extraction',
)
_WARM_CATEGORIES = (
    *_SUPPORTED_SOURCE_CATEGORIES,
    'all',
    'text-generation',
)
_WARM_SUPPORTED_COMPOSED = True

_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] = {}
_detail_memory: dict[str, dict[str, Any]] = {}
_loaded = False
_refreshing: set[str] = set()
_refresh_loop_started = False


def _cache_key(*, query: str, sort: str, category: str, limit: int) -> str:
    return f'{category}|{sort}|{limit}|{query.strip().lower()}'


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        _loaded = True
        if not _CACHE_PATH.is_file():
            return
        try:
            payload = json.loads(_CACHE_PATH.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning('hf catalog cache read failed: %s', exc)
            return
        if not isinstance(payload, dict):
            return
        if int(payload.get('version') or 0) != _CACHE_VERSION:
            return
        entries = payload.get('entries')
        if isinstance(entries, dict):
            _memory.update(entries)
        details = payload.get('details')
        if isinstance(details, dict):
            _detail_memory.update(details)


def _save_disk() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(
                {'version': _CACHE_VERSION, 'entries': _memory, 'details': _detail_memory},
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        tmp.replace(_CACHE_PATH)
    except OSError as exc:
        logger.warning('hf catalog cache write failed: %s', exc)


def get_cached_search(
    *,
    query: str = '',
    sort: str = 'downloads',
    category: str = 'dflash',
    limit: int = 25,
) -> dict[str, Any] | None:
    _ensure_loaded()
    key = _cache_key(query=query, sort=sort, category=category, limit=limit)
    with _lock:
        row = _memory.get(key)
    if not isinstance(row, dict):
        return None
    fetched_at = float(row.get('fetched_at') or 0.0)
    payload = row.get('payload')
    if not isinstance(payload, dict):
        return None
    age = max(0.0, time.time() - fetched_at)
    return {
        'payload': dict(payload),
        'fetched_at': fetched_at,
        'age_seconds': age,
        'stale': age >= _REFRESH_SECONDS,
    }


def put_cached_search(
    *,
    query: str,
    sort: str,
    category: str,
    limit: int,
    payload: dict[str, Any],
) -> None:
    key = _cache_key(query=query, sort=sort, category=category, limit=limit)
    with _lock:
        _memory[key] = {
            'fetched_at': time.time(),
            'payload': dict(payload),
            'query': query,
            'sort': sort,
            'category': category,
            'limit': limit,
        }
        _save_disk()


def _detail_key(repo_id: str, category: str) -> str:
    return f'{str(category or "dflash").strip().lower()}|{str(repo_id or "").strip().lower()}'


def get_cached_detail(*, repo_id: str, category: str = 'dflash') -> dict[str, Any] | None:
    _ensure_loaded()
    with _lock:
        row = _detail_memory.get(_detail_key(repo_id, category))
    if not isinstance(row, dict) or not isinstance(row.get('payload'), dict):
        return None
    fetched_at = float(row.get('fetched_at') or 0.0)
    return {
        'payload': dict(row['payload']),
        'fetched_at': fetched_at,
        'age_seconds': max(0.0, time.time() - fetched_at),
        'stale': time.time() - fetched_at >= _DETAIL_REFRESH_SECONDS,
    }


def put_cached_detail(*, repo_id: str, category: str, payload: dict[str, Any]) -> None:
    with _lock:
        _detail_memory[_detail_key(repo_id, category)] = {
            'fetched_at': time.time(),
            'payload': dict(payload),
        }
        _save_disk()


def _refresh_detail_fit(payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get('model')
    if not isinstance(model, dict):
        return payload
    from core.config import load_config
    from core.hf_model_fit import assess_hf_model_fit

    config = load_config()
    gguf_files = model.get('gguf_files')
    download_files = model.get('download_files')
    model.update(
        assess_hf_model_fit(
            model,
            cfg=config,
            gguf_files=gguf_files if isinstance(gguf_files, list) else None,
            download_files=download_files if isinstance(download_files, list) else None,
        ),
    )
    label = str(model.get('size_label') or '').strip()
    size_gb = model.get('size_gb')
    if (not isinstance(size_gb, (int, float)) or float(size_gb or 0) <= 0) or label in ('', '—', '0 GB', '0.0 GB'):
        from core.hf_model_fit import repo_disk_size_gb

        files = download_files if isinstance(download_files, list) else None
        if files:
            disk_gb = repo_disk_size_gb(files, has_gguf=bool(model.get('has_gguf')))
            if isinstance(disk_gb, (int, float)) and disk_gb > 0:
                model['size_gb'] = round(float(disk_gb), 2)
                model['size_label'] = f'{float(disk_gb):g} GB'
    return payload


def get_or_fetch_detail(
    *,
    repo_id: str,
    category: str,
    fetcher: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    cached = get_cached_detail(repo_id=repo_id, category=category)
    if cached and cached.get('payload'):
        if cached.get('stale'):
            _schedule_detail_refresh(repo_id, category, fetcher)
        payload = dict(cached['payload'])
        payload['cached'] = True
        payload['stale'] = bool(cached.get('stale'))
        payload['cache_age_seconds'] = round(float(cached.get('age_seconds') or 0.0), 1)
        return _refresh_detail_fit(payload)
    payload = fetcher()
    if payload.get('success'):
        put_cached_detail(repo_id=repo_id, category=category, payload=payload)
    return _refresh_detail_fit(payload)


def _schedule_detail_refresh(repo_id: str, category: str, fetcher: Callable[[], dict[str, Any]]) -> None:
    key = f'detail|{_detail_key(repo_id, category)}'
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run() -> None:
        try:
            payload = fetcher()
            if payload.get('success'):
                put_cached_detail(repo_id=repo_id, category=category, payload=payload)
        except Exception as exc:
            logger.warning('hf detail background refresh failed: %s', exc)
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True, name='hf-detail-refresh').start()


def _schedule_refresh(key: str, fetcher: Callable[[], dict[str, Any]]) -> None:
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run() -> None:
        try:
            payload = fetcher()
            if payload.get('success'):
                put_cached_search(
                    query=str(payload.get('query') or ''),
                    sort=str(payload.get('sort') or 'downloads'),
                    category=str(payload.get('category') or 'dflash'),
                    limit=int(payload.get('limit') or 25),
                    payload=payload,
                )
        except Exception as exc:
            logger.warning('hf catalog background refresh failed: %s', exc)
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True, name=f'hf-cache-{key[:24]}').start()


def _supported_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    repo_id = str(row.get('id') or '').lower()
    has_gguf = bool(row.get('has_gguf')) or int(row.get('gguf_count') or 0) > 0
    return (
        0 if 'dflash' in repo_id else 1,
        0 if has_gguf else 1,
        -int(row.get('downloads') or 0),
    )


def _try_composed_supported_search(
    *,
    sort: str,
    limit: int,
) -> dict[str, Any] | None:
    """Build supported catalog from warmed per-modality snapshots (no live HF round-trip)."""
    from core.huggingface import _is_console_supported_model

    merged: dict[str, dict[str, Any]] = {}
    oldest_age = 0.0
    any_hit = False
    for source_category in _SUPPORTED_SOURCE_CATEGORIES:
        cached = get_cached_search(
            query='',
            sort=sort,
            category=source_category,
            limit=limit,
        )
        if not cached or not cached.get('payload'):
            continue
        any_hit = True
        oldest_age = max(oldest_age, float(cached.get('age_seconds') or 0.0))
        models = cached['payload'].get('models')
        if not isinstance(models, list):
            continue
        for row in models:
            if not isinstance(row, dict):
                continue
            repo_id = str(row.get('id') or '').strip()
            if not repo_id or repo_id in merged:
                continue
            if _is_console_supported_model(row):
                merged[repo_id] = row

    min_models = _MIN_CACHED_MODELS.get('supported', 0)
    if not any_hit or len(merged) < min_models:
        return None

    models = sorted(merged.values(), key=_supported_sort_key)[:limit]
    stale = oldest_age >= _REFRESH_SECONDS
    return {
        'success': True,
        'models': models,
        'query': '',
        'category': 'supported',
        'sort': sort,
        'limit': limit,
        'cached': True,
        'stale': stale,
        'cache_age_seconds': round(oldest_age, 1),
        'composed_from_subcaches': True,
    }


def _fill_missing_search_sizes(payload: dict[str, Any]) -> bool:
    """Fetch Hub file sizes for catalog rows that still show Disk —."""
    models = payload.get('models')
    if not isinstance(models, list) or not models:
        return False
    from core.huggingface import _enrich_summaries_sizes, _row_needs_size_enrich

    if not any(_row_needs_size_enrich(row) for row in models if isinstance(row, dict)):
        return False
    _enrich_summaries_sizes(models)
    return True


def _annotate_search_fit(payload: dict[str, Any], *, category: str) -> dict[str, Any]:
    """Recompute fits_machine on cached rows (VRAM budget + detail files may have changed)."""
    models = payload.get('models')
    if not isinstance(models, list) or not models:
        return payload
    from core.hf_model_fit import annotate_hf_models_fit

    annotate_hf_models_fit(models, category=category)
    return payload


def _repo_query_cache_hit(cached: dict[str, Any], query: str) -> bool:
    """Repo-style queries need a cache hit that actually resolved the model."""
    needle = str(query or '').strip().lower()
    if '/' not in needle:
        return True
    payload = cached.get('payload')
    if not isinstance(payload, dict):
        return False
    models = payload.get('models')
    if not isinstance(models, list) or not models:
        return False
    if any(str(row.get('id') or '').lower() == needle for row in models):
        return True
    from core.huggingface import _normalize_repo_slug

    slug = _normalize_repo_slug(needle.split('/')[-1])
    return any(
        _normalize_repo_slug(str(row.get('id') or '').split('/')[-1]) == slug
        for row in models
    )


def search_with_cache(
    *,
    query: str,
    sort: str,
    category: str,
    limit: int,
    fetcher: Callable[[], dict[str, Any]],
    force_refresh: bool = False,
    enrich_sizes: bool = True,
) -> dict[str, Any]:
    """Return cached search results immediately when available; refresh in background when stale."""
    key = _cache_key(query=query, sort=sort, category=category, limit=limit)
    cached = None if force_refresh else get_cached_search(
        query=query, sort=sort, category=category, limit=limit,
    )

    def _finish(payload: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
        filled = _fill_missing_search_sizes(payload) if enrich_sizes else False
        if persist or filled:
            models = payload.get('models')
            repo_query = '/' in str(query or '')
            if not repo_query or (isinstance(models, list) and models):
                put_cached_search(
                    query=query,
                    sort=sort,
                    category=category,
                    limit=limit,
                    payload=payload,
                )
        return _annotate_search_fit(payload, category=category)

    if cached and cached.get('payload'):
        payload = dict(cached['payload'])
        models = payload.get('models')
        if not _repo_query_cache_hit(cached, query):
            cached = None
            payload = {}
            models = None
        min_models = _MIN_CACHED_MODELS.get(category, 0) if not query.strip() else 0
        too_thin = (
            min_models > 0
            and isinstance(models, list)
            and len(models) < min_models
        )
        if cached and not too_thin:
            payload['cached'] = True
            payload['stale'] = bool(cached.get('stale'))
            payload['cache_age_seconds'] = round(float(cached.get('age_seconds') or 0.0), 1)
            if payload['stale']:
                _schedule_refresh(key, fetcher)
            return _finish(payload)

    if (
        not force_refresh
        and str(category or '').strip().lower() == 'supported'
        and not str(query or '').strip()
    ):
        composed = _try_composed_supported_search(sort=sort, limit=limit)
        if composed:
            _schedule_refresh(key, fetcher)
            return _finish(composed)

    payload = fetcher()
    if payload.get('success'):
        enriched = {
            **payload,
            'query': query,
            'sort': sort,
            'category': category,
            'limit': limit,
            'cached': False,
            'stale': False,
        }
        return _finish(enriched, persist=True)
    if cached and cached.get('payload'):
        payload = dict(cached['payload'])
        payload['cached'] = True
        payload['stale'] = True
        payload['refresh_failed'] = True
        return _finish(payload)
    return _finish(payload)


def preload_hf_catalog_cache() -> None:
    """Load the on-disk catalog cache into memory at process start."""
    _ensure_loaded()


def _cache_supported_from_subcaches(*, sort: str = 'downloads', limit: int = 25) -> bool:
    composed = _try_composed_supported_search(sort=sort, limit=limit)
    if not composed:
        return False
    put_cached_search(
        query='',
        sort=sort,
        category='supported',
        limit=limit,
        payload={
            'success': True,
            'models': composed.get('models') or [],
            'query': '',
            'sort': sort,
            'category': 'supported',
            'limit': limit,
            'cached': True,
            'stale': bool(composed.get('stale')),
            'composed_from_subcaches': True,
        },
    )
    return True


def warm_hf_catalog_cache() -> None:
    """Prefetch common HF catalog queries at startup and keep them fresh."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.huggingface import search_models

    preload_hf_catalog_cache()

    def warm_one(category: str) -> None:
        try:
            search_with_cache(
                query='',
                sort='downloads',
                category=category,
                limit=25,
                fetcher=lambda cat=category: search_models('', limit=25, sort='downloads', category=cat),
            )
        except Exception as exc:
            logger.warning('hf catalog warm failed for %s: %s', category, exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(warm_one, category) for category in _WARM_CATEGORIES]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass

    if _WARM_SUPPORTED_COMPOSED:
        if not _cache_supported_from_subcaches(sort='downloads', limit=25):
            try:
                search_with_cache(
                    query='',
                    sort='downloads',
                    category='supported',
                    limit=25,
                    fetcher=lambda: search_models('', limit=25, sort='downloads', category='supported'),
                )
            except Exception as exc:
                logger.warning('hf catalog warm failed for supported: %s', exc)


def start_hf_catalog_refresh_loop(*, interval_seconds: float = 600.0) -> None:
    """Refresh common catalog snapshots periodically without blocking requests."""
    global _refresh_loop_started
    with _lock:
        if _refresh_loop_started:
            return
        _refresh_loop_started = True

    def refresh_loop() -> None:
        while True:
            time.sleep(max(120.0, float(interval_seconds)))
            try:
                warm_hf_catalog_cache()
            except Exception as exc:
                logger.warning('hf catalog periodic refresh failed: %s', exc)

    threading.Thread(
        target=refresh_loop,
        daemon=True,
        name='hf-catalog-refresh-loop',
    ).start()


preload_hf_catalog_cache()
