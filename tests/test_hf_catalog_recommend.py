from __future__ import annotations

from core.hf_catalog_recommend import apply_catalog_recommendations, family_key


def test_family_key_collapses_gguf_and_publisher_prefix():
    assert family_key({'id': 'google/gemma-4-E2B-it'}) == family_key(
        {'id': 'unsloth/gemma-4-E2B-it-GGUF'},
    )
    assert family_key({'id': 'bartowski/google_gemma-4-E2B-it-GGUF'}) == family_key(
        {'id': 'google/gemma-4-E2B-it'},
    )


def test_recommends_official_full_and_gguf_for_this_pc():
    rows = apply_catalog_recommendations([
        {
            'id': 'HauhauCS/Gemma-4-E2B-Uncensored-HauhauCS-Aggressive',
            'author': 'HauhauCS',
            'downloads': 50_000,
            'has_gguf': True,
            'fits_machine': True,
            'runnable': True,
            'tags': ['gguf'],
        },
        {
            'id': 'unsloth/gemma-4-E2B-it-GGUF',
            'author': 'unsloth',
            'downloads': 400_000,
            'has_gguf': True,
            'fits_machine': True,
            'runnable': True,
            'tags': ['gguf'],
        },
        {
            'id': 'google/gemma-4-E2B-it',
            'author': 'google',
            'downloads': 3_200_000,
            'has_gguf': False,
            'fits_machine': True,
            'runnable': True,
            'tags': ['transformers', 'safetensors'],
        },
        {
            'id': 'google/gemma-4-31B-it',
            'author': 'google',
            'downloads': 9_000_000,
            'has_gguf': False,
            'fits_machine': False,
            'runnable': False,
            'tags': ['transformers'],
        },
        {
            'id': 'some/tiny-other',
            'author': 'some',
            'downloads': 10,
            'has_gguf': True,
            'fits_machine': True,
            'runnable': True,
            'tags': ['gguf'],
        },
    ])
    recommended = [row for row in rows if row.get('catalog_recommended')]
    assert [row['catalog_recommended_rank'] for row in recommended] == [1, 2]
    assert rows[0]['id'] == 'google/gemma-4-E2B-it'
    assert rows[1]['id'] == 'unsloth/gemma-4-E2B-it-GGUF'
    assert 'google/gemma-4-31B-it' not in {row['id'] for row in recommended}
    assert all(row.get('fits_machine') for row in recommended)


def test_does_not_recommend_three_copies_of_the_same_gguf():
    rows = apply_catalog_recommendations([
        {
            'id': 'unsloth/gemma-4-E2B-it-GGUF',
            'author': 'unsloth',
            'downloads': 400_000,
            'has_gguf': True,
            'fits_machine': True,
            'runnable': True,
            'tags': ['gguf'],
        },
        {
            'id': 'bartowski/google_gemma-4-E2B-it-GGUF',
            'author': 'bartowski',
            'downloads': 200_000,
            'has_gguf': True,
            'fits_machine': True,
            'runnable': True,
            'tags': ['gguf'],
        },
        {
            'id': 'lmstudio-community/gemma-4-E2B-it-GGUF',
            'author': 'lmstudio-community',
            'downloads': 150_000,
            'has_gguf': True,
            'fits_machine': True,
            'runnable': True,
            'tags': ['gguf'],
        },
    ])
    recommended = [row for row in rows if row.get('catalog_recommended')]
    assert len(recommended) == 1
    assert recommended[0]['id'] == 'unsloth/gemma-4-E2B-it-GGUF'
