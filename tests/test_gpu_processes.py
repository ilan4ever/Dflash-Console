from core.gpu_processes import (
    _attach_external_inference_stats,
    _classify_app,
    _external_card_detail,
    _is_gpu_model_load,
    _model_hint_from_cmdline,
    _probe_lmstudio_loaded_models,
    _resolve_stt_model_path,
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
        lambda url, server_id='', model_id='': {'tokens_loaded': 128, 'generation_tokens': 42, 'tokens_per_second': 12.3},
    )
    card = _attach_external_inference_stats({
        'api_url': 'http://127.0.0.1:8891',
        'model_kind': 'embedding',
        'pid': 1234,
    })
    assert card['inference_stats']['tokens_loaded'] == 128

    skipped = _attach_external_inference_stats({'model_kind': 'speech-to-text', 'api_url': 'http://127.0.0.1:1'})
    assert 'inference_stats' not in skipped


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

    result = gpu_processes.unload_external_gpu_process(1234)

    assert result['success'] is False
    assert 'current GPU compute process' in result['error']
