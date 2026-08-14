"""Unified Hugging Face search → download → load orchestration."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import HTTPException

from core.catalog_load import execute_catalog_load
from core.config import load_config
from core.huggingface import (
    _preferred_gguf_file,
    get_download_job,
    get_model_detail,
    search_models,
    start_download,
)
from core.local_models import invalidate_model_catalog_cache, list_local_models


def resolve_download_filenames(
    files: list[dict[str, Any]],
    filename: str | None,
    *,
    include_shards: bool = True,
) -> list[str]:
    """Return one or more filenames to download for a repo."""
    gguf_files = [
        row for row in files
        if isinstance(row, dict) and str(row.get('filename') or '').lower().endswith('.gguf')
    ]
    if not gguf_files:
        return []

    chosen = str(filename or '').strip()
    if not chosen:
        preferred = _preferred_gguf_file(gguf_files)
        if not preferred:
            return []
        chosen = str(preferred.get('filename') or '').strip()
    if not chosen:
        return []

    basename = chosen.replace('\\', '/').split('/')[-1]
    shard_match = re.search(r'^(.*)-(\d{5})-of-(\d{5})\.gguf$', basename, re.IGNORECASE)
    if not include_shards or not shard_match:
        return [chosen]

    prefix = shard_match.group(1)
    total = int(shard_match.group(3))
    group: list[str] = []
    for row in gguf_files:
        fn = str(row.get('filename') or '').replace('\\', '/')
        base = fn.split('/')[-1]
        match = re.search(r'^(.*)-(\d{5})-of-(\d{5})\.gguf$', base, re.IGNORECASE)
        if match and match.group(1) == prefix and int(match.group(3)) == total:
            group.append(fn)
    if len(group) == total:
        return sorted(group)
    return [chosen]


def _wait_for_jobs(
    job_ids: list[str],
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.5,
) -> list[dict[str, Any]]:
    deadline = time.time() + max(10.0, float(timeout_seconds))
    pending = {job_id: job_id for job_id in job_ids}
    completed: list[dict[str, Any]] = []
    while pending and time.time() < deadline:
        finished: list[str] = []
        for job_id in list(pending):
            payload = get_download_job(job_id)
            if not payload.get('success'):
                raise HTTPException(status_code=404, detail=f'unknown download job: {job_id}')
            job = dict(payload.get('job') or {})
            status = str(job.get('status') or '')
            if status == 'done':
                completed.append(job)
                finished.append(job_id)
            elif status == 'error':
                raise HTTPException(
                    status_code=400,
                    detail=str(job.get('error') or f'download failed for {job.get("filename") or job_id}'),
                )
        for job_id in finished:
            pending.pop(job_id, None)
        if pending:
            time.sleep(poll_seconds)
    if pending:
        raise HTTPException(
            status_code=408,
            detail={
                'error': 'download timed out',
                'pending_job_ids': list(pending),
                'completed_jobs': completed,
            },
        )
    return completed


def _resolve_search_target(
    *,
    query: str,
    category: str,
    sort: str,
    limit: int,
    result_index: int,
) -> dict[str, Any]:
    payload = search_models(query, limit=limit, sort=sort, category=category)
    models = [row for row in (payload.get('models') or []) if isinstance(row, dict)]
    if not models:
        raise HTTPException(status_code=404, detail=f'no Hugging Face models matched query: {query}')
    index = max(0, min(int(result_index), len(models) - 1))
    return models[index]


def _find_catalog_path_for_download(path: str, *, cfg: dict[str, Any]) -> str | None:
    try:
        resolved = str(path)
    except OSError:
        return None
    catalog = list_local_models(cfg=cfg)
    for row in catalog.get('models') or []:
        if not isinstance(row, dict):
            continue
        row_path = str(row.get('path') or '')
        if row_path and row_path == resolved:
            return row_path
    return resolved if resolved else None


def execute_hf_install(
    *,
    query: str | None = None,
    repo_id: str | None = None,
    filename: str | None = None,
    category: str = 'supported',
    sort: str = 'downloads',
    search_limit: int = 25,
    result_index: int = 0,
    library_id: str | None = None,
    download_all_shards: bool = True,
    wait: bool = True,
    wait_timeout_seconds: int = 3600,
    load: bool = True,
    server_id: str | None = None,
    context_size: int | None = None,
    load_settings: dict[str, Any] | None = None,
    inference_settings: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search Hugging Face, download model files, and optionally load into an engine."""
    config = cfg or load_config()
    selected_repo = str(repo_id or '').strip()
    search_row: dict[str, Any] | None = None

    if not selected_repo:
        needle = str(query or '').strip()
        if not needle:
            raise HTTPException(status_code=400, detail='provide query or repo_id')
        search_row = _resolve_search_target(
            query=needle,
            category=category,
            sort=sort,
            limit=search_limit,
            result_index=result_index,
        )
        selected_repo = str(search_row.get('id') or '').strip()
    if not selected_repo or '/' not in selected_repo:
        raise HTTPException(status_code=400, detail='invalid repo_id')

    detail = get_model_detail(selected_repo, category=category)
    if not detail.get('success'):
        raise HTTPException(status_code=404, detail=detail.get('error') or 'model detail unavailable')
    model = dict(detail.get('model') or {})
    files = list(model.get('gguf_files') or model.get('download_files') or [])
    filenames = resolve_download_filenames(
        files,
        filename,
        include_shards=download_all_shards,
    )
    if not filenames:
        raise HTTPException(status_code=400, detail='no downloadable GGUF file found for this model')

    downloads: list[dict[str, Any]] = []
    for name in filenames:
        result = start_download(selected_repo, name, library_id=library_id, cfg=config)
        if result.get('success'):
            downloads.append({
                'filename': name,
                'job_id': result.get('job_id'),
                'path': result.get('path'),
                'status': 'downloading',
            })
            continue
        if result.get('already_installed'):
            downloads.append({
                'filename': name,
                'path': result.get('path') or (result.get('matches') or [{}])[0].get('path'),
                'status': 'already_installed',
                'already_installed': True,
            })
            continue
        raise HTTPException(status_code=400, detail=result.get('error') or f'download failed for {name}')

    job_ids = [str(row['job_id']) for row in downloads if row.get('job_id')]
    completed_jobs: list[dict[str, Any]] = []
    if job_ids:
        if wait:
            completed_jobs = _wait_for_jobs(job_ids, timeout_seconds=float(wait_timeout_seconds))
            invalidate_model_catalog_cache()
            for row in downloads:
                if not row.get('job_id'):
                    continue
                match = next((job for job in completed_jobs if job.get('id') == row['job_id']), None)
                if match:
                    row['status'] = 'done'
                    row['path'] = match.get('path') or row.get('path')
        else:
            return {
                'success': True,
                'phase': 'downloading',
                'repo_id': selected_repo,
                'model': model,
                'search': search_row,
                'downloads': downloads,
                'load': None,
                'message': 'Downloads started. Poll GET /api/hf/download/{job_id}, then POST /api/models/load.',
            }

    primary_path = ''
    for row in reversed(downloads):
        candidate = str(row.get('path') or '').strip()
        if candidate:
            primary_path = candidate
            break
    if not primary_path:
        raise HTTPException(status_code=500, detail='download completed but no local path was recorded')

    catalog_path = _find_catalog_path_for_download(primary_path, cfg=config)
    load_result = None
    if load:
        load_result = execute_catalog_load(
            path=catalog_path or primary_path,
            server_id=server_id,
            context_size=context_size,
            load_settings=load_settings,
            inference_settings=inference_settings,
            loaded_by='api:/api/hf/install',
            cfg=config,
        )

    return {
        'success': True,
        'phase': 'ready' if load else 'downloaded',
        'repo_id': selected_repo,
        'model': model,
        'search': search_row,
        'downloads': downloads,
        'path': catalog_path or primary_path,
        'load': load_result,
    }
