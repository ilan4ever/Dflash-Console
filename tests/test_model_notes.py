"""Personal model notes: stable keys, user overrides, starter notes."""

from __future__ import annotations

from core import model_notes as notes


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(notes, '_NOTES_PATH', tmp_path / 'model-notes.json')


def test_note_key_prefers_hf_repo_then_path(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    file_row = {
        'hf_repo': 'Wan-AI/Wan2.2-TI2V-5B',
        'path': str(tmp_path / 'somewhere' / 'model.safetensors'),
    }
    assert notes.note_key(file_row) == 'hf:wan-ai/wan2.2-ti2v-5b'

    external = {'path': str(tmp_path / 'LM Studio Models' / 'org' / 'file.gguf')}
    key = notes.note_key(external)
    assert key.startswith('path:')
    assert key == key.lower()
    # Same file scanned again (different separators) yields the same key.
    assert notes.note_key({'path': str(tmp_path / 'LM Studio Models' / 'org' / 'file.gguf')}) == key


def test_seed_note_is_used_until_the_user_edits(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    row = {'id': 'glm-ocr', 'path': str(tmp_path / 'zai-org' / 'GLM-OCR')}

    seed = notes.note_for(row)
    assert seed['note_source'] == 'seed'
    assert 'OCR' in seed['note']

    notes.set_note(seed['note_key'], 'My own note about OCR.')
    assert notes.note_for(row)['note_source'] == 'user'
    assert notes.note_for(row)['note'] == 'My own note about OCR.'


def test_empty_user_note_hides_the_seed(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    row = {'id': 'glm-ocr', 'path': str(tmp_path / 'zai-org' / 'GLM-OCR')}
    key = notes.note_key(row)

    notes.set_note(key, '   ')
    resolved = notes.note_for(row)
    assert resolved['note'] == ''
    assert resolved['note_source'] == 'user'


def test_delete_restores_the_seed(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    row = {'id': 'glm-ocr', 'path': str(tmp_path / 'zai-org' / 'GLM-OCR')}
    key = notes.note_key(row)

    notes.set_note(key, 'temporary')
    assert notes.note_for(row)['note_source'] == 'user'
    notes.delete_note(key)
    assert notes.note_for(row)['note_source'] == 'seed'


def test_notes_are_capped_and_persisted(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    key = 'path:x'
    notes.set_note(key, 'a' * (notes._MAX_NOTE_CHARS + 500))
    stored = notes.load_notes()[key]['text']
    assert len(stored) == notes._MAX_NOTE_CHARS
    assert notes._notes_path().is_file()


def test_attach_notes_marks_source(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    rows = [
        {'id': 'wan2.2-ti2v-5b', 'path': str(tmp_path / 'Wan-AI' / 'Wan2.2-TI2V-5B')},
        {'id': 'unknown-model', 'path': str(tmp_path / 'x' / 'unknown-model')},
    ]
    notes.attach_notes(rows)
    assert rows[0]['note_source'] == 'seed'
    assert 'diffusion' in rows[0]['note']
    assert rows[1]['note'] == ''
    assert rows[1]['note_source'] == ''
    assert rows[0]['note_key'].startswith('path:')
