"""Source-aware Hugging Face update checks and safe model replacement.

The local catalog deliberately does not infer a remote version from a file's
mtime.  A model is updateable only when its Hugging Face source is verified
from Console download history or a canonical Hugging Face cache layout.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.config import ROOT, list_servers, load_config, normalize_server

_CACHE_PATH = ROOT / 'logs' / 'model-update-cache.json'
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 6 * 60 * 60
_UPDATE_JOBS: dict[str, dict[str, Any]] = {}
_UPDATE_JOBS_LOCK = threading.Lock()
_REFRESH_LOOP_STARTED = False


def _read_cache() -> dict[str, Any]:
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault('version', 1)
    payload.setdefault('repos', {})
    payload.setdefault('models', {})
    if not isinstance(payload['repos'], dict):
        payload['repos'] = {}
    if not isinstance(payload['models'], dict):
        payload['models'] = {}
    return payload


def _write_cache(payload: dict[str, Any]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = _CACHE_PATH.with_name(f'{_CACHE_PATH.name}.tmp')
    temp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(temp, _CACHE_PATH)


def _path_key(value: str | Path) -> str:
    try:
        return str(Path(str(value)).expanduser().resolve()).lower()
    except OSError:
        return str(value or '').replace('/', '\\').lower()


def _repo_id(row: dict[str, Any]) -> str:
    value = str(row.get('hf_repo') or row.get('repo_id') or '').strip().strip('/')
    return value if value.count('/') == 1 and ' ' not in value else ''


def _history_rows() -> list[dict[str, Any]]:
    path = ROOT / 'logs' / 'hf-download-history.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get('jobs') if isinstance(payload, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _is_same_or_child(path: Path, candidate: Path) -> bool:
    left = _path_key(path)
    right = _path_key(candidate)
    if left == right:
        return True
    try:
        candidate.resolve().relative_to(path.resolve())
        return True
    except (OSError, ValueError):
        return right.startswith(left.rstrip('\\/') + os.sep)


def source_provenance(row: dict[str, Any]) -> dict[str, Any]:
    """Return verified source information without guessing from a filename."""
    repo = _repo_id(row)
    path_text = str(row.get('path') or '').strip()
    path = Path(path_text) if path_text else None
    if not repo or path is None:
        return {'verified': False, 'repo_id': repo, 'reason': 'source-not-known'}

    matching_history: list[dict[str, Any]] = []
    for item in _history_rows():
        if str(item.get('repo_id') or '').strip().lower() != repo.lower():
            continue
        item_path = str(item.get('path') or '').strip()
        if not item_path:
            continue
        candidate = Path(item_path)
        if _is_same_or_child(path, candidate) or _is_same_or_child(candidate, path):
            matching_history.append(item)

    if matching_history:
        finished = max(
            (float(item.get('finished_at') or item.get('started_at') or 0) for item in matching_history),
            default=0.0,
        )
        return {
            'verified': True,
            'repo_id': repo,
            'kind': 'download-history',
            'installed_at': finished or None,
        }

    normalized = path_text.replace('\\', '/')
    marker = f'models--{repo.replace("/", "--")}/'
    if marker.lower() in normalized.lower():
        return {
            'verified': True,
            'repo_id': repo,
            'kind': 'huggingface-cache',
            'installed_at': None,
        }
    return {
        'verified': False,
        'repo_id': repo,
        'reason': 'source-not-verified',
    }


def _date_timestamp(value: str) -> float:
    text = str(value or '').strip()
    if not text:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _model_state_key(row: dict[str, Any], repo: str | None = None) -> str:
    return f'{_path_key(str(row.get("path") or ""))}|{str(repo or _repo_id(row)).lower()}'


def _remote_files(remote: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'filename': str(item.get('filename') or item.get('rfilename') or item.get('path') or ''),
            'size_bytes': int(item.get('size') or item.get('size_bytes') or 0),
        }
        for item in (remote.get('files') or [])
        if isinstance(item, dict)
        and str(item.get('filename') or item.get('rfilename') or item.get('path') or '').strip()
    ]


def _remote_payload(remote: dict[str, Any]) -> dict[str, Any]:
    files = _remote_files(remote)
    size_bytes = sum(int(item.get('size_bytes') or 0) for item in files)
    return {
        'revision': str(remote.get('revision') or remote.get('sha') or '').strip(),
        'last_modified': str(remote.get('last_modified') or '').strip(),
        'files': files,
        'size_bytes': size_bytes,
        'checked_at': time.time(),
    }


def _state_details(row: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_id(row)
    provenance = source_provenance(row)
    state_key = _model_state_key(row, repo)
    state = cache.get('models', {}).get(state_key) if repo else None
    remote = cache.get('repos', {}).get(repo.lower()) if repo else None
    state = dict(state) if isinstance(state, dict) else {}
    remote = dict(remote) if isinstance(remote, dict) else {}
    available = False
    status = 'unknown'
    if provenance.get('verified') and remote.get('revision'):
        installed = str(state.get('installed_revision') or '').strip()
        remote_revision = str(remote.get('revision') or '').strip()
        available = bool(installed and installed != remote_revision)
        status = 'update-ready' if available else 'current'
        if state.get('update_unknown_baseline') is True:
            available = True
            status = 'update-ready'
    elif provenance.get('verified'):
        status = 'checking'
    result = {
        'source_verified': bool(provenance.get('verified')),
        'source_kind': provenance.get('kind') or '',
        'source_reason': provenance.get('reason') or '',
        'repo_id': repo,
        'status': status,
        'available': available,
        'installed_revision': str(state.get('installed_revision') or ''),
        'remote_revision': str(remote.get('revision') or ''),
        'remote_last_modified': str(remote.get('last_modified') or ''),
        'remote_files': list(remote.get('files') or []),
        'remote_size_bytes': int(remote.get('size_bytes') or 0),
        'checked_at': remote.get('checked_at'),
    }
    if state.get('installed_at'):
        result['installed_at'] = state['installed_at']
    return result


def annotate_models(models: list[dict[str, Any]], *, cfg: dict[str, Any] | None = None) -> None:
    """Attach cached remote state to catalog rows without doing network I/O."""
    del cfg  # Kept in the signature so callers can pass their catalog config.
    with _CACHE_LOCK:
        cache = _read_cache()
    for row in models:
        if not isinstance(row, dict):
            continue
        local_changed = str(row.get('modified') or '').strip()
        row['local_changed'] = local_changed
        row['local_changed_label'] = local_changed
        details = _state_details(row, cache)
        row['hf_source_verified'] = details['source_verified']
        row['hf_update_status'] = details['status']
        row['hf_update_available'] = details['available']
        row['hf_update'] = details
        if details.get('remote_revision'):
            row['hf_remote_revision'] = details['remote_revision']
            row['hf_remote_last_modified'] = details['remote_last_modified']


def _fetch_remote(repo: str) -> dict[str, Any]:
    from core.huggingface import get_model_revision_metadata

    return get_model_revision_metadata(repo, timeout=20.0)


def refresh_model_updates(
    models: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh remote revisions for verified sources and save a small cache."""
    del cfg
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in models:
        repo = _repo_id(row)
        if repo and source_provenance(row).get('verified'):
            groups.setdefault(repo.lower(), []).append(row)
    now = time.time()
    checked: list[str] = []
    errors: list[dict[str, str]] = []
    with _CACHE_LOCK:
        cache = _read_cache()
        for repo_key, rows in groups.items():
            old_remote = cache['repos'].get(repo_key)
            old_checked = float(old_remote.get('checked_at') or 0) if isinstance(old_remote, dict) else 0
            remote = old_remote if old_remote and not force and now - old_checked < _CACHE_TTL_SECONDS else None
            if remote is None:
                try:
                    fetched = _fetch_remote(repo_key)
                    if not fetched.get('success'):
                        raise RuntimeError(str(fetched.get('error') or 'Hugging Face check failed'))
                    remote = _remote_payload(fetched)
                    cache['repos'][repo_key] = remote
                    checked.append(repo_key)
                except Exception as exc:
                    errors.append({'repo_id': repo_key, 'error': str(exc)})
                    continue
            for row in rows:
                key = _model_state_key(row, repo_key)
                state = cache['models'].get(key)
                state = dict(state) if isinstance(state, dict) else {}
                if not str(state.get('installed_revision') or '').strip():
                    provenance = source_provenance(row)
                    installed_at = float(provenance.get('installed_at') or 0)
                    remote_at = _date_timestamp(str(remote.get('last_modified') or ''))
                    if installed_at and remote_at > installed_at:
                        state['installed_revision'] = ''
                        state['update_unknown_baseline'] = True
                    else:
                        state['installed_revision'] = str(remote.get('revision') or '')
                        state['update_unknown_baseline'] = False
                    state['installed_at'] = installed_at or now
                state['repo_id'] = repo_key
                state['path'] = str(row.get('path') or '')
                state['source_verified'] = True
                cache['models'][key] = state
        _write_cache(cache)
    annotate_models(models)
    return {
        'success': not errors,
        'checked': checked,
        'errors': errors,
        'updates': [row for row in models if row.get('hf_update_available')],
    }


