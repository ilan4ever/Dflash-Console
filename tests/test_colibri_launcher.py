from pathlib import Path

import core.external_launchers as launchers


def _colibri_root(tmp_path: Path) -> Path:
    root = tmp_path / 'colibri'
    root.mkdir()
    (root / 'coli').write_text('# coli launcher', encoding='utf-8')
    (root / 'glm53.exe').write_bytes(b'MZ')
    return root


def _entry(tmp_path: Path, monkeypatch) -> dict:
    root = _colibri_root(tmp_path)
    monkeypatch.setattr(launchers, '_COLIBRI_ROOTS', (root,))
    monkeypatch.delenv('COLIBRI_HOME', raising=False)
    gguf = tmp_path / 'models' / 'GLM-5.3-Flash-UD-IQ2_XXS-00001-of-00004.gguf'
    gguf.parent.mkdir()
    gguf.write_bytes(b'GGUF')
    entry = launchers.colibri_launcher_for_gguf(gguf, port=8120, context=32768, gpu=0, gpu2=1)
    assert entry is not None
    return entry


def test_colibri_entry_serves_the_imported_model_dir_on_vulkan(tmp_path, monkeypatch):
    entry = _entry(tmp_path, monkeypatch)

    assert entry['kind'] == 'colibri'
    assert entry['id'] == 'colibri-glm-5.3-flash-ud-iq2_xxs'
    assert entry['model_id'] == 'glm-5.3-flash-ud-iq2_xxs'
    assert entry['model_dir'].endswith('GLM-5.3-Flash-UD-IQ2_XXS.colibri')
    assert Path(entry['model_dir']) == Path(entry['cwd']) / 'models' / 'GLM-5.3-Flash-UD-IQ2_XXS.colibri'
    assert Path(entry['model_path']) == tmp_path / 'models' / 'GLM-5.3-Flash-UD-IQ2_XXS-00001-of-00004.gguf'
    args = entry['args']
    assert args[1] == 'serve'
    assert args[args.index('--model') + 1] == entry['model_dir']
    assert args[args.index('--port') + 1] == '8120'
    assert entry['env']['COLI_VULKAN'] == '1'
    assert entry['env']['COLI_VK_DEV'] == '0'
    assert entry['env']['COLI_VK_DEV2'] == '1'
    assert entry['parallel_flag'] == '--kv-slots'
    assert launchers.launcher_context_size(entry) == 32768
    assert launchers.launcher_model_path(entry) == entry['model_path']


def test_single_gpu_entry_has_no_second_device(tmp_path, monkeypatch):
    root = _colibri_root(tmp_path)
    monkeypatch.setattr(launchers, '_COLIBRI_ROOTS', (root,))
    entry = launchers.colibri_launcher_for_gguf(tmp_path / 'm.gguf', gpu=0, gpu2=None)
    assert 'COLI_VK_DEV2' not in entry['env']


def test_colibri_health_ok_counts_as_loaded():
    entry = {'kind': 'colibri'}
    assert launchers.health_loaded(entry, {'status': 'ok'}) is True
    assert launchers.health_loaded(entry, {'status': 'engine_down'}) is False
    assert launchers.health_loaded(entry, None) is False
    # Strata still needs its explicit flag.
    assert launchers.health_loaded({'kind': 'strata'}, {'status': 'ok'}) is False
    assert launchers.health_loaded({'kind': 'strata'}, {'loaded': True}) is True


def test_launcher_for_process_matches_coli_serve_and_its_engine(tmp_path, monkeypatch):
    entry = _entry(tmp_path, monkeypatch)
    monkeypatch.setattr(launchers, 'launchers', lambda: [dict(entry)])
    root = entry['cwd']

    server = launchers.launcher_for_process(
        process_name='python.exe',
        command_line=f'"C:\\Python314\\python.exe" {root}\\coli serve --model x --port 8120',
    )
    engine = launchers.launcher_for_process(
        process_name='glm53.exe',
        command_line=f'"{root}\\glm53.exe" --serve',
    )
    other_port = launchers.launcher_for_process(
        process_name='python.exe',
        command_line=f'python {root}\\coli serve --port 9000',
    )
    assert server and server['id'] == entry['id']
    assert engine and engine['id'] == entry['id']
    assert other_port is None


def test_prepare_refuses_an_incomplete_split_download(tmp_path, monkeypatch):
    entry = _entry(tmp_path, monkeypatch)
    result = launchers.prepare_colibri_model(entry)
    assert result['success'] is False
    assert '00002-of-00004' in result['error']


def test_prepare_skips_an_existing_import(tmp_path, monkeypatch):
    entry = _entry(tmp_path, monkeypatch)
    model_dir = Path(entry['model_dir'])
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'gguf.json').write_text('{}', encoding='utf-8')
    result = launchers.prepare_colibri_model(entry)
    assert result == {'success': True, 'imported': False, 'model_dir': str(model_dir)}


def test_register_keeps_user_settings(tmp_path, monkeypatch):
    entry = _entry(tmp_path, monkeypatch)
    monkeypatch.setattr(launchers, 'LAUNCHERS_PATH', tmp_path / 'external_launchers.json')
    launchers._write_registry([{**entry, 'parallel': 3, 'loaded_by': 'Harness'}])

    result = launchers.register_colibri_launcher(entry['model_path'], port=8120, context=32768)

    assert result['success'] is True
    saved = launchers.get_launcher(entry['id'])
    assert saved['parallel'] == 3
    assert saved['loaded_by'] == 'Harness'
    assert saved['start_timeout_s'] == 900
    assert launchers._start_timeout(saved, 300) == 900
