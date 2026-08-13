"""Hugging Face Hub search and download helpers."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.config import load_config
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
        'label': 'DFlash Accelerator',
        'search': 'dflash gguf',
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


def _model_files(siblings: list[Any] | None, *, gguf_only: bool = True) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    allowed = ('.gguf',) if gguf_only else _DOWNLOAD_EXTENSIONS
    for entry in siblings or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('rfilename') or entry.get('path') or '')
        lower = name.lower()
        if not any(lower.endswith(ext) for ext in allowed):
            continue
        size = entry.get('size')
        if not isinstance(size, int) or size <= 0:
            lfs = entry.get('lfs')
            if isinstance(lfs, dict) and isinstance(lfs.get('size'), int):
                size = lfs['size']
        size_gb = round(int(size) / (1024 ** 3), 2) if isinstance(size, int) and size > 0 else None
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


def _fetch_repo_tree(repo: str) -> list[dict[str, Any]]:
    """Return HF tree entries (with sizes). Falls back to empty list on error."""
    encoded = urllib.parse.quote(repo, safe='/')
    url = f'{HF_API}/models/{encoded}/tree/main?recursive=1'
    try:
        payload = _request_json(url, timeout=30)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _siblings_with_sizes(siblings: list[Any] | None, tree: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Merge model siblings with tree sizes when the Hub omits size on siblings."""
    size_by_name: dict[str, int] = {}
    for row in tree or []:
        path = str(row.get('path') or '').strip()
        if not path:
            continue
        size = row.get('size')
        if not isinstance(size, int) or size <= 0:
            lfs = row.get('lfs')
            if isinstance(lfs, dict) and isinstance(lfs.get('size'), int):
                size = lfs['size']
        if isinstance(size, int) and size > 0:
            size_by_name[path] = size

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in siblings or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('rfilename') or entry.get('path') or '').strip()
        if not name:
            continue
        row = dict(entry)
        row['rfilename'] = name
        if name in size_by_name and not (isinstance(row.get('size'), int) and row['size'] > 0):
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
    if not isinstance(size_gb, (int, float)):
        return None, '—'
    return float(size_gb), f'{float(size_gb):g} GB'


def _preferred_gguf_size(siblings: list[Any] | None) -> tuple[float | None, str]:
    return _size_from_preferred_file(_preferred_gguf_file(_gguf_files(siblings)))


def _preferred_download_size(siblings: list[Any] | None) -> tuple[float | None, str]:
    return _size_from_preferred_file(_preferred_gguf_file(_model_files(siblings, gguf_only=False)))


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
    else:
        modality, runtime_id, kind = 'llm', '', 'repo'
    task = {
        'speech-to-text': 'transcribe',
        'text-to-speech': 'speech',
        'embedding': 'embed',
        'vision': 'vision',
        'llm': 'chat',
    }.get(modality, 'chat')
    return {
        'modality': modality,
        'runtime_id': runtime_id,
        'kind': kind,
        'catalog_visible': True,
        'downloadable': bool(downloadable) or has_gguf,
        'runnable': runtime_id == 'llama-server',
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
    accelerator_only = _is_accelerator_only_repo(siblings, repo_id=repo_id, size_gb=size_gb)
    updated_days = _days_since(last_modified)
    repo_label = repo_id.split('/')[-1] if '/' in repo_id else repo_id
    lab = infer_model_lab(repo_id=repo_id, author=author, tags=tags, title=repo_label)
    has_gguf = any(name.endswith('.gguf') for name in tags) or bool(_gguf_files(siblings))
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
        'tags': tags,
        'pipeline_tag': str(raw.get('pipeline_tag') or ''),
        'description': description,
        'gguf_count': len(_gguf_files(siblings)),
        'file_count': len(downloadable),
        'has_gguf': has_gguf,
        'has_files': bool(downloadable),
        'size_bytes': int(size_gb * (1024 ** 3)) if isinstance(size_gb, (int, float)) and size_gb > 0 else None,
        **modality_fields,
    }


def _summaries_from_models(
    raw_models: list[dict[str, Any]],
    *,
    enrich_sizes: bool = False,
) -> list[dict[str, Any]]:
    """Build summaries and fill missing GGUF sizes from the Hub tree."""
    raw_items = [item for item in raw_models if isinstance(item, dict)]
    rows = [_summary_from_model(item) for item in raw_items]
    if not enrich_sizes or not rows:
        return rows

    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw_by_index = dict(enumerate(raw_items))
    futures = {}
    with ThreadPoolExecutor(max_workers=min(12, len(rows))) as pool:
        for index, row in enumerate(rows):
            repo_id = str(row.get('id') or '')
            if not repo_id:
                continue
            if row.get('size_label') and str(row.get('size_label')) != '—':
                continue
            if isinstance(row.get('size_gb'), (int, float)):
                continue
            raw = raw_by_index.get(index)
            if not raw:
                continue
            futures[pool.submit(_fetch_repo_tree, repo_id)] = (index, raw)

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
            rows[index] = _summary_from_model(enriched)
    return rows


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
    response_limit = max(1, min(int(limit), 50))
    sort_key = sort if sort in ('downloads', 'likes', 'lastModified', 'createdAt') else 'downloads'
    per_source = response_limit if needle else max(
        8,
        (response_limit + len(SUPPORTED_SOURCE_CATEGORIES) - 1) // len(SUPPORTED_SOURCE_CATEGORIES),
    )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    merged: dict[str, dict[str, Any]] = {}

    def fetch_category(category: str) -> list[dict[str, Any]]:
        payload = search_models(needle, limit=per_source, sort=sort_key, category=category)
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
    if not models and needle and '/' in needle:
        try:
            detail = get_model_detail(needle, category='all')
            if detail.get('success') and isinstance(detail.get('model'), dict):
                candidate = detail['model']
                if _is_console_supported_model(candidate):
                    models = [candidate]
        except Exception:
            pass

    models.sort(
        key=lambda row: (
            0 if 'dflash' in str(row.get('id') or '').lower() else 1,
            0 if bool(row.get('has_gguf')) or int(row.get('gguf_count') or 0) > 0 else 1,
            -int(row.get('downloads') or 0),
        ),
    )
    models = models[:response_limit]
    return {'success': True, 'models': models, 'query': needle, 'category': 'supported'}


