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

_CACHE_VERSION = 6
_MIN_CACHED_MODELS = {
    'dflash': 8,
    'all-gguf': 8,
    'text-generation': 8,
}
_CACHE_PATH = ROOT / 'logs' / 'hf-catalog-cache.json'
_REFRESH_SECONDS = 10 * 60
_WARM_CATEGORIES = ('dflash', 'all-gguf', 'text-generation')

_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] = {}
_loaded = False
_refreshing: set[str] = set()


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


def _save_disk() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({'version': _CACHE_VERSION, 'entries': _memory}, ensure_ascii=False),
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
