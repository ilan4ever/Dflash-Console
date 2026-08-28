"""Tests for DFlash 1 vs DFlash 2 generation helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.dflash_generation import (
    infer_dflash_generation,
    repo_dflash_generation,
    spec_draft_n_max,
)
from core.model_presets import write_server_preset
from core.stack_match import build_hf_search_query, find_local_accelerators


class DflashGenerationTests(unittest.TestCase):
    def test_infer_generation(self):
        self.assertEqual(infer_dflash_generation('Qwen3.8-27B-DFlash2-Q4_K_M.gguf'), 'dflash2')
        self.assertEqual(infer_dflash_generation('Qwen3.5-27B-DFlash-F16.gguf'), 'dflash1')

    def test_spec_draft_n_max(self):
        self.assertEqual(spec_draft_n_max(draft_path='accel-DFlash2.gguf'), 7)
        self.assertEqual(spec_draft_n_max(draft_path='accel-DFlash.gguf'), 8)

    def test_hf_query_generation(self):
        query = build_hf_search_query('Qwen3.8-27B-Q6_K_L.gguf', dflash_generation='dflash2')
        self.assertIn('DFlash 2', query)

    def test_find_local_accelerators_respects_generation_filter(self):
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
                gen1 = find_local_accelerators(target, dflash_generation='dflash1')
                gen2 = find_local_accelerators(target, dflash_generation='dflash2')
            self.assertEqual([Path(row['path']).name for row in gen1], [dflash1.name])
            self.assertEqual([Path(row['path']).name for row in gen2], [dflash2.name])

    def test_preset_uses_dflash2_block_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'target.gguf'
            draft = root / 'target-DFlash2-F16.gguf'
            target.write_bytes(b'x')
            draft.write_bytes(b'x')
            preset = write_server_preset({
                'id': 'stack',
                'model_id': 'custom-target',
                'profile': 'qwen-dflash',
                'target_path': str(target),
                'draft_path': str(draft),
                'context_size': 8192,
                'load_settings': {},
            })
            text = preset.read_text(encoding='utf-8')
            self.assertIn('spec-draft-n-max = 7', text)

    def test_repo_generation(self):
        self.assertEqual(repo_dflash_generation('incoai/Qwen3.8-27B-DFlash2'), 'dflash2')
        self.assertEqual(repo_dflash_generation('org/Qwen3.5-27B-DFlash-GGUF'), 'dflash1')


if __name__ == '__main__':
    unittest.main()
