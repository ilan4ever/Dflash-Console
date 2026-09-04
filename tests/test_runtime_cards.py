from core.gpu_processes import _build_external_card
from core.runtime import _acceleration_metadata, _build_visible_cards, _annotate_model_stack


def test_visible_card_is_single_composite_when_loaded():
    stack = _annotate_model_stack(
        [
            {'role': 'alias', 'id': 'gemma-4-12b-it-qat', 'label': 'API alias', 'source': 'api'},
            {'role': 'target', 'id': 'gemma-4-12b-it-qat-q4-0', 'label': 'Target', 'path': r'C:\x\gemma.gguf', 'size_gb': 6.5, 'source': 'lmstudio'},
        ],
        booting=False,
        loaded_models=['gemma-4-12b-it-qat'],
        progress=None,
    )
    cards = _build_visible_cards(
        stack,
        server_label='Gemma 4 12B AR',
        display_name='Gemma 4 12B it qat dflash Q4',
        booting=False,
        loaded_models=['gemma-4-12b-it-qat'],
        progress=None,
    )
    assert len(cards) == 1
    assert cards[0]['title'] == 'Gemma 4 12B it qat dflash Q4'
    assert cards[0]['ejectable'] is True
    assert cards[0]['size_gb'] == 6.5
    assert cards[0]['dflash_stack'] is False


def test_visible_card_marks_adhoc_gguf_as_plain_llm():
    stack = _annotate_model_stack(
        [
            {'role': 'alias', 'id': 'gemma-4-12b-it-qat', 'label': 'API alias', 'source': 'api'},
            {'role': 'target', 'id': 'gemma-4-12b-it-qat-q4-0', 'label': 'Target', 'path': r'C:\x\gemma.gguf', 'size_gb': 6.5, 'source': 'lmstudio'},
            {'role': 'draft-dflash', 'id': 'draft', 'label': 'Draft', 'path': r'C:\x\draft.gguf', 'source': 'dflash'},
        ],
        booting=False,
        loaded_models=['deepseek-v2-lite-q4-k-m'],
        progress=None,
    )
    cards = _build_visible_cards(
        stack,
        server_label='Gemma 12B',
        display_name='Gemma 4 12B it qat dflash Q4',
        booting=False,
        loaded_models=['deepseek-v2-lite-q4-k-m'],
        progress=None,
    )
    assert len(cards) == 1
    assert cards[0]['is_adhoc'] is True
    assert cards[0]['plain_llm'] is True
    assert cards[0]['dflash_stack'] is False
    assert cards[0]['title'] == 'deepseek v2 lite q4 k m'


def test_visible_card_uses_filename_when_api_returns_default_alias():
    stack = _annotate_model_stack(
        [
            {'role': 'alias', 'id': 'default', 'label': 'API alias', 'source': 'api'},
            {
                'role': 'target',
                'id': 'nomic-embed-text-v1.5.Q8_0.gguf',
                'label': 'Nomic Embed v1.5',
                'path': r'C:\models\nomic-embed-text-v1.5.Q8_0.gguf',
                'size_gb': 0.14,
                'source': 'onevoice',
            },
        ],
        booting=False,
        loaded_models=['default'],
        progress=None,
    )
    cards = _build_visible_cards(
        stack,
        server_label='Nomic Embed',
        display_name='nomic-embed-text-v1.5.Q8_0.gguf',
        booting=False,
        loaded_models=['nomic-embed-text-v1.5.Q8_0.gguf'],
        progress=None,
    )
    assert len(cards) == 1
    assert cards[0]['title'] == 'nomic-embed-text-v1.5.Q8_0.gguf'
    assert cards[0].get('is_adhoc') is not True


def test_visible_cards_keep_all_loaded_models_when_router_reports_multiple():
    stack = _annotate_model_stack(
        [
            {'role': 'alias', 'id': 'main', 'label': 'API alias', 'source': 'api'},
            {'role': 'target', 'id': 'main-target', 'label': 'Target', 'source': 'dflash'},
        ],
        booting=False,
        loaded_models=['main', 'vision-model'],
        progress=None,
    )
    cards = _build_visible_cards(
        stack,
        server_label='Main engine',
        display_name='Main engine',
        booting=False,
        loaded_models=['main', 'vision-model'],
        progress=None,
    )
    assert [card['id'] for card in cards] == ['main', 'vision-model']
    assert cards[1]['role'] == 'loaded-model'
    assert cards[1]['is_adhoc'] is True


