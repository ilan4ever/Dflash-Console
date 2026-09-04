import json

from core.gpu_processes import (
    _attach_external_gpu_activity,
    _attach_external_inference_stats,
    _build_external_card,
    _classify_app,
    _external_card_detail,
    _external_card_path_missing,
    _external_acceleration_fields,
    _fetch_process_details,
    _is_gpu_model_load,
    _model_hint_from_cmdline,
    _probe_loaded_model,
    _probe_lmstudio_loaded_models,
    _resolve_ai_tools_model_name,
    _resolve_ai_tools_stt_model_path,
    _resolve_external_model_name,
    _resolve_stt_model_path,
    _read_speak_stt_active_model,
    _speak_stt_log_paths,
    _discover_speak_stt_listener_cards,
    _retain_alive_external_cards,
    _size_gb_from_path,
)
import core.gpu_processes as gpu_processes


def test_classify_onevoice_llama_with_lmstudio_model_path():
    source, label = _classify_app(
        process_name='llama-server.exe',
        command_line=(
            r'"C:\dev\OneVoice\.tmp\llama-b8418-win-cuda12\llama-server.exe" '
            r'-m C:\Users\me\.lmstudio\models\lmstudio-community\Qwen3.5-9B-GGUF\Qwen3.5-9B-Q4_K_M.gguf '
            r'--host 127.0.0.1 --port 32491'
        ),
        parent_name='',
    )
    assert source == 'onevoice'
    assert label == 'OneVoice'


def test_classify_lmstudio_parent():
    source, label = _classify_app(
        process_name='llama-server.exe',
        command_line=r'C:\apps\llama-server.exe -m C:\Users\me\.lmstudio\models\google\model.gguf',
        parent_name='LM Studio.exe',
    )
    assert source == 'lmstudio'
    assert label == 'LM Studio'


def test_external_acceleration_fields_skip_plain_lmstudio_load():
    fields = _external_acceleration_fields(
        command_line=(
            r'C:\Users\me\.lmstudio\extensions\backends\llama-server.exe '
            r'--model C:\Users\me\.lmstudio\models\google\gemma-4-31B_q4_0-it.gguf '
            r'--host 127.0.0.1 --port 31086'
        ),
        app_source='lmstudio',
        draft_name='',
        draft_path='',
        llama_process=True,
    )
    assert fields == {}


def test_external_acceleration_fields_keep_lmstudio_dflash_draft():
    fields = _external_acceleration_fields(
        command_line=(
            r'llama-server.exe --model C:\models\target.gguf '
            r'--model-draft C:\models\gemma-draft.gguf'
        ),
        app_source='lmstudio',
        draft_name='gemma-draft.gguf',
        draft_path=r'C:\models\gemma-draft.gguf',
        llama_process=True,
    )
    assert fields['acceleration_mode'] == 'dflash'
    assert fields['draft_status'] == 'active'


