"""Tests for DFlash stack matching and custom stacks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.model_stack import resolve_model_stack
from core.stack_match import (
    build_hf_search_query,
    infer_dflash_profile,
    is_accelerator_path,
    list_capable_targets,
    match_stack_for_target,
    is_target_candidate,
    is_viable_stack_pair,
    preflight_stack_target,
    score_accelerator_pair,
    suggest_server_id,
)


class StackMatchTests(unittest.TestCase):
    def test_accelerator_detection(self):
        self.assertTrue(is_accelerator_path('Qwen3.5-27B-DFlash-F16.gguf'))
        self.assertFalse(is_accelerator_path('Qwen3.5-27B-Q4_K_M.gguf'))

    def test_pair_scoring(self):
        target = 'Qwen3.5-27B-Q4_K_M.gguf'
        good = 'Qwen3.5-27B-DFlash-F16.gguf'
        bad = 'gemma-4-12B-it-DFlash-Q4_K_M.gguf'
        self.assertGreater(score_accelerator_pair(target, good), score_accelerator_pair(target, bad))

    def test_profile_inference(self):
        self.assertEqual(infer_dflash_profile('gemma-4-12b-it-qat-q4_0.gguf'), 'gemma-12-dflash')
        self.assertEqual(infer_dflash_profile('Qwen3.5-27B-Q4_K_M.gguf'), 'qwen-dflash')

    def test_hf_query(self):
        query = build_hf_search_query('Qwen3.6-9B-Instruct-Q4_K_M.gguf')
        self.assertIn('DFlash', query)

    def test_hf_query_removes_shard_and_quant_noise(self):
        query = build_hf_search_query(
            'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf',
        )
        self.assertEqual(query, 'Laguna S 2.1 DFlash gguf')

    def test_hf_suggestions_only_include_selected_target_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf'
            target.write_bytes(b'x')
            search_rows = {
                'success': True,
                'models': [
                    {
                        'id': 'wimmmm/poolside-Laguna-S-2.1-DFlash-GGUF',
                        'title': 'poolside-Laguna-S-2.1-DFlash-GGUF',
                        'author': 'wimmmm',
                        'size_gb': 0.61,
                        'size_label': '0.61 GB',
                        'accelerator_only': True,
                    },
                    {
                        'id': 'Alittlehammmer/Qwen3.6-27B-DFlash-GGUF-llama.cpp',
                        'title': 'Qwen3.6-27B-DFlash-GGUF-llama.cpp',
                        'author': 'Alittlehammmer',
                        'size_gb': 0.96,
                        'size_label': '0.96 GB',
                        'accelerator_only': True,
                    },
                ],
            }
            with patch('core.stack_match.find_local_accelerators', return_value=[]), patch(
                'core.huggingface.search_models',
                return_value=search_rows,
            ):
                result = match_stack_for_target(target, cfg={'servers': []})

        self.assertEqual(
            [row['id'] for row in result['hf_suggestions']],
            ['wimmmm/poolside-Laguna-S-2.1-DFlash-GGUF'],
        )

    def test_custom_stack_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'target.gguf'
            draft = root / 'target-DFlash-F16.gguf'
            target.write_bytes(b'x')
            draft.write_bytes(b'x')
            stack = resolve_model_stack({
                'profile': 'qwen-dflash',
                'model_id': 'custom-target',
                'target_path': str(target),
                'draft_path': str(draft),
            })
            roles = [row['role'] for row in stack]
            self.assertIn('target', roles)
            self.assertIn('draft-dflash', roles)
            target_row = next(row for row in stack if row['role'] == 'target')
            self.assertEqual(target_row['path'], str(target))

    def test_target_candidate_filters(self):
        self.assertFalse(is_target_candidate('translategemma-12b-it.Q4_K_S.gguf'))
        self.assertFalse(is_target_candidate('translategemma-12b-it.mmproj-f16.gguf'))
        self.assertTrue(is_target_candidate('Qwen3.5-27B-Q4_K_M.gguf'))

    def test_viable_stack_pair_rejects_cross_family(self):
        target = 'ornith-1.0-35b-Q4_K_M.gguf'
        accel = 'Qwen3.6-35B-A3B-DFlash-BF16.gguf'
        score = score_accelerator_pair(target, accel)
        self.assertFalse(is_viable_stack_pair(target, accel, score))

    def test_viable_stack_pair_accepts_qwen_pair(self):
        target = 'Qwen3.5-27B-Q4_K_M.gguf'
        accel = 'Qwen3.5-27B-DFlash-F16.gguf'
        score = score_accelerator_pair(target, accel)
        self.assertTrue(is_viable_stack_pair(target, accel, score))

    def test_unique_server_id(self):
        with tempfile.NamedTemporaryFile(suffix='.gguf') as tmp:
            path = Path(tmp.name)
            cfg = {'servers': [{'id': 'qwen-dflash', 'port': 8090}]}
            suggested = suggest_server_id(path, cfg=cfg)
            self.assertNotEqual(suggested, 'qwen-dflash')

    def test_preflight_explains_missing_accelerator(self):
        with tempfile.NamedTemporaryFile(suffix='-Q4_K_M.gguf') as tmp:
            target = Path(tmp.name)
            with patch('core.stack_match.find_local_accelerators', return_value=[]):
                result = preflight_stack_target(target, cfg={'servers': []})
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason_code'], 'no-accelerator')
        self.assertIn('Download', result['reason'])

    def test_plain_loadable_gguf_can_be_a_capable_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'Qwen3.5-27B-Q4_K_M.gguf'
            draft = root / 'Qwen3.5-27B-DFlash-F16.gguf'
            target.write_bytes(b'x')
            draft.write_bytes(b'x')
            catalog = {
                'models': [{
                    'path': str(target),
                    'filename': target.name,
                    'label': target.name,
                    'loadable': True,
                    'plain_gguf': True,
                }],
            }
            accelerator = [{
                'path': str(draft),
                'filename': draft.name,
                'score': 8.5,
                'size_gb': 1.0,
            }]
            with patch('core.stack_match.list_local_models', return_value=catalog), patch(
                'core.stack_match.find_local_accelerators',
                return_value=accelerator,
            ):
                result = list_capable_targets(cfg={'servers': []})
        self.assertEqual(result['total_count'], 1)
        self.assertEqual(result['targets'][0]['path'], str(target.resolve()))


if __name__ == '__main__':
    unittest.main()
