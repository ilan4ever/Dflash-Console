from core.runtime import _build_visible_cards, _annotate_model_stack


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
        booting=False,
        loaded_models=['gemma-4-12b-it-qat'],
        progress=None,
    )
    assert len(cards) == 1
    assert cards[0]['title'] == 'Gemma 4 12B AR'
    assert cards[0]['ejectable'] is True
    assert len(cards[0]['stack_details']) == 1


def test_api_base_url_strips_v1():
    from core.runtime import api_base_url

    assert api_base_url('http://127.0.0.1:8092/v1') == 'http://127.0.0.1:8092'
