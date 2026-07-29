"""Hugging Face Hub search and download helpers."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.config import load_config
from core.model_paths import get_download_dir, get_library_by_id

HF_API = 'https://huggingface.co/api'
HF_BASE = 'https://huggingface.co'

HF_CATEGORIES: dict[str, dict[str, Any]] = {
    'dflash': {
        'label': 'DFlash / speculative',
        'search': 'dflash gguf',
        'filter': 'gguf',
        'gguf_only': True,
    },
    'text-generation': {
        'label': 'Text generation',
        'search': 'gguf',
        'filter': 'text-generation',
        'gguf_only': True,
    },
    'all-gguf': {
        'label': 'All GGUF',
        'search': 'gguf',
        'filter': 'gguf',
        'gguf_only': True,
    },
    'text-to-speech': {
        'label': 'Text-to-speech',
        'search': '',
        'filter': 'text-to-speech',
        'gguf_only': False,
    },
    'automatic-speech-recognition': {
        'label': 'Speech-to-text',
        'search': '',
        'filter': 'automatic-speech-recognition',
        'gguf_only': False,
    },
    'image-to-text': {
        'label': 'OCR / image-to-text',
        'search': '',
        'filter': 'image-to-text',
        'gguf_only': False,
    },
    'feature-extraction': {
        'label': 'Embeddings',
        'search': '',
        'filter': 'feature-extraction',
        'gguf_only': False,
    },
}

_DOWNLOAD_EXTENSIONS = ('.gguf', '.safetensors', '.onnx', '.bin', '.pt', '.ggml', '.mlmodel')

_download_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _request_json(url: str, *, timeout: float = 20.0) -> Any:
    headers = {'Accept': 'application/json', 'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace') or 'null')


def _request_text(url: str, *, timeout: float = 20.0) -> str:
    headers = {'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def _time_ago(iso_ts: str | None) -> str:
    if not iso_ts:
        return '—'
    try:
        from datetime import datetime, timezone

        if iso_ts.endswith('Z'):
            iso_ts = iso_ts[:-1] + '+00:00'
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    except (TypeError, ValueError):
        return '—'
    if seconds < 3600:
        return f'{max(1, seconds // 60)}m ago'
    if seconds < 86400:
        return f'{seconds // 3600}h ago'
    return f'{seconds // 86400}d ago'


def _format_downloads(count: int | float | None) -> str:
    value = int(count or 0)
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    if value >= 1000:
        return f'{value / 1000:.1f}k'
    return str(value)


def _model_files(siblings: list[Any] | None, *, gguf_only: bool = True) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    allowed = ('.gguf',) if gguf_only else _DOWNLOAD_EXTENSIONS
    for entry in siblings or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('rfilename') or entry.get('path') or '')
        lower = name.lower()
        if not any(lower.endswith(ext) for ext in allowed):
            continue
        size = entry.get('size')
        size_gb = round(int(size) / (1024 ** 3), 2) if isinstance(size, int) and size > 0 else None
        files.append({
            'filename': name,
            'size_bytes': size,
            'size_gb': size_gb,
            'label': name.split('/')[-1],
            'format': Path(name).suffix.lower().lstrip('.') or 'file',
        })
    files.sort(key=lambda row: (0 if str(row.get('format')) == 'gguf' else 1, row.get('size_bytes') or 0))
    return files


def _gguf_files(siblings: list[Any] | None) -> list[dict[str, Any]]:
    return _model_files(siblings, gguf_only=True)


def _author_avatar_url(author: str) -> str:
    author = str(author or '').strip()
    if not author:
        return ''
    return f'{HF_BASE}/{urllib.parse.quote(author, safe="")}/avatar'


def _summary_from_model(raw: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(raw.get('id') or raw.get('modelId') or '')
    author = str(raw.get('author') or (repo_id.split('/')[0] if '/' in repo_id else ''))
    card = raw.get('cardData') if isinstance(raw.get('cardData'), dict) else {}
    description = str(card.get('short_description') or card.get('description') or '').strip()
    if len(description) > 160:
        description = description[:157] + '…'
    tags = [str(t) for t in (raw.get('tags') or []) if t]
    siblings = raw.get('siblings')
    downloadable = _model_files(siblings, gguf_only=False)
    return {
        'id': repo_id,
        'author': author,
        'author_avatar_url': _author_avatar_url(author),
        'label': repo_id.split('/')[-1] if '/' in repo_id else repo_id,
        'downloads': int(raw.get('downloads') or 0),
        'downloads_label': _format_downloads(raw.get('downloads')),
        'likes': int(raw.get('likes') or 0),
        'last_modified': str(raw.get('lastModified') or ''),
        'updated_ago': _time_ago(str(raw.get('lastModified') or '')),
        'tags': tags,
        'pipeline_tag': str(raw.get('pipeline_tag') or ''),
        'description': description,
        'gguf_count': len(_gguf_files(siblings)),
        'file_count': len(downloadable),
        'has_gguf': any(name.endswith('.gguf') for name in tags) or bool(_gguf_files(siblings)),
        'has_files': bool(downloadable),
    }


def search_models(
    query: str = '',
    *,
    limit: int = 25,
    sort: str = 'downloads',
    category: str = 'dflash',
    gguf_only: bool | None = None,
) -> dict[str, Any]:
    needle = str(query or '').strip()
    cat_key = str(category or 'dflash').strip().lower()
    cat = HF_CATEGORIES.get(cat_key, HF_CATEGORIES['dflash'])
    use_gguf_only = cat.get('gguf_only', True) if gguf_only is None else gguf_only
    params: dict[str, str | int] = {
        'limit': max(1, min(int(limit), 50)),
        'sort': sort if sort in ('downloads', 'likes', 'lastModified', 'createdAt') else 'downloads',
        'direction': '-1',
    }
    if needle:
        if use_gguf_only and 'gguf' not in needle.lower():
            params['search'] = f'{needle} gguf'
            params['filter'] = 'gguf'
        else:
            params['search'] = needle
            if use_gguf_only:
                params['filter'] = 'gguf'
            elif cat.get('filter'):
                params['filter'] = str(cat['filter'])
    else:
        params['search'] = str(cat.get('search') or 'gguf')
        if cat.get('filter'):
            params['filter'] = str(cat['filter'])
    url = f'{HF_API}/models?{urllib.parse.urlencode(params)}'
    try:
        payload = _request_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {'success': False, 'error': str(exc), 'models': [], 'category': cat_key}
    if not isinstance(payload, list):
        return {'success': False, 'error': 'unexpected Hugging Face response', 'models': [], 'category': cat_key}
    models = [_summary_from_model(item) for item in payload if isinstance(item, dict)]
    if use_gguf_only:
        models = [
            row for row in models
            if row.get('has_gguf') or row.get('gguf_count', 0) > 0 or 'gguf' in ' '.join(row.get('tags') or []).lower()
        ]
        if not models:
            models = [_summary_from_model(item) for item in payload if isinstance(item, dict)]
    elif cat_key != 'all-gguf':
        models = [row for row in models if row.get('has_files') or row.get('file_count', 0) > 0]
    if not models and needle:
        fallback_params: dict[str, str | int] = {
            'limit': params['limit'],
            'sort': params['sort'],
            'direction': '-1',
            'search': needle,
        }
        if use_gguf_only:
            fallback_params['filter'] = 'gguf'
        elif cat.get('filter'):
            fallback_params['filter'] = str(cat['filter'])
        try:
            fallback_payload = _request_json(f'{HF_API}/models?{urllib.parse.urlencode(fallback_params)}')
            if isinstance(fallback_payload, list):
                models = [_summary_from_model(item) for item in fallback_payload if isinstance(item, dict)]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            pass
    models.sort(
        key=lambda row: (
            0 if 'dflash' in str(row.get('id') or '').lower() else 1,
            0 if str(row.get('pipeline_tag') or '') == 'text-generation' else 1,
            -int(row.get('downloads') or 0),
        ),
    )
    return {'success': True, 'models': models, 'query': needle, 'category': cat_key}


def get_model_detail(repo_id: str, *, category: str = 'dflash') -> dict[str, Any]:
    repo = str(repo_id or '').strip().strip('/')
    if not repo or '/' not in repo:
        return {'success': False, 'error': 'invalid repo id'}
    cat = HF_CATEGORIES.get(str(category or 'dflash').strip().lower(), HF_CATEGORIES['dflash'])
    use_gguf_only = bool(cat.get('gguf_only', True))
    url = f'{HF_API}/models/{urllib.parse.quote(repo, safe="/")}'
    try:
        raw = _request_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {'success': False, 'error': f'model not found: {repo}'}
        return {'success': False, 'error': str(exc)}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {'success': False, 'error': str(exc)}
    if not isinstance(raw, dict):
        return {'success': False, 'error': 'unexpected Hugging Face response'}

    readme = ''
    for candidate in (f'{HF_BASE}/{repo}/raw/main/README.md', f'{HF_BASE}/{repo}/raw/main/readme.md'):
        try:
            readme = _request_text(candidate, timeout=15)
            if readme.strip():
                break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            continue

    card = raw.get('cardData') if isinstance(raw.get('cardData'), dict) else {}
    tags = [str(t) for t in (raw.get('tags') or []) if t]
    gguf_files = _gguf_files(raw.get('siblings'))
    downloadable_files = _model_files(raw.get('siblings'), gguf_only=use_gguf_only)
    files = gguf_files if use_gguf_only else downloadable_files
    summary = _summary_from_model(raw)
    description = str(card.get('short_description') or card.get('description') or summary.get('description') or '').strip()

    return {
        'success': True,
        'model': {
            **summary,
            'description': description,
            'tags': tags,
            'gguf_files': gguf_files,
            'download_files': files,
            'readme': readme[:12000],
            'url': f'{HF_BASE}/{repo}',
            'gated': bool(raw.get('gated')),
            'private': bool(raw.get('private')),
            'category': str(category or 'dflash'),
        },
    }


def _download_worker(job_id: str, repo_id: str, filename: str, dest: Path) -> None:
    url = f'{HF_BASE}/{repo_id}/resolve/main/{urllib.parse.quote(filename, safe="/")}'
    headers = {'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + '.part')
            read = 0
            chunk_size = 1024 * 1024
            with tmp.open('wb') as handle:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)
                    read += len(chunk)
                    pct = round(read / total * 100, 1) if total > 0 else None
                    with _jobs_lock:
                        job = _download_jobs.get(job_id)
                        if job:
                            job['bytes_read'] = read
                            job['bytes_total'] = total or None
                            job['progress'] = pct
            tmp.replace(dest)
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['status'] = 'done'
                job['path'] = str(dest)
                job['progress'] = 100.0
    except Exception as exc:
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['status'] = 'error'
                job['error'] = str(exc)


def start_download(
    repo_id: str,
    filename: str,
    *,
    library_id: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    repo = str(repo_id or '').strip().strip('/')
    name = str(filename or '').strip()
    lower = name.lower()
    if not repo or '/' not in repo or not any(lower.endswith(ext) for ext in _DOWNLOAD_EXTENSIONS):
        return {'success': False, 'error': 'repo_id and downloadable filename required'}
    library = get_library_by_id(library_id, config) if library_id else None
    root = Path(str((library or {}).get('path') or get_download_dir(config))).expanduser().resolve()
    author, repo_name = repo.split('/', 1)
    dest = root / author / repo_name / Path(name).name
    job_id = f'{int(time.time())}-{abs(hash(repo + name)) % 1_000_000:06d}'
    with _jobs_lock:
        _download_jobs[job_id] = {
            'id': job_id,
            'repo_id': repo,
            'filename': name,
            'status': 'downloading',
            'progress': 0.0,
            'bytes_read': 0,
            'bytes_total': None,
            'path': str(dest),
            'library_id': (library or {}).get('id') or '',
            'started_at': time.time(),
        }
    thread = threading.Thread(target=_download_worker, args=(job_id, repo, name, dest), daemon=True)
    thread.start()
    return {'success': True, 'job_id': job_id, 'path': str(dest), 'library_id': (library or {}).get('id') or ''}


def get_download_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _download_jobs.get(str(job_id or '').strip())
        if not job:
            return {'success': False, 'error': 'unknown job'}
        return {'success': True, 'job': dict(job)}
