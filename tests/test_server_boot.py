from __future__ import annotations

import json

import core.server_boot as server_boot


def test_managed_process_identity_rejects_unrelated_process(monkeypatch):
    monkeypatch.setattr(server_boot.sys, 'platform', 'win32')
    monkeypatch.setattr(
        server_boot.subprocess,
        'run',
        lambda *args, **kwargs: type(
            'Result',
            (),
            {'returncode': 0, 'stdout': json.dumps({'Name': 'python.exe', 'CommandLine': 'python unrelated.py'})},
        )(),
    )

    assert server_boot.managed_process_identity(1234) is False


def test_managed_process_identity_accepts_llama_process(monkeypatch):
    monkeypatch.setattr(server_boot.sys, 'platform', 'win32')
    monkeypatch.setattr(
        server_boot.subprocess,
        'run',
        lambda *args, **kwargs: type(
            'Result',
            (),
            {'returncode': 0, 'stdout': json.dumps({
                'Name': 'llama-server.exe',
                'CommandLine': r'C:\dev\Dflash\llama-server.exe --port 8090',
            })},
        )(),
    )

    assert server_boot.managed_process_identity(1234) is True


def test_wait_for_port_closed_retries_until_listener_is_gone(monkeypatch):
    states = iter([True, True, False])
    monkeypatch.setattr(server_boot, '_tcp_port_open', lambda host, port: next(states))
    monkeypatch.setattr(server_boot.time, 'sleep', lambda seconds: None)

    assert server_boot.wait_for_port_closed('127.0.0.1', 8090, timeout=1) is True


def test_ensure_managed_listen_port_rebinds_foreign_occupant(monkeypatch):
    server = {
        'id': 'qwen3-8-27b-q6-k-l',
        'host': '127.0.0.1',
        'port': 8090,
        'api_url': 'http://127.0.0.1:8090/v1',
    }
    saved: list[dict] = []

    monkeypatch.setattr(server_boot, '_tcp_port_open', lambda host, port: port == 8090)
    monkeypatch.setattr(server_boot, 'listener_is_managed_engine', lambda host, port: False)
    monkeypatch.setattr(
        'core.config.suggest_server_port',
        lambda cfg=None: 8097,
    )
    monkeypatch.setattr(
        'core.config.apply_server_listen_port',
        lambda server_id, port, cfg=None, persist=True: saved.append({'id': server_id, 'port': port, 'persist': persist}) or {
            'success': True,
            'port': port,
            'api_url': f'http://127.0.0.1:{port}/v1',
        },
    )

    result = server_boot.ensure_managed_listen_port(server, cfg={'servers': [dict(server)]})
    assert result['success'] is True
    assert result['reason'] == 'rebound'
    assert result['previous_port'] == 8090
    assert result['port'] == 8097
    assert server['port'] == 8097
    assert server['api_url'] == 'http://127.0.0.1:8097/v1'
    assert saved == [{'id': 'qwen3-8-27b-q6-k-l', 'port': 8097, 'persist': True}]


def test_checkpoint_load_failure_error_prefers_log_message(monkeypatch, tmp_path):
    server_id = 'qwen3-8-27b-q6-k-l-dflash'
    log_path = tmp_path / f'{server_id}.log'
    log_path.write_text(
        '\n'.join([
            '=== boot 2026-01-01 12:00:00 profile=x router=1 ===',
            'load: spawning server instance with name=qwen3.8-27b-q6-k-l',
            'load_model: loading model qwen3.8-27b-q6-k-l',
            'llama_model_load: error loading model: done_getting_tensors: wrong number of tensors; expected 81, got 58',
            "srv    load_model: failed to load draft model, 'C:\\\\models\\\\Qwen3.8-27B-DFlash2-Q4_K_M.gguf'",
            'llama-server: exiting due to model loading error',
        ]),
        encoding='utf-8',
    )
    monkeypatch.setattr('core.load_progress.LOG_DIR', tmp_path)

    message = server_boot._checkpoint_load_failure_error(
        server_id,
        {'failed': True, 'args': ['9', '--ubatch-size', '512']},
    )

    assert 'draft' in message.lower()
    assert '9' not in message


def test_dflash2_engine_capability_rejects_old_build(monkeypatch, tmp_path):
    binary = tmp_path / 'llama-server.exe'
    binary.write_bytes(b'engine')
    monkeypatch.setattr(
        server_boot.subprocess,
        'run',
        lambda *args, **kwargs: type(
            'Result',
            (),
            {'stdout': 'version: 0.1.0-dev (build 10405)', 'stderr': ''},
        )(),
    )

    result = server_boot.llama_server_capabilities(binary=binary)

    assert result['available'] is True
    assert result['build'] == 10405
    assert result['dflash2'] is False


def test_dflash2_engine_capability_accepts_supported_build(monkeypatch, tmp_path):
    binary = tmp_path / 'llama-server.exe'
    binary.write_bytes(b'engine')
    monkeypatch.setattr(
        server_boot.subprocess,
        'run',
        lambda *args, **kwargs: type(
            'Result',
            (),
            {'stdout': 'version: 0.3.0-dev (build 10702)', 'stderr': ''},
        )(),
    )

    result = server_boot.llama_server_capabilities(binary=binary)

    assert result['available'] is True
    assert result['build'] == 10702
    assert result['dflash2'] is True


def test_dflash_load_failure_returns_repair_instead_of_fallback(monkeypatch, tmp_path):
    target = tmp_path / 'Qwen3.8-27B-Q6_K_L.gguf'
    draft = tmp_path / 'Qwen3.8-27B-DFlash2-Q4_K_M.gguf'
    target.write_bytes(b'target')
    draft.write_bytes(b'draft')
    entry = {
        'id': 'qwen3-dflash',
        'profile': 'qwen-dflash',
        'model_id': 'qwen3',
        'target_path': str(target),
        'draft_path': str(draft),
        'host': '127.0.0.1',
        'port': 8091,
        'api_url': 'http://127.0.0.1:8091/v1',
    }
    monkeypatch.setattr(
        server_boot,
        'validate_dflash_stack',
        lambda *args, **kwargs: {
            'success': True,
            'valid': True,
            'required': True,
            'target_path': str(target),
            'draft_path': str(draft),
            'dflash_generation': 'dflash2',
        },
    )
    monkeypatch.setattr(server_boot, 'find_target_loaded_elsewhere', lambda *args, **kwargs: None)
    monkeypatch.setattr(server_boot, '_tcp_port_open', lambda *args, **kwargs: False)
    monkeypatch.setattr(
        server_boot,
        'write_server_preset',
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        server_boot,
        'start_router_listener',
        lambda *args, **kwargs: {'success': True},
    )
    monkeypatch.setattr(
        'core.runtime._fetch_models_payload',
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        'core.runtime.load_model',
        lambda *args, **kwargs: {
            'success': False,
            'error': 'failed to load draft model: wrong number of tensors',
        },
    )

    result = server_boot.load_server_checkpoint(entry, cfg={'servers': []})

    assert result['success'] is False
    assert result['reason_code'] == 'draft-load-failed'
    assert result['repair']['action'] == 'attach_draft'
    assert 'without' not in str(result).lower()
