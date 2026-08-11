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

_CACHE_VERSION = 7
_MIN_CACHED_MODELS = {
    'dflash': 8,
    'all-gguf': 8,
    'text-generation': 8,
}
_CACHE_PATH = ROOT / 'logs' / 'hf-catalog-cache.json'
_REFRESH_SECONDS = 10 * 60
_DETAIL_REFRESH_SECONDS = 30 * 60
_WARM_CATEGORIES = ('all', 'dflash', 'all-gguf', 'text-generation')

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
        return payload
    payload = fetcher()
    if payload.get('success'):
        put_cached_detail(repo_id=repo_id, category=category, payload=payload)
    return payload


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


def search_with_cache(
    *,
    query: str,
    sort: str,
    category: str,
    limit: int,
    fetcher: Callable[[], dict[str, Any]],
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return cached search results immediately when available; refresh in background when stale."""
    key = _cache_key(query=query, sort=sort, category=category, limit=limit)
    cached = None if force_refresh else get_cached_search(
        query=query, sort=sort, category=category, limit=limit,
    )

    if cached and cached.get('payload'):
        payload = dict(cached['payload'])
        models = payload.get('models')
        min_models = _MIN_CACHED_MODELS.get(category, 0) if not query.strip() else 0
        too_thin = (
            min_models > 0
            and isinstance(models, list)
            and len(models) < min_models
        )
        if too_thin:
            cached = None
        else:
            payload['cached'] = True
            payload['stale'] = bool(cached.get('stale'))
            payload['cache_age_seconds'] = round(float(cached.get('age_seconds') or 0.0), 1)
            if payload['stale']:
                _schedule_refresh(key, fetcher)
            return payload

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
        put_cached_search(
            query=query,
            sort=sort,
            category=category,
            limit=limit,
            payload=enriched,
        )
        return enriched
    if cached and cached.get('payload'):
        payload = dict(cached['payload'])
        payload['cached'] = True
        payload['stale'] = True
        payload['refresh_failed'] = True
        return payload
    return payload


def warm_hf_catalog_cache() -> None:
    """Prefetch common HF catalog queries at startup and keep them fresh."""
    from core.huggingface import search_models

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

    for category in _WARM_CATEGORIES:
        threading.Thread(
            target=warm_one,
            args=(category,),
            daemon=True,
            name=f'hf-warm-{category}',
        ).start()


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
