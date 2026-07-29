from __future__ import annotations

import core.system_stats as system_stats


def test_normalize_cpu_percent_clamps():
    assert system_stats._normalize_cpu_percent(-5) == 0
    assert system_stats._normalize_cpu_percent(150.4) == 100
    assert system_stats._normalize_cpu_percent(42.6) == 43


def test_cpu_from_process_delta_uses_core_count(monkeypatch):
    monkeypatch.setattr(system_stats, '_cpu_process_load_last', {
        'total_cpu_seconds': 100.0,
        'sample_time_ms': 1000.0,
    })
    monkeypatch.setattr(system_stats, '_cpu_load_smoothed', None)
    monkeypatch.setattr(system_stats.os, 'cpu_count', lambda: 4)
    monkeypatch.setattr(system_stats.time, 'time', lambda: 2.0)

    # 4 CPU-seconds over 1 wall second on 4 cores => 100%
    value = system_stats._cpu_from_process_delta(104.0)
    assert value == 100


def test_cpu_from_process_delta_returns_none_without_prior_sample():
    system_stats._cpu_process_load_last = None
    system_stats._cpu_load_smoothed = None
    assert system_stats._cpu_from_process_delta(12.5) is None
