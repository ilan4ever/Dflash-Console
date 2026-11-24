"""Owner password, sessions, machine keys, and the sign-in throttle (core/auth.py)."""

from __future__ import annotations

import json

import pytest

from core import auth


def _stored() -> dict:
    return json.loads(auth.AUTH_PATH.read_text(encoding='utf-8'))


# ---------------------------------------------------------------- password hashing

def test_production_scrypt_cost_is_owasp_minimum():
    assert auth.SCRYPT_N_DEFAULT >= 2 ** 17
    assert auth.SCRYPT_R == 8
    assert auth.SCRYPT_P == 1


def test_password_hash_round_trip_and_format():
    stored = auth.hash_password('correct horse battery')
    scheme, n, r, p, salt, digest = stored.split('$')
    assert (scheme, int(n), int(r), int(p)) == ('scrypt', auth.SCRYPT_N, auth.SCRYPT_R, auth.SCRYPT_P)
    assert salt and digest
    assert auth.verify_password('correct horse battery', stored)
    assert not auth.verify_password('correct horse batter', stored)
    # A fresh salt every time.
    assert auth.hash_password('correct horse battery') != stored


def test_verify_password_rejects_tampered_or_absurd_hashes():
    stored = auth.hash_password('pw-12345678')
    scheme, n, r, p, salt, digest = stored.split('$')
    assert not auth.verify_password('pw-12345678', f'bcrypt${n}${r}${p}${salt}${digest}')
    assert not auth.verify_password('pw-12345678', f'scrypt${2 ** 30}${r}${p}${salt}${digest}')
    assert not auth.verify_password('pw-12345678', f'scrypt$1000${r}${p}${salt}${digest}')
    assert not auth.verify_password('pw-12345678', 'garbage')
    assert not auth.verify_password('pw-12345678', '')


# ---------------------------------------------------------------- owner password

def test_set_password_stores_only_a_hash_in_a_users_list():
    auth.set_owner_password('hunter22hunter', require_current=False)
    data = _stored()
    assert 'hunter22hunter' not in auth.AUTH_PATH.read_text(encoding='utf-8')
    assert [user['id'] for user in data['users']] == ['owner']
    assert data['users'][0]['role'] == 'owner'
    assert data['users'][0]['password_hash'].startswith('scrypt$')
    assert auth.password_is_set()
    assert auth.check_owner_password('hunter22hunter')
    assert not auth.check_owner_password('wrong-password')


def test_password_rules_and_current_password_check():
    with pytest.raises(auth.AuthError) as short:
        auth.set_owner_password('short', require_current=False)
    assert short.value.error == 'password_too_short'

    auth.set_owner_password('first-password', require_current=False)
    with pytest.raises(auth.AuthError) as wrong:
        auth.set_owner_password('second-password', current_password='nope', require_current=True)
    assert wrong.value.status == 403
    assert auth.check_owner_password('first-password')

    auth.set_owner_password('second-password', current_password='first-password', require_current=True)
    assert auth.check_owner_password('second-password')
    # On this PC the owner may change it without the old one.
    auth.set_owner_password('third-password', require_current=False)
    assert auth.check_owner_password('third-password')


def test_remove_password_signs_everyone_out():
    auth.set_owner_password('owner-password', require_current=False)
    token, _session = auth.create_session(ip='192.168.1.5')
    with pytest.raises(auth.AuthError):
        auth.remove_owner_password(current_password='bad', require_current=True)
    auth.remove_owner_password(current_password='owner-password', require_current=True)
    assert not auth.password_is_set()
    assert auth.validate_session(token) is None
    assert _stored()['sessions'] == []


# ---------------------------------------------------------------- sessions

def test_sessions_need_a_password_and_store_only_a_hash():
    with pytest.raises(auth.AuthError) as no_pw:
        auth.create_session(ip='192.168.1.5')
    assert no_pw.value.error == 'no_password'

    auth.set_owner_password('owner-password', require_current=False)
    token, session = auth.create_session(ip='192.168.1.5', user_agent='Firefox')
    assert len(token) >= 43  # 256 bits, base64url
    assert token not in auth.AUTH_PATH.read_text(encoding='utf-8')
    found = auth.validate_session(token)
    assert found is not None and found['id'] == session['id']
    assert auth.validate_session(token + 'x') is None
    assert auth.validate_session('') is None


def test_session_revoke_and_expiry():
    auth.set_owner_password('owner-password', require_current=False)
    token_a, a = auth.create_session(ip='10.0.0.2')
    token_b, b = auth.create_session(ip='10.0.0.3')
    token_c, c = auth.create_session(ip='10.0.0.4')

    assert auth.revoke_session(a['id'])
    assert auth.validate_session(token_a) is None
    assert not auth.revoke_session(a['id'])

    assert auth.revoke_all_sessions(except_id=b['id']) == 1
    assert auth.validate_session(token_b) is not None
    assert auth.validate_session(token_c) is None

    auth._load()['sessions'][0]['expires_at'] = 0.0
    assert auth.validate_session(token_b) is None


def test_password_change_signs_out_other_browsers_only():
    auth.set_owner_password('owner-password', require_current=False)
    token_keep, keep = auth.create_session(ip='10.0.0.2')
    token_other, _other = auth.create_session(ip='10.0.0.3')
    auth.set_owner_password(
        'new-owner-password',
        current_password='owner-password',
        require_current=True,
        keep_session_id=keep['id'],
    )
    assert auth.validate_session(token_keep) is not None
    assert auth.validate_session(token_other) is None


