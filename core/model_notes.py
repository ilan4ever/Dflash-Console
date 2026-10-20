"""Personal per-model notes (plain text or Markdown).

Notes are the user's own context - what a checkpoint is for, which stack it
belongs to, which prompt style works. They live in ``model-notes.json`` in the
Console data root so they survive updates, and are keyed by a stable model
identity (HF repo first, then the file/folder path) so they also work for
external models the Console did not download itself.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.config import ROOT

logger = logging.getLogger(__name__)

_NOTES_PATH = ROOT / 'model-notes.json'
_MAX_NOTE_CHARS = 20000
_NOTE_FIELDS = ('note', 'note_key', 'note_source', 'note_updated_at')

_lock = threading.Lock()

# Starter notes for models that are commonly installed. Keyed by "name:<stem>"
# or "hf:<org/name>" (lowercase). Any user note overrides a seed, and deleting
# a user note brings the seed back.
_SEED_NOTES: dict[str, str] = {
    # --- Speech / TTS / STT ---------------------------------------------------
    'name:faster-whisper-small-en': (
        'Systran faster-whisper small.en - the Console STT default for English '
        '(CTranslate2, GPU float16). Fast enough for live dictation; use '
        'large-v3 when accuracy matters more than latency.'
    ),
    'name:faster-whisper-base-en': (
        'Small English faster-whisper model used by OneVoice Speak. Good fallback '
        'when GPU memory is tight; slightly weaker on accents than small.en.'
    ),
    'name:faster-whisper-medium-en': (
        'English faster-whisper medium - middle ground between small.en speed and '
        'large-v3 accuracy. Pick this for long English recordings.'
    ),
    'name:faster-whisper-large-v2': (
        'Multilingual faster-whisper large-v2 (OneVoice copy). Older than v3 but '
        'stable; keep for languages where v3 over-normalises text.'
    ),
    'name:faster-whisper-large-v3': (
        'Multilingual faster-whisper large-v3 (OneVoice copy). Best accuracy for '
        'non-English speech, but slower and heavier than small.en.'
    ),
    'name:faster-whisper-large-v3-turbo-ct2': (
        'Turbo (distilled) large-v3 CTranslate2 build - near large-v3 quality at '
        'much higher speed. Good default for batch transcription on the 4090.'
    ),
    'name:faster-whisper-tiny': (
        'Tiny faster-whisper model - only for quick tests or CPU fallback; expect '
        'mistakes on noisy audio.'
    ),
    'name:parakeet-redux': (
        'moondream/parakeet-redux STT model (NeMo/parakeet family). Alternative '
        'English transcriber to the faster-whisper family; compare before '
        'switching a workflow over.'
    ),
    'name:wav2vec2-xlsr-53-emotion-v1-crosslingual': (
        'Aniemore wav2vec2 cross-lingual emotion classifier used alongside '
        'transcription to label speaker emotion. The fp8/int4/int8 folders are '
        'precision variants of this same model.'
    ),
    'name:fp8': (
        'FP8 precision variant of the Aniemore wav2vec2 emotion model '
        '(subfolder of wav2vec2-xlsr-53-emotion-v1-crosslingual).'
    ),
    'name:int4': (
        'INT4 precision variant of the Aniemore wav2vec2 emotion model - the '
        'smallest one; use when the full/FP8 weights do not fit.'
    ),
    'name:int8': (
        'INT8 precision variant of the Aniemore wav2vec2 emotion model.'
    ),
    'name:breeze-tts-2': (
        'BreezeBlue Breeze-TTS-2 speech synthesis model. Needs the sibling '
        "'audio-tokenizer' folder - keep both together when moving it."
    ),
    'name:audio-tokenizer': (
        'Audio tokenizer component of Breeze-TTS-2. Not a standalone model; '
        'deleting it breaks the TTS runtime.'
    ),
    'name:vibevoice-realtime-0.5b': (
        'microsoft/VibeVoice-Realtime-0.5B TTS with voice presets. Low latency, '
        'good for short spoken answers in the Playground.'
    ),
    # --- OCR / translation ----------------------------------------------------
    'name:glm-ocr': (
        'zai-org/GLM-OCR - OCR engine for scanned documents, screenshots and '
        'mixed-language PDFs. Default OCR choice in this Console.'
    ),
    'name:teleocr': (
        'XingChen-AGI/TeleOCR - dense-text OCR model. Keep as a second OCR option '
        'for small print or handwriting where GLM-OCR struggles.'
    ),
    'name:chandra-ocr-q4-k-s': (
        'Datalab Chandra-OCR (Q4_K_S GGUF) - document OCR with layout awareness, '
        'served by llama-server. Slower than GLM-OCR but stronger on tables.'
    ),
    'name:lightonocr-1b-1025-q8-0': (
        'LightOn LightOnOCR-1B-1025 (Q8_0) - lightweight document OCR, fast on the '
        '4090; good for clean scans and receipts.'
    ),
    'name:indictrans2-indic-en-1b': (
        'AI4Bharat IndicTrans2 1B (Indic -> English). Use for Hindi/Indic source '
        'text before feeding an English-only model.'
    ),
    'name:translategemma-12b-it.q4-k-s': (
        'Google TranslateGemma 12B (Q4_K_S) - translation quality tier; heavier '
        'and slower than the 4B variant.'
    ),
    'name:translategemma-4b-it.q4-k-s': (
        'Google TranslateGemma 4B (Q4_K_S) - fast translation for routine text; '
        'fall back to the 12B variant for nuance.'
    ),
    # --- Embeddings -----------------------------------------------------------
    'name:nomic-embed-text-v1-5-q8-0': (
        'Nomic-embed-text v1.5 (Q8_0 GGUF) - embeddings for retrieval/RAG. Served '
        'by the embedding engine, not a chat model.'
    ),
    # --- Tiny HF-format helpers ----------------------------------------------
    'name:qwen2.5-0.5b': (
        'Qwen2.5 0.5B base weights (HF format) - smoke-test the transformers '
        'runtime with something tiny before loading a real target.'
    ),
    'name:qwen2.5-0.5b-instruct': (
        'Qwen2.5 0.5B Instruct (HF format) - tiny instruct model for quick '
        'pipeline tests; not meant for real answers.'
    ),
    'name:qwen3-0.6b': (
        'Qwen3 0.6B (HF format) - minimal Qwen3 checkpoint used to validate the '
        'vLLM/transformers paths.'
    ),
    'name:qwen-draft-hf': (
        'Draft/accelerator checkpoint folder used to pair speculative decoding '
        'with Qwen targets. Stack-only - do not load it as a target.'
    ),
    'name:qwen35-target-meta': (
        'Config/metadata folder for a Qwen3.5 target stack (no weights of its '
        'own). Kept so the stack wizard can resolve the pair.'
    ),
    'name:text-encoder': (
        'Qwen-Image-2.1 text encoder component - part of the image pipeline, not '
        'a standalone LLM.'
    ),
    'name:wan2.2-ti2v-5b': (
        'Wan-AI Wan2.2-TI2V-5B text/image-to-video diffusion model. The folder '
        'needs all three transformer shards plus the umt5-xxl text encoder (.pth) '
        'and Wan2.2 VAE - it runs through a diffusers pipeline, not llama.cpp.'
    ),
    # --- GGUF targets and accelerators ---------------------------------------
    'name:qwen3-8-27b-q6-k-l-dflash': (
        'Qwen3.8 27B (bartowski) Q6_K_L as the stack target with a DFlash '
        'accelerator. Best quality/latency balance on the 4090; ~22 GB VRAM.'
    ),
    'name:qwen3-8-27b-gsq-rco-iq3-xxs-mtp-dflash': (
        'Qwen3.8 27B GSQ-RCO IQ3_XXS - heavily quantised target for tight VRAM. '
        'Use when Q6 does not fit alongside the draft model.'
    ),
    'name:qwen3-8-27b-uncensored-ymq-xxs-no-mtp-dflash': (
        'Qwen3.8 27B Uncensored (YMQ XXS, no MTP) - permissive variant for '
        'unfiltered drafts; expect lower reasoning quality than the stock target.'
    ),
    'name:qwen3.8-27b-dflash2-q4-k-m': (
        'Qwen3.8 27B DFlash2 Q4_K_M accelerator build - the DFlash 2 generation '
        'draft; pair it only with a matching DFlash2 target.'
    ),
    'name:qwen3.5-27b-dflash-f16': (
        'Qwen3.5 27B DFlash accelerator in F16 - highest-fidelity draft for the '
        'Qwen3.5 27B stack; costs more VRAM than the quantised variants.'
    ),
    'name:qwen3.6-35b-a3b-dflash-bf16': (
        'Qwen3.6 35B-A3B MoE (BF16) DFlash accelerator - large but sparse; only '
        '~3B parameters active per token.'
    ),
    'name:laguna-s-2.1-dflash-q4-k-m': (
        'Poolside Laguna-S 2.1 Q4_K_M draft accelerator - niche stack; keep it '
        'paired with its own target profile.'
    ),
    'name:gemma-4-12b-it-dflash-f16': (
        'Gemma 4 12B DFlash accelerator (F16) - draft model for the Gemma 4 12B '
        'stack.'
    ),
    'name:gemma-4-31b-it-dflash-q4-k-m': (
        'Gemma 4 31B DFlash accelerator (Q4_K_M) - draft for the 31B Gemma stack.'
    ),
    'name:gemma-31b-it-quant-dflash': (
        'CompressedGemma Gemma 4 31B compressed target - 4-bit compressed weights '
        'that keep quality while fitting 24 GB.'
    ),
    'name:gemma-4-12b-it-q4-k-m-dflash': (
        'Gemma 4 12B IT (Q4_K_M) as a DFlash stack target - fast, good default '
        'for everyday chat on this machine.'
    ),
    'name:gemma-4-31b-q4-0-it-dflash': (
        'Gemma 4 31B IT QAT Q4_0 target - near-lossless QAT quant; slower than the '
        '12B but noticeably stronger reasoning.'
    ),
    'name:gemma-4-e2b-q4-0-it': (
        'Gemma 4 E2B IT QAT Q4_0 - tiny Gemma for quick checks and tooling tests.'
    ),
    'name:gemma-4-e4b-q4-0-it': (
        'Gemma 4 E4B IT QAT Q4_0 - small Gemma target; use for drafts before '
        'committing VRAM to a 31B stack.'
    ),
    'name:gemma-4-e4b-it-mmproj': (
        'Vision projector (mmproj) paired with Gemma 4 E4B - required for image '
        'input; useless on its own.'
    ),
    'name:qwen3vl-8b-heretic-q4-k-m': (
        'Qwen3-VL 8B Heretic Q4_K_M - vision-language target for image questions '
        'and OCR-style tasks. Reminder: lightweight, not a document OCR engine.'
    ),
    'name:underdog-saluki-27b-1-0-iq2-mix': (
        'ConwayResearch Underdog-Saluki 27B IQ2 mix - aggressive quantisation to '
        'fit 27B into small VRAM; quality drops noticeably on long prompts.'
    ),
}

_HF_SEED_NOTES: dict[str, str] = {
    'hf:wan-ai/wan2.2-ti2v-5b': _SEED_NOTES['name:wan2.2-ti2v-5b'],
    'hf:microsoft/vibevoice-realtime-0.5b': _SEED_NOTES['name:vibevoice-realtime-0.5b'],
    'hf:zai-org/glm-ocr': _SEED_NOTES['name:glm-ocr'],
    'hf:xingchen-agi/teleocr': _SEED_NOTES['name:teleocr'],
    'hf:moondream/parakeet-redux': _SEED_NOTES['name:parakeet-redux'],
    'hf:aniemore/wav2vec2-xlsr-53-emotion-v1-crosslingual': (
        _SEED_NOTES['name:wav2vec2-xlsr-53-emotion-v1-crosslingual']
    ),
    'hf:ai4bharat/indictrans2-indic-en-1b': _SEED_NOTES['name:indictrans2-indic-en-1b'],
}

_FILE_EXTENSIONS = (
    '.gguf', '.safetensors', '.bin', '.pt', '.pth', '.ckpt', '.onnx', '.h5', '.msgpack',
)


def _notes_path() -> Path:
    return _NOTES_PATH


def _normalize_text(text: Any) -> str:
    value = str(text or '').replace('\r\n', '\n').strip()
    if len(value) > _MAX_NOTE_CHARS:
        value = value[:_MAX_NOTE_CHARS]
    return value


def _norm_key(value: Any) -> str:
    return str(value or '').strip().strip('/').replace('\\', '/').lower()


def note_key(row: dict[str, Any] | None) -> str:
    """Stable identity for a catalog row (survives rescans and renames)."""
    row = row or {}
    repo = _norm_key(row.get('hf_repo'))
    if repo and '/' in repo:
        return f'hf:{repo}'
    ollama = _norm_key(row.get('ollama_model'))
    if ollama:
        return f'ollama:{ollama}'
    path = str(row.get('path') or '').strip()
    if path:
        try:
            resolved = str(Path(path).expanduser().resolve())
        except OSError:
            resolved = path
        return f'path:{_norm_key(resolved)}'
    ident = _norm_key(row.get('id') or row.get('label') or row.get('filename'))
    return f'id:{ident}' if ident else ''


def _name_key(row: dict[str, Any] | None) -> str:
    """Filename stem (or folder name) used for starter notes."""
    row = row or {}
    raw = str(row.get('path') or '').strip()
    name = Path(raw).name if raw else ''
    if not name:
        name = str(row.get('filename') or row.get('label') or row.get('id') or '').strip()
    if not name:
        return ''
    lower = name.lower()
    for ext in _FILE_EXTENSIONS:
        if lower.endswith(ext):
            return lower[: -len(ext)]
    return lower


def _canonical(value: Any) -> str:
    """Lowercase identity for seed matching: 12b-it.Q4_K_S == 12b-it-q4-k-s."""
    text = str(value or '').strip().strip('/').lower()
    for ext in _FILE_EXTENSIONS:
        if text.endswith(ext):
            text = text[: -len(ext)]
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def _seed_candidates(row: dict[str, Any] | None) -> list[tuple[str, str]]:
    row = row or {}
    candidates: list[tuple[str, str]] = []
    repo = _canonical(row.get('hf_repo'))
    if repo:
        candidates.append(('repo', repo))
    for value in (row.get('id'), row.get('label'), _name_key(row)):
        ident = _canonical(value)
        if ident:
            candidates.append(('name', ident))
    return candidates


def _build_seed_indexes() -> tuple[dict[str, str], dict[str, str]]:
    """Return (repo index, name index); id and file-name keys share one index."""
    repo_index: dict[str, str] = {}
    name_index: dict[str, str] = {}
    for key, text in {**_SEED_NOTES, **_HF_SEED_NOTES}.items():
        prefix, _, value = str(key).partition(':')
        if prefix == 'hf':
            repo_index[_canonical(value)] = text
        else:
            name_index[_canonical(value)] = text
    return repo_index, name_index


_REPO_SEEDS, _NAME_SEEDS = _build_seed_indexes()


def seed_note_for(row: dict[str, Any] | None) -> str:
    for namespace, value in _seed_candidates(row):
        text = _REPO_SEEDS.get(value) if namespace == 'repo' else _NAME_SEEDS.get(value)
        if text:
            return text
    return ''


def load_notes() -> dict[str, dict[str, Any]]:
    path = _notes_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('model notes read failed: %s', exc)
        return {}
    rows = payload.get('notes') if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}
    notes: dict[str, dict[str, Any]] = {}
    for key, value in rows.items():
        if not isinstance(value, dict):
            continue
        text = _normalize_text(value.get('text'))
        notes[str(key)] = {
            'text': text,
            'label': str(value.get('label') or ''),
            'updated_at': float(value.get('updated_at') or 0.0),
        }
    return notes


def _save_notes(notes: dict[str, dict[str, Any]]) -> None:
    path = _notes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(
        json.dumps({'version': 1, 'notes': notes}, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    os.replace(tmp, path)


def _trim_for_storage(text: str) -> str:
    """Markdown notes can be long; drop trailing blank lines only."""
    return re.sub(r'\n{4,}', '\n\n\n', _normalize_text(text))


def set_note(key: str, text: str, *, label: str = '') -> dict[str, Any]:
    ident = str(key or '').strip()
    if not ident:
        return {'success': False, 'error': 'note key is required'}
    value = _trim_for_storage(text)
    with _lock:
        notes = load_notes()
        notes[ident] = {
            'text': value,
            'label': str(label or notes.get(ident, {}).get('label') or ''),
            'updated_at': time.time(),
        }
        _save_notes(notes)
    return {'success': True, 'key': ident, 'note': value}


def delete_note(key: str) -> dict[str, Any]:
    ident = str(key or '').strip()
    if not ident:
        return {'success': False, 'error': 'note key is required'}
    with _lock:
        notes = load_notes()
        if ident not in notes:
            return {'success': True, 'removed': False}
        notes.pop(ident, None)
        _save_notes(notes)
    return {'success': True, 'removed': True}


def note_for(row: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the note for a catalog row: user note, then starter note."""
    key = note_key(row)
    user = load_notes().get(key) if key else None
    if user is not None:
        return {
            'note': str(user.get('text') or ''),
            'note_key': key,
            'note_source': 'user',
            'note_updated_at': user.get('updated_at'),
        }
    seed = seed_note_for(row)
    if seed:
        return {'note': seed, 'note_key': key, 'note_source': 'seed', 'note_updated_at': None}
    return {'note': '', 'note_key': key, 'note_source': '', 'note_updated_at': None}


def attach_notes(models: list[dict[str, Any]] | None) -> None:
    """Add note fields to catalog rows in place (one file read)."""
    if not models:
        return
    notes = load_notes()
    for row in models:
        if not isinstance(row, dict):
            continue
        key = note_key(row)
        user = notes.get(key) if key else None
        if user is not None:
            row['note'] = str(user.get('text') or '')
            row['note_source'] = 'user'
            row['note_updated_at'] = user.get('updated_at')
        else:
            seed = seed_note_for(row)
            row['note'] = seed
            row['note_source'] = 'seed' if seed else ''
            row['note_updated_at'] = None
        row['note_key'] = key
