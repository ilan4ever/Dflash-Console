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

    def test_normalize_inference_settings_keeps_reasoning_effort(self):
        normalized = cfg.normalize_inference_settings({'reasoning_effort': 'high'})
        self.assertEqual(normalized['reasoning_effort'], 'high')

    def test_normalize_inference_settings_defaults_and_rejects_bad_effort(self):
        defaulted = cfg.normalize_inference_settings(None)
        self.assertEqual(defaulted['reasoning_effort'], 'auto')
        bad = cfg.normalize_inference_settings({'reasoning_effort': 'extreme'})
        self.assertEqual(bad['reasoning_effort'], 'auto')

    def test_reasoning_effort_levels_order(self):
        # The canonical order surfaced in the UI.
        self.assertEqual(
            list(cfg.REASONING_EFFORT_LEVELS),
            ['auto', 'none', 'low', 'medium', 'high', 'max'],
        )

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

    def test_normalize_runtime_keeps_default_voice_and_model(self):
        entry = cfg.normalize_runtime({
            'id': 'tts-main',
            'runtime_id': 'piper',
            'port': 0,
            'device_policy': 'cpu',
            'default_voice': 'en_US-lessac-medium',
            'default_model': r'C:\models\whisper\model_q4_k.gguf',
        })
        self.assertEqual(entry['default_voice'], 'en_US-lessac-medium')
        self.assertEqual(entry['default_model'], r'C:\models\whisper\model_q4_k.gguf')
        blank = cfg.normalize_runtime({'id': 'rt', 'runtime_id': 'piper', 'port': 0})
        self.assertEqual(blank['default_voice'], '')
        self.assertEqual(blank['default_model'], '')

    def test_config_patch_round_trips_new_toggles(self):
        # The loading-behavior toggles must survive model_dump too, otherwise
        # PUT /api/config would silently drop them (same pydantic pitfall).
        from api.app import ConfigPatch

        patch = ConfigPatch(runtime_stop_others_on_load=True, cpu_slow_warn=False)
        dumped = patch.model_dump(exclude_none=True)
        self.assertIs(dumped['runtime_stop_others_on_load'], True)
        self.assertIs(dumped['cpu_slow_warn'], False)

    def test_put_config_persists_runtime_defaults_and_toggles(self):
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
                    {'id': 'stt-main', 'runtime_id': 'stt', 'port': 0, 'device_policy': 'gpu'},
                ],
            })
            try:
                body = ConfigPatch(
                    runtimes=[
                        {
                            'id': 'tts-main', 'runtime_id': 'piper', 'port': 0,
                            'device_policy': 'cpu', 'default_voice': 'en_US-lessac-medium',
                            'allow_cpu_fallback': True, 'vram_budget_mb': 0,
                        },
                        {
                            'id': 'stt-main', 'runtime_id': 'stt', 'port': 0,
                            'device_policy': 'gpu', 'default_model': r'C:\models\w\m.gguf',
                            'allow_cpu_fallback': True, 'vram_budget_mb': 0,
                        },
                    ],
                    runtime_stop_others_on_load=True,
                    cpu_slow_warn=False,
                )
                result = put_config(body)
                self.assertTrue(result['success'])
                reloaded = cfg.load_config()
                self.assertIs(reloaded['runtime_stop_others_on_load'], True)
                self.assertIs(reloaded['cpu_slow_warn'], False)
                runtimes = {r['id']: r for r in cfg.list_runtimes(reloaded)}
                self.assertEqual(runtimes['tts-main']['default_voice'], 'en_US-lessac-medium')
                self.assertEqual(runtimes['stt-main']['default_model'], r'C:\models\w\m.gguf')
            finally:
                cfg.CONFIG_PATH = original

    def test_auto_stop_other_servers_gated_by_toggle(self):
        from api.app import _auto_stop_other_servers

        cfg_payload = {
            'ui_port': 8900,
            'servers': [{'id': 'a', 'port': 8090, 'host': '127.0.0.1'}],
        }
        with patch('core.runtimes.contention.gpu_contention_report',
                   return_value={'recommendation': 'stop-others'}):
            with patch('api.app.server_unload') as unload:
                stopped = _auto_stop_other_servers(
                    {**cfg_payload, 'runtime_stop_others_on_load': False}, 'a',
                )
        self.assertEqual(stopped, [])
        unload.assert_not_called()

    def test_auto_stop_other_servers_skips_target_and_embedding(self):
        from api.app import _auto_stop_other_servers

        cfg_payload = {
            'ui_port': 8900,
            'servers': [
                {'id': 'a', 'port': 8090, 'host': '127.0.0.1'},
                {'id': 'b', 'port': 8091, 'host': '127.0.0.1', 'engine_mode': 'embedding'},
                {'id': 'c', 'port': 8092, 'host': '127.0.0.1'},
            ],
            'runtime_stop_others_on_load': True,
        }
        report = {
            'recommendation': 'stop-others',
            'console_runtimes': [
                {'id': 'a', 'running': True},   # target -> skipped
                {'id': 'b', 'running': True},   # embedding -> skipped
                {'id': 'c', 'running': True},   # unload
                {'id': 'z', 'running': False},  # not running -> skipped
            ],
        }
        with patch('core.runtimes.contention.gpu_contention_report', return_value=report):
            with patch('api.app.server_unload', return_value={'success': True}) as unload:
                stopped = _auto_stop_other_servers(cfg_payload, 'a')
        self.assertEqual(stopped, ['c'])
        unload.assert_called_once_with('c')

    def test_auto_stop_other_servers_noop_without_stop_others(self):
        from api.app import _auto_stop_other_servers

        cfg_payload = {
            'ui_port': 8900,
            'servers': [{'id': 'a', 'port': 8090, 'host': '127.0.0.1'}],
            'runtime_stop_others_on_load': True,
        }
        with patch('core.runtimes.contention.gpu_contention_report',
                   return_value={'recommendation': 'none'}):
            with patch('api.app.server_unload') as unload:
                stopped = _auto_stop_other_servers(cfg_payload, 'a')
        self.assertEqual(stopped, [])
        unload.assert_not_called()

    def test_load_plan_route_registered_to_server_load_plan(self):
        # Regression: _auto_stop_other_servers was once inserted between the
        # @app.get('/api/servers/{server_id}/load-plan') decorator and
        # server_load_plan, hijacking the route and breaking the load preview.
        from api.app import app

        found = False
        for route in app.routes:
            if getattr(route, 'path', None) == '/api/servers/{server_id}/load-plan':
                endpoint = getattr(route, 'endpoint', None)
                self.assertEqual(
                    getattr(endpoint, '__name__', ''),
                    'server_load_plan',
                    'load-plan route must be owned by server_load_plan, not a helper',
                )
                found = True
                break
        self.assertTrue(found, 'load-plan route not registered')

    def test_models_catalog_adds_load_route(self):
        from api.app import models_catalog

        rows = [
            {'path': r'C:\models\whisper\w.gguf', 'modality': 'speech-to-text', 'runtime_id': 'stt'},
            {'path': r'C:\models\llm\l.gguf', 'modality': 'llm', 'runtime_id': 'llama-server', 'server_id': 'srv'},
        ]
        with patch('api.app.list_local_models', return_value={'models': rows}):
            payload = models_catalog()
        routes = {m['path']: m['load_route'] for m in payload['models']}
        self.assertEqual(routes[r'C:\models\whisper\w.gguf']['path'], '/api/models/load')
        self.assertEqual(routes[r'C:\models\llm\l.gguf']['path'], '/api/servers/srv/load')

    def test_model_load_404_for_unknown_path(self):
        from fastapi import HTTPException

        from api.app import ModelLoadRequest, model_load

        with patch('core.catalog_load.list_local_models', return_value={'models': [
            {'path': r'C:\known\model.gguf', 'modality': 'llm', 'runtime_id': 'llama-server'},
        ]}):
            with self.assertRaises(HTTPException) as ctx:
                model_load(ModelLoadRequest(path=r'Z:\unknown\model.gguf'))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_model_load_dispatches_stt(self):
        from types import SimpleNamespace

        from api.app import ModelLoadRequest, model_load

        fake = SimpleNamespace(load=lambda model: {'success': True, 'loaded': True, 'port': 8910})
        stt_path = r'C:\models\whisper\model_q4_k.gguf'
        with patch('core.catalog_load.list_local_models', return_value={'models': [
            {'path': stt_path, 'modality': 'speech-to-text', 'runtime_id': 'stt'},
        ]}):
            with patch('core.runtimes.get_runtime_adapter', return_value=fake) as ga:
                result = model_load(ModelLoadRequest(path=stt_path))
        self.assertTrue(result['success'])
        self.assertEqual(result['runtime_id'], 'stt')
        ga.assert_called_once_with('stt')

    def test_model_load_dispatches_llama(self):
        from types import SimpleNamespace

        from api.app import ModelLoadRequest, model_load

        llm_path = r'C:\models\gemma\model.gguf'
        server_entry = {'id': 'srv', 'port': 8090, 'host': '127.0.0.1', 'label': 'Srv'}
        with patch('core.catalog_load.list_local_models', return_value={'models': [
            {'path': llm_path, 'modality': 'llm', 'runtime_id': 'llama-server'},
        ]}):
            with patch('core.catalog_load.list_servers', return_value=[server_entry]):
                with patch('core.memory_guardrails.assess_load', return_value={'level': 'ok'}):
                    with patch('core.catalog_load.load_server_checkpoint', return_value={'success': True, 'loaded': True}) as load_fn:
                        result = model_load(ModelLoadRequest(path=llm_path))
        self.assertTrue(result['success'])
        self.assertEqual(result['runtime_id'], 'llama-server')
        self.assertEqual(result['server_id'], 'srv')
        load_fn.assert_called_once()
        self.assertEqual(load_fn.call_args.kwargs.get('model_path'), llm_path)

    def test_runtime_start_stop_endpoints(self):
        from types import SimpleNamespace

        from api.app import runtime_start, runtime_stop

        fake = SimpleNamespace(
            start=lambda profile: {'success': True, 'started': True},
            stop=lambda: {'success': True, 'stopped': True},
        )
        with patch('core.runtimes.get_runtime_adapter', return_value=fake):
            with patch('api.app.list_runtimes', return_value=[]):
                started = runtime_start('stt')
                stopped = runtime_stop('stt')
        self.assertTrue(started['started'])
        self.assertTrue(stopped['stopped'])

    def test_gateway_port_rules(self):
        # Valid gateway_port accepted; colliding with ui_port rejected; out of
        # range rejected; 0 means "use default" (accepted, normalized to 8001).
        cfg.validate_config({'ui_port': 8900, 'gateway_port': 8001, 'servers': []})
        with self.assertRaisesRegex(ValueError, 'must differ from ui_port'):
            cfg.validate_config({'ui_port': 8900, 'gateway_port': 8900, 'servers': []})
        with self.assertRaisesRegex(ValueError, 'must be between'):
            cfg.validate_config({'ui_port': 8900, 'gateway_port': 70000, 'servers': []})
        cfg.validate_config({'ui_port': 8900, 'gateway_port': 0, 'servers': []})

    def test_reserved_ports_includes_gateway(self):
        ports = cfg.reserved_ports({'ui_port': 8900, 'gateway_port': 8001, 'servers': []})
        self.assertEqual(ports[8900], 'ui_port')
        self.assertEqual(ports[8001], 'gateway_port')

    def test_config_patch_round_trips_gateway(self):
        from api.app import ConfigPatch

        patch = ConfigPatch(gateway_port=8001, gateway_server_id='gemma-12b-ar')
        dumped = patch.model_dump(exclude_none=True)
        self.assertEqual(dumped['gateway_port'], 8001)
        self.assertEqual(dumped['gateway_server_id'], 'gemma-12b-ar')

    def test_put_config_persists_gateway(self):
        from api.app import ConfigPatch, put_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            original = cfg.CONFIG_PATH
            cfg.CONFIG_PATH = path
            cfg.save_config({'ui_port': 8900, 'gateway_port': 8001, 'servers': []})
            try:
                body = ConfigPatch(gateway_port=8001, gateway_server_id='gemma-12b-ar')
                result = put_config(body)
                self.assertTrue(result['success'])
                reloaded = cfg.load_config()
                self.assertEqual(reloaded['gateway_port'], 8001)
                self.assertEqual(reloaded['gateway_server_id'], 'gemma-12b-ar')
            finally:
                cfg.CONFIG_PATH = original

    def test_gateway_chat_server_picks_configured_default(self):
        from api.gateway import _chat_server

        cfg_payload = {
            'ui_port': 8900,
            'gateway_server_id': 'gemma-12b-ar',
            'servers': [
                {'id': 'gemma-31b-dflash', 'enabled': True, 'port': 8091},
                {'id': 'gemma-12b-ar', 'enabled': True, 'port': 8301},
                {'id': 'nomic-embed', 'enabled': True, 'port': 8891, 'engine_mode': 'embedding'},
            ],
        }
        self.assertEqual(_chat_server(cfg_payload)['id'], 'gemma-12b-ar')

    def test_gateway_chat_server_auto_falls_back_to_first(self):
        from api.gateway import _chat_server

        cfg_payload = {
            'ui_port': 8900,
            'gateway_server_id': '',
            'servers': [
                {'id': 'gemma-31b-dflash', 'enabled': True, 'port': 8091},
                {'id': 'nomic-embed', 'enabled': True, 'port': 8891, 'engine_mode': 'embedding'},
            ],
        }
        self.assertEqual(_chat_server(cfg_payload)['id'], 'gemma-31b-dflash')

    def test_gateway_list_models_lists_engines(self):
        import asyncio

        from api.gateway import list_models

        with patch('api.gateway.load_config', return_value={
            'ui_port': 8900,
            'gateway_port': 8001,
            'servers': [
                {'id': 'gemma-12b-ar', 'enabled': True, 'port': 8301, 'label': 'Gemma 12B', 'model_id': 'gemma-4-12b-it-qat'},
                {'id': 'nomic-embed', 'enabled': True, 'port': 8891, 'label': 'Nomic Embed', 'engine_mode': 'embedding'},
            ],
        }):
            result = asyncio.run(list_models())
        ids = {m['id'] for m in result['data']}
        self.assertEqual(ids, {'gemma-4-12b-it-qat', 'nomic-embed'})

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
            # No live listeners in the test: reserved 8910 is skipped -> 8911.
            with patch('socket.create_connection', side_effect=OSError):
                self.assertEqual(cfg.suggest_runtime_port(), 8911)
                self.assertEqual(cfg.suggest_server_port(), 8091)

    def test_suggest_server_port_skips_live_listeners(self):
        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_connect(addr, timeout=0.4):
            _host, port = addr[0], addr[1]
            if port == 8091:
                return FakeConn()
            raise OSError('connection refused')

        with patch.object(cfg, 'load_config', return_value={
            'ui_port': 8900,
            'servers': [{'id': 'one', 'port': 8090, 'host': '127.0.0.1'}],
            'runtimes': [],
        }):
            with patch('socket.create_connection', side_effect=fake_connect):
                self.assertEqual(cfg.suggest_server_port(), 8092)

    def test_suggest_runtime_port_skips_live_listeners(self):
        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_connect(addr, timeout=0.4):
            host, port = addr[0], addr[1]
            if port in (8910, 8911):
                raise OSError('connection refused')  # free
            return FakeConn()  # 8912+ already has a listener

        with patch.object(cfg, 'load_config', return_value={
            'ui_port': 8900,
            'servers': [],
            'runtimes': [],
        }):
            with patch('socket.create_connection', side_effect=fake_connect):
                # 8910/8911 are free, 8912+ are live -> pick 8910.
                self.assertEqual(cfg.suggest_runtime_port(), 8910)

        def fake_connect_all_live(addr, timeout=0.4):
            host, port = addr[0], addr[1]
            if port < 8918:
                return FakeConn()  # band ports are live
            raise OSError('connection refused')  # 8918+ free

        with patch.object(cfg, 'load_config', return_value={
            'ui_port': 8900,
            'servers': [],
            'runtimes': [],
        }):
            with patch('socket.create_connection', side_effect=fake_connect_all_live):
                # Every band port is live -> fall past the band to a free port.
                self.assertEqual(cfg.suggest_runtime_port(), 8918)

    def test_reserved_ports_covers_ui_servers_and_runtimes(self):
        ports = cfg.reserved_ports({
            'ui_port': 8900,
            'servers': [{'id': 'one', 'port': 8090}],
            'runtimes': [{'id': 'rt', 'runtime_id': 'piper', 'port': 8910}],
        })
        self.assertEqual(ports[8900], 'ui_port')
        self.assertEqual(ports[8090], 'server:one')
        self.assertEqual(ports[8910], 'runtime:rt')

    def test_resolve_console_root_honors_env(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw)
            with patch.dict('os.environ', {'DFLASH_CONSOLE_ROOT': str(target)}, clear=False):
                self.assertEqual(cfg.resolve_console_root(), target.resolve())

    def test_ensure_console_data_root_seeds_config(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / 'data'
            with patch.dict('os.environ', {'DFLASH_CONSOLE_ROOT': str(target)}, clear=False):
                root = cfg.ensure_console_data_root()
                self.assertEqual(root, target.resolve())
                self.assertTrue((root / 'config.json').is_file())
                self.assertTrue((root / 'logs').is_dir())

    def test_source_checkout_detects_repo(self):
        self.assertTrue(cfg.is_source_checkout())


if __name__ == '__main__':
    unittest.main()
