from pathlib import Path

from core.model_presets import infer_profile_from_path, model_id_from_path, write_server_preset


def test_infer_profile_from_path():
    assert infer_profile_from_path('gemma-4-12b-q4.gguf') == 'gemma-ar'
    assert infer_profile_from_path('Qwen3.5-27B-Q4_K_M.gguf') == 'qwen-ar'
    assert infer_profile_from_path('some-random-model.gguf') == 'generic-ar'


def test_model_id_from_path():
    assert model_id_from_path('My_Model_v2.gguf') == 'my-model-v2'


def test_write_server_preset_with_target_path(tmp_path, monkeypatch):
    gguf = tmp_path / 'custom-checkpoint.gguf'
    gguf.write_bytes(b'fake')

    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)

    server = {
        'id': 'test-engine',
        'model_id': 'profile-id',
        'profile': 'gemma-chat',
        'context_size': 8192,
        'load_settings': {'gpu_layers': 50},
        'gpu_device': 'auto',
    }
    path = write_server_preset(
        server,
        target_path=str(gguf),
        model_id='custom-checkpoint',
        profile='generic-ar',
        use_draft=False,
    )
    text = path.read_text(encoding='utf-8')
    assert '[custom-checkpoint]' in text
    assert str(gguf) in text
    assert 'model-draft' not in text
