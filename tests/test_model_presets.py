from pathlib import Path

from core.config import SPECULATIVE_PROFILES
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


def test_write_server_preset_skips_mmproj_for_speculative_profiles(tmp_path, monkeypatch):
    target = tmp_path / 'target.gguf'
    draft = tmp_path / 'draft-dflash.gguf'
    mmproj = tmp_path / 'mmproj-gemma-4-12b-it-qat-q4_0.gguf'
    target.write_bytes(b'target')
    draft.write_bytes(b'draft')
    mmproj.write_bytes(b'mmproj')

    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    monkeypatch.setattr(
        'core.vision_setup.resolve_mmproj_path',
        lambda server, cfg=None: str(mmproj),
    )

    server = {
        'id': 'gemma-12b-ar',
        'model_id': 'gemma-4-12b-it-qat',
        'profile': 'gemma-12-dflash',
        'context_size': 8192,
        'load_settings': {'gpu_layers': 88},
        'gpu_device': 'auto',
        'target_path': str(target),
        'draft_path': str(draft),
    }
    path = write_server_preset(server, profile='gemma-12-dflash')
    text = path.read_text(encoding='utf-8')
    assert 'model-draft' in text
    assert 'spec-type = draft-dflash' in text
    assert 'mmproj' not in text
    assert 'gemma-12-dflash' in SPECULATIVE_PROFILES
