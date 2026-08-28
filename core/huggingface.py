"""Hugging Face Hub search and download helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT, load_config, normalize_download_settings
from core.dflash_generation import dflash_generation_label, repo_dflash_generation
from core.model_paths import allowed_model_roots, get_download_dir, get_library_by_id

HF_API = 'https://huggingface.co/api'
HF_BASE = 'https://huggingface.co'

HF_CATEGORIES: dict[str, dict[str, Any]] = {
    'supported': {
        'label': 'Supported in Console',
        'search': '',
        'filter': '',
        'gguf_only': False,
        'composite': True,
    },
    'all': {
        'label': 'All models',
        'search': '',
        'filter': '',
        'gguf_only': False,
    },
    'dflash': {
        'label': 'DFlash 1 Accelerator',
        'search': 'dflash gguf',
        'filter': 'gguf',
        'gguf_only': True,
    },
    'dflash2': {
        'label': 'DFlash 2 Accelerator',
        'search': 'dflash2 gguf',
        'filter': 'gguf',
        'gguf_only': True,
    },
    'text-generation': {
        'label': 'Text generation',
        'search': 'gguf',
        'filter': 'text-generation',
        'gguf_only': True,
    },
    'all-gguf': {
        'label': 'All GGUF',
        'search': 'gguf',
        'filter': 'gguf',
        'gguf_only': True,
    },
    'text-to-speech': {
        'label': 'Text-to-speech',
        'search': '',
        'filter': 'text-to-speech',
        'gguf_only': False,
    },
    'automatic-speech-recognition': {
        'label': 'Speech-to-text',
        'search': '',
        'filter': 'automatic-speech-recognition',
        'gguf_only': False,
    },
    'image-to-text': {
        'label': 'OCR / image-to-text',
        'search': '',
        'filter': 'image-to-text',
        'gguf_only': False,
    },
    'feature-extraction': {
        'label': 'Embeddings',
        'search': '',
        'filter': 'feature-extraction',
        'gguf_only': False,
    },
}

SUPPORTED_SOURCE_CATEGORIES = (
    'dflash',
    'all-gguf',
    'automatic-speech-recognition',
    'text-to-speech',
    'image-to-text',
    'feature-extraction',
)

_SUPPORTED_MODALITIES = frozenset({
    'llm',
    'vision',
    'embedding',
    'speech-to-text',
    'text-to-speech',
})

_DOWNLOAD_EXTENSIONS = ('.gguf', '.safetensors', '.onnx', '.bin', '.pt', '.ggml', '.mlmodel')

_download_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_HISTORY_PATH = ROOT / 'logs' / 'hf-download-history.json'
_PENDING_PATH = ROOT / 'logs' / 'hf-download-pending.json'
_MAX_HISTORY = 200
_history_loaded = False
_pending_loaded = False
_cleared_ids: set[str] = set()
_discover_roots_override: list[Path] | None = None
_disk_scan_at = 0.0
_DISK_SCAN_TTL = 60.0
_MIN_DISK_FILE_BYTES = 10_000
_repo_lookup_lock = threading.Lock()
_repo_lookup_inflight: dict[str, threading.Event] = {}
_repo_lookup_results: dict[str, list[dict[str, Any]]] = {}


def _is_under_allowed_model_root(path: Path, cfg: dict[str, Any]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for root in allowed_model_roots(cfg):
        try:
            if resolved.is_relative_to(root.expanduser().resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def _request_json(url: str, *, timeout: float = 20.0) -> Any:
    headers = {'Accept': 'application/json', 'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace') or 'null')


def _request_text(url: str, *, timeout: float = 20.0) -> str:
    headers = {'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def _parse_iso_ts(iso_ts: str | None):
    if not iso_ts:
        return None
    try:
        from datetime import datetime, timezone

        value = str(iso_ts)
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _time_ago(iso_ts: str | None) -> str:
    dt = _parse_iso_ts(iso_ts)
    if not dt:
        return '—'
    from datetime import datetime, timezone

    seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if seconds < 3600:
        return f'{max(1, seconds // 60)}m ago'
    if seconds < 86400:
        return f'{seconds // 3600}h ago'
    return f'{seconds // 86400}d ago'


def _days_since(iso_ts: str | None) -> int | None:
    dt = _parse_iso_ts(iso_ts)
    if not dt:
        return None
    from datetime import datetime, timezone

    seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    return seconds // 86400


def _format_downloads(count: int | float | None) -> str:
    value = int(count or 0)
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    if value >= 1000:
        return f'{value / 1000:.1f}k'
    return str(value)


def _entry_name(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ''
    return str(entry.get('rfilename') or entry.get('path') or entry.get('filename') or '').strip()


def _entry_size_bytes(entry: dict[str, Any] | None) -> int | None:
    """Best available file size. Prefer LFS blob size over a tiny pointer."""
    if not isinstance(entry, dict):
        return None
    candidates: list[int] = []
    for key in ('size_bytes', 'size'):
        value = entry.get(key)
        if isinstance(value, int) and value > 0:
            candidates.append(value)
    lfs = entry.get('lfs')
    if isinstance(lfs, dict):
        value = lfs.get('size')
        if isinstance(value, int) and value > 0:
            candidates.append(value)
    if not candidates:
        return None
    return max(candidates)


def _model_files(siblings: list[Any] | None, *, gguf_only: bool = True) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    allowed = ('.gguf',) if gguf_only else _DOWNLOAD_EXTENSIONS
    for entry in siblings or []:
        if not isinstance(entry, dict):
            continue
        name = _entry_name(entry)
        lower = name.lower()
        if not name or not any(lower.endswith(ext) for ext in allowed):
            continue
        size = _entry_size_bytes(entry)
        from core.hf_model_fit import bytes_to_size_gb

        size_gb = bytes_to_size_gb(size)
        files.append({
            'filename': name,
            'size_bytes': size if isinstance(size, int) and size > 0 else None,
            'size_gb': size_gb,
            'label': name.split('/')[-1],
            'format': Path(name).suffix.lower().lstrip('.') or 'file',
        })
    files.sort(key=_catalog_file_sort_key)
    return files


def _gguf_files(siblings: list[Any] | None) -> list[dict[str, Any]]:
    return _model_files(siblings, gguf_only=True)


def _files_as_siblings(files: list[Any] | None) -> list[dict[str, Any]]:
    siblings: list[dict[str, Any]] = []
    for entry in files or []:
        if not isinstance(entry, dict):
            continue
        name = _entry_name(entry)
        if not name:
            continue
        row = {'rfilename': name, 'path': name}
        size = _entry_size_bytes(entry)
        if size:
            row['size'] = size
        siblings.append(row)
    return siblings


def _siblings_have_file_sizes(
    siblings: list[Any] | None,
    *,
    extensions: tuple[str, ...] = _DOWNLOAD_EXTENSIONS,
) -> bool:
    for entry in siblings or []:
        if not isinstance(entry, dict):
            continue
        name = _entry_name(entry).lower()
        if not name or not any(name.endswith(ext) for ext in extensions):
            continue
        size = _entry_size_bytes(entry)
        if isinstance(size, int) and size > 0:
            return True
    return False


def _tree_entry_is_dir(row: dict[str, Any]) -> bool:
    typ = str(row.get('type') or '').lower()
    if typ == 'directory':
        return True
    if typ == 'file':
        return False
    path = str(row.get('path') or '').strip()
    if not path or _entry_size_bytes(row):
        return False
    name = path.split('/')[-1]
    return '.' not in name


def _sibling_parent_dirs(siblings: list[Any] | None) -> list[str]:
    dirs: list[str] = []
    seen: set[str] = set()
    for entry in siblings or []:
        name = _entry_name(entry) if isinstance(entry, dict) else ''
        if '/' not in name:
            continue
        parent = name.rsplit('/', 1)[0].strip('/')
        if parent and parent not in seen:
            seen.add(parent)
            dirs.append(parent)
    return dirs


def _fetch_repo_tree(repo: str, *, recursive: bool = False, path: str = '') -> list[dict[str, Any]]:
    """Return HF tree entries (with sizes). Falls back to empty list on error."""
    encoded = urllib.parse.quote(repo, safe='/')
    rel = str(path or '').strip().strip('/')
    suffix = f'tree/main/{urllib.parse.quote(rel, safe="/")}' if rel else 'tree/main'
    if recursive:
        suffix += '?recursive=1'
    url = f'{HF_API}/models/{encoded}/{suffix}'
    timeout = 22.0 if recursive else 10.0
    try:
        payload = _request_json(url, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _fetch_repo_siblings_with_blobs(repo: str) -> list[dict[str, Any]]:
    """Hub model info with blob sizes — fallback when the tree listing is folders-only."""
    encoded = urllib.parse.quote(repo, safe='/')
    try:
        payload = _request_json(f'{HF_API}/models/{encoded}?blobs=true', timeout=15.0)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    siblings = payload.get('siblings')
    return [row for row in siblings if isinstance(row, dict)] if isinstance(siblings, list) else []


def _preferred_size_folders(tree: list[dict[str, Any]], siblings: list[Any] | None) -> list[str]:
    folders = list(_sibling_parent_dirs(siblings))
    seen = set(folders)
    for row in tree:
        if not _tree_entry_is_dir(row):
            continue
        path = str(row.get('path') or '').strip().strip('/')
        if path and path not in seen:
            seen.add(path)
            folders.append(path)
    folders.sort(key=_quant_rank)
    return folders


def _blob_tree_from_siblings(blobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'path': _entry_name(row),
            'rfilename': _entry_name(row),
            'size': _entry_size_bytes(row),
            'lfs': row.get('lfs'),
            'type': 'file',
        }
        for row in blobs
        if _entry_name(row)
    ]


def _resolve_repo_tree(
    repo: str,
    siblings: list[Any] | None,
    *,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch the smallest Hub tree needed to fill missing file sizes."""
    if _siblings_have_file_sizes(siblings):
        return []

    def timed_out() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    # One Hub round-trip with LFS sizes — avoid walking every quant folder first.
    blobs = _fetch_repo_siblings_with_blobs(repo)
    blob_tree = _blob_tree_from_siblings(blobs) if blobs else []
    if blob_tree and _siblings_have_file_sizes(_siblings_with_sizes(siblings, blob_tree)):
        return blob_tree

    if timed_out():
        return blob_tree

    tree = _fetch_repo_tree(repo, recursive=False)
    if _siblings_have_file_sizes(_siblings_with_sizes(siblings, tree)):
        return tree

    extra: list[dict[str, Any]] = []
    for folder in _preferred_size_folders(tree, siblings)[:4]:
        if timed_out():
            break
        extra.extend(_fetch_repo_tree(repo, path=folder, recursive=False))
        combined = tree + extra
        if _siblings_have_file_sizes(_siblings_with_sizes(siblings, combined)):
            return combined
        if timed_out():
            break
        extra.extend(_fetch_repo_tree(repo, path=folder, recursive=True))
        combined = tree + extra
        if _siblings_have_file_sizes(_siblings_with_sizes(siblings, combined)):
            return combined

    if blob_tree and _siblings_have_file_sizes(_siblings_with_sizes(siblings, blob_tree)):
        return tree + extra + blob_tree

    if timed_out():
        return tree + extra + blob_tree

    file_count = sum(1 for row in tree if not _tree_entry_is_dir(row))
    if not extra and (len(tree) <= 64 or file_count == 0):
        recursive = _fetch_repo_tree(repo, recursive=True)
        if recursive:
            return recursive
    return tree + extra


