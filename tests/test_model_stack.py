"""Tests for model stack resolution."""

from __future__ import annotations

import unittest

from core.model_stack import resolve_model_stack


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


if __name__ == '__main__':
    unittest.main()
