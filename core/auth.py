"""Owner password, browser sessions, and machine keys (sign-in, Phase 1).

Everything lives in ``auth.json`` next to ``config.json`` in the Console data
root. It is a separate file on purpose:

- ``GET /api/config``, config export, presets, and the diagnostics bundle never
  read it, so password hashes and key hashes cannot leak through them;
- frequent session writes cannot race the busy ``config.json`` saves.

Nothing secret is stored in plain text:

- the owner password is an scrypt hash (``hashlib.scrypt``, standard library);
- sessions and machine keys are 256-bit random values; only their SHA-256 is
  kept. A fast hash is enough for values with that much entropy.

``users`` is a list with one ``owner`` entry so named users can be added later
without a migration. There are no roles or user management yet.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from core.config import ROOT

AUTH_PATH = ROOT / 'auth.json'
SCHEMA_VERSION = 1
OWNER_ID = 'owner'

# scrypt cost. N=2**17, r=8, p=1 is the OWASP minimum for scrypt (128 MiB,
# about half a second). Parameters are stored inside each hash, so they can be
# raised later without breaking existing passwords.
SCRYPT_N_DEFAULT = 2 ** 17
SCRYPT_N = SCRYPT_N_DEFAULT
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 1024

SESSION_TTL_SECONDS = 30 * 24 * 3600
SESSION_IDLE_SECONDS = 7 * 24 * 3600
MAX_SESSIONS = 50
# last_seen / last_used are kept in memory and written at most this often.
TOUCH_WRITE_INTERVAL_SECONDS = 60.0

KEY_PREFIX = 'dfk_'
KEY_RANDOM_BYTES = 32  # 256 bits
KEY_NAME_MAX = 80
MAX_KEYS = 200
DOWNLOAD_LIMIT_DEFAULT_GB = 50.0
DOWNLOAD_LIMIT_MAX_GB = 100_000.0

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {'path': None, 'mtime': None, 'data': None}
_LAST_TOUCH_SAVE = {'at': 0.0}
# Password checks are serialized: one scrypt run at a time, so a burst of
# sign-in attempts cannot exhaust memory and the throttle sees every result.
_PASSWORD_CHECK_LOCK = threading.Lock()


class AuthError(Exception):
    """A refused auth operation, with an HTTP status for the API layer."""

    def __init__(self, error: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status

    def detail(self) -> dict[str, Any]:
        return {'error': self.error, 'message': self.message}


# ---------------------------------------------------------------- hashing

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    padded = text + '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode('ascii'))


def _scrypt_maxmem(n: int, r: int) -> int:
    # scrypt needs 128 * r * n bytes plus a little bookkeeping.
    return 128 * r * n + 2 * 1024 * 1024


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash: ``scrypt$N$r$p$salt$digest``."""
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        str(password).encode('utf-8'),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=_scrypt_maxmem(SCRYPT_N, SCRYPT_R),
        dklen=SCRYPT_DKLEN,
    )
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_text, r_text, p_text, salt_text, digest_text = str(stored or '').split('$')
        if scheme != 'scrypt':
            return False
        n, r, p = int(n_text), int(r_text), int(p_text)
        salt = _unb64(salt_text)
        expected = _unb64(digest_text)
    except (ValueError, TypeError):
        return False
    # A hand-edited file must not be able to request absurd memory or time.
    if not (2 <= n <= 2 ** 20 and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
        return False
    if not salt or not expected:
        return False
    try:
        digest = hashlib.scrypt(
            str(password).encode('utf-8'),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=_scrypt_maxmem(n, r),
            dklen=len(expected),
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(digest, expected)


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def csrf_token_for(session_token: str) -> str:
    """CSRF token derived from the session token (nothing extra to store).

    The session token sits in an HttpOnly cookie, so a page script can only
    learn the CSRF token from the readable CSRF cookie, never the reverse.
    """
    return hmac.new(
        str(session_token).encode('utf-8'),
        b'dflash-console-csrf-v1',
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------- storage

def _empty() -> dict[str, Any]:
    return {
        'version': SCHEMA_VERSION,
        'users': [{'id': OWNER_ID, 'name': 'Owner', 'role': 'owner', 'password_hash': '', 'password_set_at': None}],
        'sessions': [],
        'keys': [],
    }


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize(raw: Any) -> dict[str, Any]:
    data = _empty()
    if not isinstance(raw, dict):
        return data
    users = [row for row in raw.get('users') or [] if isinstance(row, dict) and str(row.get('id') or '')]
    owner = next((row for row in users if row.get('id') == OWNER_ID), None)
    if owner is not None:
        data['users'][0].update({
            'name': str(owner.get('name') or 'Owner'),
            'password_hash': str(owner.get('password_hash') or ''),
            'password_set_at': _as_float(owner.get('password_set_at')),
        })
    data['users'].extend(dict(row) for row in users if row.get('id') != OWNER_ID)
    for row in raw.get('sessions') or []:
        if not isinstance(row, dict) or not str(row.get('token_hash') or '') or not str(row.get('id') or ''):
            continue
        data['sessions'].append({
            'id': str(row['id']),
            'user': str(row.get('user') or OWNER_ID),
            'token_hash': str(row['token_hash']),
            'created_at': _as_float(row.get('created_at'), 0.0),
            'last_seen_at': _as_float(row.get('last_seen_at'), 0.0),
            'expires_at': _as_float(row.get('expires_at'), 0.0),
            'ip': str(row.get('ip') or ''),
            'user_agent': str(row.get('user_agent') or '')[:200],
        })
    for row in raw.get('keys') or []:
        if not isinstance(row, dict) or not str(row.get('key_hash') or '') or not str(row.get('id') or ''):
            continue
        data['keys'].append({
            'id': str(row['id']),
            'name': str(row.get('name') or 'Machine key')[:KEY_NAME_MAX],
            'hint': str(row.get('hint') or '')[:16],
            'key_hash': str(row['key_hash']),
            'created_at': _as_float(row.get('created_at'), 0.0),
            'last_used_at': _as_float(row.get('last_used_at')),
            'last_used_ip': str(row.get('last_used_ip') or ''),
            'may_download': row.get('may_download') is True,
            'download_limit_gb': _clamp_limit(row.get('download_limit_gb')),
            'downloaded_bytes': max(0, int(_as_float(row.get('downloaded_bytes'), 0.0) or 0)),
        })
    return data


def _path() -> Path:
    return Path(AUTH_PATH)


def _load() -> dict[str, Any]:
    """Current auth data (cached; reloaded when the file changes on disk)."""
    path = _path()
    try:
        mtime: float | None = path.stat().st_mtime
    except OSError:
        mtime = None
    if _CACHE['data'] is not None and _CACHE['path'] == str(path) and _CACHE['mtime'] == mtime:
        return _CACHE['data']
    data = _empty()
    if mtime is not None:
        try:
            data = _normalize(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            # Unreadable file: keep a copy for the owner and start empty. That
            # fails closed for other computers (no password, no keys).
            try:
                path.replace(path.with_name(f'{path.name}.corrupt-{int(time.time())}'))
            except OSError:
                pass
            mtime = None
    _CACHE.update({'path': str(path), 'mtime': mtime, 'data': data})
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + '\n'
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != 'nt':
            os.chmod(temp_name, 0o600)
        attempts = 6 if os.name == 'nt' else 1
        for attempt in range(attempts):
            try:
                os.replace(temp_name, path)
                break
            except PermissionError:
                # Antivirus or an indexer can hold the file for a moment on Windows.
                if attempt >= attempts - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))
        temp_name = ''
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    try:
        mtime: float | None = path.stat().st_mtime
    except OSError:
        mtime = None
    _CACHE.update({'path': str(path), 'mtime': mtime, 'data': data})
    _LAST_TOUCH_SAVE['at'] = time.time()


def _touch_save(data: dict[str, Any]) -> None:
    """Persist last-seen times, at most once per TOUCH_WRITE_INTERVAL_SECONDS."""
    if time.time() - _LAST_TOUCH_SAVE['at'] < TOUCH_WRITE_INTERVAL_SECONDS:
        return
    try:
        _save(data)
    except OSError:
        pass


def reset_cache() -> None:
    """Forget cached state (tests, or after editing auth.json by hand)."""
    with _LOCK:
        _CACHE.update({'path': None, 'mtime': None, 'data': None})
        _LAST_TOUCH_SAVE['at'] = 0.0


def _owner(data: dict[str, Any]) -> dict[str, Any]:
    return data['users'][0]


# ---------------------------------------------------------------- password

def password_is_set() -> bool:
    with _LOCK:
        return bool(_owner(_load()).get('password_hash'))


def password_info() -> dict[str, Any]:
    with _LOCK:
        owner = _owner(_load())
        return {
            'set': bool(owner.get('password_hash')),
            'set_at': owner.get('password_set_at'),
        }


def _check_new_password(password: str) -> str:
    text = str(password or '')
    if len(text) < PASSWORD_MIN_LENGTH:
        raise AuthError('password_too_short', f'Use at least {PASSWORD_MIN_LENGTH} characters.')
    if len(text) > PASSWORD_MAX_LENGTH:
        raise AuthError('password_too_long', f'Use at most {PASSWORD_MAX_LENGTH} characters.')
    return text


def check_owner_password(password: str) -> bool:
    """True when ``password`` matches the owner password (serialized scrypt)."""
    with _LOCK:
        stored = str(_owner(_load()).get('password_hash') or '')
    if not stored:
        return False
    with _PASSWORD_CHECK_LOCK:
        return verify_password(str(password or ''), stored)


def _require_current(current_password: str | None) -> None:
    if not check_owner_password(str(current_password or '')):
        raise AuthError('wrong_password', 'The current password is wrong.', status=403)


def set_owner_password(
    new_password: str,
    *,
    current_password: str | None = None,
    require_current: bool = True,
    keep_session_id: str = '',
) -> dict[str, Any]:
    """Set or change the owner password.

    Changing it needs the current password when ``require_current`` (a remote
    session). Every other session is signed out; ``keep_session_id`` keeps the
    browser that made the change signed in.
    """
    text = _check_new_password(new_password)
    if password_is_set() and require_current:
        _require_current(current_password)
    digest = hash_password(text)
    with _LOCK:
        data = _load()
        owner = _owner(data)
        owner['password_hash'] = digest
        owner['password_set_at'] = time.time()
        data['sessions'] = [row for row in data['sessions'] if keep_session_id and row['id'] == keep_session_id]
        _save(data)
    return password_info()


def remove_owner_password(*, current_password: str | None = None, require_current: bool = True) -> dict[str, Any]:
    """Remove the password. Without one, nobody can sign in from another computer."""
    if not password_is_set():
        return password_info()
    if require_current:
        _require_current(current_password)
    with _LOCK:
        data = _load()
        owner = _owner(data)
        owner['password_hash'] = ''
        owner['password_set_at'] = None
        data['sessions'] = []
        _save(data)
    return password_info()


# ---------------------------------------------------------------- sessions

def _session_live(row: dict[str, Any], now: float) -> bool:
    return now < float(row.get('expires_at') or 0.0) and now - float(row.get('last_seen_at') or 0.0) < SESSION_IDLE_SECONDS


def public_session(row: dict[str, Any], *, current_id: str = '') -> dict[str, Any]:
    return {
        'id': row['id'],
        'created_at': row.get('created_at'),
        'last_seen_at': row.get('last_seen_at'),
        'expires_at': row.get('expires_at'),
        'ip': row.get('ip') or '',
        'user_agent': row.get('user_agent') or '',
        'current': bool(current_id) and row['id'] == current_id,
    }


def create_session(*, ip: str = '', user_agent: str = '') -> tuple[str, dict[str, Any]]:
    """Start a browser session for the owner. Returns (token, public record)."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    record = {
        'id': f's_{secrets.token_hex(6)}',
        'user': OWNER_ID,
        'token_hash': _sha256(token),
        'created_at': now,
        'last_seen_at': now,
        'expires_at': now + SESSION_TTL_SECONDS,
        'ip': str(ip or ''),
        'user_agent': str(user_agent or '')[:200],
    }
    with _LOCK:
        data = _load()
        if not _owner(data).get('password_hash'):
            raise AuthError('no_password', 'No owner password is set on this Console.', status=409)
        sessions = [row for row in data['sessions'] if _session_live(row, now)]
        sessions.append(record)
        data['sessions'] = sessions[-MAX_SESSIONS:]
        _save(data)
    return token, public_session(record, current_id=record['id'])


def validate_session(token: str, *, ip: str = '') -> dict[str, Any] | None:
    """The live session for ``token``, or None (unknown, revoked, or expired)."""
    text = str(token or '')
    if not text or len(text) > 256:
        return None
    digest = _sha256(text)
    now = time.time()
    with _LOCK:
        data = _load()
        if not _owner(data).get('password_hash'):
            return None
        for row in data['sessions']:
            if not hmac.compare_digest(str(row['token_hash']), digest):
                continue
            if not _session_live(row, now):
                return None
            row['last_seen_at'] = now
            if ip:
                row['ip'] = str(ip)
            _touch_save(data)
            return dict(row)
    return None


def list_sessions(*, current_id: str = '') -> list[dict[str, Any]]:
    now = time.time()
    with _LOCK:
        rows = [row for row in _load()['sessions'] if _session_live(row, now)]
    rows.sort(key=lambda row: float(row.get('last_seen_at') or 0.0), reverse=True)
    return [public_session(row, current_id=current_id) for row in rows]


def revoke_session(session_id: str) -> bool:
    key = str(session_id or '')
    with _LOCK:
        data = _load()
        kept = [row for row in data['sessions'] if row['id'] != key]
        if len(kept) == len(data['sessions']):
            return False
        data['sessions'] = kept
        _save(data)
    return True


def revoke_all_sessions(*, except_id: str = '') -> int:
    with _LOCK:
        data = _load()
        kept = [row for row in data['sessions'] if except_id and row['id'] == except_id]
        removed = len(data['sessions']) - len(kept)
        if removed:
            data['sessions'] = kept
            _save(data)
    return removed


# ---------------------------------------------------------------- machine keys

def _clamp_limit(value: Any) -> float:
    limit = _as_float(value, DOWNLOAD_LIMIT_DEFAULT_GB)
    if limit is None or limit != limit:  # NaN
        limit = DOWNLOAD_LIMIT_DEFAULT_GB
    return round(max(0.0, min(DOWNLOAD_LIMIT_MAX_GB, float(limit))), 3)


def _clean_key_name(name: Any) -> str:
    text = ' '.join(str(name or '').split())[:KEY_NAME_MAX]
    if not text:
        raise AuthError('name_required', 'Give the key a name, such as the computer that will use it.')
    return text


def public_key(row: dict[str, Any]) -> dict[str, Any]:
    limit_gb = float(row.get('download_limit_gb') or 0.0)
    return {
        'id': row['id'],
        'name': row.get('name') or '',
        'hint': row.get('hint') or '',
        'created_at': row.get('created_at'),
        'last_used_at': row.get('last_used_at'),
        'last_used_ip': row.get('last_used_ip') or '',
        'may_download': row.get('may_download') is True,
        'download_limit_gb': limit_gb,
        'downloaded_bytes': int(row.get('downloaded_bytes') or 0),
        'download_limit_bytes': int(limit_gb * (1024 ** 3)),
    }


def create_key(
    name: str,
    *,
    may_download: bool = False,
    download_limit_gb: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create a machine key. Returns (full key, public record).

    The full key is returned only here; afterwards only its hash is kept.
    """
    label = _clean_key_name(name)
    raw = KEY_PREFIX + secrets.token_urlsafe(KEY_RANDOM_BYTES)
    record = {
        'id': f'k_{secrets.token_hex(6)}',
        'name': label,
        'hint': raw[:len(KEY_PREFIX) + 4],
        'key_hash': _sha256(raw),
        'created_at': time.time(),
        'last_used_at': None,
        'last_used_ip': '',
        'may_download': bool(may_download),
        'download_limit_gb': _clamp_limit(download_limit_gb if download_limit_gb is not None else DOWNLOAD_LIMIT_DEFAULT_GB),
        'downloaded_bytes': 0,
    }
    with _LOCK:
        data = _load()
        if len(data['keys']) >= MAX_KEYS:
            raise AuthError('too_many_keys', f'This Console already has {MAX_KEYS} machine keys. Revoke one first.', status=409)
        data['keys'].append(record)
        _save(data)
    return raw, public_key(record)


def validate_key(raw_key: str, *, ip: str = '') -> dict[str, Any] | None:
    """The key record for a presented machine key, or None."""
    text = str(raw_key or '').strip()
    if not text.startswith(KEY_PREFIX) or len(text) > 200:
        return None
    digest = _sha256(text)
    found: dict[str, Any] | None = None
    with _LOCK:
        data = _load()
        for row in data['keys']:
            # Compare every row so timing does not depend on the key's position.
            if hmac.compare_digest(str(row['key_hash']), digest):
                found = row
        if found is None:
            return None
        found['last_used_at'] = time.time()
        if ip:
            found['last_used_ip'] = str(ip)
        _touch_save(data)
        return dict(found)


def get_key(key_id: str) -> dict[str, Any] | None:
    with _LOCK:
        for row in _load()['keys']:
            if row['id'] == str(key_id or ''):
                return dict(row)
    return None


def list_keys() -> list[dict[str, Any]]:
    with _LOCK:
        rows = list(_load()['keys'])
    rows.sort(key=lambda row: float(row.get('created_at') or 0.0))
    return [public_key(row) for row in rows]


def update_key(
    key_id: str,
    *,
    name: str | None = None,
    may_download: bool | None = None,
    download_limit_gb: float | None = None,
    reset_usage: bool = False,
) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        row = next((item for item in data['keys'] if item['id'] == str(key_id or '')), None)
        if row is None:
            raise AuthError('key_not_found', 'That machine key does not exist (it may have been revoked).', status=404)
        if name is not None:
            row['name'] = _clean_key_name(name)
        if may_download is not None:
            row['may_download'] = bool(may_download)
        if download_limit_gb is not None:
            row['download_limit_gb'] = _clamp_limit(download_limit_gb)
        if reset_usage:
            row['downloaded_bytes'] = 0
        _save(data)
        return public_key(row)


def revoke_key(key_id: str) -> bool:
    with _LOCK:
        data = _load()
        kept = [row for row in data['keys'] if row['id'] != str(key_id or '')]
        if len(kept) == len(data['keys']):
            return False
        data['keys'] = kept
        _save(data)
    return True


def reserve_key_download_bytes(key_id: str, size_bytes: int) -> bool:
    """Count bytes against a key's disk limit, atomically.

    Returns False (and counts nothing) when they do not fit, so parallel
    downloads from one key cannot overshoot the limit together.
    """
    amount = max(0, int(size_bytes or 0))
    with _LOCK:
        data = _load()
        for row in data['keys']:
            if row['id'] != str(key_id or ''):
                continue
            used = int(row.get('downloaded_bytes') or 0)
            limit = int(float(row.get('download_limit_gb') or 0.0) * (1024 ** 3))
            if used + amount > limit:
                return False
            row['downloaded_bytes'] = used + amount
            _save(data)
            return True
    return False


def release_key_download_bytes(key_id: str, size_bytes: int) -> None:
    """Give back reserved bytes of a download that did not start."""
    amount = max(0, int(size_bytes or 0))
    if not amount:
        return
    with _LOCK:
        data = _load()
        for row in data['keys']:
            if row['id'] == str(key_id or ''):
                row['downloaded_bytes'] = max(0, int(row.get('downloaded_bytes') or 0) - amount)
                _save(data)
                return


# ---------------------------------------------------------------- sign-in throttle

class LoginThrottle:
    """Slow down wrong passwords, then lock sign-in for a few minutes.

    Per address: each failure adds a growing delay; ``MAX_FAILURES`` failures
    within ``WINDOW_SECONDS`` lock that address for ``LOCK_SECONDS``. Across
    all addresses, ``GLOBAL_MAX_FAILURES`` failures lock every sign-in, so
    guessing from many addresses does not help. The owner can always use the
    Console on its own PC, which never needs a password.
    """

    MAX_FAILURES = 5
    GLOBAL_MAX_FAILURES = 20
    WINDOW_SECONDS = 15 * 60
    LOCK_SECONDS = 5 * 60
    MAX_DELAY_SECONDS = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._global_failures: list[float] = []
        self._global_locked_until = 0.0

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()
            self._global_failures.clear()
            self._global_locked_until = 0.0

    def locked_for(self, ip: str) -> float:
        """Seconds until ``ip`` may try again (0 when it may try now)."""
        now = time.time()
        with self._lock:
            until = max(self._locked_until.get(ip, 0.0), self._global_locked_until)
        return max(0.0, until - now)

    def record_failure(self, ip: str) -> tuple[float, float]:
        """Note a wrong password. Returns (delay seconds, lock seconds)."""
        now = time.time()
        with self._lock:
            recent = [ts for ts in self._failures.get(ip, []) if now - ts < self.WINDOW_SECONDS]
            recent.append(now)
            self._global_failures = [ts for ts in self._global_failures if now - ts < self.WINDOW_SECONDS]
            self._global_failures.append(now)
            lock_seconds = 0.0
            if len(recent) >= self.MAX_FAILURES:
                self._locked_until[ip] = now + self.LOCK_SECONDS
                recent = []
                lock_seconds = float(self.LOCK_SECONDS)
            if len(self._global_failures) >= self.GLOBAL_MAX_FAILURES:
                self._global_locked_until = now + self.LOCK_SECONDS
                self._global_failures = []
                lock_seconds = float(self.LOCK_SECONDS)
            self._failures[ip] = recent
            delay = min(self.MAX_DELAY_SECONDS, 0.25 * (2 ** max(0, len(recent) - 1))) if recent else 0.0
        return delay, lock_seconds

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)


LOGIN_THROTTLE = LoginThrottle()
_SIGN_IN_LOCK = threading.Lock()


def attempt_sign_in(ip: str, password: str) -> dict[str, Any]:
    """Check the owner password under the sign-in throttle.

    The throttle check, the password check, and the failure count happen
    under one lock, so parallel guesses cannot slip past the lock-out.
    Returns ``{'result': 'ok' | 'wrong' | 'locked' | 'no_password',
    'delay': seconds to wait before answering, 'retry_after': seconds}``.
    """
    address = str(ip or '')
    with _SIGN_IN_LOCK:
        if not password_is_set():
            return {'result': 'no_password', 'delay': 0.0, 'retry_after': 0.0}
        locked = LOGIN_THROTTLE.locked_for(address)
        if locked > 0:
            return {'result': 'locked', 'delay': 0.0, 'retry_after': locked}
        if check_owner_password(password):
            LOGIN_THROTTLE.record_success(address)
            return {'result': 'ok', 'delay': 0.0, 'retry_after': 0.0}
        delay, lock_seconds = LOGIN_THROTTLE.record_failure(address)
        if lock_seconds > 0:
            return {'result': 'locked', 'delay': delay, 'retry_after': lock_seconds}
        return {'result': 'wrong', 'delay': delay, 'retry_after': 0.0}