def search_models(
    query: str = '',
    *,
    limit: int = 25,
    sort: str = 'downloads',
    category: str = 'dflash',
    gguf_only: bool | None = None,
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
    models = _summaries_from_models(payload, enrich_sizes=True)
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
                    enrich_sizes=True,
                )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            pass
    # Exact repo-id lookup: when the user types a full "org/repo" id (e.g.
    # microsoft/VibeVoice-Realtime-0.5B) the search may still miss it — fetch
    # it directly from the Hub so ANY model on Hugging Face is discoverable.
    if not models and needle and '/' in needle:
        try:
            detail = get_model_detail(needle, category=cat_key)
            if detail.get('success') and isinstance(detail.get('model'), dict):
                models = [detail['model']]
        except Exception:
            pass
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
        if row.get('size_label') and str(row.get('size_label')) != '—':
            continue
        if isinstance(row.get('size_gb'), (int, float)):
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
        models = [row for row in models if row.get('accelerator_only')]
    models = models[:response_limit]
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
    tree = _fetch_repo_tree(repo)
    siblings = _siblings_with_sizes(raw.get('siblings'), tree)
    gguf_files = _gguf_files(siblings)
    downloadable_files = _model_files(siblings, gguf_only=use_gguf_only)
    files = gguf_files if use_gguf_only else downloadable_files
    preferred = _preferred_gguf_file(files)
    enriched_raw = dict(raw)
    enriched_raw['siblings'] = siblings
    summary = _summary_from_model(enriched_raw)
    # Prefer the recommended quant size for the detail summary when siblings omit them.
    if preferred and isinstance(preferred.get('size_gb'), (int, float)):
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

    return {
        'success': True,
        'model': {
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
        },
    }


def _download_worker(job_id: str, repo_id: str, filename: str, dest: Path) -> None:
    url = f'{HF_BASE}/{repo_id}/resolve/main/{urllib.parse.quote(filename, safe="/")}'
    headers = {'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + '.part')
            read = 0
            chunk_size = 1024 * 1024
            with tmp.open('wb') as handle:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)
                    read += len(chunk)
                    pct = round(read / total * 100, 1) if total > 0 else None
                    with _jobs_lock:
                        job = _download_jobs.get(job_id)
                        if job:
                            job['bytes_read'] = read
                            job['bytes_total'] = total or None
                            job['progress'] = pct
            tmp.replace(dest)
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['status'] = 'done'
                job['path'] = str(dest)
                job['progress'] = 100.0
        post_action = None
        with _jobs_lock:
            post_action = (_download_jobs.get(job_id) or {}).get('post_action')
        if isinstance(post_action, dict) and post_action.get('type') == 'wire_vision':
            try:
                from core.vision_setup import wire_vision_after_download

                wire_vision_after_download({**post_action, 'mmproj_path': str(dest)})
            except Exception as exc:
                with _jobs_lock:
                    job = _download_jobs.get(job_id)
                    if job:
                        job['post_action_error'] = str(exc)
        from core.local_models import invalidate_model_catalog_cache
        invalidate_model_catalog_cache()
    except Exception as exc:
        with _jobs_lock:
            job = _download_jobs.get(job_id)
            if job:
                job['status'] = 'error'
                job['error'] = str(exc)


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
    suffix = abs(hash(repo + name + str(dest))) % 1_000_000
    job_id = f'{int(time.time())}-{suffix:06d}'
    with _jobs_lock:
        _download_jobs[job_id] = {
            'id': job_id,
            'repo_id': repo,
            'filename': name,
            'status': 'downloading',
            'progress': 0.0,
            'bytes_read': 0,
            'bytes_total': None,
            'path': str(dest),
            'library_id': (library_id or '') if not dest_path else '',
            'started_at': time.time(),
            'post_action': post_action,
            'kind': 'vision' if isinstance(post_action, dict) else '',
        }
    thread = threading.Thread(target=_download_worker, args=(job_id, repo, name, dest), daemon=True)
    thread.start()
    return {
        'success': True,
        'job_id': job_id,
        'path': str(dest),
        'library_id': (library_id or '') if not dest_path else '',
    }


def get_download_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _download_jobs.get(str(job_id or '').strip())
        if not job:
            return {'success': False, 'error': 'unknown job'}
        return {'success': True, 'job': dict(job)}


def list_download_jobs(*, active_only: bool = False) -> dict[str, Any]:
    with _jobs_lock:
        jobs = [dict(job) for job in _download_jobs.values()]
    if active_only:
        jobs = [job for job in jobs if str(job.get('status') or '') == 'downloading']
    jobs.sort(key=lambda row: float(row.get('started_at') or 0), reverse=True)
    active_count = sum(1 for job in jobs if str(job.get('status') or '') == 'downloading')
    return {
        'success': True,
        'jobs': jobs,
        'count': len(jobs),
        'active_count': active_count,
    }
