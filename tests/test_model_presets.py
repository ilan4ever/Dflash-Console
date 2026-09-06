from pathlib import Path

import pytest

from core.config import SPECULATIVE_PROFILES
from core.model_presets import (
    infer_profile_from_path,
    model_id_from_path,
    profile_uses_jinja,
    sanitize_preset_model_id,
    write_server_preset,
)


def test_infer_profile_from_path():
    assert infer_profile_from_path('gemma-4-12b-q4.gguf') == 'gemma-ar'
    assert infer_profile_from_path('translategemma-12b-it.Q4_K_S.gguf') == 'translategemma'
    assert infer_profile_from_path('Qwen3.5-27B-Q4_K_M.gguf') == 'qwen-ar'
    assert infer_profile_from_path('some-random-model.gguf') == 'generic-ar'


def test_model_id_from_path():
    assert model_id_from_path('My_Model_v2.gguf') == 'my-model-v2'


def test_sanitize_preset_model_id_strips_library_file_alias():
    assert sanitize_preset_model_id('library-file:gemma-4-31b-q4-0-it') == 'gemma-4-31b-q4-0-it'
    assert sanitize_preset_model_id(
        'library-file:IT',
        r'C:\models\gemma-4-31B_q4_0-it.gguf',
    ) == 'gemma-4-31b-q4-0-it'


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


def test_write_server_preset_resolves_draft_when_only_target_path_set(tmp_path, monkeypatch):
    target = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    draft = tmp_path / 'gemma-4-31B-it-DFlash-Q4_K_M.gguf'
    target.write_bytes(b'target')
    draft.write_bytes(b'draft')

    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    monkeypatch.setattr(
        'core.model_presets.resolve_model_stack',
        lambda server, cfg=None: [
            {'role': 'target', 'label': target.name, 'path': str(target)},
            {'role': 'draft-dflash', 'label': draft.name, 'path': str(draft)},
        ],
    )

    path = write_server_preset(
        {
            'id': 'gemma-31b-dflash',
            'model_id': 'gemma-4-31b-it-dflash',
            'profile': 'gemma-chat',
            'context_size': 8192,
            'load_settings': {'gpu_layers': 99},
            'gpu_device': 'auto',
            'target_path': str(target),
        },
        profile='gemma-chat',
    )
    text = path.read_text(encoding='utf-8')
    assert f'model = {target}' in text
    assert f'model-draft = {draft}' in text


def test_write_server_preset_includes_split_layout(tmp_path, monkeypatch):
    gguf = tmp_path / 'custom-checkpoint.gguf'
    gguf.write_bytes(b'fake')
    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)
    monkeypatch.setattr(
        'core.model_presets.resolve_role_gpu_launch_params',
        lambda *args, **kwargs: {
            'main_gpu': 0,
            'split_mode': 'layer',
            'tensor_split': '0.5000,0.5000',
        },
    )

    path = write_server_preset(
        {
            'id': 'test-engine',
            'model_id': 'profile-id',
            'profile': 'generic-ar',
            'context_size': 8192,
            'load_settings': {},
            'gpu_device': 'auto',
        },
        cfg={'hardware_settings': {'gpu_strategy': 'split_evenly'}},
        target_path=str(gguf),
        model_id='custom-checkpoint',
        profile='generic-ar',
        use_draft=False,
    )
    text = path.read_text(encoding='utf-8')

    assert 'split-mode = layer' in text
    assert 'tensor-split = 0.5000,0.5000' in text


def test_write_server_preset_sanitizes_library_file_model_id(tmp_path, monkeypatch):
    gguf = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    gguf.write_bytes(b'fake')
    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)

    path = write_server_preset(
        {
            'id': 'test-engine',
            'model_id': 'profile-id',
            'profile': 'generic-ar',
            'context_size': 8192,
            'load_settings': {'gpu_layers': 50},
            'gpu_device': 'auto',
        },
        target_path=str(gguf),
        model_id='library-file:gemma-4-31b-q4-0-it',
        profile='generic-ar',
        use_draft=False,
    )
    text = path.read_text(encoding='utf-8')
    assert '[gemma-4-31b-q4-0-it]' in text
    assert 'library-file:' not in text


def test_write_server_preset_includes_mmproj_for_speculative_profiles(tmp_path, monkeypatch):
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
    assert f'mmproj = {mmproj}' in text
    assert 'gemma-12-dflash' in SPECULATIVE_PROFILES


def test_write_server_preset_includes_mmproj_for_gemma_chat_31b(tmp_path, monkeypatch):
    target = tmp_path / 'gemma-4-31B_q4_0-it.gguf'
    draft = tmp_path / 'gemma-4-31B-it-DFlash-Q4_K_M.gguf'
    mmproj = tmp_path / 'mmproj-BF16.gguf'
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
        'id': 'gemma-4-31b-q4-0-it-dflash',
        'model_id': 'gemma-4-31b-q4-0-it',
        'profile': 'gemma-chat',
        'context_size': 8192,
        'load_settings': {'gpu_layers': 99},
        'gpu_device': 'auto',
        'target_path': str(target),
        'draft_path': str(draft),
        'mmproj_path': str(mmproj),
    }
    path = write_server_preset(server, profile='gemma-chat')
    text = path.read_text(encoding='utf-8')
    assert 'model-draft' in text
    assert 'spec-type = draft-dflash' in text
    assert f'mmproj = {mmproj}' in text
    assert 'gemma-chat' in SPECULATIVE_PROFILES


def test_write_server_preset_cannot_disable_required_draft(tmp_path, monkeypatch):
    target = tmp_path / 'target.gguf'
    draft = tmp_path / 'target-DFlash2.gguf'
    target.write_bytes(b'target')
    draft.write_bytes(b'draft')
    monkeypatch.setattr('core.model_presets.PRESET_DIR', tmp_path / 'presets')

    with pytest.raises(ValueError, match='cannot disable'):
        write_server_preset(
            {
                'id': 'qwen-stack',
                'model_id': 'qwen-target',
                'profile': 'qwen-dflash',
                'target_path': str(target),
                'draft_path': str(draft),
            },
            use_draft=False,
        )


def test_write_server_preset_translategemma_disables_jinja_and_draft(tmp_path, monkeypatch):
    gguf = tmp_path / 'translategemma-12b-it.Q4_K_S.gguf'
    mmproj = tmp_path / 'mmproj-gemma-4-12B-it-BF16.gguf'
    draft = tmp_path / 'gemma-4-12B-it-DFlash-F16.gguf'
    gguf.write_bytes(b'target')
    mmproj.write_bytes(b'mmproj')
    draft.write_bytes(b'draft')

    preset_dir = tmp_path / 'presets'
    monkeypatch.setattr('core.model_presets.PRESET_DIR', preset_dir)

    path = write_server_preset(
        {
            'id': 'gemma-12-dflash',
            'model_id': 'translategemma-12b-it-q4-k-s',
            'profile': 'translategemma',
            'context_size': 8192,
            'load_settings': {'gpu_layers': 99},
            'gpu_device': 'auto',
            'target_path': str(gguf),
            'draft_path': str(draft),
            'mmproj_path': str(mmproj),
            'vision': False,
        },
        target_path=str(gguf),
        model_id='translategemma-12b-it-q4-k-s',
        profile='translategemma',
        use_draft=False,
    )
    text = path.read_text(encoding='utf-8')
    assert 'jinja = false' in text
    assert text.count('jinja = false') >= 2
    assert 'model-draft' not in text
    assert 'mmproj' not in text
    assert profile_uses_jinja('translategemma') is False