def test_build_external_card_lmstudio_plain_load_has_no_dflash_badge(monkeypatch):
    monkeypatch.setattr(gpu_processes, '_listening_ports_for_pid', lambda _pid: [])
    card = _build_external_card(
        {'pid': 44108, 'gpu_index': 0, 'vram_mb': None, 'vram_gb': None, 'process_name': 'llama-server.exe'},
        details={
            'process_name': 'llama-server.exe',
            'command_line': (
                r'C:\Users\me\.lmstudio\extensions\backends\llama-server.exe '
                r'--model C:\Users\me\.lmstudio\models\google\gemma-4-31B_q4_0-it.gguf '
                r'--host 127.0.0.1 --port 31086'
            ),
            'parent_process_name': 'LM Studio.exe',
        },
        gpus=[{'index': 0, 'display_name': 'RTX 4090', 'name': 'RTX 4090'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is not None
    assert card['app_source'] == 'lmstudio'
    assert 'acceleration_expected' not in card


def test_classify_whisper():
    source, label = _classify_app(
        process_name='python.exe',
        command_line='python -m faster_whisper transcribe --model small.en',
        parent_name='',
    )
    assert source == 'whisper'
    assert label == 'Whisper'


def test_classify_onevoice_stt():
    source, label = _classify_app(
        process_name='python.exe',
        command_line=r'python.exe -u C:\tools\stt\speak_stt.py',
        parent_name='',
    )
    assert source == 'onevoice'
    assert label == 'OneVoice'


def test_model_hint_hf_hub():
    name, _path = _model_hint_from_cmdline(
        r'C:\Users\me\AppData\Local\OneVoiceSpeakData\models\stt\huggingface\hub\models--Systran--faster-whisper-small.en\snapshots\abc'
    )
    assert name == 'Systran/faster-whisper-small.en'


def test_probe_lmstudio_ignores_not_loaded(monkeypatch):
    payload = {
        'data': [
            {'id': 'gemma-4-12b-it-qat', 'state': 'not-loaded'},
            {'id': 'qwen3.5-4b', 'state': 'loaded', 'quantization': 'Q4_K_M'},
        ],
    }

    class FakeResp:
        def read(self):
            import json as json_mod
            return json_mod.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr('core.gpu_processes.urllib.request.urlopen', lambda *args, **kwargs: FakeResp())
    loaded = _probe_lmstudio_loaded_models()
    assert len(loaded) == 1
    assert loaded[0]['model_id'] == 'qwen3.5-4b'


def test_model_hint_from_cmdline():
    name, path = _model_hint_from_cmdline(
        r'C:\bin\llama-server.exe --model "D:\models\gemma-4-12b_q4_0-it.gguf" --port 1234'
    )
    assert name == 'gemma-4-12b_q4_0-it.gguf'
    assert path.endswith('gemma-4-12b_q4_0-it.gguf')


def test_build_external_card_rejects_notepadpp():
    card = _build_external_card(
        {'pid': 9090, 'gpu_index': 0, 'vram_mb': 48.0, 'vram_gb': 0.05, 'process_name': 'notepad++.exe'},
        details={
            'process_name': r'C:\Program Files\Notepad++\notepad++.exe',
            'command_line': r'"C:\Program Files\Notepad++\notepad++.exe" notes.txt',
            'parent_process_name': 'explorer.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is None


def test_build_external_card_rejects_windows_voice_recorder():
    card = _build_external_card(
        {'pid': 36528, 'gpu_index': 0, 'vram_mb': 48.0, 'vram_gb': 0.05, 'process_name': 'VoiceRecorder.exe'},
        details={
            'process_name': 'VoiceRecorder.exe',
            'command_line': (
                r'C:\Program Files\WindowsApps\Microsoft.WindowsSoundRecorder_11.2606.0.0_x64__8wekyb3d8bbwe'
                r'\VoiceRecorder.exe -ServerName:App.AppXrkpg5btabgrsz8zvhn83qm92evdrv0ak.mca'
            ),
            'parent_process_name': 'svchost.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is None


def test_is_gpu_model_load_rejects_desktop_gpu_noise():
    assert not _is_gpu_model_load(
        process_name='CHXSmartScreen.exe',
        command_line=r'C:\Windows\System32\CHXSmartScreen.exe',
        model_name='CHXSmartScreen',
        model_path='',
    )
    assert not _is_gpu_model_load(
        process_name='TabTip.exe',
        command_line=r'C:\Program Files\Common Files\microsoft shared\ink\TabTip.exe',
        model_name='TabTip',
        model_path='',
    )


def test_build_external_card_rejects_desktop_noise():
    card = _build_external_card(
        {'pid': 1234, 'gpu_index': 0, 'vram_mb': 64.0, 'vram_gb': 0.06, 'process_name': 'HWiNFO64.exe'},
        details={
            'process_name': 'HWiNFO64.exe',
            'command_line': r'C:\Program Files\HWiNFO64\HWiNFO64.exe',
            'parent_process_name': '',
        },
        gpus=[{'index': 0, 'name': 'GPU', 'display_name': 'GPU 0'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is None


def test_is_gpu_model_load_rejects_ui_server():
    assert not _is_gpu_model_load(
        process_name='python.exe',
        command_line=r'"C:\envs\onevoice\python.exe" -u C:\dev\OneVoice\ui\server.py',
        model_name='OneVoice UI server',
        model_path='',
    )


def test_is_gpu_model_load_accepts_llama_gguf():
    assert _is_gpu_model_load(
        process_name='llama-server.exe',
        command_line=(
            r'"C:\dev\OneVoice\.tmp\llama-b8418-win-cuda12\llama-server.exe" '
            r'-m C:\dev\OneVoice\models\nomic-embed\nomic-embed-text-v1.5.Q8_0.gguf '
            r'--host 127.0.0.1 --port 8891 --embedding'
        ),
        model_name='nomic-embed-text-v1.5.Q8_0.gguf',
        model_path=r'C:\dev\OneVoice\models\nomic-embed\nomic-embed-text-v1.5.Q8_0.gguf',
    )


def test_is_gpu_model_load_accepts_stt():
    assert _is_gpu_model_load(
        process_name='python.exe',
        command_line=r'python.exe -u C:\tools\stt\speak_stt.py',
        model_name='small.en',
        model_path='',
    )


def test_resolve_external_model_name_speak_stt_loading(monkeypatch):
    monkeypatch.setattr(gpu_processes, '_probe_onevoice_stt_status', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(gpu_processes, '_read_speak_stt_active_model', lambda **_kwargs: '')
    name, path = _resolve_external_model_name(
        app_source='onevoice',
        app_label='OneVoice',
        process_name='python.exe',
        command_line=r'python.exe -u C:\tools\stt\speak_stt.py',
    )
    assert name == 'Loading…'
    assert path == ''


def test_resolve_external_model_name_speak_stt_websocket_ready(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        '_probe_onevoice_stt_status',
        lambda *_args, **_kwargs: {'model_loaded': True, 'model': 'small.en', 'loading': False},
    )
    name, path = _resolve_external_model_name(
        app_source='onevoice',
        app_label='OneVoice',
        process_name='python.exe',
        command_line=r'python.exe -u C:\tools\stt\speak_stt.py',
    )
    assert name == 'small.en'


def test_speak_stt_log_paths_prefers_script_tree():
    command_line = (
        r'C:\Users\me\AppData\Roaming\onevoice-speak-dev\models\stt\runtime\.venv\Scripts\python.exe -u '
        r'C:\dev\Speak-OneVoice\tools\stt\speak_stt.py'
    )
    paths = _speak_stt_log_paths(command_line)
    assert 'Speak-OneVoice' in str(paths[0])
    assert str(paths[0]).endswith(r'tools\logs\speak_stt.debug.log')
    assert any('OneVoiceSpeak' in str(path) for path in paths)


def test_read_speak_stt_active_model_uses_dev_log(tmp_path):
    gpu_processes._STT_MODEL_CACHE.clear()
    tools = tmp_path / 'Speak-OneVoice' / 'tools'
    stt_dir = tools / 'stt'
    stt_dir.mkdir(parents=True)
    (stt_dir / 'speak_stt.py').write_text('', encoding='utf-8')
    log_dir = tools / 'logs'
    log_dir.mkdir(parents=True)
    log_path = log_dir / 'speak_stt.debug.log'
    log_path.write_text(
        json.dumps(
            {
                'ts': '2026-08-22 09:15:13',
                'event': 'model-ready',
                'detail': {'model': 'small.en'},
            }
        )
        + '\n',
        encoding='utf-8',
    )
    command_line = f'python.exe -u {stt_dir / "speak_stt.py"}'
    assert _read_speak_stt_active_model(command_line=command_line, max_age_seconds=0.0) == 'small.en'

def test_probe_loaded_model_marks_busy_port_as_loading(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'tcp_port_open', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(gpu_processes, '_probe_models_fast', lambda *_args, **_kwargs: ([], True))
    probe = _probe_loaded_model('127.0.0.1', 32491)
    assert probe.get('loading') is True
    assert probe.get('api_url')


def test_build_external_card_shows_loading_for_gpu_vram(monkeypatch):
    monkeypatch.setattr(gpu_processes, '_probe_onevoice_stt_status', lambda *_args, **_kwargs: {'loading': True})
    monkeypatch.setattr(gpu_processes, '_read_speak_stt_active_model', lambda **_kwargs: '')
    monkeypatch.setattr(
        gpu_processes,
        '_listening_ports_for_pid',
        lambda _pid: [],
    )
    card = _build_external_card(
        {'pid': 4242, 'gpu_index': 0, 'vram_mb': 512.0, 'vram_gb': 0.5, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': r'python.exe -u C:\tools\stt\speak_stt.py',
            'parent_process_name': 'OneVoiceSpeak.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root='',
    )
    assert card is not None
    assert card['card_state'] == 'loading'
    assert card['title'] == 'Loading…'


def test_build_external_card_app_worker_ready_when_named(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        '_probe_loaded_model',
        lambda *_args, **_kwargs: {'loading': True, 'api_url': 'http://127.0.0.1:9999/v1'},
    )
    monkeypatch.setattr(gpu_processes, '_listening_ports_for_pid', lambda _pid: [9999])
    card = _build_external_card(
        {'pid': 9001, 'gpu_index': 0, 'vram_mb': 2048.0, 'vram_gb': 2.0, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': r'python.exe -u C:\dev\OneVoice\tools\speech_hermes_ws.py',
            'parent_process_name': 'OneVoiceSpeak.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root='',
    )
    assert card is not None
    assert card['card_state'] == 'ready'
    assert card['title'] == 'speech_hermes_ws'


def test_build_external_card_llama_timeout_stays_loading(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        '_probe_loaded_model',
        lambda *_args, **_kwargs: {'loading': True, 'api_url': 'http://127.0.0.1:32491/v1'},
    )
    monkeypatch.setattr(gpu_processes, '_listening_ports_for_pid', lambda _pid: [32491])
    card = _build_external_card(
        {'pid': 44108, 'gpu_index': 0, 'vram_mb': 4096.0, 'vram_gb': 4.0, 'process_name': 'llama-server.exe'},
        details={
            'process_name': 'llama-server.exe',
            'command_line': (
                r'C:\apps\llama-server.exe -m C:\models\gemma-4-31B_q4_0-it.gguf '
                r'--host 127.0.0.1 --port 32491'
            ),
            'parent_process_name': 'LM Studio.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root='',
    )
    assert card is not None
    assert card['card_state'] == 'loading'


def test_build_external_card_speak_stt_ready_via_websocket(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        '_probe_onevoice_stt_status',
        lambda *_args, **_kwargs: {'model_loaded': True, 'model': 'small.en', 'loading': False, 'device': 'cuda'},
    )
    monkeypatch.setattr(gpu_processes, '_listening_ports_for_pid', lambda _pid: [2711])
    card = _build_external_card(
        {'pid': 4242, 'gpu_index': 0, 'vram_mb': 512.0, 'vram_gb': 0.5, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': r'python.exe -u C:\tools\stt\speak_stt.py',
            'parent_process_name': 'OneVoiceSpeak.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root='',
    )
    assert card is not None
    assert card['card_state'] == 'ready'
    assert card['title'] == 'small.en'
    assert card['listen_port'] == 2711


def test_external_card_detail_stt():
    detail = _external_card_detail(
        model_kind='stt',
        model_name='small.en',
        model_path='',
        command_line=r'python.exe -u C:\tools\stt\speak_stt.py',
    )
    assert 'Whisper' in detail
    assert 'faster-whisper' in detail
    assert 'small.en' in detail


def test_external_card_detail_embedding():
    detail = _external_card_detail(
        model_kind='embedding',
        model_name='nomic-embed-text-v1.5.Q8_0.gguf',
        model_path=r'C:\dev\OneVoice\models\nomic-embed\nomic-embed-text-v1.5.Q8_0.gguf',
        command_line='--embedding --pooling mean',
        listen_port=8891,
    )
    assert 'Embedding model' in detail
    assert 'nomic-embed-text v1.5' in detail
    assert 'Q8_0' in detail


def test_size_gb_from_directory(tmp_path):
    sample = tmp_path / 'model.bin'
    sample.write_bytes(b'x' * (128 * 1024 * 1024))
    size = _size_gb_from_path(str(tmp_path))
    assert size is not None
    assert size >= 0.12


def test_size_gb_sums_split_shards(tmp_path):
    # First shard is tiny (header); the rest carry the weights. The reported
    # size must be the sum of ALL shards, not the first part alone.
    (tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf').write_bytes(b'x' * 1024)
    (tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00002-of-00003.gguf').write_bytes(b'x' * (2 * 1024 * 1024 * 1024))
    (tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00003-of-00003.gguf').write_bytes(b'x' * (1 * 1024 * 1024 * 1024))
    size = _size_gb_from_path(str(tmp_path / 'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf'))
    assert size is not None
    assert size >= 2.9  # ~3 GB summed, not ~0 GB from the tiny first shard


def test_size_gb_single_gguf_not_treated_as_shard(tmp_path):
    single = tmp_path / 'Qwen3.5-9B-Q4_K_M.gguf'
    single.write_bytes(b'x' * (128 * 1024 * 1024))
    size = _size_gb_from_path(str(single))
    assert size is not None
    assert size >= 0.12


def test_resolve_stt_model_path_from_hub(tmp_path, monkeypatch):
    hub = tmp_path / 'hub'
    snapshot = hub / 'models--Systran--faster-whisper-small.en' / 'snapshots' / 'abc123'
    snapshot.mkdir(parents=True)
    (snapshot / 'model.bin').write_text('demo')
    monkeypatch.setattr('core.gpu_processes._stt_hub_search_roots', lambda: [hub])
    path = _resolve_stt_model_path('small.en')
    assert path.endswith('abc123')
    size = _size_gb_from_path(path)
    assert size is not None


def test_attach_external_inference_stats(monkeypatch):
    monkeypatch.setattr(
        'core.inference_stats.fetch_inference_stats',
        lambda url, server_id='', model_id='', api_key='': {'tokens_loaded': 128, 'generation_tokens': 42, 'tokens_per_second': 12.3},
    )
    card = _attach_external_inference_stats({
        'api_url': 'http://127.0.0.1:8891',
        'model_kind': 'embedding',
        'pid': 1234,
    })
    assert card['inference_stats']['tokens_loaded'] == 128

    # Any llama-server-backed kind with an API URL is polled (LLM/OCR/vision…);
    # cards without an API URL are skipped.
    ocr = _attach_external_inference_stats({
        'api_url': 'http://127.0.0.1:8891',
        'model_kind': 'ocr',
        'pid': 1234,
    })
    assert ocr['inference_stats']['tokens_loaded'] == 128

    skipped = _attach_external_inference_stats({'model_kind': 'speech-to-text', 'api_url': ''})
    assert 'inference_stats' not in skipped


def test_external_card_path_missing(tmp_path):
    existing = tmp_path / 'model.gguf'
    existing.write_bytes(b'GGUF')
    assert _external_card_path_missing({'model_path': str(existing)}) is False
    assert _external_card_path_missing({'model_path': str(tmp_path)}) is False  # directory counts as existing
    assert _external_card_path_missing({'model_path': str(tmp_path / 'gone.gguf')}) is True
    assert _external_card_path_missing({'model_path': ''}) is False
    assert _external_card_path_missing({'path': str(tmp_path / 'gone2.gguf')}) is True
    assert _external_card_path_missing({}) is False


def test_unload_external_requires_current_approved_gpu_process(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        'query_compute_apps',
        lambda: [{
            'pid': 1234,
            'process_name': 'python.exe',
            'command_line': r'python.exe C:\tools\stt\speak_stt.py',
            'model_name': 'small.en',
            'model_path': '',
        }],
    )
    monkeypatch.setattr(
        gpu_processes.subprocess,
        'run',
        lambda *args, **kwargs: type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
    )

    result = gpu_processes.unload_external_gpu_process(1234)

    assert result['success'] is True
    assert result['method'] == 'kill'


def test_unload_external_uses_lmstudio_native_api(monkeypatch):
    responses = {
        '/api/v1/models': {
            'models': [{
                'key': 'gemma-4-12b-it-qat',
                'loaded_instances': [{'id': 'gemma-4-12b-it-qat'}],
            }],
        },
        '/api/v1/models/unload': {'instance_id': 'gemma-4-12b-it-qat'},
    }
    requests = []

    class FakeResp:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            import json as json_mod
            return json_mod.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, **_kwargs):
        requests.append(request)
        path = request.full_url.split('http://127.0.0.1:1234', 1)[-1]
        return FakeResp(responses[path])

    monkeypatch.setattr(gpu_processes, 'query_compute_apps', lambda: [])
    monkeypatch.setattr(gpu_processes.urllib.request, 'urlopen', fake_urlopen)

    result = gpu_processes.unload_external_gpu_process(
        22556,
        api_url='http://127.0.0.1:1234/v1',
        model_id='gemma-4-12b-it-qat',
    )

    assert result['success'] is True
    assert result['method'] == 'lmstudio-api'
    assert len(requests) == 2
    assert requests[1].data == b'{"instance_id": "gemma-4-12b-it-qat"}'


def test_unload_external_uses_main_lmstudio_api_for_worker_card(monkeypatch):
    calls = []

    def fake_native(*, api_url, model_id):
        calls.append((api_url, model_id))
        if ':1234/' in api_url:
            return {'success': True, 'unloaded': True, 'model': 'gemma-4-12b-it-qat'}
        return {'success': False, 'http_status': 401}

    monkeypatch.setattr(gpu_processes, '_unload_lmstudio_model', fake_native)
    monkeypatch.setattr(gpu_processes, 'query_compute_apps', lambda: [])

    result = gpu_processes.unload_external_gpu_process(
        14252,
        api_url='http://127.0.0.1:38380/v1',
        model_id=r'C:\models\gemma-4-12b-it-qat-q4_0.gguf',
    )

    assert result['success'] is True
    assert result['method'] == 'lmstudio-api'
    assert calls == [
        ('http://127.0.0.1:38380/v1', r'C:\models\gemma-4-12b-it-qat-q4_0.gguf'),
        ('http://127.0.0.1:1234/v1', r'C:\models\gemma-4-12b-it-qat-q4_0.gguf'),
    ]


def test_unload_external_kills_worker_after_api_key_failure(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        'query_compute_apps',
        lambda: [{
            'pid': 1234,
            'process_name': 'llama-server.exe',
            'command_line': r'C:\lmstudio\llama-server.exe -m C:\models\gemma.gguf',
            'model_name': 'gemma.gguf',
            'model_path': r'C:\models\gemma.gguf',
        }],
    )
    monkeypatch.setattr(
        gpu_processes,
        '_unload_lmstudio_model',
        lambda **_kwargs: {'success': False, 'http_status': 401},
    )
    monkeypatch.setattr(
        gpu_processes,
        'unload_model',
        lambda **_kwargs: {'success': False, 'http_status': 401, 'error': 'Invalid API Key'},
    )
    monkeypatch.setattr(
        gpu_processes.subprocess,
        'run',
        lambda *args, **kwargs: type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
    )

    result = gpu_processes.unload_external_gpu_process(
        1234,
        api_url='http://127.0.0.1:38380/v1',
        model_id=r'C:\models\gemma.gguf',
    )

    assert result['success'] is True
    assert result['method'] == 'kill'


def test_unload_external_rejects_unknown_pid(monkeypatch):
    monkeypatch.setattr(gpu_processes, 'query_compute_apps', lambda: [])
    gpu_processes._EXTERNAL_SCAN_CACHE['cards'] = []

    result = gpu_processes.unload_external_gpu_process(1234)

    assert result['success'] is False
    assert 'current GPU compute process' in result['error']


def test_unload_external_kills_python_stt_worker(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        'query_compute_apps',
        lambda: [{'pid': 4411, 'process_name': 'python.exe'}],
    )
    monkeypatch.setattr(
        gpu_processes,
        '_query_process_details',
        lambda _pids: {
            4411: {
                'process_name': 'python.exe',
                'command_line': r'python.exe C:\dev\OneVoice\speak_stt.py --model small.en',
            },
        },
    )
    monkeypatch.setattr(
        gpu_processes.subprocess,
        'run',
        lambda *args, **kwargs: type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
    )

    result = gpu_processes.unload_external_gpu_process(4411)

    assert result['success'] is True
    assert result['method'] == 'kill'


def test_unload_external_kills_cached_python_card(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        'query_compute_apps',
        lambda: [{'pid': 5522, 'process_name': 'python.exe'}],
    )
    monkeypatch.setattr(gpu_processes, '_query_process_details', lambda _pids: {})
    monkeypatch.setattr(
        gpu_processes.subprocess,
        'run',
        lambda *args, **kwargs: type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
    )
    gpu_processes._EXTERNAL_SCAN_CACHE['cards'] = [{
        'pid': 5522,
        'process_name': 'python.exe',
        'model_kind': 'stt',
        'model_name': 'small.en',
        'app_label': 'OneVoice',
    }]

    result = gpu_processes.unload_external_gpu_process(5522)

    assert result['success'] is True
    assert result['method'] == 'kill'
    gpu_processes._EXTERNAL_SCAN_CACHE['cards'] = []


def test_unload_external_kills_live_stt_when_card_pid_is_stale(monkeypatch):
    monkeypatch.setattr(
        gpu_processes,
        'query_compute_apps',
        lambda: [{'pid': 34460, 'process_name': 'python.exe'}],
    )
    monkeypatch.setattr(
        gpu_processes,
        '_query_process_details',
        lambda _pids: {
            34460: {
                'process_name': 'python.exe',
                'command_line': r'python.exe C:\dev\Speak-OneVoice\tools\stt\speak_stt.py',
            },
        },
    )
    killed = []

    def fake_kill(pid):
        killed.append(int(pid))
        return {'success': True, 'pid': int(pid), 'method': 'kill'}

    monkeypatch.setattr(gpu_processes, '_kill_external_pid', fake_kill)
    gpu_processes._EXTERNAL_SCAN_CACHE['cards'] = [{
        'pid': 5000,
        'process_name': 'python.exe',
        'model_kind': 'stt',
        'model_name': 'small.en',
        'app_source': 'onevoice',
        'app_label': 'OneVoice',
        'command_line': r'python.exe speak_stt.py',
    }]

    result = gpu_processes.unload_external_gpu_process(5000)

    assert result['success'] is True
    assert killed == [34460]
    gpu_processes._EXTERNAL_SCAN_CACHE['cards'] = []


def test_unload_external_uses_ollama_native_api(monkeypatch):
    calls = []

    def fake_ollama(*, api_url, model_id):
        calls.append((api_url, model_id))
        return {'success': True, 'unloaded': True, 'model': model_id}

    monkeypatch.setattr(gpu_processes, '_unload_ollama_model', fake_ollama)
    monkeypatch.setattr(gpu_processes, 'query_compute_apps', lambda: [])

    result = gpu_processes.unload_external_gpu_process(
        7336,
        api_url='http://127.0.0.1:11434/v1',
        model_id='qwen3.5:9b',
    )

    assert result['success'] is True
    assert result['method'] == 'ollama-api'
    assert calls == [('http://127.0.0.1:11434/v1', 'qwen3.5:9b')]


def test_unload_external_uses_api_before_pid_lookup(monkeypatch):
    # llama-server-style card: the stored PID is NOT a current GPU compute
    # process, but the OpenAI-compatible API unload works. The generic API
    # path must run before the PID lookup or this fails with "process is not a
    # current GPU compute process".
    monkeypatch.setattr(
        gpu_processes,
        '_unload_lmstudio_model',
        lambda **_kwargs: {'success': False, 'http_status': 401},
    )
    monkeypatch.setattr(
        gpu_processes,
        'unload_model',
        lambda **_kwargs: {'success': True, 'unloaded': True, 'model': 'gemma-4-12b-it-qat'},
    )
    monkeypatch.setattr(gpu_processes, 'query_compute_apps', lambda: [])

    result = gpu_processes.unload_external_gpu_process(
        9876,
        api_url='http://127.0.0.1:38380/v1',
        model_id='gemma-4-12b-it-qat',
    )

    assert result['success'] is True
    assert result['method'] == 'api'


def test_classify_ai_tools_scraper():
    source, label = _classify_app(
        process_name='python.exe',
        command_line=r'C:\dev\AI-Tools\env\scraper\python.exe C:\dev\AI-Tools\scraper.py --run-functions',
        parent_name='AI-Tools.exe',
    )
    assert source == 'ai-tools'
    assert label == 'AI Tools'


def test_build_external_card_ai_tools_transcribe_without_vram():
    card = _build_external_card(
        {'pid': 47060, 'gpu_index': 0, 'vram_mb': None, 'vram_gb': None, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': (
                r'C:\dev\AI-Tools\env\scraper\python.exe -c '
                r'"from scraper_modules.transcribe_module.transcribe_module import _run_voice_core"'
            ),
            'parent_process_name': 'AI-Tools.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is not None
    assert card['app_label'] == 'AI Tools'
    assert card['title'] == 'Voice recognition'
    assert card['model_kind'] == 'stt'


def test_resolve_ai_tools_stt_model_path_uses_sibling_models(tmp_path):
    ai_root = tmp_path / 'AI-Tools'
    ai_root.mkdir()
    (ai_root / 'config.json').write_text(
        json.dumps({'whisper_model': {'model_size': 'small'}}),
        encoding='utf-8',
    )
    model_dir = tmp_path / 'Dflash-Console' / 'models' / 'faster-whisper-small.en'
    model_dir.mkdir(parents=True)
    (model_dir / 'model.bin').write_bytes(b'x' * (50 * 1024 * 1024))

    command_line = (
        rf'{ai_root}\env\scraper\python.exe -c '
        r'"from scraper_modules.transcribe_module.transcribe_module import _run_voice_core"'
    )
    path = _resolve_ai_tools_stt_model_path(command_line)
    assert path == str(model_dir)
    size = _size_gb_from_path(path)
    assert size is not None and size > 0


def test_build_external_card_ai_tools_transcribe_includes_disk_size(tmp_path):
    ai_root = tmp_path / 'AI-Tools'
    ai_root.mkdir()
    (ai_root / 'config.json').write_text(
        json.dumps({'whisper_model': {'model_size': 'small'}}),
        encoding='utf-8',
    )
    model_dir = tmp_path / 'Dflash-Console' / 'models' / 'faster-whisper-small.en'
    model_dir.mkdir(parents=True)
    (model_dir / 'model.bin').write_bytes(b'x' * (50 * 1024 * 1024))

    card = _build_external_card(
        {'pid': 47060, 'gpu_index': 0, 'vram_mb': None, 'vram_gb': None, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': (
                rf'{ai_root}\env\scraper\python.exe -c '
                r'"from scraper_modules.transcribe_module.transcribe_module import _run_voice_core"'
            ),
            'parent_process_name': 'AI-Tools.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=str(tmp_path / 'Dflash-Console'),
    )
    assert card is not None
    assert card['size_gb'] is not None and card['size_gb'] > 0
    assert card['model_path']


def test_build_external_card_ai_tools_scraper_without_vram(tmp_path):
    functions_file = tmp_path / 'functions.json'
    functions_file.write_text(
        json.dumps({'processing_functions': {'speaker_name_transcription': True}}),
        encoding='utf-8',
    )
    card = _build_external_card(
        {'pid': 39776, 'gpu_index': 0, 'vram_mb': None, 'vram_gb': None, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': (
                rf'C:\dev\AI-Tools\env\scraper\python.exe C:\dev\AI-Tools\scraper.py '
                rf'--run-functions --functions-file={functions_file}'
            ),
            'parent_process_name': 'AI-Tools.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is not None
    assert card['app_label'] == 'AI Tools'
    assert card['title'] == 'Speaker names'


def test_build_external_card_unknown_cuda_app():
    card = _build_external_card(
        {'pid': 4243, 'gpu_index': 1, 'vram_mb': 1024.0, 'vram_gb': 1.0, 'process_name': 'blender.exe'},
        details={
            'process_name': 'blender.exe',
            'command_line': r'C:\Program Files\Blender\blender.exe -b scene.blend -f 1',
            'parent_process_name': '',
        },
        gpus=[{'index': 1, 'display_name': 'GPU 1', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is None


def test_build_external_card_rejects_uvicorn_api_server():
    card = _build_external_card(
        {'pid': 8787, 'gpu_index': 0, 'vram_mb': None, 'vram_gb': None, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': r'python.exe -m uvicorn ui_backend.app:app --host 127.0.0.1 --port 8787',
            'parent_process_name': 'AI-Tools.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is None


def test_build_external_card_rejects_generic_python_training():
    card = _build_external_card(
        {'pid': 5555, 'gpu_index': 0, 'vram_mb': None, 'vram_gb': None, 'process_name': 'python.exe'},
        details={
            'process_name': 'python.exe',
            'command_line': r'C:\apps\MyLab\python.exe C:\apps\MyLab\train.py --epochs 3',
            'parent_process_name': 'MyTrainer.exe',
        },
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\Dflash-Console',
    )
    assert card is None


def test_attach_external_gpu_activity_keeps_per_process_vram(monkeypatch):
    card = _attach_external_gpu_activity(
        {'gpu_index': 0, 'title': 'worker', 'vram_gb': 1.25},
        gpu_live={0: {'index': 0, 'load_percent': 42, 'vram_used_gb': 9.2}},
    )
    assert card['vram_gb'] == 1.25
    assert 'gpu_busy' not in card
    assert 'gpu_load_percent' not in card


def test_discover_speak_stt_listener_cards(monkeypatch):
    monkeypatch.setattr(gpu_processes, '_pid_listening_on_port', lambda port: 2711 if port == 2711 else None)
    monkeypatch.setattr(
        gpu_processes,
        '_query_process_details',
        lambda pids: {
            2711: {
                'process_name': 'python.exe',
                'command_line': r'python.exe -u C:\dev\Speak-OneVoice\tools\stt\speak_stt.py',
                'parent_process_name': 'OneVoiceSpeak.exe',
            }
        },
    )
    monkeypatch.setattr(
        gpu_processes,
        '_build_external_card',
        lambda *_args, **_kwargs: {
            'id': 'external-gpu-2711',
            'pid': 2711,
            'title': 'small.en',
            'model_kind': 'stt',
            'card_state': 'ready',
        },
    )
    cards = _discover_speak_stt_listener_cards(
        gpus=[{'index': 0, 'display_name': 'GPU 0', 'name': 'RTX'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root='',
        seen_pids=set(),
    )
    assert len(cards) == 1
    assert cards[0]['listen_port'] == 2711


def test_retain_alive_external_cards_keeps_stt_listener(monkeypatch):
    monkeypatch.setattr(gpu_processes, '_query_process_details', lambda _pids: {})
    monkeypatch.setattr(gpu_processes, '_pid_listening_on_port', lambda port: 5555 if port == 2711 else None)
    kept = _retain_alive_external_cards([
        {
            'pid': 4444,
            'model_kind': 'stt',
            'title': 'small.en',
            'listen_port': 2711,
        },
    ])
    assert len(kept) == 1
    assert kept[0]['pid'] == 5555


def test_fetch_process_details_tolerates_access_denied_cmdline(monkeypatch):
  import psutil

  class _DeniedProc:
      def ppid(self):
          return 0

      def cmdline(self):
          raise psutil.AccessDenied(self)

      def exe(self):
          return ''

      def name(self):
          return 'blocked.exe'

  class _OkProc:
      def ppid(self):
          return 0

      def cmdline(self):
          return ['python.exe', 'worker.py']

      def exe(self):
          return r'C:\Python\python.exe'

      def name(self):
          return 'python.exe'

  def _fake_process(pid):
      if int(pid) == 2220:
          return _DeniedProc()
      return _OkProc()

  monkeypatch.setattr('psutil.Process', _fake_process)
  details = _fetch_process_details([2220, 3333])
  assert details[2220]['process_name'] == 'blocked.exe'
  assert details[2220]['command_line'] == ''
  assert details[3333]['process_name'] == 'python.exe'
  assert 'worker.py' in details[3333]['command_line']
