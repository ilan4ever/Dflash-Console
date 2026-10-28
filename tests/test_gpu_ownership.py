from unittest.mock import patch

import pytest

from core.gpu_ownership import GpuOwnershipConflict, enforce_gpu_ownership


def _cfg(policy='reject'):
    return {
        'hardware_settings': {
            'gpu_ownership_policy': policy,
            'gpu_strategy': 'single_largest',
        },
        'servers': [],
    }


def test_reject_policy_returns_structured_conflict():
    state = {
        'requested_gpu_indices': [0],
        'conflicts': [{
            'gpu_index': 0,
            'owner_type': 'strata',
            'owner_label': 'Strata · DeepSeek',
            'owner_id': 'strata',
            'vram_gb': 40.0,
        }],
    }
    with patch('core.gpu_ownership.inspect_gpu_ownership', return_value=state):
        with pytest.raises(GpuOwnershipConflict) as exc:
            enforce_gpu_ownership({'gpu_device': '0'}, cfg=_cfg())
    assert exc.value.detail['error'] == 'gpu_ownership_conflict'
    assert exc.value.detail['policy'] == 'reject'
    assert exc.value.detail['conflicts'][0]['owner_type'] == 'strata'


def test_auto_switch_can_release_console_but_not_strata():
    state = {
        'requested_gpu_indices': [0],
        'conflicts': [{
            'gpu_index': 0,
            'owner_type': 'console',
            'owner_label': 'Gemma',
            'owner_id': 'llama-main',
        }],
    }
    released = []

    def inspect(*args, **kwargs):
        return state if not released else {'requested_gpu_indices': [0], 'conflicts': []}

    with patch('core.gpu_ownership.inspect_gpu_ownership', side_effect=inspect):
        result = enforce_gpu_ownership(
            {'gpu_device': '0'},
            cfg=_cfg('auto_switch'),
            unload_console_engine=lambda server_id: released.append(server_id) or True,
        )
    assert result['released_console_engines'] == ['llama-main']

    protected = {
        **state,
        'conflicts': [{**state['conflicts'][0], 'owner_type': 'strata', 'owner_id': 'strata'}],
    }
    with patch('core.gpu_ownership.inspect_gpu_ownership', return_value=protected):
        with pytest.raises(GpuOwnershipConflict):
            enforce_gpu_ownership(
                {'gpu_device': '0'},
                cfg=_cfg('auto_switch'),
                unload_console_engine=lambda _server_id: True,
            )
