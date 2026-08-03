"""Tests for model stack resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.model_stack import _find_gemma12_target, resolve_model_stack


class ModelStackTests(unittest.TestCase):
    def test_gemma_chat_has_draft(self):
        stack = resolve_model_stack({
            'profile': 'gemma-chat',
            'model_id': 'gemma-4-31b-it-dflash',
        })
        roles = [row['role'] for row in stack]
        self.assertIn('alias', roles)
        self.assertIn('target', roles)
        self.assertIn('draft-dflash', roles)

    def test_gemma_12_ar_no_draft(self):
        stack = resolve_model_stack({
            'profile': 'gemma-12-ar',
            'model_id': 'gemma-4-12b-it-qat',
        })
        roles = [row['role'] for row in stack]
        self.assertIn('target', roles)
        self.assertNotIn('draft-dflash', roles)

    def test_find_gemma12_target_prefers_standard_it_over_qat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qat = root / 'google' / 'gemma-4-12b-it-qat-q4_0-gguf'
            qat.mkdir(parents=True)
            qat_file = qat / 'gemma-4-12b-it-qat-q4_0.gguf'
            qat_file.write_bytes(b'qat')
            standard = root / 'models' / 'gemma-4-12b-it'
            standard.mkdir(parents=True)
            standard_file = standard / 'gemma-4-12B-it-Q4_K_M.gguf'
            standard_file.write_bytes(b'standard')
            cfg = {'dflash_root': str(root), 'models_root': str(root / 'models')}
            with patch('core.model_stack.get_dflash_root', return_value=root):
                with patch('core.model_stack._lmstudio_models_dir', return_value=root / 'lmstudio'):
                    picked = _find_gemma12_target(cfg=cfg)
            self.assertEqual(picked, standard_file)


if __name__ == '__main__':
    unittest.main()
