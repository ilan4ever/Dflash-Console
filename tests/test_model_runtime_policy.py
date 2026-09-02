from __future__ import annotations

import unittest

from core.model_runtime_policy import (
    explain_freetoken_load_error,
    requires_freetoken,
    runtime_load_block,
)


class ModelRuntimePolicyTests(unittest.TestCase):
    def test_large_hf_model_requires_freetoken(self):
        model = {
            'kind': 'dir',
            'size_gb': 155.44,
            'engines': ['transformers', 'vllm', 'freetoken'],
        }
        self.assertTrue(requires_freetoken(model))
        warning = runtime_load_block(model, 'transformers')
        self.assertEqual(warning['code'], 'freetoken-required')
        self.assertIn('200 GB', warning['message'])

    def test_freetoken_is_allowed_for_large_hf_model(self):
        model = {
            'kind': 'dir',
            'size_gb': 155.44,
            'engines': ['transformers', 'vllm', 'freetoken'],
        }
        self.assertIsNone(runtime_load_block(model, 'freetoken'))

    def test_small_hf_model_keeps_other_engine_choices(self):
        model = {
            'kind': 'dir',
            'size_gb': 7.2,
            'engines': ['transformers', 'vllm', 'freetoken'],
        }
        self.assertFalse(requires_freetoken(model))
        self.assertIsNone(runtime_load_block(model, 'transformers'))

    def test_virtual_memory_failure_has_paging_file_guidance(self):
        message = explain_freetoken_load_error({
            'error': 'FreeToken exited',
            'detail': '[WinError 1455] The paging file is too small',
        })
        self.assertIn('200 GB', message)
        self.assertIn('restart WSL/Windows', message)


if __name__ == '__main__':
    unittest.main()
