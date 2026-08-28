import time

from core.inference_stats import (
    _slot_generation_state,
    fetch_inference_stats,
    mark_inference_end,
    mark_inference_start,
    note_completion_from_live,
    note_completion_stats,
)


def test_slot_generation_state_active():
    generating, tokens = _slot_generation_state({
        'is_processing': True,
        'next_token': [{'has_next_token': True, 'n_decoded': 42}],
    })
    assert generating is True
    assert tokens == 42


def test_slot_generation_state_idle():
    generating, tokens = _slot_generation_state({
        'is_processing': False,
        'next_token': [{'has_next_token': False, 'n_decoded': 505}],
    })
    assert generating is False
    assert tokens == 505


def test_fetch_inference_stats_multi_slot(monkeypatch):
    state = {
        'slots': [
            {
                'id': 0,
                'is_processing': True,
                'n_prompt_tokens': 10,
                'next_token': {'n_decoded': 12},
            },
            {
                'id': 1,
                'is_processing': True,
                'n_prompt_tokens': 20,
                'next_token': {'n_decoded': 40},
            },
        ],
    }

    def fake_fetch(url: str, *, timeout: float = 2.5):
        if '/slots?' in url:
            return state['slots']
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    live = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='multi-slot',
        model_id='demo-model',
    )
    assert len(live['slots']) == 2
    assert all(row['generating'] for row in live['slots'])

    state['slots'][1]['is_processing'] = False
    state['slots'][1]['next_token'] = {'n_decoded': 88}
    stats = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='multi-slot',
        model_id='demo-model',
    )

    assert len(stats['slots']) == 2
    assert stats['slots'][0]['generating'] is True
    assert stats['slots'][0]['generating_tokens'] == 12
    done = next(row for row in stats['slots'] if row['slot_id'] == 1)
    assert done['generation_tokens'] == 88
    assert stats['generating'] is True


def test_fetch_inference_stats_uses_model_slots(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout: float = 2.5):
        calls.append(url)
        if '/slots?' in url:
            return [{
                'is_processing': True,
                'n_ctx': 8192,
                'n_prompt_tokens_processed': 128,
                'next_token': [{'has_next_token': True, 'n_decoded': 17}],
            }]
        if url.endswith('/props'):
            return {'default_generation_settings': {'n_ctx': 8192}}
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    stats = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='gemma-31b-dflash',
        model_id='gemma-4-31b-it-dflash',
    )

    assert any('model=gemma-4-31b-it-dflash' in call for call in calls)
    assert stats['generating'] is True
    assert stats['generating_tokens'] == 17
    assert stats['generating_tokens_per_second'] is None


def test_fetch_inference_stats_ignores_stale_tokens_during_prefill(monkeypatch):
    clock = {'t': 1000.0}
    state = {'processed': 800, 'n_decoded': 419}

    def fake_fetch(url: str, *, timeout: float = 2.5):
        if '/slots?' in url:
            return [{
                'id': 0,
                'is_processing': True,
                'n_prompt_tokens': 4557,
                'n_prompt_tokens_processed': state['processed'],
                'next_token': [{'has_next_token': True, 'n_decoded': state['n_decoded']}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    monkeypatch.setattr('core.inference_stats.time.time', lambda: clock['t'])

    first = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stale-prefill',
        model_id='demo-model',
    )
    assert first['generating'] is True
    assert first['generating_tokens'] == 0
    assert first.get('generating_tokens_per_second') is None

    clock['t'] += 40.0
    state['processed'] = 4000
    second = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stale-prefill',
        model_id='demo-model',
    )
    assert second['generating_tokens'] == 0
    assert second.get('generating_tokens_per_second') is None

    clock['t'] += 0.25
    state['processed'] = 4557
    state['n_decoded'] = 439
    third = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stale-prefill',
        model_id='demo-model',
    )
    assert third['generating_tokens'] == 439
    assert third.get('generating_tokens_per_second') is None

    clock['t'] += 0.25
    state['n_decoded'] = 456
    fourth = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stale-prefill',
        model_id='demo-model',
    )
    assert fourth['generating_tokens_per_second'] is not None
    assert fourth['generating_tokens_per_second'] > 40


