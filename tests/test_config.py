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
                payload = {'ui_port': 8900, 'dflash_root': r'C:\dev\Dflash-Console', 'servers': []}
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

    def test_validate_config_rejects_non_loopback_api_urls(self):
        with self.assertRaisesRegex(ValueError, 'api_url.*loopback'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [{
                    'id': 'one',
                    'port': 8090,
                    'host': '127.0.0.1',
                    'api_url': 'http://192.168.1.20:8090/v1',
                }],
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

    def test_runtimes_validate_and_list(self):
        payload = {
            'ui_port': 8900,
            'servers': [],
            'runtimes': [
                {'id': 'piper-main', 'runtime_id': 'piper', 'port': 8910, 'device_policy': 'cpu'},
                {'id': 'stt-main', 'runtime_id': 'stt', 'port': 8911, 'host': '127.0.0.1'},
            ],
        }
        validated = cfg.validate_config(payload)
        runtimes = cfg.list_runtimes(validated)
        self.assertEqual(len(runtimes), 2)
        self.assertEqual(runtimes[0]['runtime_id'], 'piper')
        self.assertEqual(runtimes[0]['device_policy'], 'cpu')
        self.assertEqual(runtimes[1]['api_url'], 'http://127.0.0.1:8911')

    def test_runtimes_rejects_llama_server_and_non_loopback(self):
        with self.assertRaisesRegex(ValueError, 'must not be llama-server'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [],
                'runtimes': [{'id': 'bad', 'runtime_id': 'llama-server', 'port': 8910}],
            })
        with self.assertRaisesRegex(ValueError, 'loopback'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [],
                'runtimes': [{'id': 'bad', 'runtime_id': 'piper', 'port': 8910, 'host': '0.0.0.0'}],
            })

    def test_runtimes_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate runtime id'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [],
                'runtimes': [
                    {'id': 'dup', 'runtime_id': 'piper', 'port': 8910},
                    {'id': 'dup', 'runtime_id': 'stt', 'port': 8911},
                ],
            })

    def test_config_patch_round_trips_runtimes_field(self):
        # Regression: ConfigPatch must carry the runtimes list through
        # model_dump, otherwise PUT /api/config silently drops runtime edits.
        from api.app import ConfigPatch

        patch = ConfigPatch(runtimes=[
            {'id': 'tts-main', 'runtime_id': 'piper', 'device_policy': 'cpu'},
            {'id': 'stt-main', 'runtime_id': 'stt', 'device_policy': 'gpu'},
        ])
        dumped = patch.model_dump(exclude_none=True)
        self.assertIn('runtimes', dumped)
        self.assertEqual(len(dumped['runtimes']), 2)
        self.assertEqual(dumped['runtimes'][1]['device_policy'], 'gpu')

    def test_config_patch_persists_runtimes_through_put_config(self):
        # End-to-end: put_config must write the patched runtimes to disk.
        from api.app import ConfigPatch, put_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            original = cfg.CONFIG_PATH
            cfg.CONFIG_PATH = path
            cfg.save_config({
                'ui_port': 8900,
                'servers': [],
                'runtimes': [
                    {'id': 'tts-main', 'runtime_id': 'piper', 'port': 0, 'device_policy': 'cpu'},
                    {'id': 'stt-main', 'runtime_id': 'stt', 'port': 8910, 'device_policy': 'gpu'},
                ],
            })
            try:
                body = ConfigPatch(runtimes=[
                    {'id': 'tts-main', 'runtime_id': 'piper', 'port': 0, 'device_policy': 'cpu'},
                    {'id': 'stt-main', 'runtime_id': 'stt', 'port': 8910, 'device_policy': 'cpu'},
                ])
                result = put_config(body)
                self.assertTrue(result['success'])
                reloaded = cfg.load_config()
                runtimes = cfg.list_runtimes(reloaded)
                self.assertEqual(len(runtimes), 2)
                self.assertEqual(runtimes[1]['device_policy'], 'cpu')
            finally:
                cfg.CONFIG_PATH = original

    def test_shared_port_registry_rejects_cross_list_collisions(self):
        # A runtime port colliding with a server port must be rejected.
        with self.assertRaisesRegex(ValueError, 'already used by one'):
            cfg.validate_config({
                'ui_port': 8900,
                'servers': [{'id': 'one', 'port': 8090, 'host': '127.0.0.1'}],
                'runtimes': [{'id': 'rt', 'runtime_id': 'piper', 'port': 8090}],
            })

    def test_suggest_runtime_port_prefers_runtime_band_and_skips_reserved(self):
        with patch.object(cfg, 'load_config', return_value={
            'ui_port': 8900,
            'servers': [{'id': 'one', 'port': 8090, 'host': '127.0.0.1'}],
            'runtimes': [{'id': 'rt', 'runtime_id': 'piper', 'port': 8910}],
        }):
            self.assertEqual(cfg.suggest_runtime_port(), 8911)
            self.assertEqual(cfg.suggest_server_port(), 8091)

    def test_reserved_ports_covers_ui_servers_and_runtimes(self):
        ports = cfg.reserved_ports({
            'ui_port': 8900,
            'servers': [{'id': 'one', 'port': 8090}],
            'runtimes': [{'id': 'rt', 'runtime_id': 'piper', 'port': 8910}],
        })
        self.assertEqual(ports[8900], 'ui_port')
        self.assertEqual(ports[8090], 'server:one')
        self.assertEqual(ports[8910], 'runtime:rt')


if __name__ == '__main__':
    unittest.main()
