"""Basic config tests for DFlash Console."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config as cfg


class ConfigTests(unittest.TestCase):
    def test_normalize_server_idle_minutes(self):
        entry = cfg.normalize_server({
            'id': 'test',
            'profile': 'gemma-chat',
            'port': 8090,
            'idle_unload_minutes': 45,
        })
        self.assertEqual(entry['idle_unload_minutes'], 45)
        self.assertEqual(entry['api_url'], 'http://127.0.0.1:8090/v1')

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            original = cfg.CONFIG_PATH
            cfg.CONFIG_PATH = path
            try:
                payload = {'ui_port': 8900, 'dflash_root': r'C:\dev\Dflash', 'servers': []}
                cfg.save_config(payload)
                loaded = cfg.load_config()
                self.assertEqual(loaded['ui_port'], 8900)
            finally:
                cfg.CONFIG_PATH = original

    def test_validate_config_rejects_duplicate_ports_and_non_loopback_hosts(self):
        with self.assertRaisesRegex(ValueError, 'already used'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [{'id': 'one', 'port': 8900, 'host': '127.0.0.1'}],
            })
        with self.assertRaisesRegex(ValueError, 'loopback'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [{'id': 'one', 'port': 8090, 'host': '0.0.0.0'}],
            })

    def test_config_root_wins_unless_explicit_override_is_set(self):
        with patch.dict('os.environ', {'DFLASH_ROOT': r'C:\env-root'}, clear=False):
            self.assertEqual(
                cfg.get_dflash_root({'dflash_root': r'C:\config-root'}),
                Path(r'C:\config-root').resolve(),
            )
        with patch.dict('os.environ', {'DFLASH_ROOT': r'C:\env-root', 'DFLASH_ROOT_OVERRIDE': r'C:\override-root'}, clear=False):
            self.assertEqual(
                cfg.get_dflash_root({'dflash_root': r'C:\config-root'}),
                Path(r'C:\override-root').resolve(),
            )


if __name__ == '__main__':
    unittest.main()