def get_model_update_details(row: dict[str, Any]) -> dict[str, Any]:
    with _CACHE_LOCK:
        cache = _read_cache()
    details = _state_details(row, cache)
    if not details['source_verified']:
        return {'success': False, 'error': 'This model source is not verified as a Hugging Face download.'}
    if not details['available']:
        return {'success': False, 'error': 'No newer Hugging Face revision is available for this model.', 'details': details}
    remote_files = list(details.get('remote_files') or [])
    old_path = Path(str(row.get('path') or '')).expanduser().resolve()
    local_paths = [Path(str(item)).expanduser().resolve() for item in (row.get('split_files') or []) if str(item).strip()]
    if not local_paths and old_path.is_file():
        local_paths = [old_path]
    if old_path.is_dir():
        selected = remote_files
    else:
        local_names = {item.name.lower() for item in local_paths}
        selected = [
            item for item in remote_files
            if Path(str(item.get('filename') or '')).name.lower() in local_names
        ]
        if not selected:
            selected = [
                item for item in remote_files
                if Path(str(item.get('filename') or '')).name.lower() == old_path.name.lower()
            ]
    if not selected:
        return {'success': False, 'error': 'The new revision does not contain the installed model files.'}
    size_bytes = sum(int(item.get('size_bytes') or 0) for item in selected)
    if not size_bytes:
        size_bytes = int(details.get('remote_size_bytes') or 0)
    free_bytes = shutil.disk_usage(old_path.parent if old_path.parent.exists() else old_path).free
    required_bytes = size_bytes + max(256 * 1024 * 1024, int(size_bytes * 0.05))
    return {
        'success': True,
        'details': {
            **details,
            'path': str(old_path),
            'is_directory': old_path.is_dir(),
            'old_revision': details.get('installed_revision') or 'Unknown',
            'new_revision': details.get('remote_revision') or 'Unknown',
            'files': selected,
            'required_bytes': required_bytes,
            'free_bytes': int(free_bytes),
            'enough_space': free_bytes >= required_bytes,
            'server_id': str(row.get('server_id') or ''),
            'model_id': str(row.get('model_id') or ''),
        },
    }


