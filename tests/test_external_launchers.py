import json
from pathlib import Path

import core.external_launchers as launchers


def _entry(tmp_path: Path) -> dict:
    return {
        'id': 'strata',
        'label': 'Strata · test',
        'command': 'python',
        'args': ['serve/server.py', '--engine', 'strata', '--config', 'cfg.json', '--port', '8080'],
        'cwd': str(tmp_path),
        'config': 'cfg.json',
        'port': 8080,
        'health_url': 'http://127.0.0.1:8099/health',
        'parallel': 1,
        'parallel_flag': '--parallel',
    }


def test_args_keep_one_slot_clean_and_replace_an_existing_flag(tmp_path):
    entry = _entry(tmp_path)

    # One slot is the default and stays implicit.
    assert launchers._args_with_parallel(entry, 1) == entry['args']

    with_flag = {**entry, 'args': entry['args'] + ['--parallel', '3']}
    assert launchers._args_with_parallel(with_flag, 4)[-2:] == ['--parallel', '4']
    assert launchers._args_with_parallel(with_flag, 1) == entry['args']


def test_parallel_is_clamped_to_the_supported_range(tmp_path, monkeypatch):
    monkeypatch.setattr(launchers, 'LAUNCHERS_PATH', tmp_path / 'external_launchers.json')
    monkeypatch.setattr(launchers, 'launchers', lambda: [dict(_entry(tmp_path))])

    assert launchers.set_parallel('strata', 99)['parallel'] == launchers.MAX_PARALLEL
    assert launchers.set_parallel('strata', 0)['parallel'] == launchers.MIN_PARALLEL


def test_load_reports_an_already_running_server_without_restarting_it(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    monkeypatch.setattr(launchers, 'get_launcher', lambda launcher_id: dict(entry))
    monkeypatch.setattr(launchers, 'set_parallel', lambda launcher_id, n: {**entry, 'parallel': n})
    monkeypatch.setattr(launchers, '_http_json', lambda *args, **kwargs: {'loaded': True})
    monkeypatch.setattr(launchers, '_processes_for', lambda row: [])

    result = launchers.start('strata', parallel=1)

    assert result['success'] is True
    assert result['already_running'] is True
    assert result['loaded'] is True


def test_stop_without_a_running_process_reports_success(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    monkeypatch.setattr(launchers, 'get_launcher', lambda launcher_id: dict(entry))
    monkeypatch.setattr(launchers, '_processes_for', lambda row: [])
    monkeypatch.setattr(launchers, '_http_json', lambda *args, **kwargs: None)

    result = launchers.stop('strata')

    assert result['success'] is True
    assert result['stopped'] is True


def test_launcher_for_process_matches_the_strata_engine(tmp_path, monkeypatch):
    entry = {**_entry(tmp_path), 'kind': 'strata'}
    monkeypatch.setattr(launchers, 'launchers', lambda: [entry])

    assert launchers.launcher_for_process(process_name='strata.exe')['id'] == 'strata'
    assert launchers.launcher_for_process(process_name='strata-vision.exe')['id'] == 'strata'
    assert launchers.launcher_for_process(
        process_name='python.exe',
        command_line='python serve/server.py --engine strata --port 8080',
    )['id'] == 'strata'
    assert launchers.launcher_for_process(process_name='other.exe') is None


def test_launcher_model_id_reads_the_strata_config(tmp_path):
    config = tmp_path / 'strata-unsloth.json'
    config.write_text(
        '{"model_name": "qwen3.8-flash-next-unsloth-ud-iq4_xs"}',
        encoding='utf-8',
    )
    entry = {**_entry(tmp_path), 'config': str(config)}

    assert launchers.launcher_model_id(entry) == 'qwen3.8-flash-next-unsloth-ud-iq4_xs'


def test_launcher_context_and_model_path_read_the_strata_config(tmp_path):
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'x')
    config = tmp_path / 'strata.json'
    config.write_text(
        json.dumps({
            'model_name': 'qwen3.8-flash-next',
            'args': ['--max-context', '131072', '--native', str(model)],
        }),
        encoding='utf-8',
    )
    entry = {**_entry(tmp_path), 'config': str(config)}

    assert launchers.launcher_context_size(entry) == 131072
    assert launchers.launcher_model_path(entry) == str(model)


def test_note_launcher_client_records_the_requesting_app(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    saved: list[dict] = []
    monkeypatch.setattr(launchers, 'get_launcher', lambda launcher_id: dict(entry))
    monkeypatch.setattr(launchers, 'save_launcher', lambda row: saved.append(dict(row)) or dict(row))

    updated = launchers.note_launcher_client('strata', 'OneVoice')

    assert updated['loaded_by'] == 'OneVoice'
    assert saved and saved[0]['loaded_by'] == 'OneVoice'
    assert launchers.note_launcher_client('strata', 'DFlash Console') is None


def test_discovery_is_quiet_when_there_is_no_strata_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(launchers, '_STRATA_ROOTS', (tmp_path,))

    assert launchers.discover_strata_launcher() is None


def test_discovery_reads_the_checkout_config(tmp_path, monkeypatch):
    root = tmp_path / 'Strata'
    (root / 'serve').mkdir(parents=True)
    (root / 'serve' / 'server.py').write_text('', encoding='utf-8')
    (root / 'strata-unsloth-ud-iq4_xs.json').write_text(
        '{"port": 8123, "model_name": "qwen3.8-flash-next-unsloth-ud-iq4_xs"}',
        encoding='utf-8',
    )
    monkeypatch.setattr(launchers, '_STRATA_ROOTS', (root,))

    entry = launchers.discover_strata_launcher()

    assert entry is not None
    assert entry['port'] == 8123
    assert entry['health_url'] == 'http://127.0.0.1:8123/health'
    assert entry['parallel'] == 1
    assert 'qwen3.8-flash-next' in entry['label']