def test_fetch_inference_stats_tracks_live_tps(monkeypatch):
    state = {'n_decoded': 10}

    def fake_fetch(url: str, *, timeout: float = 2.5):
        if '/slots?' in url:
            return [{
                'is_processing': True,
                'next_token': [{'has_next_token': True, 'n_decoded': state['n_decoded']}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    first = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='live-test',
        model_id='demo-model',
    )
    assert first['generating_tokens'] == 10

    time.sleep(0.25)
    state['n_decoded'] = 18
    second = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='live-test',
        model_id='demo-model',
    )
    assert second['generating_tokens'] == 18
    assert second['generating_tokens_per_second'] is not None
    assert second['generating_tokens_per_second'] > 0


def test_slot_generation_state_stale_has_next_not_generating():
    generating, tokens = _slot_generation_state({
        'is_processing': False,
        'next_token': [{'has_next_token': True, 'n_decoded': 123}],
    })
    assert generating is False
    assert tokens == 123


def test_fetch_inference_stats_idle_clears_live(monkeypatch):
    monkeypatch.setattr(
        'core.inference_stats._STATS_CACHE',
        {
            'idle-test': {
                'generating': True,
                'generating_tokens': 116,
                'generating_tokens_per_second': 0.1,
                'live_updated_at': time.time() - 5,
            },
        },
    )

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'is_processing': False,
                'next_token': [{'has_next_token': False, 'n_decoded': 116}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    stats = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='idle-test',
        model_id='demo-model',
    )
    assert stats['generating'] is False
    assert stats['generating_tokens'] is None
    assert stats['generating_tokens_per_second'] is None


def test_wants_stream_true():
    note_completion_from_live('srv-a', generation_tokens=40, prompt_tokens=10, tokens_per_second=30.0)
    note_completion_from_live('srv-a', generation_tokens=40, prompt_tokens=10, tokens_per_second=30.0)
    from core.inference_stats import _LAST_COMPLETION
    assert _LAST_COMPLETION['srv-a']['generation_tokens'] == 40


def test_fetch_records_last_after_idle_slot(monkeypatch):
    state = {'processing': True, 'n_decoded': 0}

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'is_processing': state['processing'],
                'n_prompt_tokens': 22,
                'next_token': [{'n_decoded': state['n_decoded']}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='last-test', model_id='demo')
    state['processing'] = True
    state['n_decoded'] = 55
    fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='last-test', model_id='demo')
    state['processing'] = False
    stats = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='last-test', model_id='demo')
    assert stats['generating'] is False
    assert stats['generation_tokens'] == 55
    assert stats['prompt_tokens'] == 22


