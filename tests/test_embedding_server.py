"""Tests for managed embedding servers in DFlash Console."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from core import config as cfg
from core.embedding_server import (
    embedding_metadata,
    embedding_router_status,
    resolve_embedding_model_path,
    start_embedding_server,
    unload_embedding_models,
)


class EmbeddingServerTests(unittest.TestCase):
    def test_is_embedding_server_profile(self):
        self.assertTrue(cfg.is_embedding_server({'profile': 'nomic-embed'}))
        self.assertTrue(cfg.is_embedding_server({'engine_mode': 'embedding', 'profile': 'custom'}))
        self.assertFalse(cfg.is_embedding_server({'profile': 'gemma-chat'}))

    def test_normalize_server_adds_embedding_settings(self):
        entry = cfg.normalize_server({
            'id': 'nomic-embed',
            'profile': 'nomic-embed',
            'port': 8891,
        })
        self.assertEqual(entry['engine_mode'], 'embedding')
        self.assertEqual(entry['pooling'], 'mean')
        self.assertEqual(entry['embedding_settings']['dimensions'], 768)

    def test_embedding_metadata_from_path(self):
        meta = embedding_metadata(Path('nomic-embed-text-v1.5.Q8_0.gguf'))
        self.assertEqual(meta['model_kind'], 'embedding')
        self.assertEqual(meta['embedding_dimensions'], 768)
        self.assertEqual(meta['quantization'], 'Q8_0')

    def test_resolve_embedding_model_path_uses_target_path(self):
        with mock.patch.object(Path, 'is_file', return_value=True):
            path = resolve_embedding_model_path({
                'id': 'nomic-embed',
                'profile': 'nomic-embed',
                'port': 8891,
                'target_path': r'C:\dev\OneVoice\models\nomic-embed\nomic-embed-text-v1.5.Q8_0.gguf',
            })
        self.assertIn('nomic-embed-text-v1.5.Q8_0.gguf', str(path))


def _fake_models_response(monkeypatch, payload):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            import json

            return json.dumps(payload).encode('utf-8')

    monkeypatch.setattr(
        'core.embedding_server.urllib.request.urlopen',
        lambda url, timeout=0: _Response(),
    )


def test_embedding_router_status_reads_router_states(monkeypatch):
    _fake_models_response(monkeypatch, {
        'data': [
            {'id': 'nomic-embed-text-v1.5.q8-0', 'status': {'value': 'unloaded'}},
        ],
    })
    assert embedding_router_status('http://127.0.0.1:8101/v1') == {
        'nomic-embed-text-v1.5.q8-0': 'unloaded',
    }


def test_embedding_router_status_rejects_direct_server(monkeypatch):
    _fake_models_response(monkeypatch, {'data': [{'id': 'nomic-embed-text-v1.5.Q8_0.gguf'}]})
    assert embedding_router_status('http://127.0.0.1:8101/v1') is None


def test_start_embedding_server_starts_idle_router(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        'core.server_boot.start_router_listener',
        lambda entry, cfg=None: calls.append(entry['id']) or {'success': True, 'port': 8101},
    )
    result = start_embedding_server({
        'id': 'nomic-embed',
        'profile': 'nomic-embed',
        'port': 8101,
        'host': '127.0.0.1',
        'api_url': 'http://127.0.0.1:8101/v1',
    })
    assert calls == ['nomic-embed']
    assert result['success'] is True
    assert result['router_idle'] is True
    assert result['loaded'] is False


def test_unload_embedding_models_uses_router_unload(monkeypatch):
    monkeypatch.setattr(
        'core.embedding_server.embedding_router_status',
        lambda api_url, timeout=2.0: {
            'nomic-embed-text-v1.5.q8-0': 'loaded',
            'other-model': 'unloaded',
        },
    )
    calls: list[str] = []
    monkeypatch.setattr(
        'core.runtime.unload_model',
        lambda **kwargs: calls.append(str(kwargs.get('model_id'))) or {'success': True},
    )
    result = unload_embedding_models({
        'id': 'nomic-embed',
        'profile': 'nomic-embed',
        'api_url': 'http://127.0.0.1:8101/v1',
    })
    assert calls == ['nomic-embed-text-v1.5.q8-0']
    assert result['success'] is True
    assert result['models'] == ['nomic-embed-text-v1.5.q8-0']


if __name__ == '__main__':
    unittest.main()
