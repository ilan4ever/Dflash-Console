"""Tests for Qwen3.8 DFlash benchmark path discovery."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    'benchmark_qwen38_dflash',
    ROOT / 'scripts' / 'benchmark_qwen38_dflash.py',
)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules['benchmark_qwen38_dflash'] = _MOD
_SPEC.loader.exec_module(_MOD)
_discover_dflash1_draft = _MOD._discover_dflash1_draft
_discover_dflash2_draft = _MOD._discover_dflash2_draft
_discover_target = _MOD._discover_target
_draft_pair_notes = _MOD._draft_pair_notes


class BenchmarkQwen38DiscoveryTests(unittest.TestCase):
    def test_discover_target_from_server_rows(self):
        servers = [{
            'id': 'qwen3-8-27b-q6-k-l',
            'model_id': 'qwen3.8-27b-q6-k-l',
            'target_path': r'C:\models\bartowski\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q6_K_L.gguf',
        }]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Qwen3.8-27B-Q6_K_L.gguf'
            target.write_bytes(b'x')
            rows = [{**servers[0], 'target_path': str(target)}]
            path, model_id = _discover_target(rows)
            self.assertEqual(path, target.resolve())
            self.assertEqual(model_id, 'qwen3.8-27b-q6-k-l')

    def test_discover_dflash_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'Qwen3.8-27B-Q6_K_L.gguf'
            dflash1 = root / 'Qwen3.5-27B-DFlash-F16.gguf'
            dflash2 = root / 'Qwen3.8-27B-DFlash2-F16.gguf'
            target.write_bytes(b'x')
            dflash1.write_bytes(b'x')
            dflash2.write_bytes(b'x')
            catalog = {
                'models': [
                    {'path': str(dflash1), 'filename': dflash1.name, 'label': dflash1.name},
                    {'path': str(dflash2), 'filename': dflash2.name, 'label': dflash2.name},
                ],
            }
            with patch('core.stack_match.list_local_models', return_value=catalog):
                self.assertEqual(_discover_dflash1_draft(target), dflash1.resolve())
                self.assertEqual(_discover_dflash2_draft(target), dflash2.resolve())

    def test_draft_pair_notes_warns_on_qwen_version_mismatch(self):
        target = Path('Qwen3.8-27B-Q6_K_L.gguf')
        draft = Path('Qwen3.5-27B-DFlash-F16.gguf')
        notes = _draft_pair_notes(target, draft)
        self.assertTrue(any('generation mismatch' in note.lower() for note in notes))
        self.assertTrue(any('Qwen3.5' in note and 'Qwen3.8' in note for note in notes))


if __name__ == '__main__':
    unittest.main()
