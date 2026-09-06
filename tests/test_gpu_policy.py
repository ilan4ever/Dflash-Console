from core.gpu_policy import (
    gpu_policy_for_config,
    normalize_gpu_performance_mode,
    should_stop_others_on_load,
)


def test_normalize_gpu_performance_mode_defaults_to_balanced():
    assert normalize_gpu_performance_mode(None) == 'balanced'
    assert normalize_gpu_performance_mode('invalid') == 'balanced'
    assert normalize_gpu_performance_mode('power') == 'power'


def test_gpu_policy_for_config_mode_defaults():
    balanced = gpu_policy_for_config({'hardware_settings': {'gpu_performance_mode': 'balanced'}})
    assert balanced['desktop_vram_reserve_gb'] == 6.0

    performance = gpu_policy_for_config({'hardware_settings': {'gpu_performance_mode': 'performance'}})
    assert performance['desktop_vram_reserve_gb'] == 8.0
    assert performance['stop_others_on_load'] is True


def test_should_stop_others_on_load_respects_explicit_config():
    cfg = {'runtime_stop_others_on_load': False, 'hardware_settings': {'gpu_performance_mode': 'performance'}}
    assert should_stop_others_on_load(cfg) is False

    cfg['runtime_stop_others_on_load'] = True
    assert should_stop_others_on_load(cfg) is True

    cfg.pop('runtime_stop_others_on_load')
    assert should_stop_others_on_load(cfg) is True
