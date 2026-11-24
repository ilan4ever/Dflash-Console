"""A test run can never overwrite the real config.json, even between tests."""

from __future__ import annotations

import json
from contextlib import contextmanager

import core.config as config_module


def test_real_config_is_refused_between_tests(monkeypatch):
    # Between tests PYTEST_CURRENT_TEST is unset. A background thread started
    # by an earlier test used to save its fake config over the real file then.
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    monkeypatch.setattr(config_module, 'CONFIG_PATH', config_module.ROOT / 'config.json')
    entered: list[bool] = []

    @contextmanager
    def spy_lock(*_args, **_kwargs):
        entered.append(True)
        yield False  # never write, even if the guard were broken

    monkeypatch.setattr(config_module, '_config_file_lock', spy_lock)
    config_module.save_config({'servers': []})
    assert entered == []


def test_a_temporary_config_is_still_written(tmp_path, monkeypatch):
    target = tmp_path / 'config.json'
    monkeypatch.setattr(config_module, 'CONFIG_PATH', target)
    config_module.save_config({'ui_port': 8900, 'gateway_port': 8001, 'servers': []})
    assert json.loads(target.read_text(encoding='utf-8'))['ui_port'] == 8900
