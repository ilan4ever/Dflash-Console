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
    replace_stack_draft,
    resolve_recommended_generation,
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

    def test_preflight_translategemma_is_not_labeled_accelerator(self):
        with tempfile.NamedTemporaryFile(suffix='translategemma-12b-it.Q4_K_S.gguf', delete=False) as tmp:
            target = Path(tmp.name)
        try:
            result = preflight_stack_target(target)
            self.assertFalse(result['eligible'])
            self.assertEqual(result['reason_code'], 'not-stack-target')
            self.assertNotIn('accelerator', result['reason'].lower())
        finally:
            target.unlink(missing_ok=True)

    def test_engine_profile_without_draft_can_be_a_capable_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'Qwen3.5-27B-Q4_K_M.gguf'
            draft = root / 'Qwen3.5-27B-DFlash-F16.gguf'
            target.write_bytes(b'x')
            draft.write_bytes(b'x')
            catalog = {
                'models': [
                    {
                        'path': str(target),
                        'filename': target.name,
                        'label': 'Qwen3.5-27B-Q4 K M',
                        'loadable': True,
                        'server_id': 'qwen3-5-27b-q4-k-m',
                        'source': 'dflash-profile',
                    },
                    {
                        'path': str(draft),
                        'filename': draft.name,
                        'label': draft.name,
                        'loadable': False,
                    },
                ],
            }
            with patch('core.stack_match.list_local_models', return_value=catalog):
                result = list_capable_targets(cfg={'servers': []})
        self.assertEqual(result['total_count'], 1)
        self.assertEqual(result['targets'][0]['path'], str(target.resolve()))

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


    def test_replace_stack_draft_updates_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'Qwen3.8-27B-Q6_K_L.gguf'
            old_draft = root / 'Qwen3.5-27B-DFlash-F16.gguf'
            new_draft = root / 'Qwen3.8-27B-DFlash-F16.gguf'
            target.write_bytes(b'x')
            old_draft.write_bytes(b'x')
            new_draft.write_bytes(b'x')
            cfg = {
                'servers': [{
                    'id': 'qwen-stack',
                    'label': 'Qwen stack',
                    'profile': 'qwen-dflash',
                    'port': 8096,
                    'model_id': 'qwen3-8-27b-q6-k-l',
                    'target_path': str(target),
                    'draft_path': str(old_draft),
                }],
            }
            from core.stack_match import replace_stack_draft

            with patch('core.config.save_config'), patch('core.model_presets.write_server_preset'):
                result = replace_stack_draft('qwen-stack', new_draft, cfg=cfg)
            self.assertTrue(result['success'])
            self.assertEqual(result['server']['draft_path'], str(new_draft.resolve()))
            self.assertEqual(cfg['servers'][0]['draft_path'], str(new_draft.resolve()))

    def test_match_marks_better_local_accelerator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'Qwen3.8-27B-Q6_K_L.gguf'
            current = root / 'Qwen3.5-27B-DFlash-F16.gguf'
            better = root / 'Qwen3.8-27B-DFlash-F16.gguf'
            target.write_bytes(b'x')
            current.write_bytes(b'x')
            better.write_bytes(b'x')
            catalog = {
                'models': [
                    {'path': str(current), 'filename': current.name, 'label': current.name, 'size_gb': 4.0},
                    {'path': str(better), 'filename': better.name, 'label': better.name, 'size_gb': 4.0},
                ],
            }
            with patch('core.stack_match.list_local_models', return_value=catalog):
                result = match_stack_for_target(
                    target,
                    cfg={'servers': []},
                    current_draft_path=current,
                    dflash_generation='dflash1',
                )

        rows = {Path(row['path']).name: row for row in result['local_accelerators']}
        self.assertTrue(rows['Qwen3.8-27B-DFlash-F16.gguf']['better_than_current'])
        self.assertTrue(result['has_better_local'])

    def test_hf_suggestions_rank_and_recommend_dflash2(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Qwen3.8-27B-Q6_K_L.gguf'
            target.write_bytes(b'x')
            search_rows = {
                'success': True,
                'models': [
                    {
                        'id': 'Akicou/Qwen3.8-27B-DFlash2-GGUF',
                        'title': 'Qwen3.8-27B-DFlash2-GGUF',
                        'author': 'Akicou',
                        'size_label': '1.06 GB',
                        'downloads': 9,
                        'downloads_label': '9',
                        'updated_ago': '2 days ago',
                        'updated_days': 2,
                        'accelerator_only': True,
                        'dflash_generation': 'dflash2',
                    },
                    {
                        'id': 'incoai/Qwen3.8-27B-DFlash2-GGUF',
                        'title': 'Qwen3.8-27B-DFlash2-GGUF',
                        'author': 'incoai',
                        'size_label': '1.06 GB',
                        'downloads': 1400,
                        'downloads_label': '1.4k',
                        'updated_ago': '1 day ago',
                        'updated_days': 1,
                        'accelerator_only': True,
                        'dflash_generation': 'dflash2',
                        'local_loadable': True,
                    },
                    {
                        'id': 'z-lab/Qwen3.8-27B-DFlash2-GGUF',
                        'title': 'Qwen3.8-27B-DFlash2-GGUF',
                        'author': 'z-lab',
                        'size_label': '1.06 GB',
                        'downloads': 1700,
                        'downloads_label': '1.7k',
                        'updated_ago': '14h ago',
                        'updated_days': 0,
                        'accelerator_only': True,
                        'dflash_generation': 'dflash2',
                    },
                ],
            }
            with patch('core.stack_match.find_local_accelerators', return_value=[]), patch(
                'core.huggingface.search_models',
                return_value=search_rows,
            ):
                result = match_stack_for_target(
                    target,
                    cfg={'servers': []},
                    current_draft_path='Qwen3.5-27B-DFlash-F16.gguf',
                    dflash_generation='dflash2',
                )

        self.assertTrue(result['recommended_hf']['is_recommended'])
        self.assertEqual(result['recommended_hf']['id'], 'incoai/Qwen3.8-27B-DFlash2-GGUF')
        self.assertEqual(result['hf_suggestions'][0]['id'], 'incoai/Qwen3.8-27B-DFlash2-GGUF')
        self.assertIn('official DFlash 2 publisher', result['recommended_hf']['recommendation_reasons'])
        self.assertTrue(all(row.get('match_score') for row in result['hf_suggestions']))

    def test_resolve_recommended_generation_prefers_dflash2_for_qwen38(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Qwen3.8-27B-Q6_K_L.gguf'
            target.write_bytes(b'x')

            def fake_fetch(target_path, generation, **kwargs):
                if generation == 'dflash2':
                    return [{
                        'id': 'incoai/Qwen3.8-27B-DFlash2-GGUF',
                        'title': 'Qwen3.8-27B-DFlash2-GGUF',
                        'author': 'incoai',
                        'downloads': 1400,
                        'downloads_label': '1.4k',
                        'accelerator_only': True,
                        'dflash_generation': 'dflash2',
                    }]
                return [{
                    'id': 'mrchuy/Qwen3.8-27B-DFlash-drafter-bootstrap-GGUF',
                    'title': 'Qwen3.8-27B-DFlash-drafter-bootstrap-GGUF',
                    'author': 'mrchuy',
                    'downloads': 900,
                    'downloads_label': '900',
                    'accelerator_only': True,
                    'dflash_generation': 'dflash1',
                }]

            with patch('core.stack_match.find_local_accelerators', return_value=[]), patch(
                'core.stack_match._fetch_hf_suggestion_rows',
                side_effect=fake_fetch,
            ):
                picked = resolve_recommended_generation(target, cfg={'servers': []})

        self.assertEqual(picked['recommended_generation'], 'dflash2')
        self.assertGreater(
            picked['generation_scores']['dflash2'],
            picked['generation_scores']['dflash1'],
        )

    def test_match_auto_uses_recommended_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Qwen3.8-27B-Q6_K_L.gguf'
            target.write_bytes(b'x')
            search_rows = {
                'success': True,
                'models': [{
                    'id': 'incoai/Qwen3.8-27B-DFlash2-GGUF',
                    'title': 'Qwen3.8-27B-DFlash2-GGUF',
                    'author': 'incoai',
                    'size_label': '1.06 GB',
                    'downloads': 1400,
                    'downloads_label': '1.4k',
                    'accelerator_only': True,
                    'dflash_generation': 'dflash2',
                }],
            }

            def fake_fetch(target_path, generation, **kwargs):
                if generation != 'dflash2':
                    return []
                return list(search_rows['models'])

            with patch('core.stack_match.find_local_accelerators', return_value=[]), patch(
                'core.stack_match._fetch_hf_suggestion_rows',
                side_effect=fake_fetch,
            ):
                result = match_stack_for_target(
                    target,
                    cfg={'servers': []},
                    dflash_generation='auto',
                )

        self.assertEqual(result['recommended_generation'], 'dflash2')
        self.assertEqual(result['dflash_generation'], 'dflash2')
        self.assertEqual(result['requested_generation'], 'auto')
        self.assertEqual(result['hf_suggestions'][0]['id'], 'incoai/Qwen3.8-27B-DFlash2-GGUF')


if __name__ == '__main__':
    unittest.main()