def test_new_inference_wave_clears_stale_last_slots(monkeypatch):
    from core.inference_stats import (
        _LAST_COMPLETION_SLOTS,
        _PREV_ANY_GENERATING,
        _SLOT_WAVE_EPOCH,
        _WAVE_EPOCH,
        note_completion_from_live,
    )

    _PREV_ANY_GENERATING.clear()
    _LAST_COMPLETION_SLOTS.clear()
    _SLOT_WAVE_EPOCH.clear()
    _WAVE_EPOCH['wave-test'] = 1
    _SLOT_WAVE_EPOCH['wave-test'] = {2: 1, 3: 1}
    _PREV_ANY_GENERATING['wave-test'] = False
    note_completion_from_live('wave-test', generation_tokens=50, prompt_tokens=100, slot_id=2)
    note_completion_from_live('wave-test', generation_tokens=60, prompt_tokens=100, slot_id=3)

    phase = {'mode': 'idle_dual_last'}

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' not in url:
            return {}
        if phase['mode'] == 'idle_dual_last':
            return [
                {'id': 2, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 50}]},
                {'id': 3, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 60}]},
            ]
        if phase['mode'] == 'single_generating':
            return [
                {'id': 0, 'is_processing': True, 'n_prompt_tokens': 20, 'next_token': [{'n_decoded': 5}]},
                {'id': 2, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 50}]},
                {'id': 3, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 60}]},
            ]
        return [
            {'id': 0, 'is_processing': False, 'n_prompt_tokens': 20, 'next_token': [{'n_decoded': 40}]},
        ]

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    idle = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='wave-test', model_id='demo')
    assert len(idle['slots']) == 2
    assert {row['slot_id'] for row in idle['slots']} == {2, 3}

    phase['mode'] = 'single_generating'
    live = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='wave-test', model_id='demo')
    assert len(live['slots']) == 1
    assert live['slots'][0]['slot_id'] == 0
    assert live['slots'][0]['generating'] is True

    phase['mode'] = 'single_done'
    done = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='wave-test', model_id='demo')
    assert len(done['slots']) == 1
    assert done['slots'][0]['generation_tokens'] == 40


def test_new_wave_clears_stale_slot_prev_generating(monkeypatch):
    from core.inference_stats import _PREV_ANY_GENERATING, _SLOT_PREV_GENERATING

    _PREV_ANY_GENERATING.clear()
    _SLOT_PREV_GENERATING.clear()

    phase = {'mode': 'slot3_was_generating'}

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' not in url:
            return {}
        if phase['mode'] == 'slot3_was_generating':
            return [
                {'id': 2, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 50}]},
                {'id': 3, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 60}]},
            ]
        if phase['mode'] == 'single_generating':
            return [
                {'id': 0, 'is_processing': True, 'n_prompt_tokens': 20, 'next_token': [{'n_decoded': 5}]},
                {'id': 3, 'is_processing': False, 'n_prompt_tokens': 100, 'next_token': [{'n_decoded': 60}]},
            ]
        return [
            {'id': 0, 'is_processing': False, 'n_prompt_tokens': 20, 'next_token': [{'n_decoded': 40}]},
        ]

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    bootstrap = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='prev-test', model_id='demo')
    _SLOT_PREV_GENERATING['prev-test'] = {3: True}
    _PREV_ANY_GENERATING['prev-test'] = False
    assert len(bootstrap['slots']) == 0

    phase['mode'] = 'single_generating'
    live = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='prev-test', model_id='demo')
    assert len(live['slots']) == 1
    assert live['slots'][0]['slot_id'] == 0

    phase['mode'] = 'single_done'
    done = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='prev-test', model_id='demo')
    assert len(done['slots']) == 1
    assert done['slots'][0]['generation_tokens'] == 40


def test_parallel_wave_keeps_last_until_all_idle(monkeypatch):
    from core.inference_stats import _PREV_ANY_GENERATING

    _PREV_ANY_GENERATING.clear()

    slots_state = [
        {'id': 2, 'is_processing': True, 'n_prompt_tokens': 10, 'next_token': [{'n_decoded': 12}]},
        {'id': 3, 'is_processing': True, 'n_prompt_tokens': 10, 'next_token': [{'n_decoded': 9}]},
    ]

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [dict(row) for row in slots_state]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    first = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='parallel-wave', model_id='demo')
    assert len(first['slots']) == 2
    assert all(row['generating'] for row in first['slots'])

    slots_state[0]['is_processing'] = False
    slots_state[0]['next_token'] = [{'n_decoded': 50}]
    mid = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='parallel-wave', model_id='demo')
    assert len(mid['slots']) == 2
    done_slot = next(row for row in mid['slots'] if row['slot_id'] == 2)
    live_slot = next(row for row in mid['slots'] if row['slot_id'] == 3)
    assert done_slot['generating'] is False
    assert done_slot['generation_tokens'] == 50
    assert live_slot['generating'] is True

    slots_state[1]['is_processing'] = False
    slots_state[1]['next_token'] = [{'n_decoded': 60}]
    done = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='parallel-wave', model_id='demo')
    assert len(done['slots']) == 2
    assert done['slots'][1]['generation_tokens'] == 60