def test_csrf_token_is_derived_from_the_session_token():
    assert auth.csrf_token_for('abc') == auth.csrf_token_for('abc')
    assert auth.csrf_token_for('abc') != auth.csrf_token_for('abd')
    assert 'abc' not in auth.csrf_token_for('abc')


# ---------------------------------------------------------------- machine keys

def test_machine_keys_are_256_bit_and_stored_hashed():
    raw, record = auth.create_key('OMAN')
    assert raw.startswith(auth.KEY_PREFIX)
    assert len(raw) - len(auth.KEY_PREFIX) >= 43  # token_urlsafe(32)
    assert raw not in auth.AUTH_PATH.read_text(encoding='utf-8')
    stored = _stored()['keys'][0]
    assert stored['key_hash'] != raw and len(stored['key_hash']) == 64
    assert record['may_download'] is False
    assert record['download_limit_gb'] == auth.DOWNLOAD_LIMIT_DEFAULT_GB
    assert 'key_hash' not in record

    found = auth.validate_key(raw, ip='192.168.1.60')
    assert found is not None and found['id'] == record['id']
    assert auth.validate_key(raw[:-1] + ('A' if raw[-1] != 'A' else 'B')) is None
    assert auth.validate_key('not-a-key') is None
    assert auth.validate_key('') is None


def test_machine_key_update_and_revoke():
    raw, record = auth.create_key('Laptop')
    updated = auth.update_key(record['id'], name='Lab laptop', may_download=True, download_limit_gb=12)
    assert updated['name'] == 'Lab laptop'
    assert updated['may_download'] is True
    assert updated['download_limit_gb'] == 12

    assert auth.reserve_key_download_bytes(record['id'], 1024)
    assert auth.get_key(record['id'])['downloaded_bytes'] == 1024
    assert auth.update_key(record['id'], reset_usage=True)['downloaded_bytes'] == 0

    with pytest.raises(auth.AuthError):
        auth.create_key('   ')
    assert auth.revoke_key(record['id'])
    assert auth.validate_key(raw) is None
    assert not auth.revoke_key(record['id'])
    with pytest.raises(auth.AuthError) as missing:
        auth.update_key(record['id'], name='x')
    assert missing.value.status == 404


def test_download_reservations_never_overshoot_the_limit():
    _raw, record = auth.create_key('Lab', may_download=True, download_limit_gb=1)
    gib = 1024 ** 3
    assert auth.reserve_key_download_bytes(record['id'], gib // 2)
    assert auth.reserve_key_download_bytes(record['id'], gib // 2)
    # Full: nothing more fits, and a refused reservation counts nothing.
    assert not auth.reserve_key_download_bytes(record['id'], 1)
    assert auth.get_key(record['id'])['downloaded_bytes'] == gib
    auth.release_key_download_bytes(record['id'], gib // 2)
    assert auth.get_key(record['id'])['downloaded_bytes'] == gib // 2
    auth.release_key_download_bytes(record['id'], 10 * gib)
    assert auth.get_key(record['id'])['downloaded_bytes'] == 0
    assert not auth.reserve_key_download_bytes('k_missing', 1)


def test_corrupt_auth_file_fails_closed_and_is_kept():
    raw, _record = auth.create_key('OMAN')
    auth.AUTH_PATH.write_text('{not json', encoding='utf-8')
    auth.reset_cache()
    assert auth.validate_key(raw) is None
    assert not auth.password_is_set()
    assert list(auth.AUTH_PATH.parent.glob('auth.json.corrupt-*'))


# ---------------------------------------------------------------- sign-in throttle

def test_throttle_slows_down_then_locks():
    throttle = auth.LoginThrottle()
    delays = [throttle.record_failure('10.0.0.9')[0] for _ in range(throttle.MAX_FAILURES - 1)]
    assert delays == sorted(delays) and delays[-1] > delays[0]
    assert throttle.locked_for('10.0.0.9') == 0
    _delay, lock_seconds = throttle.record_failure('10.0.0.9')
    assert lock_seconds == throttle.LOCK_SECONDS
    assert throttle.locked_for('10.0.0.9') > throttle.LOCK_SECONDS - 5
    # Another address is not locked by this one.
    assert throttle.locked_for('10.0.0.10') == 0


def test_throttle_success_clears_failures_and_global_lock_applies():
    throttle = auth.LoginThrottle()
    throttle.record_failure('10.0.0.9')
    throttle.record_success('10.0.0.9')
    assert throttle.record_failure('10.0.0.9')[0] == pytest.approx(0.25)

    throttle.reset()
    for index in range(throttle.GLOBAL_MAX_FAILURES):
        throttle.record_failure(f'10.0.1.{index}')
    assert throttle.locked_for('10.0.2.1') > 0


def test_attempt_sign_in_results():
    assert auth.attempt_sign_in('10.0.0.9', 'x')['result'] == 'no_password'
    auth.set_owner_password('owner-password', require_current=False)
    assert auth.attempt_sign_in('10.0.0.9', 'owner-password')['result'] == 'ok'
    results = [auth.attempt_sign_in('10.0.0.9', 'wrong')['result'] for _ in range(auth.LoginThrottle.MAX_FAILURES)]
    assert results[:-1] == ['wrong'] * (auth.LoginThrottle.MAX_FAILURES - 1)
    assert results[-1] == 'locked'
    # Locked even with the right password.
    assert auth.attempt_sign_in('10.0.0.9', 'owner-password')['result'] == 'locked'