def _siblings_with_sizes(siblings: list[Any] | None, tree: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Merge model siblings with tree sizes when the Hub omits size on siblings."""
    size_by_name: dict[str, int] = {}
    for row in tree or []:
        path = _entry_name(row)
        if not path:
            continue
        size = _entry_size_bytes(row)
        if isinstance(size, int) and size > 0:
            size_by_name[path] = size

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in siblings or []:
        if not isinstance(entry, dict):
            continue
        name = _entry_name(entry)
        if not name:
            continue
        row = dict(entry)
        row['rfilename'] = name
        if name in size_by_name and not _entry_size_bytes(row):
            row['size'] = size_by_name[name]
        merged.append(row)
        seen.add(name)
    for path, size in size_by_name.items():
        if path in seen:
            continue
        merged.append({'rfilename': path, 'path': path, 'size': size})
    return merged


def _quant_rank(filename: str) -> int:
    """Lower rank = better default download. Prefer Q4_K_M, then nearby Q4/Q5 quants."""
    lower = str(filename or '').lower()
    if 'imatrix' in lower or lower.endswith('.part'):
        return 10_000
    ranked: list[tuple[str, int]] = [
        ('q4_k_m', 0),
        ('q4_k_s', 1),
        ('q4_0', 2),
        ('q5_k_m', 3),
        ('q5_k_s', 4),
        ('q3_k_m', 5),
        ('q3_k_s', 6),
        ('q4_k_l', 7),
        ('q5_0', 8),
        ('q6_k', 9),
        ('q8_0', 10),
        ('q2_k', 20),
        ('q2_k_s', 21),
        ('iq4_nl', 40),
        ('iq4_xs', 41),
        ('iq3_xxs', 50),
        ('iq3_s', 51),
        ('iq2_xxs', 60),
        ('iq2_xs', 61),
        ('iq2_s', 62),
        ('iq1_s', 70),
        ('q8_0', 80),
        ('f16', 900),
        ('bf16', 901),
        ('f32', 902),
    ]
    best = 500
    for token, rank in ranked:
        if token in lower:
            best = min(best, rank)
    return best


def _catalog_file_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int]:
    filename = str(row.get('filename') or '')
    return (
        0 if str(row.get('format')) == 'gguf' else 1,
        1 if 'imatrix' in filename.lower() else 0,
        _quant_rank(filename),
        int(row.get('size_bytes') or 0),
    )


def _preferred_gguf_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not files:
        return None
    return min(files, key=_catalog_file_sort_key)


def _size_from_preferred_file(preferred: dict[str, Any] | None) -> tuple[float | None, str]:
    if not preferred:
        return None, '—'
    size_gb = preferred.get('size_gb')
    if not isinstance(size_gb, (int, float)) or float(size_gb) <= 0:
        return None, '—'
    return float(size_gb), f'{float(size_gb):g} GB'


def _size_from_preferred_quant(files: list[dict[str, Any]]) -> tuple[float | None, str]:
    """Card disk size for the preferred quant, summing GGUF shards."""
    preferred = _preferred_gguf_file(files)
    if not preferred:
        return None, '—'
    from core.hf_model_fit import _shard_group_key, bytes_to_size_gb

    key = _shard_group_key(str(preferred.get('filename') or ''))
    total = 0
    for row in files:
        if _shard_group_key(str(row.get('filename') or '')) != key:
            continue
        try:
            total += int(row.get('size_bytes') or 0)
        except (TypeError, ValueError):
            continue
    size_gb = bytes_to_size_gb(total)
    if size_gb:
        return float(size_gb), f'{float(size_gb):g} GB'
    return _size_from_preferred_file(preferred)


def _preferred_gguf_size(siblings: list[Any] | None) -> tuple[float | None, str]:
    return _size_from_preferred_quant(_gguf_files(siblings))


def _preferred_download_size(siblings: list[Any] | None) -> tuple[float | None, str]:
    return _size_from_preferred_quant(_model_files(siblings, gguf_only=False))


_ACCELERATOR_MAX_SIZE_GB = 8.0


def _is_accelerator_only_repo(
    siblings: list[Any] | None,
    *,
    repo_id: str = '',
    size_gb: float | None = None,
) -> bool:
    """True when the repo ships DFlash draft weights only, not a full target model."""
    largest_gb, _ = _largest_gguf_size(siblings)
    effective_gb = largest_gb
    if size_gb is not None:
        effective_gb = max(float(size_gb), float(largest_gb or 0))
    if effective_gb is not None and effective_gb > _ACCELERATOR_MAX_SIZE_GB:
        return False

    files = _gguf_files(siblings)
    if files:
        names = [str(row.get('filename') or '').lower() for row in files]
        if names and all('dflash' in name or 'dspark' in name for name in names):
            return True
        return False
    repo_lower = str(repo_id or '').lower()
    if 'dflash' not in repo_lower and 'dspark' not in repo_lower:
        return False
    if re.search(r'-dflash(?:[-_.]|/|$)|dflash-gguf', repo_lower):
        param = re.search(r'\b(\d+(?:\.\d+)?)\s*b\b', repo_lower)
        if param and float(param.group(1)) >= 30 and effective_gb is None:
            return False
        return True
    param = re.search(r'\b(\d+(?:\.\d+)?)\s*b\b', repo_lower)
    if param and size_gb is not None and float(size_gb) < 6 and float(param.group(1)) >= 7:
        return True
    return False


def _largest_gguf_size(siblings: list[Any] | None) -> tuple[float | None, str]:
    gguf_files = _gguf_files(siblings)
    sized = [row for row in gguf_files if isinstance(row.get('size_gb'), (int, float))]
    if not sized:
        return None, '—'
    largest = max(sized, key=lambda row: int(row.get('size_bytes') or 0))
    size_gb = float(largest['size_gb'])
    label = f'{size_gb:g} GB'
    return size_gb, label


def _author_avatar_url(author: str) -> str:
    author = str(author or '').strip()
    if not author:
        return ''
    return f'{HF_BASE}/{urllib.parse.quote(author, safe="")}/avatar'


_LAB_PATTERNS: list[tuple[str, str]] = [
    ('Google', r'gemma|google/|google-|\bgoogle\b'),
    ('Qwen', r'qwen'),
    ('Meta', r'meta-llama|\bllama[-\d]|\bllama\b|facebook'),
    ('Mistral AI', r'\bmistral\b'),
    ('Microsoft', r'\bphi[-\d]|\bphi\b|microsoft'),
    ('DeepSeek', r'deepseek'),
    ('Apple', r'openelm|\bapple\b'),
    ('IBM', r'\bgranite\b|\bibm\b'),
    ('NVIDIA', r'nemotron|nvidia'),
    ('BAAI', r'\bbge[-_]|baai'),
    ('z-lab', r'z-lab|zlabs'),
    ('LM Studio', r'lmstudio|lm-studio|lm studio'),
    ('Cohere', r'command-r|cohere'),
    ('Anthropic', r'\bclaude\b|anthropic'),
    ('OpenAI', r'\bgpt-oss\b|openai'),
    ('Alibaba', r'\btongyi\b|alibaba'),
]

_AUTHOR_LAB_ALIASES = {
    'google': 'Google',
    'meta-llama': 'Meta',
    'mistralai': 'Mistral AI',
    'microsoft': 'Microsoft',
    'deepseek-ai': 'DeepSeek',
    'qwen': 'Qwen',
    'z-lab': 'z-lab',
    'lmstudio': 'LM Studio',
    'lmstudio-community': 'LM Studio',
    'nvidia': 'NVIDIA',
    'ibm-granite': 'IBM',
    'cohere': 'Cohere',
    'anthropics': 'Anthropic',
    'openai': 'OpenAI',
}


def _lab_from_patterns(text: str) -> str:
    haystack = str(text or '').lower().replace('llama.cpp', ' ')
    if not haystack.strip():
        return ''
    for label, pattern in _LAB_PATTERNS:
        if re.search(pattern, haystack, flags=re.I):
            return label
    return ''


def _author_lab_alias(author: str) -> str:
    key = str(author or '').strip().lower()
    if not key:
        return ''
    if key in _AUTHOR_LAB_ALIASES:
        return _AUTHOR_LAB_ALIASES[key]
    if key.endswith('-ai') and key[:-3] in _AUTHOR_LAB_ALIASES:
        return _AUTHOR_LAB_ALIASES[key[:-3]]
    return ''


def _base_model_hint(readme: str) -> str:
    text = str(readme or '').strip()
    if not text.startswith('---'):
        return ''
    parts = text.split('---', 2)
    if len(parts) < 3:
        return ''
    block = parts[1]
    values: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('base_model'):
            _, _, rhs = stripped.partition(':')
            rhs = rhs.strip().strip('"').strip("'")
            if rhs:
                values.append(rhs)
            continue
        if values and stripped.startswith('- '):
            values.append(stripped[2:].strip().strip('"').strip("'"))
    return ' '.join(values)


def infer_model_lab(
    *,
    repo_id: str = '',
    author: str = '',
    tags: list[str] | None = None,
    title: str = '',
    base_model: str = '',
) -> str:
    # The publisher (author) is the "lab" the UI groups and filters by —
    # prefer its alias over base-model/tag patterns so e.g.
    # microsoft/VibeVoice-Realtime-0.5B (built on Qwen) is labeled Microsoft,
    # not Qwen.
    alias = _author_lab_alias(author)
    if alias:
        return alias
    if base_model:
        matched = _lab_from_patterns(base_model)
        if matched:
            return matched
    haystack = ' '.join([
        repo_id,
        author,
        title,
        base_model,
        ' '.join(tags or []),
    ])
    matched = _lab_from_patterns(haystack)
    if matched:
        return matched
    clean_author = str(author or '').strip()
    if clean_author:
        return clean_author.replace('-', ' ').title()
    return 'Unknown'


def _readme_body(readme: str) -> str:
    text = str(readme or '').strip()
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text


def _truncate_text(text: str, limit: int = 160) -> str:
    value = ' '.join(str(text or '').split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + '…'


def _title_from_readme(readme: str, fallback: str = '') -> str:
    for line in _readme_body(readme).splitlines():
        stripped = line.strip()
        if stripped.startswith('# '):
            return stripped[2:].strip()
    return str(fallback or '').strip()


def _description_from_readme(readme: str, *, limit: int = 160) -> str:
    lines = _readme_body(readme).splitlines()
    start = 0
    if lines and lines[0].strip().startswith('#'):
        start = 1
    paragraph: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith('#'):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return _truncate_text(' '.join(paragraph), limit)


def _card_description(card: dict[str, Any] | None) -> str:
    data = card if isinstance(card, dict) else {}
    return str(data.get('short_description') or data.get('description') or '').strip()


def _fetch_readme_head(repo_id: str, *, max_chars: int = 8000, timeout: float = 1.0) -> str:
    repo = str(repo_id or '').strip().strip('/')
    if not repo:
        return ''
    for candidate in (f'{HF_BASE}/{repo}/raw/main/README.md', f'{HF_BASE}/{repo}/raw/main/readme.md'):
        try:
            text = _request_text(candidate, timeout=timeout)
            if text.strip():
                return text[:max_chars]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            continue
    return ''


def _enrich_model_card(model: dict[str, Any]) -> dict[str, Any]:
    row = dict(model)
    fallback_title = str(row.get('label') or row.get('id') or '').strip()
    card_desc = str(row.get('description') or '').strip()
    current_title = str(row.get('title') or fallback_title).strip()
    needs_title = not current_title or current_title == fallback_title
    needs_desc = not card_desc
    if not needs_title and not needs_desc:
        row['title'] = current_title
        return row
    readme = _fetch_readme_head(str(row.get('id') or ''))
    if readme:
        if needs_title:
            row['title'] = _title_from_readme(readme, fallback_title) or fallback_title
        if needs_desc:
            row['description'] = _description_from_readme(readme) or card_desc
        base_model = _base_model_hint(readme)
        row['lab'] = infer_model_lab(
            repo_id=str(row.get('id') or ''),
            author=str(row.get('author') or ''),
            tags=list(row.get('tags') or []),
            title=str(row.get('title') or fallback_title),
            base_model=base_model,
        )
    row.setdefault('title', fallback_title)
    row.setdefault(
        'lab',
        infer_model_lab(
            repo_id=str(row.get('id') or ''),
            author=str(row.get('author') or ''),
            tags=list(row.get('tags') or []),
            title=str(row.get('title') or fallback_title),
        ),
    )
    return row


def _enrich_models_from_readme(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not models:
        return models
    from concurrent.futures import ThreadPoolExecutor, as_completed

    enriched: list[dict[str, Any] | None] = [None] * len(models)
    with ThreadPoolExecutor(max_workers=min(8, len(models))) as pool:
        futures = {
            pool.submit(_enrich_model_card, row): idx
            for idx, row in enumerate(models)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                enriched[idx] = future.result()
            except Exception:
                enriched[idx] = models[idx]
    return [row if row is not None else models[idx] for idx, row in enumerate(enriched)]


def _transformers_runtime_available() -> bool:
    try:
        from core.runtimes.transformers_hf import TransformersRuntimeAdapter
        return TransformersRuntimeAdapter.is_installed()
    except Exception:
        return False


def _vllm_runtime_available() -> bool:
    try:
        from core.runtimes.vllm import VllmRuntimeAdapter
        return VllmRuntimeAdapter.is_installed()
    except Exception:
        return False


def _hf_modality_fields(
    *,
    pipeline_tag: str,
    has_gguf: bool,
    tags: list[str],
    label: str,
    downloadable: bool,
) -> dict[str, Any]:
    """Phase 0: derive modality/runtime/kind metadata for a catalog row."""
    tag = str(pipeline_tag or '').lower()
    hay = ' '.join([str(label or ''), ' '.join(str(t) for t in tags or [])]).lower()
    if tag == 'automatic-speech-recognition' or 'whisper' in hay or 'faster-whisper' in hay:
        modality, runtime_id, kind = 'speech-to-text', 'stt', 'repo'
    elif tag == 'text-to-speech' or 'piper' in hay or 'tts' in hay or 'kokoro' in hay:
        if 'vibevoice' in hay or 'vibevoice' in str(label or '').lower():
            modality, runtime_id, kind = 'text-to-speech', 'vibevoice', 'repo'
        else:
            modality, runtime_id, kind = 'text-to-speech', 'piper', 'repo'
    elif tag == 'feature-extraction':
        modality = 'embedding'
        runtime_id = 'llama-server' if has_gguf else ''
        kind = 'file' if has_gguf else 'repo'
    elif tag in ('image-to-text', 'image-text-to-text') or 'vision' in hay or 'mmproj' in hay:
        modality = 'vision'
        runtime_id = 'llama-server' if has_gguf else ''
        kind = 'file' if has_gguf else 'repo'
    elif has_gguf:
        modality, runtime_id, kind = 'llm', 'llama-server', 'file'
    elif tag == 'text2text-generation' or 't5' in hay or 'seq2seq' in hay:
        modality, runtime_id, kind = 'translation', 'transformers', 'repo'
    elif tag in ('text-generation', 'conversational', 'question-answering') or not has_gguf:
        modality, runtime_id, kind = 'llm', 'transformers', 'repo'
    else:
        modality, runtime_id, kind = 'llm', '', 'repo'
    task = {
        'speech-to-text': 'transcribe',
        'text-to-speech': 'speech',
        'embedding': 'embed',
        'vision': 'vision',
        'translation': 'translate',
        'llm': 'chat',
    }.get(modality, 'chat')
    from core.hf_engines import HF_LLM_ENGINES, preferred_hf_runtime

    transformers_ready = runtime_id == 'transformers' and _transformers_runtime_available()
    vllm_ready = runtime_id == 'transformers' and _vllm_runtime_available()
    engines: list[str] = []
    if runtime_id == 'transformers':
        runtime_id = preferred_hf_runtime()
        engines = list(HF_LLM_ENGINES)
    return {
        'modality': modality,
        'runtime_id': runtime_id,
        'engines': engines,
        'kind': kind,
        'catalog_visible': True,
        'downloadable': bool(downloadable) or has_gguf or kind == 'repo',
        'runnable': (runtime_id == 'llama-server' and has_gguf) or transformers_ready or vllm_ready,
        'family': '',
        'task': task,
    }


def _summary_from_model(raw: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(raw.get('id') or raw.get('modelId') or '')
    author = str(raw.get('author') or (repo_id.split('/')[0] if '/' in repo_id else ''))
    card = raw.get('cardData') if isinstance(raw.get('cardData'), dict) else {}
    description = _truncate_text(_card_description(card))
    tags = [str(t) for t in (raw.get('tags') or []) if t]
    siblings = raw.get('siblings')
    downloadable = _model_files(siblings, gguf_only=False)
    last_modified = str(raw.get('lastModified') or raw.get('createdAt') or '')
    size_gb, size_label = _preferred_gguf_size(siblings)
    if size_gb is None:
        size_gb, size_label = _preferred_download_size(siblings)
    updated_days = _days_since(last_modified)
    repo_label = repo_id.split('/')[-1] if '/' in repo_id else repo_id
    lab = infer_model_lab(repo_id=repo_id, author=author, tags=tags, title=repo_label)
    has_gguf = any(name.endswith('.gguf') for name in tags) or bool(_gguf_files(siblings))
    gguf_files = _gguf_files(siblings)
    if not size_gb or float(size_gb) <= 0:
        from core.hf_model_fit import repo_disk_size_gb

        files_for_size = gguf_files if has_gguf else downloadable
        disk_gb = repo_disk_size_gb(files_for_size, has_gguf=has_gguf)
        if disk_gb and disk_gb > 0:
            size_gb = disk_gb
            size_label = f'{disk_gb:g} GB'
    if isinstance(size_gb, (int, float)) and float(size_gb) <= 0:
        size_gb = None
        size_label = '—'
    accelerator_only = _is_accelerator_only_repo(siblings, repo_id=repo_id, size_gb=size_gb)
    accel_gen = repo_dflash_generation(repo_id, repo_label) if accelerator_only else None
    modality_fields = _hf_modality_fields(
        pipeline_tag=str(raw.get('pipeline_tag') or ''),
        has_gguf=has_gguf,
        tags=tags,
        label=repo_label,
        downloadable=bool(downloadable),
    )
    return {
        'id': repo_id,
        'author': author,
        'lab': lab,
        'author_avatar_url': _author_avatar_url(author),
        'label': repo_label,
        'title': repo_label,
        'downloads': int(raw.get('downloads') or 0),
        'downloads_label': _format_downloads(raw.get('downloads')),
        'likes': int(raw.get('likes') or 0),
        'last_modified': last_modified,
        'updated_ago': _time_ago(last_modified),
        'updated_days': updated_days,
        'size_gb': size_gb,
        'size_label': size_label,
        'accelerator_only': accelerator_only,
        'dflash_generation': accel_gen,
        'dflash_generation_label': dflash_generation_label(accel_gen) if accel_gen else None,
        'tags': tags,
        'pipeline_tag': str(raw.get('pipeline_tag') or ''),
        'description': description,
        'gguf_count': len(gguf_files),
        'file_count': len(downloadable),
        'has_gguf': has_gguf,
        'has_files': bool(downloadable),
        'gguf_files': gguf_files,
        'download_files': downloadable,
        'size_bytes': int(size_gb * (1024 ** 3)) if isinstance(size_gb, (int, float)) and size_gb > 0 else None,
        **modality_fields,
    }


def _row_needs_size_enrich(row: dict[str, Any]) -> bool:
    size_gb = row.get('size_gb')
    has_gguf = bool(row.get('has_gguf') or row.get('accelerator_only'))
    if isinstance(size_gb, (int, float)) and 0 < float(size_gb) < 0.05 and has_gguf:
        return True
    label = str(row.get('size_label') or '').strip()
    if label and label not in ('—', '0 GB', '0.0 GB'):
        return False
    if isinstance(size_gb, (int, float)) and float(size_gb) > 0:
        return False
    return True


_SIZE_MERGE_FIELDS = (
    'size_gb',
    'size_label',
    'size_bytes',
    'gguf_files',
    'download_files',
    'gguf_count',
    'file_count',
    'has_gguf',
    'has_files',
    'accelerator_only',
)


def _merge_size_fields(row: dict[str, Any], summary: dict[str, Any]) -> None:
    for field in _SIZE_MERGE_FIELDS:
        if field in summary:
            row[field] = summary[field]


def _enrich_summaries_sizes(
    rows: list[dict[str, Any]],
    raw_items: list[dict[str, Any]] | None = None,
    *,
    max_fetches: int | None = None,
) -> list[dict[str, Any]]:
    """Fill missing list-card sizes from the Hub tree (parallel)."""
    if not rows:
        return rows
    fetch_limit = len(rows) if max_fetches is None else max(0, int(max_fetches))
    if fetch_limit <= 0:
        return rows

    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        repo_id = str(item.get('id') or item.get('modelId') or '').strip()
        if repo_id:
            raw_by_id[repo_id] = item

    futures: dict[Any, tuple[int, dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=min(8, fetch_limit)) as pool:
        for index, row in enumerate(rows):
            if not _row_needs_size_enrich(row):
                continue
            repo_id = str(row.get('id') or '').strip()
            if not repo_id:
                continue
            if len(futures) >= fetch_limit:
                break
            raw = raw_by_id.get(repo_id)
            if not raw:
                siblings = _files_as_siblings(row.get('gguf_files') or row.get('download_files') or [])
                raw = {
                    'id': repo_id,
                    'siblings': siblings,
                    'tags': list(row.get('tags') or []),
                    'pipeline_tag': row.get('pipeline_tag') or '',
                    'author': row.get('author') or '',
                    'downloads': row.get('downloads') or 0,
                    'likes': row.get('likes') or 0,
                    'lastModified': row.get('last_modified') or '',
                    'cardData': {'description': row.get('description') or ''},
                }
            futures[pool.submit(_resolve_repo_tree, repo_id, raw.get('siblings'))] = (index, raw)

        for future in as_completed(futures):
            index, raw = futures[future]
            try:
                tree = future.result()
            except Exception:
                continue
            if not tree:
                continue
            enriched = dict(raw)
            enriched['siblings'] = _siblings_with_sizes(raw.get('siblings'), tree)
            _merge_size_fields(rows[index], _summary_from_model(enriched))
    return rows


def _summaries_from_models(
    raw_models: list[dict[str, Any]],
    *,
    enrich_sizes: bool = False,
) -> list[dict[str, Any]]:
    """Build summaries and fill missing GGUF sizes from the Hub tree."""
    raw_items = [item for item in raw_models if isinstance(item, dict)]
    rows = [_summary_from_model(item) for item in raw_items]
    if enrich_sizes and rows:
        return _enrich_summaries_sizes(rows, raw_items)
    return rows


def _normalize_repo_slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower()).strip('-')


def _is_repo_id_query(query: str) -> bool:
    needle = str(query or '').strip().strip('/')
    if '/' not in needle:
        return False
    parts = [part for part in needle.split('/') if part]
    return len(parts) >= 2


def _fetch_repo_summary_light(repo_id: str, *, category: str = 'dflash') -> dict[str, Any] | None:
    """Fast repo-id lookup for catalog search — metadata and files, no README/tree walk."""
    repo = str(repo_id or '').strip().strip('/')
    if not repo or '/' not in repo:
        return None
    url = f'{HF_API}/models/{urllib.parse.quote(repo, safe="/")}'
    try:
        raw = _request_json(url, timeout=25)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    siblings = raw.get('siblings')
    if not _siblings_have_file_sizes(siblings):
        blobs = _fetch_repo_siblings_with_blobs(repo)
        if blobs:
            siblings = _siblings_with_sizes(siblings, _blob_tree_from_siblings(blobs))

    enriched = dict(raw)
    enriched['siblings'] = siblings
    summary = _summary_from_model(enriched)
    summary['category'] = str(category or 'dflash')
    return summary


def _cached_model_detail(repo_id: str, *, category: str) -> dict[str, Any]:
    from core.hf_catalog_cache import get_or_fetch_detail

    return get_or_fetch_detail(
        repo_id=repo_id,
        category=category,
        fetcher=lambda rid=repo_id, cat=category: get_model_detail(rid, category=cat),
    )


def _lookup_hf_repo_models(needle: str, *, category: str = 'all') -> list[dict[str, Any]]:
    """Resolve a full repo id or slug (e.g. deepseek/deepseek-v4-flash) to Hub models."""
    query = str(needle or '').strip().strip('/')
    if not query:
        return []

    cache_key = f'{str(category or "all").strip().lower()}|{query.lower()}'
    with _repo_lookup_lock:
        if cache_key in _repo_lookup_results:
            return [dict(row) for row in _repo_lookup_results[cache_key]]
        inflight = _repo_lookup_inflight.get(cache_key)
        if inflight is None:
            inflight = threading.Event()
            _repo_lookup_inflight[cache_key] = inflight
            owner = True
        else:
            owner = False
    if not owner:
        inflight.wait(timeout=120.0)
        with _repo_lookup_lock:
            cached = _repo_lookup_results.get(cache_key)
        return [dict(row) for row in cached] if cached else []

    models: list[dict[str, Any]] = []
    try:
        models = _lookup_hf_repo_models_uncached(query, category=category)
    finally:
        with _repo_lookup_lock:
            _repo_lookup_results[cache_key] = models
            done = _repo_lookup_inflight.pop(cache_key, None)
        if done:
            done.set()
    return [dict(row) for row in models]


def _lookup_hf_repo_models_uncached(query: str, *, category: str = 'all') -> list[dict[str, Any]]:
    normalized = query.lower()
    slug = _normalize_repo_slug(query.split('/')[-1] if '/' in query else query)
    found: dict[str, dict[str, Any]] = {}

    def add_model(model: dict[str, Any] | None) -> None:
        if not isinstance(model, dict):
            return
        repo_id = str(model.get('id') or '').strip()
        if repo_id and repo_id not in found:
            found[repo_id] = model

    light = _fetch_repo_summary_light(query, category=category)
    if light:
        add_model(light)

    search_terms = []
    if '/' in query:
        search_terms.append(query)
    if slug:
        search_terms.append(slug.replace('-', ' '))
        search_terms.append(slug)
    for term in search_terms:
        params = {
            'limit': 12,
            'sort': 'downloads',
            'direction': '-1',
            'full': 'true',
            'search': term,
        }
        try:
            payload = _request_json(f'{HF_API}/models?{urllib.parse.urlencode(params)}', timeout=20)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            repo_id = str(item.get('id') or item.get('modelId') or '').strip()
            if not repo_id:
                continue
            repo_slug = _normalize_repo_slug(repo_id.split('/')[-1])
            repo_lower = repo_id.lower()
            if repo_lower == normalized or (
                slug and (repo_slug == slug or slug in repo_slug or repo_slug in slug)
            ):
                add_model(_summary_from_model(item))

    models = list(found.values())
    # Enrich only the best match with full detail (README/files) when needed.
    models.sort(
        key=lambda row: (
            0 if str(row.get('id') or '').lower() == normalized else 1,
            0 if slug and _normalize_repo_slug(str(row.get('id') or '').split('/')[-1]) == slug else 1,
            -int(row.get('downloads') or 0),
        ),
    )
    best_id = str(models[0].get('id') or '').strip() if models else ''
    if best_id and not models[0].get('download_files') and not models[0].get('gguf_files'):
        detail = _cached_model_detail(best_id, category=category)
        if detail.get('success') and isinstance(detail.get('model'), dict):
            found[best_id] = detail['model']
            models = list(found.values())
            models.sort(
                key=lambda row: (
                    0 if str(row.get('id') or '').lower() == normalized else 1,
                    0 if slug and _normalize_repo_slug(str(row.get('id') or '').split('/')[-1]) == slug else 1,
                    -int(row.get('downloads') or 0),
                ),
            )
    return models


def _supported_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    return (
        0 if 'dflash' in str(row.get('id') or '').lower() else 1,
        0 if bool(row.get('has_gguf')) or int(row.get('gguf_count') or 0) > 0 else 1,
        -int(row.get('downloads') or 0),
    )


def _search_supported_repo_query(
    query: str,
    *,
    limit: int,
    sort: str,
) -> dict[str, Any]:
    """Fast path for org/repo searches — one lookup instead of six modality fan-out."""
    needle = str(query or '').strip()
    response_limit = max(1, min(int(limit), 50))
    matches = [
        row for row in _lookup_hf_repo_models(needle, category='supported')
        if _is_console_supported_model(row)
    ]
    matches.sort(key=_supported_sort_key)
    models = matches[:response_limit]
    if any(_row_needs_size_enrich(row) for row in models):
        models = _enrich_summaries_sizes(models, max_fetches=1)
    from core.config import load_config
    from core.hf_model_fit import annotate_hf_models_fit

    annotate_hf_models_fit(models, cfg=load_config(), category='supported')
    return {
        'success': True,
        'models': models,
        'query': needle,
        'category': 'supported',
        'sort': sort,
    }


def _prepend_repo_lookup(
    models: list[dict[str, Any]],
    needle: str,
    *,
    category: str,
    supported_only: bool = False,
) -> list[dict[str, Any]]:
    """When the user types an org/repo id, resolve it even if text search returned other rows."""
    if not needle or '/' not in needle:
        return models
    matches = _lookup_hf_repo_models(needle, category=category)
    if supported_only:
        matches = [row for row in matches if _is_console_supported_model(row)]
    if not matches:
        return models
    merged: dict[str, dict[str, Any]] = {str(row.get('id') or ''): row for row in models if row.get('id')}
    for row in reversed(matches):
        repo_id = str(row.get('id') or '').strip()
        if repo_id:
            merged[repo_id] = row
    return list(merged.values())


def _is_console_supported_model(row: dict[str, Any]) -> bool:
    """True when a catalog row can be downloaded or run in DFlash Console."""
    if bool(row.get('accelerator_only')):
        return True
    if bool(row.get('runnable')):
        return True
    if bool(row.get('has_gguf')) or int(row.get('gguf_count') or 0) > 0:
        return True
    modality = str(row.get('modality') or '').strip().lower()
    if modality in ('llm', 'embedding', 'vision'):
        if modality == 'llm' and str(row.get('runtime_id') or '') in {'transformers', 'vllm'}:
            return bool(
                row.get('downloadable')
                or row.get('has_files')
                or int(row.get('file_count') or 0) > 0
            )
        return False
    if modality not in _SUPPORTED_MODALITIES:
        return False
    return bool(
        row.get('downloadable')
        or row.get('has_files')
        or int(row.get('file_count') or 0) > 0
    )


def _search_supported_models(
    query: str = '',
    *,
    limit: int = 25,
    sort: str = 'downloads',
) -> dict[str, Any]:
    """Merge supported Console modalities (GGUF, STT, TTS, OCR, embeddings)."""
    needle = str(query or '').strip()
    if _is_repo_id_query(needle):
        return _search_supported_repo_query(needle, limit=limit, sort=sort)
    response_limit = max(1, min(int(limit), 50))
    sort_key = sort if sort in ('downloads', 'likes', 'lastModified', 'createdAt') else 'downloads'
    per_source = response_limit if needle else max(
        8,
        (response_limit + len(SUPPORTED_SOURCE_CATEGORIES) - 1) // len(SUPPORTED_SOURCE_CATEGORIES),
    )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    merged: dict[str, dict[str, Any]] = {}

    def fetch_category(category: str) -> list[dict[str, Any]]:
        from core.hf_catalog_cache import search_with_cache

        payload = search_with_cache(
            query=needle,
            sort=sort_key,
            category=category,
            limit=per_source,
            enrich_sizes=False,
            fetcher=lambda cat=category: search_models(
                needle,
                limit=per_source,
                sort=sort_key,
                category=cat,
                enrich_sizes=False,
            ),
        )
        return [row for row in (payload.get('models') or []) if isinstance(row, dict)]

    with ThreadPoolExecutor(max_workers=len(SUPPORTED_SOURCE_CATEGORIES)) as pool:
        futures = {
            pool.submit(fetch_category, category): category
            for category in SUPPORTED_SOURCE_CATEGORIES
        }
        for future in as_completed(futures):
            try:
                rows = future.result()
            except Exception:
                continue
            for row in rows:
                repo_id = str(row.get('id') or '').strip()
                if not repo_id or repo_id in merged:
                    continue
                if _is_console_supported_model(row):
                    merged[repo_id] = row

    models = list(merged.values())
    models = _prepend_repo_lookup(models, needle, category='supported', supported_only=True)
    models.sort(key=_supported_sort_key)
    models = models[:response_limit]
    models = _enrich_summaries_sizes(models)
    from core.config import load_config
    from core.hf_model_fit import annotate_hf_models_fit

    annotate_hf_models_fit(models, cfg=load_config(), category='supported')
    return {'success': True, 'models': models, 'query': needle, 'category': 'supported'}


def search_models(
    query: str = '',
    *,
    limit: int = 25,
    sort: str = 'downloads',
    category: str = 'dflash',
    gguf_only: bool | None = None,
    enrich_sizes: bool = True,
) -> dict[str, Any]:
    needle = str(query or '').strip()
    cat_key = str(category or 'dflash').strip().lower()
    response_limit = max(1, min(int(limit), 50))
    if cat_key == 'supported':
        return _search_supported_models(query=needle, limit=response_limit, sort=sort)
    cat = HF_CATEGORIES.get(cat_key, HF_CATEGORIES['dflash'])
    use_gguf_only = cat.get('gguf_only', True) if gguf_only is None else gguf_only
    hf_limit = 50 if cat_key == 'dflash' and not needle else response_limit
    params: dict[str, str | int] = {
        'limit': hf_limit,
        'sort': sort if sort in ('downloads', 'likes', 'lastModified', 'createdAt') else 'downloads',
        'direction': '-1',
        'full': 'true',
    }
    if needle:
        if use_gguf_only and 'gguf' not in needle.lower():
            params['search'] = f'{needle} gguf'
            params['filter'] = 'gguf'
        else:
            params['search'] = needle
            if use_gguf_only:
                params['filter'] = 'gguf'
            elif cat.get('filter'):
                params['filter'] = str(cat['filter'])
    else:
        default_search = str(cat.get('search') or '')
        if default_search:
            params['search'] = default_search
        if cat.get('filter'):
            params['filter'] = str(cat['filter'])
    url = f'{HF_API}/models?{urllib.parse.urlencode(params)}'
    try:
        payload = _request_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {'success': False, 'error': str(exc), 'models': [], 'category': cat_key}
    if not isinstance(payload, list):
        return {'success': False, 'error': 'unexpected Hugging Face response', 'models': [], 'category': cat_key}
    models = _summaries_from_models(payload, enrich_sizes=enrich_sizes)
    if use_gguf_only:
        models = [
            row for row in models
            if row.get('has_gguf') or row.get('gguf_count', 0) > 0 or 'gguf' in ' '.join(row.get('tags') or []).lower()
        ]
        if not models:
            models = [_summary_from_model(item) for item in payload if isinstance(item, dict)]
    elif cat_key != 'all-gguf':
        models = [row for row in models if row.get('has_files') or row.get('file_count', 0) > 0]
    if not models and needle:
        fallback_params: dict[str, str | int] = {
            'limit': params['limit'],
            'sort': params['sort'],
            'direction': '-1',
            'full': 'true',
            'search': needle,
        }
        if use_gguf_only:
            fallback_params['filter'] = 'gguf'
        elif cat.get('filter'):
            fallback_params['filter'] = str(cat['filter'])
        try:
            fallback_payload = _request_json(f'{HF_API}/models?{urllib.parse.urlencode(fallback_params)}')
            if isinstance(fallback_payload, list):
                models = _summaries_from_models(
                    fallback_payload,
                    enrich_sizes=enrich_sizes,
                )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            pass
    # Exact repo-id lookup: when the user types a full "org/repo" id (e.g.
    # deepseek/deepseek-v4-flash) the search may still miss it — resolve directly.
    models = _prepend_repo_lookup(models, needle, category=cat_key, supported_only=False)
    models.sort(
        key=lambda row: (
            0 if 'dflash' in str(row.get('id') or '').lower() else 1,
            0 if str(row.get('pipeline_tag') or '') == 'text-generation' else 1,
            -int(row.get('downloads') or 0),
        ),
    )
    from core.config import load_config
    from core.hf_catalog_cache import get_cached_detail
    from core.hf_local_match import find_repo_local_installs, is_catalog_ready_to_load

    config = load_config()
    for row in models:
        label = str(row.get('size_label') or '').strip()
        if label and label not in ('—', '0 GB', '0.0 GB'):
            continue
        size_gb = row.get('size_gb')
        if isinstance(size_gb, (int, float)) and float(size_gb) > 0:
            continue
        repo_id = str(row.get('id') or '')
        if not repo_id:
            continue
        cached = get_cached_detail(repo_id=repo_id, category=cat_key)
        cached_model = (cached or {}).get('payload', {}).get('model')
        if not isinstance(cached_model, dict):
            continue
        cached_label = str(cached_model.get('size_label') or '').strip()
        if cached_label and cached_label != '—':
            row['size_label'] = cached_label
            if isinstance(cached_model.get('size_gb'), (int, float)):
                row['size_gb'] = float(cached_model['size_gb'])
            if isinstance(cached_model.get('size_bytes'), int):
                row['size_bytes'] = cached_model['size_bytes']
    for row in models:
        repo_id = str(row.get('id') or '')
        tags = list(row.get('tags') or [])
        installs = find_repo_local_installs(repo_id, cfg=config)
        loadable = [item for item in installs if item.get('loadable')]
        row['local_ready'] = bool(installs)
        row['local_loadable'] = bool(loadable)
        row['catalog_ready_to_load'] = is_catalog_ready_to_load(
            repo_id,
            title=str(row.get('title') or row.get('label') or repo_id),
            tags=tags,
            cfg=config,
        )
    if cat_key == 'dflash':
        models = [
            row for row in models
            if row.get('accelerator_only')
            and str(row.get('dflash_generation') or repo_dflash_generation(str(row.get('id') or ''))) != 'dflash2'
        ]
    elif cat_key == 'dflash2':
        models = [
            row for row in models
            if row.get('accelerator_only')
            and str(row.get('dflash_generation') or repo_dflash_generation(str(row.get('id') or ''))) == 'dflash2'
        ]
    models = models[:response_limit]
    from core.hf_model_fit import annotate_hf_models_fit

    annotate_hf_models_fit(models, cfg=config, category=cat_key)
    return {'success': True, 'models': models, 'query': needle, 'category': cat_key}


def get_model_detail(repo_id: str, *, category: str = 'dflash') -> dict[str, Any]:
    repo = str(repo_id or '').strip().strip('/')
    if not repo or '/' not in repo:
        return {'success': False, 'error': 'invalid repo id'}
    cat_key = str(category or 'dflash').strip().lower()
    cat = HF_CATEGORIES.get(cat_key, HF_CATEGORIES['dflash'])
    use_gguf_only = bool(cat.get('gguf_only', True))
    if cat_key in ('supported', 'all'):
        use_gguf_only = False
    url = f'{HF_API}/models/{urllib.parse.quote(repo, safe="/")}'
    try:
        raw = _request_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {'success': False, 'error': f'model not found: {repo}'}
        return {'success': False, 'error': str(exc)}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {'success': False, 'error': str(exc)}
    if not isinstance(raw, dict):
        return {'success': False, 'error': 'unexpected Hugging Face response'}

    readme = ''
    for candidate in (f'{HF_BASE}/{repo}/raw/main/README.md', f'{HF_BASE}/{repo}/raw/main/readme.md'):
        try:
            readme = _request_text(candidate, timeout=15)
            if readme.strip():
                break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            continue

    card = raw.get('cardData') if isinstance(raw.get('cardData'), dict) else {}
    tags = [str(t) for t in (raw.get('tags') or []) if t]
    tree = _resolve_repo_tree(repo, raw.get('siblings'), deadline=time.monotonic() + 25.0)
    siblings = _siblings_with_sizes(raw.get('siblings'), tree)
    gguf_files = _gguf_files(siblings)
    downloadable_files = _model_files(siblings, gguf_only=use_gguf_only)
    files = gguf_files if use_gguf_only else downloadable_files
    preferred = _preferred_gguf_file(files)
    enriched_raw = dict(raw)
    enriched_raw['siblings'] = siblings
    summary = _summary_from_model(enriched_raw)
    # Prefer the recommended quant size for the detail summary when siblings omit them.
    if preferred and isinstance(preferred.get('size_gb'), (int, float)) and float(preferred['size_gb']) > 0:
        summary['size_gb'] = float(preferred['size_gb'])
        summary['size_label'] = f"{float(preferred['size_gb']):g} GB"
    description = _truncate_text(_card_description(card) or summary.get('description') or '')
    title = str(summary.get('title') or summary.get('label') or repo).strip()
    if readme:
        title = _title_from_readme(readme, title) or title
        if not description:
            description = _description_from_readme(readme, limit=320)

    from core.config import load_config
    from core.hf_local_match import find_repo_local_installs, is_catalog_ready_to_load, local_installs_for_files

    config = load_config()
    filenames = [str(item.get('filename') or '') for item in files if item.get('filename')]
    local_installs = local_installs_for_files(repo, filenames, cfg=config)
    repo_installs = find_repo_local_installs(repo, cfg=config)

    from core.hf_model_fit import assess_hf_model_fit

    model_payload = {
        **summary,
        'title': title,
        'description': description,
        'tags': tags,
        'gguf_files': gguf_files,
        'download_files': files,
        'default_download': preferred.get('filename') if preferred else '',
        'accelerator_only': _is_accelerator_only_repo(
            siblings,
            repo_id=repo,
            size_gb=float(preferred['size_gb']) if preferred and isinstance(preferred.get('size_gb'), (int, float)) else summary.get('size_gb'),
        ),
        'local_installs': local_installs,
        'local_ready': bool(repo_installs),
        'catalog_ready_to_load': is_catalog_ready_to_load(repo, title=title, tags=tags, cfg=config),
        'readme': readme,
        'url': f'{HF_BASE}/{repo}',
        'gated': bool(raw.get('gated')),
        'private': bool(raw.get('private')),
        'category': str(category or 'dflash'),
    }
    model_payload.update(
        assess_hf_model_fit(
            model_payload,
            cfg=config,
            gguf_files=gguf_files,
            download_files=files,
        ),
    )

    return {
        'success': True,
        'model': model_payload,
    }


def _complete_transformers_repo_files(repo_id: str, dest_dir: Path, *, job_id: str = '') -> None:
    """Fetch config/tokenizer files when only weights were downloaded as a single file."""
    try:
        target = dest_dir.expanduser().resolve()
    except OSError:
        return
    if not target.is_dir():
        return
    has_weights = (target / 'model.safetensors').is_file() or any(target.glob('model-*.safetensors'))
    if not has_weights:
        return
    if (target / 'config.json').is_file() and (target / 'tokenizer.json').is_file():
        return
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        if job:
            job['status'] = 'downloading'
            job['progress'] = max(float(job.get('progress') or 0), 95.0)
            job['kind'] = 'repo-complete'
    try:
        snapshot_download(
            repo_id=str(repo_id),
            local_dir=str(target),
            local_dir_use_symlinks=False,
            token=token.strip() if token else None,
        )
    except Exception as exc:
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['post_action_error'] = f'companion files: {exc}'
        return
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        if job:
            job['path'] = str(target)
            job['progress'] = 100.0


_DOWNLOAD_CHUNK = 8 * 1024 * 1024
_MIN_PARALLEL_BYTES = 32 * 1024 * 1024
_MAX_DOWNLOAD_CONNECTIONS = 8
_DOWNLOAD_RETRY_SECONDS = 5.0
_PENDING_SAVE_INTERVAL_SECONDS = 12.0
_PENDING_SAVE_BYTES = 5 * 1024 * 1024
_last_pending_save_at: dict[str, float] = {}
_last_pending_save_bytes: dict[str, int] = {}
_BENCHMARK_REPO_ID = 'bartowski/Qwen3.8-27B-GGUF'
_BENCHMARK_FILENAME = 'Qwen3.8-27B-Q6_K_L.gguf'
_DEFAULT_BENCHMARK_MIB = 32


def get_download_parallel_connections(cfg: dict[str, Any] | None = None) -> int:
    config = cfg or load_config()
    settings = normalize_download_settings(config.get('download_settings'))
    return int(settings['parallel_connections'])


def _hf_download_headers() -> dict[str, str]:
    headers = {
        'User-Agent': 'DFlash-Console/0.1',
        'Accept-Encoding': 'identity',
    }
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    return headers


def _is_transient_download_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, 'code', 0) or 0)
        return code in (408, 429, 500, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, 'reason', None)
        if reason is None or isinstance(reason, str):
            return True
        if isinstance(reason, (TimeoutError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return True
        if isinstance(reason, OSError):
            return True
        return False
    if isinstance(exc, OSError):
        text = str(exc).lower()
        return 'ended early' in text or 'incomplete' in text
    return False


def _set_job_retrying(job_id: str, retrying: bool) -> None:
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return
        if retrying:
            job['retrying'] = True
            job['speed_bps'] = 0.0
            job['eta_seconds'] = None
        else:
            job.pop('retrying', None)


def _split_byte_ranges(total: int, connections: int) -> list[tuple[int, int]]:
    size = max(1, int(total))
    parts = max(1, min(int(connections), size))
    chunk = size // parts
    ranges: list[tuple[int, int]] = []
    start = 0
    for index in range(parts):
        end = size - 1 if index == parts - 1 else start + chunk - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def _connection_count(total: int, *, ranged: bool, parallel_connections: int | None = None) -> int:
    if not ranged or total < _MIN_PARALLEL_BYTES:
        return 1
    desired = parallel_connections
    if desired is None:
        desired = get_download_parallel_connections()
    try:
        count = int(desired)
    except (TypeError, ValueError):
        count = int(normalize_download_settings({})['parallel_connections'])
    return max(1, min(_MAX_DOWNLOAD_CONNECTIONS, count))


def _download_range_to_file(
    url: str,
    headers: dict[str, str],
    dest: Path,
    start: int,
    end: int,
) -> int:
    req_headers = dict(headers)
    req_headers['Range'] = f'bytes={start}-{end}'
    req = urllib.request.Request(url, headers=req_headers)
    written = 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        with dest.open('r+b') as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = resp.read(min(_DOWNLOAD_CHUNK, remaining))
                if not chunk:
                    raise OSError(f'range {start}-{end} ended early')
                handle.write(chunk)
                nbytes = len(chunk)
                written += nbytes
                remaining -= nbytes
    return written


def _benchmark_single_stream(url: str, headers: dict[str, str], dest: Path, end: int) -> float:
    dest.unlink(missing_ok=True)
    with dest.open('wb') as handle:
        handle.truncate(end + 1)
    started = time.perf_counter()
    _download_range_to_file(url, headers, dest, 0, end)
    elapsed = time.perf_counter() - started
    return (end + 1) / max(elapsed, 1e-6)


def _benchmark_parallel_stream(url: str, headers: dict[str, str], dest: Path, end: int, connections: int) -> float:
    dest.unlink(missing_ok=True)
    total = end + 1
    with dest.open('wb') as handle:
        handle.truncate(total)
    ranges = _split_byte_ranges(total, connections)
    errors: list[BaseException] = []

    def worker(start: int, stop: int) -> None:
        try:
            _download_range_to_file(url, headers, dest, start, stop)
        except BaseException as exc:
            errors.append(exc)

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=item, daemon=True) for item in ranges]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    if errors:
        raise errors[0]
    return total / max(elapsed, 1e-6)


def benchmark_download_connections(
    connections: list[int] | None = None,
    *,
    test_mib: int = _DEFAULT_BENCHMARK_MIB,
    repo_id: str = _BENCHMARK_REPO_ID,
    filename: str = _BENCHMARK_FILENAME,
) -> dict[str, Any]:
    """Measure HF download throughput for 1..N parallel range connections."""
    test_mib = max(8, min(128, int(test_mib)))
    test_bytes = test_mib * 1024 * 1024
    wanted = connections or [1, 2, 4, 6, 8]
    normalized: list[int] = []
    seen: set[int] = set()
    for item in wanted:
        try:
            count = max(1, min(_MAX_DOWNLOAD_CONNECTIONS, int(item)))
        except (TypeError, ValueError):
            continue
        if count in seen:
            continue
        seen.add(count)
        normalized.append(count)
    if not normalized:
        normalized = [1, 2, 4, 6, 8]

    url = f'{HF_BASE}/{repo_id}/resolve/main/{urllib.parse.quote(filename, safe="/")}'
    headers = _hf_download_headers()
    try:
        final_url, file_total, ranged = _probe_hf_download(url, headers)
    except Exception as exc:
        return {'success': False, 'error': str(exc)}

    if not ranged or file_total <= 0:
        return {
            'success': False,
            'error': 'Benchmark file does not support ranged downloads — parallel mode unavailable.',
        }

    end = min(test_bytes, file_total) - 1
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix='hf-bench-') as tmp:
        tmp_path = Path(tmp)
        for count in normalized:
            dest = tmp_path / f'sample-{count}.part'
            row: dict[str, Any] = {'connections': count}
            try:
                if count <= 1:
                    bps = _benchmark_single_stream(final_url, headers, dest, end)
                else:
                    bps = _benchmark_parallel_stream(final_url, headers, dest, end, count)
                row['bps'] = round(bps, 1)
                row['mbps'] = round(bps / (1024 * 1024), 2)
                row['success'] = True
            except Exception as exc:
                row['success'] = False
                row['error'] = str(exc)
            results.append(row)
            time.sleep(0.35)

    ok_rows = [row for row in results if row.get('success')]
    best = max(ok_rows, key=lambda row: float(row.get('bps') or 0), default=None)
    single = next((row for row in ok_rows if int(row.get('connections') or 0) <= 1), None)
    gain_pct = None
    if best and single and float(single.get('bps') or 0) > 0:
        gain_pct = round((float(best['bps']) / float(single['bps']) - 1.0) * 100.0, 1)

    return {
        'success': bool(ok_rows),
        'repo_id': repo_id,
        'filename': filename,
        'test_mib': test_mib,
        'test_bytes': end + 1,
        'results': results,
        'best_connections': int(best['connections']) if best else None,
        'best_mbps': best.get('mbps') if best else None,
        'gain_vs_single_pct': gain_pct,
    }


def _refresh_job_speed(job: dict[str, Any], *, now: float | None = None) -> None:
    current = float(now if now is not None else time.time())
    read = int(job.get('bytes_read') or 0)
    last_at = float(job.get('_speed_at') or job.get('started_at') or current)
    last_bytes = int(job.get('_speed_bytes') or 0)
    elapsed = current - last_at
    if elapsed < 0.35 and job.get('speed_bps'):
        return
    if elapsed < 0.2:
        return
    instant = max(0.0, (read - last_bytes) / elapsed)
    previous = float(job.get('speed_bps') or 0)
    job['speed_bps'] = round((instant * 0.55 + previous * 0.45) if previous else instant, 1)
    job['_speed_at'] = current
    job['_speed_bytes'] = read
    remain = int(job.get('bytes_total') or 0) - read
    speed = float(job.get('speed_bps') or 0)
    job['eta_seconds'] = int(remain / speed) if speed > 1 and remain > 0 else None


def _add_job_bytes(job_id: str, nbytes: int, total: int | None) -> None:
    if nbytes <= 0 and not total:
        return
    save_pending = False
    read = 0
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return
        job['bytes_read'] = int(job.get('bytes_read') or 0) + max(0, int(nbytes))
        if total:
            job['bytes_total'] = int(total)
        if int(nbytes) > 0:
            job.pop('retrying', None)
        read = int(job.get('bytes_read') or 0)
        known = int(job.get('bytes_total') or 0)
        job['progress'] = round(read / known * 100, 1) if known > 0 and read > 0 else None
        _refresh_job_speed(job)
        save_pending = int(nbytes) > 0
    if save_pending:
        _maybe_save_pending_during_download(job_id, read)


def _public_download_job(job: dict[str, Any]) -> dict[str, Any]:
    row = dict(job)
    for key in ('_speed_at', '_speed_bytes', 'post_action'):
        row.pop(key, None)
    return row


def _estimate_parallel_bytes_written(part: Path, total: int, connections: int) -> int:
    if not part.is_file() or total <= 0:
        return 0
    ranges = _split_byte_ranges(total, max(1, int(connections)))
    written = 0
    for start, end in ranges:
        if _range_appears_complete(part, start, end):
            written += end - start + 1
    progress = _load_part_progress(part)
    progress_bytes = int(progress.get('bytes_read') or 0)
    return min(int(total), max(written, progress_bytes))


def _inspect_download_files(dest: Path, *, total: int | None = None) -> dict[str, Any]:
    final = dest
    part = dest.with_suffix(dest.suffix + '.part')
    total_known = int(total or 0)
    connections = _connection_count(total_known, ranged=True, parallel_connections=get_download_parallel_connections()) if total_known > 0 else 1
    if final.is_file() and _looks_like_complete_download(final, total=total_known or None):
        try:
            size = int(final.stat().st_size)
        except OSError:
            size = 0
        return {
            'final_exists': True,
            'part_exists': part.is_file(),
            'disk_bytes': size,
            'complete': True,
            'needs_finalize': False,
        }
    if part.is_file():
        try:
            part_size = int(part.stat().st_size)
        except OSError:
            part_size = 0
        estimated = _estimate_parallel_bytes_written(part, total_known, connections) if total_known > 0 else part_size
        if total_known > 0 and estimated >= total_known and _looks_like_complete_download(part, total=total_known):
            return {
                'final_exists': False,
                'part_exists': True,
                'disk_bytes': total_known,
                'complete': True,
                'needs_finalize': True,
            }
        return {
            'final_exists': False,
            'part_exists': True,
            'disk_bytes': estimated,
            'complete': False,
            'needs_finalize': False,
        }
    return {
        'final_exists': False,
        'part_exists': False,
        'disk_bytes': 0,
        'complete': False,
        'needs_finalize': False,
    }


def _has_resumable_part(dest: Path, *, total: int | None = None) -> bool:
    part = dest.with_suffix(dest.suffix + '.part')
    if not part.is_file():
        return False
    total_known = int(total or 0)
    inspect = _inspect_download_files(dest, total=total_known or None)
    return bool(inspect.get('part_exists')) and not inspect.get('complete')


def _enrich_download_job(job: dict[str, Any]) -> dict[str, Any]:
    row = _public_download_job(job)
    path_text = str(job.get('path') or '').strip()
    if not path_text:
        return row
    try:
        dest = Path(path_text).expanduser().resolve()
    except OSError:
        return row
    total = int(row.get('bytes_total') or 0)
    inspect = _inspect_download_files(dest, total=total or None)
    row['disk_bytes'] = int(inspect.get('disk_bytes') or 0)
    row['disk_complete'] = bool(inspect.get('complete'))
    row['has_part_file'] = bool(inspect.get('part_exists'))
    row['needs_finalize'] = bool(inspect.get('needs_finalize'))
    status = str(row.get('status') or '')
    disk_bytes = int(row.get('disk_bytes') or 0)
    if status in {'done', 'error', 'incomplete'} and not inspect.get('complete') and inspect.get('part_exists'):
        row['status'] = 'incomplete'
        row['bytes_read'] = disk_bytes
        if total > 0 and disk_bytes > 0:
            row['progress'] = round(disk_bytes / total * 100, 1)
        row.pop('finished_at', None)
    elif status == 'downloading' and disk_bytes > int(row.get('bytes_read') or 0):
        row['bytes_read'] = disk_bytes
        if total > 0 and disk_bytes > 0:
            row['progress'] = round(min(disk_bytes, total) / total * 100, 1)
    elif status == 'done' and inspect.get('complete') and disk_bytes > 0:
        row['bytes_read'] = disk_bytes if total <= 0 else min(disk_bytes, total)
    return row


def _file_arrived_at(path: Path) -> float:
    st = path.stat()
    birth = getattr(st, 'st_birthtime', None)
    if birth and float(birth) > 0:
        return float(birth)
    if os.name == 'nt':
        return float(st.st_ctime)
    return float(st.st_mtime)


def _disk_job_id(path: Path) -> str:
    key = str(path).replace('\\', '/').lower().encode('utf-8')
    return 'disk-' + hashlib.sha1(key).hexdigest()[:16]


def _infer_repo_id(path: Path) -> str:
    parts = list(path.parts)
    lower = [part.lower() for part in parts]
    for marker in ('models', 'hub', 'huggingface'):
        if marker not in lower:
            continue
        idx = lower.index(marker)
        if idx + 2 >= len(parts):
            continue
        org = parts[idx + 1]
        repo = parts[idx + 2]
        if org.startswith('models--'):
            return org[len('models--'):].replace('--', '/', 1)
        if org and repo and '/' not in org and '/' not in repo:
            return f'{org}/{repo}'
    if len(path.parts) >= 3:
        return f'{path.parent.parent.name}/{path.parent.name}'
    return ''


def _is_disk_history_file(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith('.') or name.endswith(('.tmp', '.part', '.incomplete', '.json')):
        return False
    return any(name.endswith(ext) for ext in _DOWNLOAD_EXTENSIONS)


def _discover_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    if _discover_roots_override is not None:
        return [Path(root) for root in _discover_roots_override]
    from core.model_paths import enabled_scan_roots, get_download_dir

    roots: list[Path] = []
    seen: set[str] = set()
    candidates = [get_download_dir(cfg)]
    candidates.extend(path for path, _source in enabled_scan_roots(cfg))
    for raw in candidates:
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        key = str(path).lower()
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        roots.append(path)
    return roots


def _collect_disk_download_jobs(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    grouped: dict[Path, list[Path]] = {}
    found = 0
    for root in _discover_roots(cfg):
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    name for name in dirnames
                    if not name.startswith('.') and name.lower() not in {'__pycache__', '.git', 'blobs'}
                ]
                current = Path(dirpath)
                for name in filenames:
                    path = current / name
                    if not _is_disk_history_file(path):
                        continue
                    try:
                        if path.stat().st_size < _MIN_DISK_FILE_BYTES:
                            continue
                    except OSError:
                        continue
                    grouped.setdefault(path.parent, []).append(path)
                    found += 1
                    if found >= 800:
                        break
                if found >= 800:
                    break
        except OSError:
            continue
        if found >= 800:
            break

    root_keys = {str(root).replace('\\', '/').lower() for root in _discover_roots(cfg)}
    rows: list[dict[str, Any]] = []
    for parent, files in grouped.items():
        files.sort(key=lambda item: item.stat().st_size if item.exists() else 0, reverse=True)
        at_root = str(parent).replace('\\', '/').lower() in root_keys
        batches = [[item] for item in files] if at_root else [files]
        for batch in batches:
            primary = batch[0]
            try:
                arrived = max(_file_arrived_at(item) for item in batch)
                size = sum(item.stat().st_size for item in batch if item.exists())
            except OSError:
                continue
            repo_id = _infer_repo_id(primary)
            filename = primary.name if len(batch) == 1 else f'{len(batch)} files'
            path = primary if len(batch) == 1 else parent
            rows.append({
                'id': _disk_job_id(path),
                'repo_id': repo_id,
                'filename': filename,
                'status': 'done',
                'progress': 100.0,
                'bytes_read': size,
                'bytes_total': size,
                'speed_bps': 0.0,
                'eta_seconds': None,
                'path': str(path),
                'library_id': '',
                'started_at': arrived,
                'finished_at': arrived,
                'kind': 'disk',
                'origin': 'disk',
            })
    return rows


def _known_history_paths() -> set[str]:
    known: set[str] = set()
    for job in _download_jobs.values():
        raw = str(job.get('path') or '').strip()
        if not raw:
            continue
        known.add(raw.replace('\\', '/').lower())
        try:
            known.add(str(Path(raw).resolve()).replace('\\', '/').lower())
        except OSError:
            pass
    return known


def _merge_disk_download_history(*, force: bool = False) -> int:
    global _disk_scan_at
    now = time.time()
    if not force and _disk_scan_at and now - _disk_scan_at < _DISK_SCAN_TTL:
        return 0
    _disk_scan_at = now
    discovered = _collect_disk_download_jobs()
    added = 0
    with _jobs_lock:
        known_paths = _known_history_paths()
        for row in discovered:
            job_id = str(row.get('id') or '')
            if not job_id or job_id in _download_jobs or job_id in _cleared_ids:
                continue
            path_key = str(row.get('path') or '').replace('\\', '/').lower()
            if path_key and path_key in known_paths:
                continue
            _download_jobs[job_id] = row
            added += 1
    if added:
        try:
            _save_download_history()
        except OSError:
            pass
    return added


def _ensure_download_history_loaded() -> None:
    global _history_loaded
    if _history_loaded:
        return
    _history_loaded = True
    if not _HISTORY_PATH.is_file():
        return
    try:
        payload = json.loads(_HISTORY_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    cleared = payload.get('cleared_ids')
    if isinstance(cleared, list):
        _cleared_ids.update(str(item).strip() for item in cleared if str(item).strip())
    rows = payload.get('jobs')
    if not isinstance(rows, list):
        return
    with _jobs_lock:
        for row in rows:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get('id') or '').strip()
            if not job_id or job_id in _download_jobs or job_id in _cleared_ids:
                continue
            if str(row.get('status') or '') == 'downloading':
                continue
            _download_jobs[job_id] = dict(row)


def _save_download_history() -> None:
    with _jobs_lock:
        finished = [
            _public_download_job(job)
            for job in _download_jobs.values()
            if str(job.get('status') or '') != 'downloading'
        ]
        cleared = sorted(_cleared_ids)
    finished.sort(key=lambda row: float(row.get('finished_at') or row.get('started_at') or 0), reverse=True)
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(
        json.dumps({'version': 2, 'cleared_ids': cleared, 'jobs': finished[:_MAX_HISTORY]}, indent=2),
        encoding='utf-8',
    )


def _pending_job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    row = _public_download_job(job)
    for key in ('path', 'library_id', 'kind', 'started_at', 'bytes_read', 'bytes_total'):
        if key in job:
            row[key] = job.get(key)
    post_action = job.get('post_action')
    if isinstance(post_action, dict):
        row['post_action'] = post_action
    return row


def _save_pending_downloads() -> None:
    with _jobs_lock:
        pending = [
            _pending_job_snapshot(job)
            for job in _download_jobs.values()
            if str(job.get('status') or '') == 'downloading'
        ]
    _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_PATH.write_text(
        json.dumps({'version': 1, 'jobs': pending}, indent=2),
        encoding='utf-8',
    )


def _clear_pending_downloads() -> None:
    try:
        if _PENDING_PATH.is_file():
            _PENDING_PATH.unlink()
    except OSError:
        pass


def _load_pending_downloads() -> list[dict[str, Any]]:
    if not _PENDING_PATH.is_file():
        return []
    try:
        payload = json.loads(_PENDING_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get('jobs') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _remove_pending_job(job_id: str) -> None:
    key = str(job_id or '').strip()
    if not key:
        return
    rows = [row for row in _load_pending_downloads() if str(row.get('id') or '').strip() != key]
    if not rows:
        _clear_pending_downloads()
        return
    _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_PATH.write_text(json.dumps({'version': 1, 'jobs': rows}, indent=2), encoding='utf-8')


def _mark_job_finished(job_id: str, status: str, **fields: Any) -> None:
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return
        job['status'] = status
        job['finished_at'] = time.time()
        if status == 'done':
            job['progress'] = 100.0
            job['speed_bps'] = 0.0
            job['eta_seconds'] = None
        job.update(fields)
    try:
        _save_download_history()
    except OSError:
        pass
    if status != 'downloading':
        _remove_pending_job(job_id)
        try:
            _save_pending_downloads()
        except OSError:
            pass


def _part_progress_path(part: Path) -> Path:
    return part.with_name(part.name + '.progress.json')


def _range_key(start: int, end: int) -> str:
    return f'{int(start)}-{int(end)}'


def _load_part_progress(part: Path) -> dict[str, Any]:
    path = _part_progress_path(part)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_part_progress(part: Path, *, total: int, completed_ranges: list[str], bytes_read: int) -> None:
    path = _part_progress_path(part)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            'version': 1,
            'total': int(total),
            'bytes_read': int(bytes_read),
            'completed_ranges': sorted(set(completed_ranges)),
        }, indent=2),
        encoding='utf-8',
    )


def _delete_part_progress(part: Path) -> None:
    try:
        _part_progress_path(part).unlink(missing_ok=True)
    except OSError:
        pass


def _range_appears_complete(part: Path, start: int, end: int) -> bool:
    """Best-effort check that a parallel range was fully written (tail is non-zero)."""
    span = end - start + 1
    if span <= 0:
        return True
    try:
        with part.open('rb') as handle:
            check_len = min(4096, span)
            handle.seek(end - check_len + 1)
            chunk = handle.read(check_len)
            return len(chunk) == check_len and any(byte != 0 for byte in chunk)
    except OSError:
        return False


def _maybe_save_pending_during_download(job_id: str, bytes_read: int) -> None:
    key = str(job_id or '').strip()
    if not key:
        return
    now = time.time()
    last_at = float(_last_pending_save_at.get(key) or 0)
    last_bytes = int(_last_pending_save_bytes.get(key) or 0)
    if now - last_at < _PENDING_SAVE_INTERVAL_SECONDS and bytes_read - last_bytes < _PENDING_SAVE_BYTES:
        return
    try:
        _save_pending_downloads()
    except OSError:
        pass
    _last_pending_save_at[key] = now
    _last_pending_save_bytes[key] = bytes_read


def _probe_hf_download(url: str, headers: dict[str, str]) -> tuple[str, int, bool]:
    req = urllib.request.Request(url, headers=headers, method='HEAD')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            accept = 'bytes' in str(resp.headers.get('Accept-Ranges') or '').lower()
            return resp.geturl() or url, total, accept and total > 0
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return url, 0, False


def _download_range(
    job_id: str,
    url: str,
    headers: dict[str, str],
    dest: Path,
    start: int,
    end: int,
    total: int,
) -> None:
    req_headers = dict(headers)
    req_headers['Range'] = f'bytes={start}-{end}'
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=600) as resp:
        with dest.open('r+b') as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = resp.read(min(_DOWNLOAD_CHUNK, remaining))
                if not chunk:
                    raise OSError(f'range {start}-{end} ended early')
                handle.write(chunk)
                remaining -= len(chunk)
                _add_job_bytes(job_id, len(chunk), total)


def _download_parallel(
    job_id: str,
    url: str,
    headers: dict[str, str],
    dest: Path,
    total: int,
    connections: int,
    *,
    resume: bool = False,
) -> None:
    ranges = _split_byte_ranges(total, connections)
    progress = _load_part_progress(dest) if resume else {}
    completed = set(progress.get('completed_ranges') or [])
    if not dest.is_file() or dest.stat().st_size != total:
        with dest.open('wb') as handle:
            handle.truncate(total)
        completed.clear()
    elif not resume:
        with dest.open('wb') as handle:
            handle.truncate(total)
        completed.clear()
    errors: list[BaseException] = []

    def worker(start: int, end: int) -> None:
        key = _range_key(start, end)
        try:
            if key in completed or _range_appears_complete(dest, start, end):
                return
            _download_range(job_id, url, headers, dest, start, end, total)
            completed.add(key)
            with _jobs_lock:
                job = _download_jobs.get(job_id)
                bytes_read = int(job.get('bytes_read') or 0) if job else 0
            _save_part_progress(dest, total=total, completed_ranges=sorted(completed), bytes_read=bytes_read)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=item, daemon=True) for item in ranges]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    if dest.stat().st_size != total:
        raise OSError('incomplete parallel download')
    _delete_part_progress(dest)


def _download_single(
    job_id: str,
    url: str,
    headers: dict[str, str],
    dest: Path,
    total: int | None = None,
) -> None:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=600) as resp:
        known = int(total or resp.headers.get('Content-Length') or 0)
        with dest.open('wb') as handle:
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                _add_job_bytes(job_id, len(chunk), known or None)


def _download_single_resume(
    job_id: str,
    url: str,
    headers: dict[str, str],
    dest: Path,
    start_offset: int,
    total: int,
) -> None:
    offset = max(0, int(start_offset))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        with dest.open('wb') as handle:
            if offset > 0:
                handle.truncate(offset)
    req_headers = dict(headers)
    req_headers['Range'] = f'bytes={offset}-'
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=600) as resp:
        with dest.open('r+b') as handle:
            handle.seek(offset)
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                _add_job_bytes(job_id, len(chunk), total)


def _looks_like_complete_download(path: Path, *, total: int | None = None) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 1024:
        return False
    if total is not None and int(total) > 0 and size < int(total):
        return False
    try:
        with path.open('rb') as handle:
            head = handle.read(4)
            if head == b'GGUF':
                if size <= 64:
                    return False
                handle.seek(max(0, size - 8192))
                tail = handle.read(8192)
                return any(byte != 0 for byte in tail)
            if size > 4096:
                handle.seek(max(0, size - 4096))
                tail = handle.read(4096)
                return any(byte != 0 for byte in tail)
            return size > 0
    except OSError:
        return False


def _cleanup_incomplete_download(dest: Path, *, total: int | None = None) -> bool:
    removed = False
    part = dest.with_suffix(dest.suffix + '.part')
    if dest.is_file() and not _looks_like_complete_download(dest, total=total):
        try:
            dest.unlink()
            removed = True
        except OSError:
            pass
    if part.is_file():
        try:
            part.unlink()
            removed = True
        except OSError:
            pass
        _delete_part_progress(part)
    return removed


def _download_worker_once(job_id: str, repo_id: str, filename: str, dest: Path, *, resume: bool = False) -> None:
    url = f'{HF_BASE}/{repo_id}/resolve/main/{urllib.parse.quote(filename, safe="/")}'
    headers = _hf_download_headers()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    part_size = tmp.stat().st_size if tmp.is_file() else 0
    final_url, total, ranged = _probe_hf_download(url, headers)
    if dest.is_file():
        if _looks_like_complete_download(dest, total=total or None):
            _mark_job_finished(job_id, 'done', path=str(dest))
            return
        try:
            dest.unlink()
        except OSError:
            pass
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        known_read = int(job.get('bytes_read') or 0) if job else 0
    if total > 0:
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['bytes_total'] = int(total)
                if resume and known_read > 0:
                    job['bytes_read'] = min(known_read, int(total))
                    known_read = int(job['bytes_read'] or 0)
                    job['progress'] = round(known_read / total * 100, 1) if known_read > 0 else None
                elif resume and tmp.is_file() and total > 0:
                    connections = _connection_count(total, ranged=ranged, parallel_connections=get_download_parallel_connections())
                    estimated = _estimate_parallel_bytes_written(tmp, total, connections) if part_size >= total else part_size
                    if estimated > 0 and estimated < total:
                        job['bytes_read'] = estimated
                        known_read = estimated
                        job['progress'] = round(estimated / total * 100, 1)
        _add_job_bytes(job_id, 0, total)
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            known_read = int(job.get('bytes_read') or 0) if job else 0
    if (
        total > 0
        and known_read >= total
        and tmp.is_file()
        and _looks_like_complete_download(tmp, total=total)
    ):
        tmp.replace(dest)
        _delete_part_progress(tmp)
        if dest.suffix.lower() == '.safetensors':
            _complete_transformers_repo_files(repo_id, dest.parent, job_id=job_id)
        post_action = None
        with _jobs_lock:
            post_action = (_download_jobs.get(job_id) or {}).get('post_action')
        extra: dict[str, Any] = {'path': str(dest)}
        if isinstance(post_action, dict) and post_action.get('type') == 'wire_vision':
            try:
                from core.vision_setup import wire_vision_after_download

                wire_vision_after_download({**post_action, 'mmproj_path': str(dest)})
            except Exception as exc:
                extra['post_action_error'] = str(exc)
        _mark_job_finished(job_id, 'done', **extra)
        from core.local_models import invalidate_model_catalog_cache
        invalidate_model_catalog_cache()
        return

    connections = _connection_count(total, ranged=ranged, parallel_connections=get_download_parallel_connections())
    resumed = False
    if resume and tmp.is_file() and total > 0:
        if known_read <= 0 and part_size >= total:
            known_read = _estimate_parallel_bytes_written(tmp, total, connections)
            if known_read > 0:
                with _jobs_lock:
                    job = _download_jobs.get(job_id)
                    if job:
                        job['bytes_read'] = min(known_read, int(total))
                        known_read = int(job.get('bytes_read') or 0)
                        job['progress'] = round(known_read / total * 100, 1) if known_read > 0 else None
        if known_read > 0 and known_read < total:
            if connections > 1 and ranged:
                _download_parallel(job_id, final_url, headers, tmp, total, connections, resume=True)
                resumed = True
            elif ranged:
                offset = known_read if part_size >= total else (part_size if part_size < total else known_read)
                _download_single_resume(job_id, final_url, headers, tmp, offset, total)
                resumed = True
    elif resume and tmp.is_file() and part_size > 0 and total > 0 and part_size < total and ranged:
        _download_single_resume(job_id, final_url, headers, tmp, part_size, total)
        resumed = True

    if not resumed:
        if tmp.is_file() and known_read <= 0:
            try:
                tmp.unlink()
            except OSError:
                pass
            _delete_part_progress(tmp)
        if connections > 1 and total > 0 and ranged:
            try:
                _download_parallel(job_id, final_url, headers, tmp, total, connections, resume=False)
            except Exception as exc:
                if _is_transient_download_error(exc):
                    raise
                with _jobs_lock:
                    job = _download_jobs.get(job_id)
                    if job:
                        job['progress'] = None
                        job['speed_bps'] = 0.0
                        job['eta_seconds'] = None
                        job['_speed_at'] = time.time()
                        job['_speed_bytes'] = int(job.get('bytes_read') or 0)
                _download_single(job_id, final_url, headers, tmp, total)
        else:
            _download_single(job_id, final_url or url, headers, tmp, total or None)
    tmp.replace(dest)
    _delete_part_progress(tmp)
    if dest.suffix.lower() == '.safetensors':
        _complete_transformers_repo_files(repo_id, dest.parent, job_id=job_id)
    post_action = None
    with _jobs_lock:
        post_action = (_download_jobs.get(job_id) or {}).get('post_action')
    extra = {'path': str(dest)}
    if isinstance(post_action, dict) and post_action.get('type') == 'wire_vision':
        try:
            from core.vision_setup import wire_vision_after_download

            wire_vision_after_download({**post_action, 'mmproj_path': str(dest)})
        except Exception as exc:
            extra['post_action_error'] = str(exc)
    _mark_job_finished(job_id, 'done', **extra)
    from core.local_models import invalidate_model_catalog_cache
    invalidate_model_catalog_cache()


def _download_worker(job_id: str, repo_id: str, filename: str, dest: Path, *, resume: bool = False) -> None:
    attempt = 0
    while True:
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if not job or str(job.get('status') or '') != 'downloading':
                return
        try:
            _set_job_retrying(job_id, False)
            _download_worker_once(job_id, repo_id, filename, dest, resume=resume or attempt > 0)
            return
        except Exception as exc:
            if not _is_transient_download_error(exc):
                _mark_job_finished(job_id, 'error', error=str(exc))
                return
            with _jobs_lock:
                job = _download_jobs.get(job_id)
                if not job or str(job.get('status') or '') != 'downloading':
                    return
            _set_job_retrying(job_id, True)
            try:
                _save_pending_downloads()
            except OSError:
                pass
            time.sleep(_DOWNLOAD_RETRY_SECONDS)
            attempt += 1


def start_download(
    repo_id: str,
    filename: str,
    *,
    library_id: str | None = None,
    dest_path: str | None = None,
    post_action: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    repo = str(repo_id or '').strip().strip('/')
    name = str(filename or '').strip()
    lower = name.lower()
    if not repo or '/' not in repo or not any(lower.endswith(ext) for ext in _DOWNLOAD_EXTENSIONS):
        return {'success': False, 'error': 'repo_id and downloadable filename required'}
    if dest_path:
        dest = Path(str(dest_path)).expanduser().resolve()
        if not _is_under_allowed_model_root(dest, config):
            return {'success': False, 'error': 'download destination is not under an allowed model directory'}
        if isinstance(post_action, dict) and post_action.get('type') == 'wire_vision':
            target = Path(str(post_action.get('model_path') or '')).expanduser().resolve()
            if not target.is_file() or not _is_under_allowed_model_root(target, config):
                return {'success': False, 'error': 'vision model path is not under an allowed model directory'}
            if dest.parent != target.parent:
                return {'success': False, 'error': 'vision projector must be downloaded next to the model'}
    else:
        library = get_library_by_id(library_id, config) if library_id else None
        root = Path(str((library or {}).get('path') or get_download_dir(config))).expanduser().resolve()
        author, repo_name = repo.split('/', 1)
        dest = root / author / repo_name / Path(name).name
    from core.hf_local_match import find_local_matches

    probe_total = 0
    if dest.is_file() or dest.with_suffix(dest.suffix + '.part').is_file():
        try:
            probe_url = f'{HF_BASE}/{repo}/resolve/main/{urllib.parse.quote(name, safe="/")}'
            _, probe_total, _ = _probe_hf_download(probe_url, _hf_download_headers())
        except Exception:
            probe_total = 0
        if not _has_resumable_part(dest, total=probe_total or None):
            _cleanup_incomplete_download(dest, total=probe_total or None)

    if dest.is_file():
        if post_action and post_action.get('type') == 'wire_vision':
            try:
                from core.vision_setup import wire_vision_after_download

                wire_vision_after_download({**post_action, 'mmproj_path': str(dest)})
            except Exception as exc:
                return {'success': False, 'error': str(exc), 'path': str(dest)}
            return {
                'success': True,
                'already_installed': True,
                'path': str(dest),
                'wired': True,
                'message': 'Vision projector already present; wired to engine.',
            }
        return {
            'success': False,
            'error': 'This model file is already installed on this PC',
            'already_installed': True,
            'matches': [{
                'path': str(dest.resolve()),
                'filename': dest.name,
                'library_label': 'local',
                'match_type': 'exact_path',
            }],
            'path': str(dest.resolve()),
        }
    if not dest_path:
        existing = find_local_matches(repo, name, cfg=config)
        if existing:
            return {
                'success': False,
                'error': 'This model file is already installed on this PC',
                'already_installed': True,
                'matches': existing,
                'path': existing[0].get('path'),
            }
        from core.library_import import find_existing_in_console_library

        console_existing = find_existing_in_console_library(name, cfg=config)
        if console_existing:
            return {
                'success': False,
                'error': 'This model file is already in the DFlash Console library',
                'already_installed': True,
                'matches': console_existing,
                'path': console_existing[0].get('path'),
            }
    suffix = abs(hash(repo + name + str(dest))) % 1_000_000
    job_id = f'{int(time.time())}-{suffix:06d}'
    with _jobs_lock:
        _download_jobs[job_id] = {
            'id': job_id,
            'repo_id': repo,
            'filename': name,
            'status': 'downloading',
            'progress': None,
            'bytes_read': 0,
            'bytes_total': None,
            'speed_bps': 0.0,
            'eta_seconds': None,
            'path': str(dest),
            'library_id': (library_id or '') if not dest_path else '',
            'started_at': time.time(),
            'finished_at': None,
            'post_action': post_action,
            'kind': 'vision' if isinstance(post_action, dict) else '',
        }
    try:
        _save_pending_downloads()
    except OSError:
        pass
    thread = threading.Thread(target=_download_worker, args=(job_id, repo, name, dest), daemon=True)
    thread.start()
    return {
        'success': True,
        'job_id': job_id,
        'path': str(dest),
        'library_id': (library_id or '') if not dest_path else '',
    }


def _repo_download_worker(job_id: str, repo_id: str, dest: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        _mark_job_finished(job_id, 'error', error=f'huggingface_hub is not installed: {exc}')
        return
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['progress'] = 5.0
        local_dir = snapshot_download(
            repo_id=repo_id,
            local_dir=str(dest),
            local_dir_use_symlinks=False,
            token=token.strip() if token else None,
        )
        _mark_job_finished(job_id, 'done', path=str(local_dir))
        from core.local_models import invalidate_model_catalog_cache
        invalidate_model_catalog_cache()
    except Exception as exc:
        _mark_job_finished(job_id, 'error', error=str(exc))


def start_repo_download(
    repo_id: str,
    *,
    library_id: str | None = None,
    dest_path: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Download a full Hugging Face model repository into the models library."""
    config = cfg or load_config()
    repo = str(repo_id or '').strip().strip('/')
    if not repo or '/' not in repo:
        return {'success': False, 'error': 'repo_id is required (org/name)'}
    if dest_path:
        dest = Path(str(dest_path)).expanduser().resolve()
        if not _is_under_allowed_model_root(dest, config):
            return {'success': False, 'error': 'download destination is not under an allowed model directory'}
    else:
        library = get_library_by_id(library_id, config) if library_id else None
        root = Path(str((library or {}).get('path') or get_download_dir(config))).expanduser().resolve()
        author, repo_name = repo.split('/', 1)
        dest = root / author / repo_name
    if dest.is_dir() and any(dest.iterdir()):
        from core.hf_local_match import find_repo_local_installs
        existing = find_repo_local_installs(repo, cfg=config)
        if existing:
            return {
                'success': False,
                'error': 'This model repository is already installed on this PC',
                'already_installed': True,
                'matches': existing,
                'path': existing[0].get('path'),
            }
    suffix = abs(hash(repo + str(dest))) % 1_000_000
    job_id = f'{int(time.time())}-{suffix:06d}'
    with _jobs_lock:
        _download_jobs[job_id] = {
            'id': job_id,
            'repo_id': repo,
            'filename': '',
            'status': 'downloading',
            'progress': None,
            'bytes_read': 0,
            'bytes_total': None,
            'speed_bps': 0.0,
            'eta_seconds': None,
            'path': str(dest),
            'library_id': (library_id or '') if not dest_path else '',
            'started_at': time.time(),
            'finished_at': None,
            'post_action': None,
            'kind': 'repo',
        }
    thread = threading.Thread(target=_repo_download_worker, args=(job_id, repo, dest), daemon=True)
    thread.start()
    return {
        'success': True,
        'job_id': job_id,
        'path': str(dest),
        'library_id': (library_id or '') if not dest_path else '',
        'kind': 'repo',
    }


def repair_stale_download_jobs(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-open finished download rows that still have an incomplete .part file on disk."""
    config = cfg or load_config()
    _ensure_download_history_loaded()
    repaired: list[str] = []
    with _jobs_lock:
        rows = list(_download_jobs.items())
    for job_id, job in rows:
        if str(job.get('status') or '') not in {'done', 'error', 'incomplete'}:
            continue
        path_text = str(job.get('path') or '').strip()
        if not path_text:
            continue
        try:
            dest = Path(path_text).expanduser().resolve()
        except OSError:
            continue
        if not _is_under_allowed_model_root(dest, config):
            continue
        inspect = _inspect_download_files(dest, total=int(job.get('bytes_total') or 0) or None)
        if inspect.get('complete'):
            if inspect.get('needs_finalize') and dest.with_suffix(dest.suffix + '.part').is_file():
                with _jobs_lock:
                    live = _download_jobs.get(job_id)
                    if not live:
                        continue
                    live['status'] = 'downloading'
                    live['finished_at'] = None
                    live['error'] = None
                    live['bytes_read'] = int(inspect.get('disk_bytes') or 0)
                repaired.append(job_id)
            continue
        if not inspect.get('part_exists'):
            continue
        disk_bytes = int(inspect.get('disk_bytes') or 0)
        with _jobs_lock:
            live = _download_jobs.get(job_id)
            if not live:
                continue
            live['status'] = 'downloading'
            live['finished_at'] = None
            live['error'] = None
            live['bytes_read'] = disk_bytes
            total = int(live.get('bytes_total') or 0)
            live['progress'] = round(disk_bytes / total * 100, 1) if total > 0 and disk_bytes > 0 else None
        repaired.append(job_id)
    for job_id in repaired:
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if not job:
                continue
            repo = str(job.get('repo_id') or '').strip()
            name = str(job.get('filename') or '').strip()
            path_text = str(job.get('path') or '').strip()
        if not repo or not name or not path_text:
            continue
        try:
            dest = Path(path_text).expanduser().resolve()
        except OSError:
            continue
        thread = threading.Thread(
            target=_download_worker,
            args=(job_id, repo, name, dest),
            kwargs={'resume': True},
            daemon=True,
        )
        thread.start()
    if repaired:
        try:
            _save_pending_downloads()
            _save_download_history()
        except OSError:
            pass
    return {'success': True, 'repaired': repaired, 'count': len(repaired)}


def resume_interrupted_downloads(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Restart Hugging Face file downloads that were active when the server last stopped."""
    config = cfg or load_config()
    _ensure_download_history_loaded()
    pending = _load_pending_downloads()
    resumed: list[str] = []
    skipped: list[str] = []
    for row in pending:
        job_id = str(row.get('id') or '').strip()
        repo = str(row.get('repo_id') or '').strip()
        name = str(row.get('filename') or '').strip()
        path_text = str(row.get('path') or '').strip()
        if not job_id or not repo or not name or not path_text:
            skipped.append(job_id or 'invalid')
            continue
        try:
            dest = Path(path_text).expanduser().resolve()
        except OSError:
            skipped.append(job_id)
            continue
        if not _is_under_allowed_model_root(dest, config):
            skipped.append(job_id)
            continue
        if dest.is_file() and _inspect_download_files(dest, total=int(row.get('bytes_total') or 0) or None).get('complete'):
            with _jobs_lock:
                _download_jobs[job_id] = {
                    **row,
                    'status': 'done',
                    'progress': 100.0,
                    'path': str(dest),
                    'finished_at': time.time(),
                }
            _remove_pending_job(job_id)
            skipped.append(job_id)
            continue
        with _jobs_lock:
            already = any(
                str(job.get('status') or '') == 'downloading'
                and str(job.get('repo_id') or '') == repo
                and str(job.get('filename') or '') == name
                for job in _download_jobs.values()
            )
            if already:
                skipped.append(job_id)
                continue
            restored = dict(row)
            restored['status'] = 'downloading'
            restored['speed_bps'] = 0.0
            restored['eta_seconds'] = None
            restored['finished_at'] = None
            restored['error'] = None
            _download_jobs[job_id] = restored
        thread = threading.Thread(
            target=_download_worker,
            args=(job_id, repo, name, dest),
            kwargs={'resume': True},
            daemon=True,
        )
        thread.start()
        resumed.append(job_id)
    if not pending:
        _clear_pending_downloads()
    repair = repair_stale_download_jobs(cfg=config)
    return {
        'success': True,
        'resumed': resumed,
        'skipped': skipped,
        'count': len(resumed),
        'repaired': repair.get('repaired') or [],
        'repaired_count': repair.get('count') or 0,
    }


def get_download_job(job_id: str) -> dict[str, Any]:
    _ensure_download_history_loaded()
    with _jobs_lock:
        job = _download_jobs.get(str(job_id or '').strip())
        if not job:
            return {'success': False, 'error': 'unknown job'}
        return {'success': True, 'job': _public_download_job(job)}


def _is_console_download_job(job: dict[str, Any]) -> bool:
    """True when DFlash Console started the download (HF job), not a disk scan."""
    if str(job.get('origin') or '').strip().lower() == 'disk':
        return False
    if str(job.get('kind') or '').strip().lower() == 'disk':
        return False
    job_id = str(job.get('id') or '').strip()
    if job_id.startswith('disk-'):
        return False
    return True


def list_download_jobs(
    *,
    active_only: bool = False,
    discover: bool = False,
    console_only: bool = True,
) -> dict[str, Any]:
    _ensure_download_history_loaded()
    if not console_only:
        _merge_disk_download_history(force=discover)
    with _jobs_lock:
        jobs = [_enrich_download_job(job) for job in _download_jobs.values()]
    if console_only:
        jobs = [job for job in jobs if _is_console_download_job(job)]
    if active_only:
        jobs = [job for job in jobs if str(job.get('status') or '') == 'downloading']
    jobs.sort(
        key=lambda row: float(row.get('finished_at') or row.get('started_at') or 0),
        reverse=True,
    )
    active_count = sum(1 for job in jobs if str(job.get('status') or '') == 'downloading')
    return {
        'success': True,
        'jobs': jobs,
        'count': len(jobs),
        'active_count': active_count,
    }


def clear_download_job(job_id: str) -> dict[str, Any]:
    _ensure_download_history_loaded()
    key = str(job_id or '').strip()
    if not key:
        return {'success': False, 'error': 'job_id is required'}
    with _jobs_lock:
        job = _download_jobs.get(key)
        if not job:
            return {'success': False, 'error': 'unknown job'}
        if str(job.get('status') or '') == 'downloading':
            return {'success': False, 'error': 'cannot clear an active download'}
        _cleared_ids.add(key)
        del _download_jobs[key]
    try:
        _save_download_history()
    except OSError as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'cleared': key}


def clear_download_history() -> dict[str, Any]:
    _ensure_download_history_loaded()
    cleared = 0
    with _jobs_lock:
        stale = [
            key
            for key, job in _download_jobs.items()
            if str(job.get('status') or '') != 'downloading'
        ]
        for key in stale:
            _cleared_ids.add(key)
            del _download_jobs[key]
        cleared = len(stale)
    try:
        _save_download_history()
    except OSError as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'cleared': cleared}


def save_pending_downloads() -> None:
    """Persist in-progress downloads so they can resume after a server restart."""
    _save_pending_downloads()
