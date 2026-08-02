"""Tests for managed embedding servers in DFlash Console."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from core import config as cfg
from core.embedding_server import embedding_metadata, resolve_embedding_model_path


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


if __name__ == '__main__':
    unittest.main()
