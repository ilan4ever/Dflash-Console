from core.runtimes.contention import gpu_contention_report


def test_contention_stop_others_requires_loaded_models(monkeypatch):
    monkeypatch.setattr(
        'core.runtimes.contention.tcp_port_open',
        lambda host, port: True,
    )
    monkeypatch.setattr(
        'core.runtimes.contention.probe_runtime_state',
        lambda api_url: ([], False, True, None),
    )
    report = gpu_contention_report(cfg={'servers': [{
        'id': 'demo',
        'label': 'Demo',
        'port': 8091,
        'host': '127.0.0.1',
        'api_url': 'http://127.0.0.1:8091/v1',
        'enabled': True,
    }]})
    assert report['recommendation'] == 'none'

    monkeypatch.setattr(
        'core.runtimes.contention.probe_runtime_state',
        lambda api_url: (['model-a'], False, True, None),
    )
    report = gpu_contention_report(cfg={'servers': [{
        'id': 'demo',
        'label': 'Demo',
        'port': 8091,
        'host': '127.0.0.1',
        'api_url': 'http://127.0.0.1:8091/v1',
        'enabled': True,
    }]})
    assert report['recommendation'] == 'stop-others'
