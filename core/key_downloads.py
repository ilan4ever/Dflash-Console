"""Downloads started by a machine key (another Console sharing this GPU).

A key may download only when the owner turned on "May download models here"
for it, and then only:

- files that the Hugging Face Hub lists in a real ``owner/name`` repository,
  never an arbitrary URL or a path outside that repository;
- within the key's disk limit, counted over every download it started.

The owner, signed in or on this PC, is not limited by any of this.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from fastapi import HTTPException

from core import auth

# Hugging Face repository ids: ``owner/name`` with letters, digits, '.', '_', '-'.
_REPO_PART = r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}'
HF_REPO_ID = re.compile(rf'^{_REPO_PART}/{_REPO_PART}$')
_MAX_FILENAME = 500


def is_hf_repo_id(value: str) -> bool:
    text = str(value or '')
    return bool(HF_REPO_ID.fullmatch(text)) and '..' not in text


def is_repo_file_name(value: str) -> bool:
    """A file path inside a repository: relative, no '..', no scheme or drive."""
    text = str(value or '')
    if not text or len(text) > _MAX_FILENAME:
        return False
    if text.startswith('/') or '\\' in text or ':' in text:
        return False
    if any(ord(ch) < 32 for ch in text):
        return False
    return all(part not in ('', '.', '..') for part in text.split('/'))


def _refuse(status: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={'error': error, 'message': message, 'retryable': False})


def _gib(size_bytes: int) -> str:
    return f'{size_bytes / (1024 ** 3):.1f} GB'


def plan_key_download(
    key_id: str,
    *,
    repo_id: str,
    filenames: Iterable[str],
    whole_repo: bool,
    file_sizes: Callable[[str], dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Check a machine-key download before it starts.

    Returns ``{'sizes': {file: bytes}, 'total': bytes}`` for the files that
    will be fetched. Raises HTTPException (403) when the key may not download
    them.
    """
    key = auth.get_key(key_id)
    if key is None:
        raise _refuse(401, 'invalid_key', 'This machine key was revoked.')
    if key.get('may_download') is not True:
        raise _refuse(
            403,
            'download_not_allowed',
            'This machine key may not download models here. The owner can turn on '
            '“May download models here” for it in Settings → Sign-in & keys.',
        )
    repo = str(repo_id or '').strip()
    names = [str(name or '').strip() for name in filenames if str(name or '').strip()]
    if not is_hf_repo_id(repo):
        raise _refuse(
            403,
            'not_catalog',
            'A machine key can only download files from a Hugging Face repository (owner/name), '
            'never from another address.',
        )
    bad = next((name for name in names if not is_repo_file_name(name)), None)
    if bad is not None:
        raise _refuse(403, 'not_catalog', f'“{bad}” is not a file name inside a Hugging Face repository.')
    if not whole_repo and not names:
        raise _refuse(400, 'filename_required', 'Name the file to download.')

    lookup = file_sizes
    if lookup is None:
        from core.huggingface import repo_file_sizes

        lookup = repo_file_sizes
    listed = lookup(repo) or {}
    if not listed:
        raise _refuse(
            403,
            'not_catalog',
            f'Could not find {repo} on Hugging Face, or Hugging Face did not answer. '
            'A machine key can only download files that Hugging Face lists.',
        )
    if whole_repo:
        sizes = {name: int(size) for name, size in listed.items() if int(size) > 0}
    else:
        missing = [name for name in names if name not in listed]
        if missing:
            raise _refuse(403, 'not_catalog', f'“{missing[0]}” is not a file in {repo} on Hugging Face.')
        sizes = {name: int(listed[name]) for name in names}
    total = sum(sizes.values())
    if total <= 0:
        raise _refuse(403, 'size_unknown', 'Hugging Face does not report the size of this download, so it cannot be checked against the disk limit.')

    # Reserve the bytes now (atomically); settle_key_download gives back what
    # did not start.
    if not auth.reserve_key_download_bytes(key_id, total):
        current = auth.get_key(key_id) or key
        limit = int(float(current.get('download_limit_gb') or 0.0) * (1024 ** 3))
        left = max(0, limit - int(current.get('downloaded_bytes') or 0))
        raise _refuse(
            403,
            'download_limit',
            f'This download needs {_gib(total)}, but this machine key has {_gib(left)} left of its '
            f'{_gib(limit)} limit on this Console. The owner can raise it in Settings → Sign-in & keys.',
        )
    return {'sizes': sizes, 'total': total, 'reserved': total}


def settle_key_download(key_id: str, plan: dict[str, Any] | None, *, files: Iterable[str] | None = None) -> int:
    """Keep the bytes of the files that started and give back the rest.

    ``files=None`` means everything planned started (a whole repository).
    Returns the bytes kept against the key's limit.
    """
    if not plan:
        return 0
    sizes = plan.get('sizes') or {}
    reserved = int(plan.get('reserved') or 0)
    if files is None:
        kept = int(plan.get('total') or 0)
    else:
        kept = sum(int(sizes.get(name) or 0) for name in set(files))
    kept = min(kept, reserved)
    auth.release_key_download_bytes(key_id, reserved - kept)
    plan['reserved'] = kept
    return kept
