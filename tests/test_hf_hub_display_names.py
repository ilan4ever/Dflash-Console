"""Tests for friendly HF hub model naming."""

from __future__ import annotations

from pathlib import Path

from core.local_models import _faster_whisper_display_name, _guess_params


def test_hf_hub_repo_display_from_snapshot_hash(tmp_path: Path):
    snapshot = (
        tmp_path
        / 'hub'
        / 'models--facebook--nllb-200-distilled-600M'
        / 'snapshots'
        / 'f8d333a098d19b4fd9a8b18f94170487ad3f821d'
    )
    snapshot.mkdir(parents=True)
    display, publisher = _faster_whisper_display_name(snapshot)
    assert display == 'nllb-200-distilled-600M'
    assert publisher == 'facebook'


def test_guess_params_ignores_snapshot_hash():
    assert _guess_params('f8d333a098d19b4fd9a8b18f94170487ad3f821d') == '—'
