from core.gpu_process_memory_windows import apply_windows_process_vram, lookup_windows_process_vram_gb


def test_lookup_windows_process_vram_gb_stays_on_its_own_gpu():
    mapping = {(9001, 1): 2_147_483_648}  # 2 GiB on GPU 1
    assert lookup_windows_process_vram_gb(9001, 0, mapping) is None
    assert lookup_windows_process_vram_gb(9001, 1, mapping) == 2.0
    assert lookup_windows_process_vram_gb(9002, 0, mapping) is None


def test_apply_windows_process_vram_does_not_borrow_another_gpu(monkeypatch):
    monkeypatch.setattr(
        'core.gpu_process_memory_windows.query_windows_process_gpu_bytes',
        lambda: {(11532, 1): 1_073_741_824},
    )
    processes = [{'pid': 11532, 'gpu_index': 0, 'label': 'speech'}]
    assert apply_windows_process_vram(processes) is False
    assert processes[0].get('vram_gb') is None


def test_match_luids_to_gpus_separates_boards_that_share_phys_0():
    from core.gpu_process_memory_windows import match_luids_to_gpus

    gib = 1024 ** 3
    assigned = match_luids_to_gpus(
        {'luid_4090': 36 * gib, 'luid_titan': 20 * gib},
        [
            {'index': 0, 'vram_used_gb': 29},
            {'index': 1, 'vram_used_gb': 20},
        ],
    )
    assert assigned['luid_4090'] == 0
    assert assigned['luid_titan'] == 1


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
