from core.release_notice import is_newer_release, release_notice, version_key


def test_version_key_ignores_v_prefix():
    assert version_key('v0.3.264') == (0, 3, 264)
    assert version_key('0.3.267') == (0, 3, 267)


def test_newer_release_only_when_github_is_ahead():
    assert is_newer_release('v0.3.268', '0.3.267') is True
    assert is_newer_release('v0.3.267', '0.3.267') is False
    assert is_newer_release('v0.3.264', '0.3.267') is False


def test_release_notice_message_for_server_users():
    notice = release_notice(current='0.3.100', fetch_latest=lambda: 'v0.3.200')
    assert notice['update_available'] is True
    assert notice['latest_version'] == '0.3.200'
    assert 'Pull the latest code and restart the server' in notice['message']
    assert notice['release_url'].endswith('/releases/latest')


def test_release_notice_stays_quiet_when_github_fails():
    def _fail():
        raise TimeoutError('offline')

    notice = release_notice(current='0.3.267', fetch_latest=_fail)
    assert notice['update_available'] is False
    assert notice['message'] == ''