def test_note_completion_kept_while_generating(monkeypatch):
    from core.inference_stats import _PREV_ANY_GENERATING

    _PREV_ANY_GENERATING.clear()
    note_completion_stats('srv-1', {
        'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20},
        'timings': {'predicted_per_second': 25.5},
    })

    def fake_fetch(url: str, *, timeout: float = 2.5):
        if '/slots?' in url:
            return [{
                'is_processing': True,
                'next_token': [{'has_next_token': True, 'n_decoded': 3}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    mark_inference_start('srv-1')
    stats = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='srv-1', model_id='demo')
    mark_inference_end('srv-1')

    assert stats['prompt_tokens'] is None
    assert stats['generation_tokens'] is None
    assert stats['generating'] is True
    assert stats['generating_tokens'] == 3


def test_fresh_proxy_start_clears_previous_last(monkeypatch):
    from core.inference_stats import _PREV_ANY_GENERATING, _SLOT_WAVE_EPOCH, _WAVE_EPOCH, note_completion_from_live

    _PREV_ANY_GENERATING.clear()
    _WAVE_EPOCH['fresh-test'] = 1
    _SLOT_WAVE_EPOCH['fresh-test'] = {2: 1, 3: 1}
    note_completion_from_live('fresh-test', generation_tokens=50, prompt_tokens=10, slot_id=2)
    note_completion_from_live('fresh-test', generation_tokens=60, prompt_tokens=10, slot_id=3)
    _PREV_ANY_GENERATING['fresh-test'] = False

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{'id': 0, 'is_processing': True, 'n_prompt_tokens': 8, 'next_token': [{'n_decoded': 4}]}]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    mark_inference_start('fresh-test')
    stats = fetch_inference_stats('http://127.0.0.1:8090/v1', server_id='fresh-test', model_id='demo')
    mark_inference_end('fresh-test')

    assert len(stats['slots']) == 1
    assert stats['slots'][0]['slot_id'] == 0


def test_decode_tokens_per_second_ignores_prefill():
    import core.inference_stats as stats_mod

    track = {
        'started_at': time.time() - 40.0,
        'decode_started_at': time.time() - 10.0,
        'decode_base_tokens': 0,
    }
    tps = stats_mod._decode_tokens_per_second(track, 500)
    assert tps is not None
    assert tps >= 45.0


def test_fetch_inference_stats_counts_cached_prompt_as_prefill_done(monkeypatch):
    clock = {'t': 2000.0}

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'is_processing': True,
                'n_prompt_tokens': 622,
                'n_prompt_tokens_processed': 513,
                'n_prompt_tokens_cache': 109,
                'next_token': [{'has_next_token': False, 'n_decoded': 216}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    monkeypatch.setattr('core.inference_stats.time.time', lambda: clock['t'])

    first = fetch_inference_stats(
        'http://127.0.0.1:8095/v1',
        server_id='gemma-cached-prefill',
        model_id='gemma-4-31b-q4-0-it',
    )
    assert first['generating'] is True
    assert first['generating_tokens'] == 216
    assert first.get('generating_tokens_per_second') is None

    clock['t'] += 0.4
    second = fetch_inference_stats(
        'http://127.0.0.1:8095/v1',
        server_id='gemma-cached-prefill',
        model_id='gemma-4-31b-q4-0-it',
    )
    # Same snapshot — speed stays unset until decoded tokens increase.
    assert second['generating_tokens'] == 216


def test_fetch_inference_stats_shows_zero_out_during_prefill(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'is_processing': True,
                'n_prompt_tokens_processed': 12000,
                'next_token': [{'has_next_token': True, 'n_decoded': 0}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)

    stats = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='prefill-test',
        model_id='demo-model',
    )

    assert stats['generating'] is True
    assert stats['generating_tokens'] == 0
    assert stats.get('prefill_tokens') == 12000
    assert stats.get('generating_tokens_per_second') is None


def test_fetch_inference_stats_shows_live_decode_during_open_prefill(monkeypatch):
    """Reasoning/streaming prompts can grow n_prompt_tokens while n_decoded advances."""
    clock = {'t': 3000.0}
    state = {'n_decoded': 2242, 'n_prompt_tokens': 5521}

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'id': 0,
                'is_processing': True,
                'n_prompt_tokens': state['n_prompt_tokens'],
                'n_prompt_tokens_processed': 3010,
                'n_prompt_tokens_cache': 270,
                'next_token': [{'has_next_token': True, 'n_decoded': state['n_decoded']}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    monkeypatch.setattr('core.inference_stats.time.time', lambda: clock['t'])

    first = fetch_inference_stats(
        'http://127.0.0.1:8095/v1',
        server_id='gemma-live-decode',
        model_id='gemma-4-31b-q4-0-it',
    )
    assert first['generating'] is True
    assert first['generating_tokens'] == 0

    clock['t'] += 0.35
    state['n_decoded'] = 2254
    state['n_prompt_tokens'] = 5533
    second = fetch_inference_stats(
        'http://127.0.0.1:8095/v1',
        server_id='gemma-live-decode',
        model_id='gemma-4-31b-q4-0-it',
    )
    assert second['generating_tokens'] == 2254

    clock['t'] += 0.35
    state['n_decoded'] = 2267
    third = fetch_inference_stats(
        'http://127.0.0.1:8095/v1',
        server_id='gemma-live-decode',
        model_id='gemma-4-31b-q4-0-it',
    )
    assert third['generating_tokens'] == 2267
    assert third.get('generating_tokens_per_second') is not None
    assert third['generating_tokens_per_second'] > 0


def test_slot_completion_prefers_api_timings(monkeypatch):
    note_completion_stats(
        'timing-test',
        {
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 200, 'total_tokens': 1200},
            'timings': {'predicted_per_second': 52.3},
        },
    )

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'is_processing': False,
                'n_prompt_tokens': 1000,
                'next_token': [{'n_decoded': 200}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    monkeypatch.setattr('core.inference_stats._SLOT_PREV_GENERATING', {'timing-test': {0: True}})

    stats = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='timing-test',
        model_id='demo-model',
    )
    assert stats['generation_tokens'] == 200
    assert stats['tokens_per_second'] == 52.3


def test_fetch_inference_stats_retains_live_decode_rate_on_unchanged_sample(monkeypatch):
    clock = {'t': 4000.0}
    state = {'n_decoded': 10}

    def fake_fetch(url: str, *, timeout: float = 0.9):
        if '/slots?' in url:
            return [{
                'id': 0,
                'is_processing': True,
                'next_token': [{'has_next_token': True, 'n_decoded': state['n_decoded']}],
            }]
        return {}

    monkeypatch.setattr('core.inference_stats._fetch_json', fake_fetch)
    monkeypatch.setattr('core.inference_stats.time.time', lambda: clock['t'])

    fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stable-live-rate',
        model_id='demo-model',
    )
    clock['t'] += 0.25
    state['n_decoded'] = 18
    moving = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stable-live-rate',
        model_id='demo-model',
    )
    clock['t'] += 0.25
    steady = fetch_inference_stats(
        'http://127.0.0.1:8090/v1',
        server_id='stable-live-rate',
        model_id='demo-model',
    )

    assert moving['generating_tokens_per_second'] == 32.0
    assert steady['generating_tokens_per_second'] == 32.0
