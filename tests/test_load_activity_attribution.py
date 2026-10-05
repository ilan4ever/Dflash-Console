from core.load_activity import active_model_loads, begin_model_load, end_model_load


def test_active_load_preserves_component_identity():
    server_id = 'test-component-load'
    end_model_load(server_id)
    begin_model_load(
        server_id,
        label='TeleOCR',
        model_id='teleocr',
        model_path=r'C:\models\TeleOCR',
        runtime_id='transformers',
        component_key='onevoice.ocr',
        component_label='OneVoice OCR',
        component_role='image_ocr',
    )
    try:
        row = next(item for item in active_model_loads() if item['server_id'] == server_id)
        assert row['component_key'] == 'onevoice.ocr'
        assert row['component_label'] == 'OneVoice OCR'
        assert row['component_role'] == 'image_ocr'
    finally:
        end_model_load(server_id)
