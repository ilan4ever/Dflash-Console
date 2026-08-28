from core.load_progress import (
    boot_failure_message,
    clear_vram_progress_baseline,
    estimate_vram_load_progress,
    is_active_boot,
    is_active_model_load,
    merge_load_progress,
    model_load_failure_message,
    parse_load_progress,
    stop_log_line,
)


def test_active_boot_requires_stop_after_boot():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x ===',
        'load 50%',
    ]
    assert is_active_boot(lines) is True
    assert parse_load_progress(lines) == 50.0

    lines.append('=== stop 2026-01-01 12:05:00 ===')
    assert is_active_boot(lines) is False
    assert parse_load_progress(lines) is None


def test_new_boot_after_stop():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x ===',
        '=== stop 2026-01-01 12:05:00 ===',
        '=== boot 2026-01-01 12:06:00 profile=x ===',
        'tensor offload 12.5%',
    ]
    assert is_active_boot(lines) is True
    assert parse_load_progress(lines) == 12.5


def test_boot_failure_clears_active_boot():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x ===',
        "couldn't bind HTTP server socket, hostname: 127.0.0.1, port: 8092",
        'exiting due to HTTP server error',
    ]
    assert boot_failure_message(lines) == 'Port already in use — free the port or stop the other engine'
    assert is_active_boot(lines) is False


def test_boot_failed_marker():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x ===',
        '=== boot failed 2026-01-01 12:00:05 reason=timed out waiting for port 8092 ===',
    ]
    assert boot_failure_message(lines) == 'timed out waiting for port 8092'
    assert is_active_boot(lines) is False


def test_on_demand_router_load_progress_survives_idle_marker():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x router=1 ===',
        '=== router idle ready 2026-01-01 12:00:01 ===',
        'load: spawning server instance with name=model',
        'load_model: loading model model',
        'cmd_child_to_router:state:{"state":"loading","payload":{"stages":["text_model","spec_model"],"current":"text_model","value":0.5}}',
    ]
    assert is_active_model_load(lines) is True
    assert parse_load_progress(lines) == 25.0

    lines.append(
        'cmd_child_to_router:state:{"state":"ready","payload":{"id":"model"}}',
    )
    assert is_active_model_load(lines) is False
    assert parse_load_progress(lines) is None


def test_model_load_failure_reports_cuda_oom():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x router=1 ===',
        'load: spawning server instance with name=large-model',
        'load_model: loading model large-model',
        'common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 99, abort',
        'ggml_backend_cuda_buffer_type_alloc_buffer: allocating 69415.95 MiB on device 0: cudaMalloc failed: out of memory',
        'llama_model_load: error loading model: unable to allocate CUDA0 buffer',
        'llama-server: exiting due to model loading error',
    ]

    message = model_load_failure_message(lines)

    assert message is not None
    assert 'not enough GPU memory' in message
    assert '67.8 GiB' in message
    assert 'GPU 0' in message


def test_model_load_failure_reports_incompatible_draft():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x router=1 ===',
        'load: spawning server instance with name=qwen3.8-27b-q6-k-l',
        'load_model: loading model qwen3.8-27b-q6-k-l',
        'llama_model_load: error loading model: done_getting_tensors: wrong number of tensors; expected 81, got 58',
        "srv    load_model: failed to load draft model, 'C:\\models\\Qwen3.8-27B-DFlash2-Q4_K_M.gguf'",
        'llama-server: exiting due to model loading error',
    ]
    message = model_load_failure_message(lines)
    assert message is not None
    assert 'draft' in message.lower()
    assert 'compatible' in message.lower()


def test_stop_log_line_format():
    line = stop_log_line()
    assert line.startswith('=== stop ')


def test_merge_load_progress_prefers_highest_value():
    assert merge_load_progress(None, 12.5, 40.0) == 40.0
    assert merge_load_progress(55.0, 40.0) == 55.0


def test_vram_progress_tracks_vram_growth():
    clear_vram_progress_baseline('demo')
    assert estimate_vram_load_progress('demo', 10.0, 20.0, active=True) is None
    assert estimate_vram_load_progress('demo', 15.0, 20.0, active=True) == 25.0
    assert estimate_vram_load_progress('demo', 29.0, 20.0, active=True) == 95.0
    clear_vram_progress_baseline('demo')


def test_parse_load_progress_uses_latest_loading_state():
    lines = [
        'load: spawning server instance with name=model',
        'cmd_child_to_router:state:{"state":"loading","payload":{"stages":["text_model","spec_model"],"current":"text_model","value":0.2}}',
        'tensor offload noise',
        'cmd_child_to_router:state:{"state":"loading","payload":{"stages":["text_model","spec_model"],"current":"text_model","value":0.5}}',
    ]
    assert parse_load_progress(lines) == 25.0
