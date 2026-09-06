from core.client_identity import (
    LABEL_CONSOLE_UI,
    LABEL_UNKNOWN_API,
    display_loaded_by_label,
    request_strict_model_match,
    resolve_client_label,
)


class _Headers:
    def __init__(self, mapping: dict[str, str]):
        self._mapping = {k.lower(): v for k, v in mapping.items()}

    def get(self, name: str, default: str = '') -> str:
        return self._mapping.get(str(name or '').lower(), default)


class _Request:
    def __init__(self, headers: dict[str, str]):
        self.headers = _Headers(headers)


def test_resolve_client_label_uses_explicit_header():
    req = _Request({'X-DFlash-Client': 'OneVoice'})
    assert resolve_client_label(req) == 'OneVoice'


def test_request_strict_model_match_header():
    assert request_strict_model_match(_Request({'X-DFlash-Strict-Model': '1'})) is True
    assert request_strict_model_match(_Request({'X-DFlash-Strict-Model': 'true'})) is True
    assert request_strict_model_match(_Request({})) is False


def test_resolve_client_label_detects_onevoice_user_agent():
    req = _Request({'user-agent': 'OneVoice/1.0 python-requests'})
    assert resolve_client_label(req) == 'OneVoice'


def test_resolve_client_label_detects_console_ui_referer():
    req = _Request({'referer': 'http://127.0.0.1:8900/'})
    assert resolve_client_label(req) == LABEL_CONSOLE_UI


def test_resolve_client_label_unknown_api_without_hints():
    req = _Request({'user-agent': 'python-requests/2.32'})
    assert resolve_client_label(req) == LABEL_UNKNOWN_API


def test_display_loaded_by_label_defaults_to_unknown():
    assert display_loaded_by_label('') == LABEL_UNKNOWN_API
    assert display_loaded_by_label('OneVoice') == 'OneVoice'


def test_active_client_label_updates_and_resolves():
    from core.client_identity import (
        begin_active_client,
        clear_active_clients,
        end_active_client,
        list_active_clients,
        resolve_active_clients,
        resolve_engine_client_label,
        set_active_client_label,
    )

    clear_active_clients('gemma-test')
    assert resolve_engine_client_label('gemma-test', 'LoaderApp') == 'LoaderApp'
    assert set_active_client_label('gemma-test', 'OneVoice') is True
    assert resolve_engine_client_label('gemma-test', 'LoaderApp') == 'OneVoice'
    assert set_active_client_label('gemma-test', 'OneVoice') is False
    assert set_active_client_label('gemma-test', 'MyApp') is True
    assert resolve_engine_client_label('gemma-test', 'LoaderApp') == 'MyApp'
    clear_active_clients('gemma-test')


def test_parallel_active_clients_are_listed():
    from core.client_identity import (
        begin_active_client,
        clear_active_clients,
        end_active_client,
        list_active_clients,
        resolve_active_clients,
    )

    clear_active_clients('gemma-test')
    begin_active_client('gemma-test', 'OneVoice')
    begin_active_client('gemma-test', 'MyApp')
    begin_active_client('gemma-test', 'OneVoice')
    assert list_active_clients('gemma-test') == ['MyApp', 'OneVoice']
    assert resolve_active_clients('gemma-test', 'LoaderApp') == ['MyApp', 'OneVoice']
    end_active_client('gemma-test', 'OneVoice')
    assert list_active_clients('gemma-test') == ['MyApp', 'OneVoice']
    end_active_client('gemma-test', 'OneVoice')
    assert list_active_clients('gemma-test') == ['MyApp']
    end_active_client('gemma-test', 'MyApp')
    assert resolve_active_clients('gemma-test', 'LoaderApp') == ['LoaderApp']
    clear_active_clients('gemma-test')