def _set_update_job(job_id: str, **fields: Any) -> None:
    with _UPDATE_JOBS_LOCK:
        job = _UPDATE_JOBS.get(job_id)
        if job:
            job.update(fields)


def get_update_job(job_id: str) -> dict[str, Any]:
    with _UPDATE_JOBS_LOCK:
        job = copy.deepcopy(_UPDATE_JOBS.get(str(job_id or '').strip()))
    if not job:
        return {'success': False, 'error': 'update job not found'}
    job.pop('_row', None)
    return {'success': True, 'job': job}


def _wait_download(job_id: str, update_job_id: str) -> dict[str, Any]:
    from core.huggingface import get_download_job

    while True:
        status = get_download_job(job_id)
        job = status.get('job') or {}
        state = str(job.get('status') or '')
        _set_update_job(
            update_job_id,
            progress=float(job.get('progress') or 0),
            detail=str(job.get('detail') or 'Downloading model files…'),
        )
        if state == 'done':
            return job
        if state in {'error', 'incomplete'}:
            raise RuntimeError(str(job.get('error') or f'Download {state}'))
        time.sleep(0.5)


def _server_for_row(row: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    wanted = str(row.get('server_id') or '').strip()
    if not wanted:
        return None
    for raw in list_servers(cfg):
        if str(raw.get('id') or '').strip() == wanted:
            return normalize_server(raw)
    return None


def _unload_if_loaded(server: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not server:
        return False, []
    from core.runtime import probe_models, unload_model

    api_url = str(server.get('api_url') or '').strip()
    if not api_url:
        return False, []
    loaded = [str(item).strip() for item in probe_models(api_url) if str(item).strip()]
    for model_id in loaded:
        result = unload_model(api_url=api_url, model_id=model_id)
        if not result.get('success'):
            raise RuntimeError(str(result.get('error') or 'The running model could not be unloaded.'))
    return bool(loaded), loaded


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _swap_in(
    old_path: Path,
    stage: Path,
    *,
    is_directory: bool,
    local_paths: list[Path],
    selected_files: list[dict[str, Any]],
    backup: Path,
) -> None:
    backup.mkdir(parents=True, exist_ok=True)
    if is_directory:
        os.replace(old_path, backup / old_path.name)
        os.replace(stage, old_path)
        return
    for local in local_paths:
        if not local.is_file():
            raise RuntimeError(f'Installed model file is missing: {local}')
        os.replace(local, backup / local.name)
    for item, local in zip(selected_files, local_paths):
        staged = stage / Path(str(item.get('filename') or '')).name
        if not staged.is_file():
            raise RuntimeError(f'Staged model file is missing: {staged}')
        os.replace(staged, local)


def _rollback(
    old_path: Path,
    backup: Path,
    *,
    is_directory: bool,
    local_paths: list[Path],
) -> None:
    if is_directory:
        if old_path.exists():
            _remove_path(old_path)
        saved = backup / old_path.name
        if saved.exists():
            os.replace(saved, old_path)
        return
    for local in local_paths:
        if local.exists():
            _remove_path(local)
        saved = backup / local.name
        if saved.exists():
            os.replace(saved, local)


def _run_update_job(job_id: str, row: dict[str, Any], details: dict[str, Any], cfg: dict[str, Any]) -> None:
    from core.huggingface import start_download, start_repo_download
    from core.local_models import invalidate_model_catalog_cache

    old_path = Path(str(details['path'])).expanduser().resolve()
    is_directory = bool(details.get('is_directory'))
    selected_files = list(details.get('files') or [])
    local_paths = [Path(str(item)).expanduser().resolve() for item in (row.get('split_files') or []) if str(item).strip()]
    if not local_paths and not is_directory:
        local_paths = [old_path]
    if not is_directory and local_paths:
        by_name = {
            Path(str(item.get('filename') or '')).name.lower(): item
            for item in selected_files
        }
        selected_files = [
            by_name[local.name.lower()]
            for local in local_paths
            if local.name.lower() in by_name
        ]
    stage = old_path.parent / f'.dflash-update-{job_id}'
    backup = old_path.parent / f'.dflash-backup-{job_id}'
    server = _server_for_row(row, cfg)
    was_loaded = False
    try:
        if stage.exists():
            _remove_path(stage)
        stage.mkdir(parents=True, exist_ok=True)
        _set_update_job(job_id, status='downloading', detail='Downloading the new revision…', progress=0)
        if is_directory:
            result = start_repo_download(
                str(details['repo_id']),
                dest_path=str(stage),
                cfg=cfg,
                allow_incomplete_resume=False,
            )
            if not result.get('success'):
                raise RuntimeError(str(result.get('error') or 'Could not stage the new repository.'))
            _wait_download(str(result['job_id']), job_id)
            for item in selected_files:
                rel = Path(str(item.get('filename') or ''))
                target = stage / rel
                if not target.is_file() or target.stat().st_size <= 0:
                    raise RuntimeError(f'New revision is missing {rel}.')
        else:
            for index, item in enumerate(selected_files):
                filename = str(item.get('filename') or '').strip()
                target = stage / Path(filename).name
                result = start_download(
                    str(details['repo_id']),
                    filename,
                    dest_path=str(target),
                    cfg=cfg,
                )
                if not result.get('success'):
                    raise RuntimeError(str(result.get('error') or f'Could not stage {filename}.'))
                _wait_download(str(result['job_id']), job_id)
                if not target.is_file() or target.stat().st_size <= 0:
                    raise RuntimeError(f'Staged file is empty: {target.name}.')
                _set_update_job(job_id, progress=round(((index + 1) / len(selected_files)) * 80, 1))

        _set_update_job(job_id, status='staged', progress=85, detail='Download verified. Preparing the engine…')
        was_loaded, _loaded_ids = _unload_if_loaded(server)
        _set_update_job(job_id, status='replacing', progress=90, detail='Replacing the old files safely…')
        _swap_in(
            old_path,
            stage,
            is_directory=is_directory,
            local_paths=local_paths,
            selected_files=selected_files,
            backup=backup,
        )
        if is_directory:
            if not old_path.is_dir():
                raise RuntimeError('The replacement folder was not installed.')
        elif any(not path.is_file() or path.stat().st_size <= 0 for path in local_paths):
            raise RuntimeError('The replacement files did not pass verification.')

        if was_loaded and server:
            from core.server_boot import load_server_checkpoint

            reloaded = load_server_checkpoint(server, cfg=cfg)
            if not reloaded.get('success'):
                raise RuntimeError(str(reloaded.get('error') or 'The updated model could not be loaded.'))

        with _CACHE_LOCK:
            cache = _read_cache()
            key = _model_state_key(row, str(details['repo_id']))
            state = dict(cache['models'].get(key) or {})
            state.update({
                'repo_id': str(details['repo_id']).lower(),
                'path': str(old_path),
                'installed_revision': str(details.get('new_revision') or ''),
                'installed_at': time.time(),
                'update_unknown_baseline': False,
                'source_verified': True,
            })
            cache['models'][key] = state
            _write_cache(cache)
        invalidate_model_catalog_cache()
        _set_update_job(job_id, status='done', progress=100, detail='Model update installed successfully.')
    except Exception as exc:
        try:
            if backup.exists():
                _rollback(old_path, backup, is_directory=is_directory, local_paths=local_paths)
            if was_loaded and server:
                from core.server_boot import load_server_checkpoint

                load_server_checkpoint(server, cfg=cfg)
        except Exception as rollback_error:
            exc = RuntimeError(f'{exc}; rollback failed: {rollback_error}')
        _set_update_job(job_id, status='error', progress=0, error=str(exc), detail='Update failed; the previous model was kept.')
    finally:
        for path in (stage, backup):
            try:
                if path.exists():
                    _remove_path(path)
            except OSError:
                # A failed cleanup is safer left for a later manual cleanup
                # than deleting a user's model unexpectedly.
                pass


def start_model_update(row: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    plan = get_model_update_details(row)
    if not plan.get('success'):
        return plan
    details = plan['details']
    if not details.get('enough_space'):
        return {'success': False, 'error': 'There is not enough free disk space for the staged update.', 'details': details}
    path = Path(str(details['path']))
    if not path.exists():
        return {'success': False, 'error': 'The installed model file or folder no longer exists.'}
    job_id = f'model-update-{uuid.uuid4().hex[:16]}'
    with _UPDATE_JOBS_LOCK:
        _UPDATE_JOBS[job_id] = {
            'id': job_id,
            'status': 'queued',
            'progress': 0,
            'detail': 'Waiting to stage the new revision…',
            'repo_id': details.get('repo_id'),
            'path': str(path),
            'server_id': details.get('server_id') or '',
            'old_revision': details.get('old_revision'),
            'new_revision': details.get('new_revision'),
            '_row': copy.deepcopy(row),
        }
    thread = threading.Thread(
        target=_run_update_job,
        args=(job_id, copy.deepcopy(row), details, config),
        daemon=True,
        name=f'model-update-{job_id[-8:]}',
    )
    thread.start()
    return {'success': True, 'job_id': job_id, 'details': details}


def start_model_update_refresh_loop(*, interval_seconds: float = 6 * 60 * 60) -> None:
    global _REFRESH_LOOP_STARTED
    with _CACHE_LOCK:
        if _REFRESH_LOOP_STARTED:
            return
        _REFRESH_LOOP_STARTED = True

    def run() -> None:
        first_run = True
        while True:
            time.sleep(5.0 if first_run else max(900.0, float(interval_seconds)))
            first_run = False
            try:
                from core.local_models import list_local_models

                payload = list_local_models(cfg=load_config(), force_refresh=True)
                refresh_model_updates(list(payload.get('models') or []), force=False)
            except Exception:
                continue

    threading.Thread(target=run, daemon=True, name='model-update-refresh').start()
