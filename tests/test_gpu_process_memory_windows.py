from core.gpu_process_memory_windows import apply_windows_process_vram, lookup_windows_process_vram_gb


def test_lookup_windows_process_vram_gb_falls_back_to_pid_on_other_gpu():
    mapping = {(9001, 1): 2_147_483_648}  # 2 GiB on GPU 1
    assert lookup_windows_process_vram_gb(9001, 0, mapping) == 2.0
    assert lookup_windows_process_vram_gb(9001, 1, mapping) == 2.0
    assert lookup_windows_process_vram_gb(9002, 0, mapping) is None


def test_apply_windows_process_vram_falls_back_when_gpu_index_mismatches(monkeypatch):
    monkeypatch.setattr(
        'core.gpu_process_memory_windows.query_windows_process_gpu_bytes',
        lambda: {(11532, 1): 1_073_741_824},
    )
    processes = [{'pid': 11532, 'gpu_index': 0, 'label': 'speech'}]
    assert apply_windows_process_vram(processes) is True
    assert processes[0]['vram_gb'] == 1.0


def test_apply_windows_process_vram_enriches_missing_rows(monkeypatch):
    monkeypatch.setattr(
        'core.gpu_process_memory_windows.query_windows_process_gpu_bytes',
        lambda: {(11532, 0): 106_409_984, (27304, 0): 1_364_811_776},
    )
    processes = [
        {'pid': 11532, 'gpu_index': 0, 'label': 'explorer'},
        {'pid': 27304, 'gpu_index': 0, 'label': 'Cursor'},
        {'pid': 999, 'gpu_index': 0, 'label': 'missing'},
    ]
    assert apply_windows_process_vram(processes) is True
    assert processes[0]['vram_gb'] == 0.099
    assert processes[1]['vram_gb'] == 1.271
    assert processes[2].get('vram_gb') is None


def test_apply_windows_process_vram_includes_sub_10mb(monkeypatch):
    monkeypatch.setattr(
        'core.gpu_process_memory_windows.query_windows_process_gpu_bytes',
        lambda: {(42, 0): 5_242_880},  # 5 MiB
    )
    processes = [{'pid': 42, 'gpu_index': 0, 'label': 'tiny'}]
    assert apply_windows_process_vram(processes) is True
    assert processes[0]['vram_gb'] == 0.005


def test_apply_windows_process_vram_keeps_nvidia_values(monkeypatch):
    monkeypatch.setattr(
        'core.gpu_process_memory_windows.query_windows_process_gpu_bytes',
        lambda: {(100, 0): 999_999_999},
    )
    processes = [{'pid': 100, 'gpu_index': 0, 'vram_gb': 4.0, 'label': 'llama'}]
    assert apply_windows_process_vram(processes) is False
    assert processes[0]['vram_gb'] == 4.0
