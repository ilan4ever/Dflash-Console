import core.external_models as external_models
import core.gpu_processes as gpu_processes


def _card(**over):
    card = {
        'pid': 4242,
        'app_source': 'strata',
        'title': 'Strata · Qwen3.8-Flash-Next',
        'model_id': 'qwen3.8-flash-next-unsloth-ud-iq4_xs',
        'api_url': 'http://127.0.0.1:8080/v1',
        'context_size': 131072,
    }
    card.update(over)
    return card


def test_external_entries_expose_the_strata_model(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', lambda: [_card()])
    external_models.forget_external_models()

    entries = external_models.external_model_entries(force=True)
    ids = [entry['id'] for entry in entries]

    # The app-prefixed id comes first so a list is easy to read, and the raw
    # name the upstream server reports also works for clients that know it.
    assert ids[0] == 'strata-qwen3-8-flash-next-unsloth-ud-iq4-xs'
    assert 'qwen3.8-flash-next-unsloth-ud-iq4_xs' in ids
    assert {entry['api_url'] for entry in entries} == {'http://127.0.0.1:8080/v1'}
    assert {entry['api_model_id'] for entry in entries} == {'qwen3.8-flash-next-unsloth-ud-iq4_xs'}


def test_external_entries_skip_workloads_without_an_openai_endpoint(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', lambda: [_card(api_url='')])
    monkeypatch.setattr('core.external_models._launcher_entries', lambda entries, seen: None)
    external_models.forget_external_models()

    assert external_models.external_model_entries(force=True) == []


def test_external_entries_skip_console_owned_launcher_cards(monkeypatch):
    card = _card(console_engine=True, external=False, launcher_id='strata')
    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', lambda: [card])
    monkeypatch.setattr('core.external_models._launcher_entries', lambda entries, seen: None)
    external_models.forget_external_models()

    assert external_models.external_model_entries(force=True) == []


def test_launcher_entry_is_listed_before_the_model_is_loaded(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', lambda: [])
    monkeypatch.setattr('core.external_launchers.launchers', lambda: [{
        'id': 'strata',
        'label': 'Strata · qwen3.8-flash-next',
        'port': 8080,
        'health_url': 'http://127.0.0.1:8080/health',
        'model_id': 'qwen3.8-flash-next',
    }])
    monkeypatch.setattr('core.external_launchers._http_json', lambda *args, **kwargs: {})
    external_models.forget_external_models()

    entries = external_models.external_model_entries(force=True)
    strata = [entry for entry in entries if entry.get('launcher_id') == 'strata']

    assert strata
    assert strata[0]['loaded'] is False
    assert strata[0]['api_url'] == 'http://127.0.0.1:8080/v1'
    assert strata[0]['api_model_id'] == 'qwen3.8-flash-next'


def test_resolve_external_model_entry_matches_advertised_ids(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', lambda: [_card()])
    external_models.forget_external_models()

    entry = external_models.resolve_external_model_entry('qwen3.8-flash-next-unsloth-ud-iq4_xs')

    assert entry is not None
    assert entry['api_url'] == 'http://127.0.0.1:8080/v1'
    assert external_models.resolve_external_model_entry('a-model-that-is-not-there') is None


def test_resolve_external_model_entry_never_captures_cursor_aliases(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', lambda: [_card()])
    external_models.forget_external_models()

    for reserved in ('gpt-4', 'gpt-4o', 'gpt-4-turbo', 'gpt-4o-mini'):
        assert external_models.resolve_external_model_entry(reserved) is None


def test_resolving_a_model_does_not_rescan_the_gpu_for_every_request(monkeypatch):
    calls = {'n': 0}

    def fake_loads():
        calls['n'] += 1
        return [_card()]

    monkeypatch.setattr(gpu_processes, 'get_external_gpu_loads', fake_loads)
    external_models.forget_external_models()

    first = external_models.resolve_external_model_entry('qwen3.8-flash-next-unsloth-ud-iq4_xs')
    second = external_models.resolve_external_model_entry('qwen3.8-flash-next-unsloth-ud-iq4_xs')

    assert first is not None and second is not None
    assert second['api_url'] == 'http://127.0.0.1:8080/v1'
    # A chat request must not pay for a fresh nvidia-smi/VRAM scan.
    assert calls['n'] == 1