def test_acceleration_metadata_requires_repair_for_dflash_profile_without_draft():
    stack = [
        {'role': 'target', 'path': r'C:\models\qwen.gguf', 'path_missing': False},
    ]

    result = _acceleration_metadata(
        {'profile': 'qwen3-8-27b-q6-k-l-dflash'},
        stack,
    )

    assert result == {
        'acceleration_mode': 'autoregressive',
        'acceleration_expected': True,
        'acceleration_label': 'Draft required · repair',
        'draft_loaded': False,
        'draft_status': 'repair_required',
    }


def test_acceleration_metadata_marks_present_dflash_draft():
    stack = [
        {'role': 'target', 'path': r'C:\models\qwen.gguf', 'path_missing': False},
        {'role': 'draft-dflash', 'path': r'C:\models\draft.gguf', 'path_missing': False},
    ]

    result = _acceleration_metadata(
        {'profile': 'qwen-dflash'},
        stack,
    )

    assert result['acceleration_mode'] == 'dflash'
    assert result['acceleration_label'] == 'DFlash active'
    assert result['draft_loaded'] is True


def test_acceleration_metadata_honors_draft_free_preset(monkeypatch, tmp_path):
    preset = tmp_path / 'qwen-dflash.ini'
    preset.write_text(
        '[qwen]\nmodel = C:\\models\\qwen.gguf\n',
        encoding='utf-8',
    )
    monkeypatch.setattr('core.runtime.preset_path_for', lambda server_id: preset)

    result = _acceleration_metadata(
        {'id': 'qwen-dflash', 'profile': 'qwen-dflash'},
        [
            {'role': 'target', 'path': r'C:\models\qwen.gguf', 'path_missing': False},
            {'role': 'draft-dflash', 'path': r'C:\models\draft.gguf', 'path_missing': False},
        ],
    )

    assert result['acceleration_mode'] == 'autoregressive'
    assert result['acceleration_label'] == 'Draft required · repair'
    assert result['draft_loaded'] is False
    assert result['draft_status'] == 'repair_required'


def test_external_llama_card_reports_observable_draft(monkeypatch):
    monkeypatch.setattr('core.gpu_processes._listening_ports_for_pid', lambda pid: [])
    card = _build_external_card(
        {'pid': 1234, 'gpu_index': 0, 'vram_mb': 8192, 'vram_gb': 8.0},
        details={
            'process_name': 'llama-server.exe',
            'command_line': (
                r'llama-server.exe --model "C:\models\target.gguf" '
                r'--model-draft "C:\models\target-DFlash2.gguf" --spec-type draft-dflash'
            ),
        },
        gpus=[{'index': 0, 'name': 'NVIDIA Test GPU'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\other-checkout',
    )

    assert card is not None
    assert card['draft_loaded'] is True
    assert card['draft_status'] == 'active'
    assert card['draft_path'] == r'C:\models\target-DFlash2.gguf'


def test_external_llama_card_reports_unknown_draft_state(monkeypatch):
    monkeypatch.setattr('core.gpu_processes._listening_ports_for_pid', lambda pid: [])
    card = _build_external_card(
        {'pid': 1234, 'gpu_index': 0, 'vram_mb': 8192, 'vram_gb': 8.0},
        details={
            'process_name': 'llama-server.exe',
            'command_line': r'llama-server.exe --model "C:\models\target.gguf"',
        },
        gpus=[{'index': 0, 'name': 'NVIDIA Test GPU'}],
        managed_pids=set(),
        configured_ports=set(),
        dflash_root=r'C:\dev\other-checkout',
    )

    assert card is not None
    assert card['draft_loaded'] is None
    assert card['draft_status'] == 'unknown'


def test_api_base_url_strips_v1():
    from core.runtime import api_base_url

    assert api_base_url('http://127.0.0.1:8092/v1') == 'http://127.0.0.1:8092'
