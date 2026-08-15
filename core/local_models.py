"""Scan local GGUF models and map them to DFlash Console server profiles."""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.config import EMBEDDING_PROFILES, ROOT, list_servers, load_config, normalize_server
from core.model_paths import (
    allowed_model_roots,
    disk_scan_roots,
    get_download_dir,
    get_model_libraries,
    storage_presets,
)
from core.model_stack import resolve_model_stack

_CATALOG_CACHE: dict[str, Any] | None = None
_CATALOG_CACHE_AT: float = 0.0
_CATALOG_CACHE_KEY = ''
_CATALOG_CACHE_PLAIN: dict[str, Any] | None = None
_CATALOG_CACHE_PLAIN_AT: float = 0.0
_CATALOG_CACHE_PLAIN_KEY = ''
_CATALOG_TTL_SECONDS = 120.0
_PERSISTED_CACHE_TTL_SECONDS = 24 * 60 * 60
_PERSISTED_CACHE_PATH = ROOT / 'logs' / 'local-model-catalog-cache.json'
_CATALOG_REFRESH_LOCK = threading.Lock()
_CATALOG_REFRESHING = False
_CATALOG_REFRESH_LOOP_STARTED = False

_QUANT_RE = re.compile(r'Q\d[_A-Z0-9]+', re.I)
_PARAM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*[Bb]', re.I)
_SPLIT_SHARD_RE = re.compile(r'^(?P<prefix>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})(?P<suffix>\.gguf)$', re.I)
_PHI_RE = re.compile(r'(?:^|[^a-z])phi(?:[^a-z]|$)', re.I)
_GPT_RE = re.compile(r'(?:^|[^a-z])gpt(?:[^a-z]|$)', re.I)
_FW_WHISPER_RE = re.compile(r'whisper', re.I)
# Strong name markers that explicitly signal a reasoning/thinking model.
_REASONING_RE = re.compile(
    r'(?:^|[^a-z0-9])(?:'
    r'r1(?:\.\d+)?|reasoner|reasoning|thinking|think|'
    r'chain[-_ ]?of[-_ ]?thought|cot(?:[-_ ]?thinking)?|'
    r'qwq|o1|o3|o4[-_ ]?mini|kimi(?:[-_ ]?thinking)?|'
    r'glm[-_ ]?4\.5|glm[-_ ]?5|glm[-_ ]?thinking|'
    r'deepseek[-_ ]?r1|phi[-_ ]?4[-_ ]?reasoning'
    r')(?:[^a-z0-9]|$)',
    re.I,
)
# Architectures whose chat templates ship a thinking mode (llama.cpp auto).
_REASONING_FAMILIES = frozenset({'gemma4', 'qwen', 'deepseekv2'})


def _guess_reasoning(name: str) -> bool:
    """Best-effort: does this model expose a reasoning/thinking mode?

    Strong name markers (r1, qwq, reasoning, think, o1/o3, glm-4.5, …) always
    count. Otherwise we trust the architecture's chat template: gemma4, qwen
    and deepseekv2 ship thinking templates that llama-server enables in auto
    mode.
    """
    if not name:
        return False
    text = str(name)
    if _REASONING_RE.search(text):
        return True
    return _guess_arch(text) in _REASONING_FAMILIES


def _append_reasoning_capability(caps: list[str], name: str) -> None:
    if 'reasoning' in caps:
        return
    if _guess_reasoning(name):
        caps.append('reasoning')


def model_has_reasoning(entry: dict[str, Any]) -> bool:
    """True when a catalog row or raw server entry is reasoning-capable.

    Prefers the precomputed ``capabilities`` (catalog rows) and falls back to
    name heuristics for raw ``config.json`` server entries at API time.
    """
    if not isinstance(entry, dict):
        return False
    caps = entry.get('capabilities')
    if isinstance(caps, list) and 'reasoning' in caps:
        return True
    if entry.get('reasoning') is True:
        return True
    name = ' '.join(
        str(entry.get(key) or '')
        for key in ('label', 'model_id', 'filename', 'id')
    )
    return _guess_reasoning(name)


def _row_reasoning_field(entry: dict[str, Any]) -> bool:
    return model_has_reasoning(entry)


def _catalog_cache_key(config: dict[str, Any]) -> str:
    try:
        return json.dumps(config, sort_keys=True, default=str, separators=(',', ':'))
    except (TypeError, ValueError):
        return repr(config)


def _read_persisted_catalog(cache_key: str, *, include_dflash_stacks: bool) -> dict[str, Any] | None:
    if not _PERSISTED_CACHE_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSISTED_CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get('version') != 1:
        return None
    if payload.get('cache_key') != cache_key:
        return None
    if bool(payload.get('include_dflash_stacks', True)) != include_dflash_stacks:
        return None
    saved_at = float(payload.get('saved_at') or 0.0)
    if not saved_at or time.time() - saved_at > _PERSISTED_CACHE_TTL_SECONDS:
        return None
    catalog = payload.get('payload')
    if not isinstance(catalog, dict) or not isinstance(catalog.get('models'), list):
        return None
    result = dict(catalog)
    result['cached'] = True
    result['stale'] = time.time() - saved_at >= _CATALOG_TTL_SECONDS
    result['cache_age_seconds'] = round(max(0.0, time.time() - saved_at), 1)
    return result


def _write_persisted_catalog(payload: dict[str, Any], *, cache_key: str, include_dflash_stacks: bool) -> None:
    try:
        _PERSISTED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _PERSISTED_CACHE_PATH.with_suffix('.tmp')
        temporary.write_text(
            json.dumps({
                'version': 1,
                'saved_at': time.time(),
                'cache_key': cache_key,
                'include_dflash_stacks': include_dflash_stacks,
                'payload': payload,
            }, ensure_ascii=False),
            encoding='utf-8',
        )
        temporary.replace(_PERSISTED_CACHE_PATH)
    except (OSError, TypeError, ValueError):
        pass


def _schedule_catalog_refresh(config: dict[str, Any], *, include_dflash_stacks: bool) -> None:
    global _CATALOG_REFRESHING
    with _CATALOG_REFRESH_LOCK:
        if _CATALOG_REFRESHING:
            return
        _CATALOG_REFRESHING = True

    def refresh() -> None:
        global _CATALOG_REFRESHING
        try:
            list_local_models(
                cfg=config,
                scan_disk=True,
                force_refresh=True,
                include_dflash_stacks=include_dflash_stacks,
            )
        except Exception:
            pass
        finally:
            with _CATALOG_REFRESH_LOCK:
                _CATALOG_REFRESHING = False

    threading.Thread(target=refresh, daemon=True, name='model-catalog-refresh').start()


def _guess_arch(name: str) -> str:
    lower = name.lower()
    if 'gemma' in lower:
        return 'gemma4'
    if 'qwen' in lower:
        return 'qwen'
    if 'deepseek' in lower:
        return 'deepseekv2'
    if 'bonsai' in lower:
        return 'bonsai'
    if 'ovis' in lower or 'maas' in lower:
        return 'ovis'
    if 'glm' in lower:
        return 'glm'
    if 'llama' in lower:
        return 'llama'
    if 'mistral' in lower:
        return 'mistral'
    if 'whisper' in lower:
        return 'whisper'
    if 'chandra' in lower:
        return 'chandra'
    if 'lighton' in lower:
        return 'lighton'
    if 'ornith' in lower:
        return 'ornith'
    if 'laguna' in lower:
        return 'laguna'
    if 'bls' in lower or 'mini-code' in lower:
        return 'bls'
    if _GPT_RE.search(lower):
        return 'gpt'
    if _PHI_RE.search(lower):
        return 'phi'
    return 'unknown'


def _guess_params(name: str) -> str:
    match = _PARAM_RE.search(name)
    if match:
        return f"{match.group(1)}B"
    return '—'


def _guess_quant(name: str) -> str:
    match = _QUANT_RE.search(name)
    return match.group(0).upper() if match else '—'


def _publisher(path: Path) -> str:
    parts = path.parts
    try:
        idx = parts.index('models')
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return path.parent.name


def _modified_label(path: Path) -> str:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return '—'
    if age < 86400:
        return 'today'
    if age < 86400 * 2:
        return '1 day ago'
    if age < 86400 * 7:
        return f"{int(age / 86400)} days ago"
    if age < 86400 * 30:
        return f"{int(age / (86400 * 7))} weeks ago"
    return f"{int(age / (86400 * 30))} months ago"


def _size_gb(path: Path) -> float | None:
    try:
        return round(path.stat().st_size / (1024 ** 3), 2)
    except OSError:
        return None


def _has_vision_support(path: Path | None) -> bool:
    """True when a multimodal projector (mmproj) or VL naming indicates vision."""
    from core.vision_setup import _is_mmproj_name

    if not path or not path.is_file():
        return False
    lower = path.name.lower()
    if any(token in lower for token in ('-vl-', '_vl_', 'vision', 'multimodal')):
        return True
    try:
        for sibling in path.parent.glob('*.gguf'):
            if _is_mmproj_name(sibling.name):
                return True
    except OSError:
        pass
    return False


def _append_vision_capability(caps: list[str], path: Path | None, *, mmproj_path: str | None = None) -> None:
    if 'vision' in caps:
        return
    if _has_vision_support(path):
        caps.append('vision')
        return
    explicit = str(mmproj_path or '').strip()
    if explicit and Path(explicit).is_file():
        caps.append('vision')


def _is_gguf_file(path: Path) -> bool:
    """True when a file begins with the GGUF magic (Ollama blobs are raw GGUF)."""
    try:
        with path.open('rb') as handle:
            return handle.read(4) == b'GGUF'
    except OSError:
        return False


def _ollama_manifests_root() -> Path | None:
    """Root of Ollama's model manifests (``~/.ollama/models/manifests``)."""
    candidates = [
        Path(os.environ.get('OLLAMA_MODELS') or '') / 'manifests',
        Path.home() / '.ollama' / 'models' / 'manifests',
    ]
    for candidate in candidates:
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _ollama_blobs_root() -> Path | None:
    root = _ollama_manifests_root()
    if root is None:
        return None
    return root.parent / 'blobs'


def _resolve_ollama_manifest(model_id: str, path: str, manifests_root: Path) -> Path | None:
    """Locate an Ollama manifest by model name (``name:tag``) or blob path."""
    name = str(model_id or '').strip()
    if name:
        base, _, tag = name.partition(':')
        tag = tag or 'latest'
        for manifest_path in manifests_root.rglob('*'):
            if not manifest_path.is_file():
                continue
            parts = manifest_path.relative_to(manifests_root).parts
            if len(parts) >= 3 and parts[-2] == base and parts[-1] == tag:
                return manifest_path
    if path:
        try:
            resolved = Path(path).expanduser().resolve()
            if resolved.is_file() and 'blobs' in resolved.parts:
                digest = resolved.name[len('sha256-'):]
                for manifest_path in manifests_root.rglob('*'):
                    if not manifest_path.is_file():
                        continue
                    try:
                        data = json.loads(manifest_path.read_text(encoding='utf-8'))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if any(
                        str(layer.get('digest') or '') == f'sha256:{digest}'
                        for layer in data.get('layers') or []
                    ):
                        return manifest_path
        except (OSError, ValueError):
            pass
    return None


def _ollama_label_from_manifest(manifest_path: Path, manifests_root: Path) -> str:
    try:
        parts = manifest_path.relative_to(manifests_root).parts
    except ValueError:
        return ''
    if len(parts) < 3:
        return ''
    name = str(parts[-2] or '').strip()
    tag = str(parts[-1] or '').strip()
    return f'{name}:{tag}' if tag and tag.lower() != 'latest' else name


def _delete_ollama_model(path: str, model_id: str = '') -> dict[str, Any]:
    """Remove an installed Ollama model (manifest + unreferenced blobs).

    Prefers the running Ollama daemon (``DELETE /api/delete`` handles shared
    blob ref-counting); if the daemon is offline it removes the manifest and
    any blobs no other manifest still references.
    """
    manifests_root = _ollama_manifests_root()
    blobs_root = _ollama_blobs_root()
    if manifests_root is None:
        return {'success': False, 'error': 'Ollama models folder not found'}

    manifest_path = _resolve_ollama_manifest(model_id, path, manifests_root)
    if manifest_path is None:
        return {'success': False, 'error': 'Ollama model manifest not found'}
    label = _ollama_label_from_manifest(manifest_path, manifests_root) or str(model_id or '')

    if label:
        try:
            import urllib.request

            body = json.dumps({'model': label}).encode('utf-8')
            request = urllib.request.Request(
                'http://127.0.0.1:11434/api/delete',
                data=body,
                method='DELETE',
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(request, timeout=15):
                pass
            return {'success': True, 'method': 'ollama-api', 'model': label}
        except Exception:
            pass  # daemon offline — fall back to manual removal below

    digests: set[str] = set()
    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        data = {}
    if isinstance(data, dict):
        for layer in data.get('layers') or []:
            if isinstance(layer, dict):
                digest = str(layer.get('digest') or '')
                if digest.startswith('sha256:'):
                    digests.add(digest[7:])
        config = data.get('config')
        if isinstance(config, dict):
            digest = str(config.get('digest') or '')
            if digest.startswith('sha256:'):
                digests.add(digest[7:])

    referenced: set[str] = set()
    for other in manifests_root.rglob('*'):
        if not other.is_file() or other == manifest_path:
            continue
        try:
            other_data = json.loads(other.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(other_data, dict):
            continue
        for layer in other_data.get('layers') or []:
            if isinstance(layer, dict):
                digest = str(layer.get('digest') or '')
                if digest.startswith('sha256:'):
                    referenced.add(digest[7:])
        other_config = other_data.get('config')
        if isinstance(other_config, dict):
            digest = str(other_config.get('digest') or '')
            if digest.startswith('sha256:'):
                referenced.add(digest[7:])

    try:
        manifest_path.unlink(missing_ok=True)
    except OSError as exc:
        return {'success': False, 'error': f'could not remove manifest: {exc}'}

    removed_blobs = 0
    if blobs_root is not None:
        for digest in digests:
            if digest in referenced:
                continue
            blob = blobs_root / f'sha256-{digest}'
            try:
                if blob.is_file():
                    blob.unlink()
                    removed_blobs += 1
            except OSError:
                pass
    return {'success': True, 'method': 'files', 'model': label, 'removed_blobs': removed_blobs}


def _scan_ollama_models() -> list[dict[str, Any]]:
    """Discover models installed under Ollama (offline, from its manifests).

    Ollama stores models as content-addressed blobs with a small JSON
    manifest per model. The manifest lists every layer's byte size and the
    config blob carries family / parameter size / quantization, so we can
    present each installed Ollama model in the Model library without needing
    the Ollama daemon to be running.
    """
    manifests_root = _ollama_manifests_root()
    blobs_root = _ollama_blobs_root()
    if manifests_root is None:
        return []

    rows: list[dict[str, Any]] = []
    for manifest_path in manifests_root.rglob('*'):
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        parts = manifest_path.relative_to(manifests_root).parts
        if len(parts) < 3:
            continue
        model_name = str(parts[-2] or '').strip()
        tag = str(parts[-1] or '').strip()
        if not model_name:
            continue
        label = f'{model_name}:{tag}' if tag and tag.lower() != 'latest' else model_name

        total_size = 0
        model_digest = ''
        for layer in manifest.get('layers') or []:
            if not isinstance(layer, dict):
                continue
            if 'image.model' not in str(layer.get('mediaType') or ''):
                continue
            try:
                total_size += int(layer.get('size') or 0)
            except (TypeError, ValueError):
                continue
            digest = str(layer.get('digest') or '')
            if digest.startswith('sha256:'):
                model_digest = digest[7:]

        family = ''
        params = ''
        quant = ''
        config = manifest.get('config')
        if isinstance(config, dict) and blobs_root is not None:
            digest = str(config.get('digest') or '')
            if digest.startswith('sha256:'):
                blob = blobs_root / f'sha256-{digest[7:]}'
                try:
                    meta = json.loads(blob.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    meta = {}
                if isinstance(meta, dict):
                    family = str(meta.get('model_family') or '')
                    params = str(meta.get('model_type') or '')
                    quant = str(meta.get('file_type') or '')

        # Prefer the model blob as the row path when present (it exists and is
        # the real weights file); otherwise fall back to the manifest file.
        row_path = str(manifest_path)
        gguf_blob = False
        if blobs_root is not None and model_digest:
            blob_path = blobs_root / f'sha256-{model_digest}'
            if blob_path.is_file() and _is_gguf_file(blob_path):
                row_path = str(blob_path)
                gguf_blob = True

        size_gb = round(total_size / (1024 ** 3), 2) if total_size > 0 else None
        caps = ['llm']
        gguf_arch = ''
        if gguf_blob:
            from core.gguf_meta import read_gguf_architecture

            gguf_arch = read_gguf_architecture(row_path)
            if gguf_arch == 'glmocr':
                caps.extend(['ocr', 'vision'])
        if _guess_reasoning(label):
            caps.append('reasoning')
        model_id = re.sub(r'[^a-z0-9._-]+', '-', label.lower())[:80].strip('-') or label
        rows.append({
            'id': f'ollama:{model_name}:{tag}',
            'server_id': '',
            'label': label,
            'filename': label,
            'path': row_path,
            'arch': gguf_arch or family or _guess_arch(label),
            'params': params or _guess_params(label),
            'publisher': 'ollama',
            'quant': quant or _guess_quant(label),
            'size_gb': size_gb,
            'modified': '—',
            'source': 'ollama',
            'capabilities': caps,
            'reasoning': _guess_reasoning(label),
            'loadable': gguf_blob,
            'context_max': 131072,
            'context_size': 8192,
            'load_settings': {},
            'inference_settings': {},
            'gpu_layers_max': 128,
            'dflash_stack': False,
            'stack_status': '',
            'plain_gguf': gguf_blob,
            'model_id': model_id,
            'ollama_model': label,
        })
    return sorted(rows, key=lambda row: (row.get('label') or '').lower())


def _scan_gguf(root: Path, *, source: str, max_files: int = 800) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for path in root.rglob('*.gguf'):
            if len(rows) >= max_files:
                break
            name = path.name
            if name.lower().startswith('mmproj'):
                continue
            row = {
                'id': path.stem.replace('_', '-').lower()[:120],
                'path': str(path),
                'filename': name,
                'arch': _guess_arch(name),
                'params': _guess_params(name),
                'publisher': _publisher(path),
                'quant': _guess_quant(name),
                'size_gb': _size_gb(path),
                'modified': _modified_label(path),
                'source': source,
                'capabilities': [],
            }
            caps = row['capabilities']
            _append_vision_capability(caps, path)
            _append_reasoning_capability(caps, name)
            row['reasoning'] = 'reasoning' in caps
            rows.append(row)
    except OSError:
        pass
    return rows


def _is_faster_whisper_model_dir(path: Path) -> bool:
    """True when a ``model.bin`` directory is a faster-whisper (whisper) model.

    Checks the model ``config.json`` ``model_type``/``architectures`` first,
    then falls back to whisper markers in meaningful path segments (the folder
    name, an HF ``models--<org>--<name>`` repo segment, or an ``stt`` folder).
    This deliberately excludes other CTranslate2 snapshots that also ship
    ``model.bin`` (e.g. NLLB / M2M translation packages) and avoids matching
    unrelated temp/workspace folders that merely contain the word "whisper".
    """
    if _FW_WHISPER_RE.search(path.name):
        return True
    config = path / 'config.json'
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding='utf-8', errors='replace'))
            model_type = str(data.get('model_type') or '')
            architectures = str(data.get('architectures') or '')
            if _FW_WHISPER_RE.search(model_type) or _FW_WHISPER_RE.search(architectures):
                return True
            # Whisper CTranslate2 configs often drop model_type/architectures
            # but keep whisper-specific fields (alignment heads, language ids).
            if any(key in data for key in ('alignment_heads', 'lang_ids', 'suppress_ids')):
                return True
        except (OSError, ValueError):
            pass
    parts = list(path.parts)
    if any(part.startswith('models--') and _FW_WHISPER_RE.search(part) for part in parts):
        return True
    lowered = {part.lower() for part in parts}
    return 'stt' in lowered


def _faster_whisper_display_name(path: Path) -> tuple[str, str]:
    """Friendly name + publisher for a faster-whisper model directory.

    HF snapshots live under ``models--<org>--<name>/snapshots/<hash>``; use the
    repo name as the display name and the org as the publisher. Other layouts
    fall back to the directory name.
    """
    try:
        for part in path.parents:
            if part.name.startswith('models--'):
                bits = part.name.split('--', 2)
                if len(bits) >= 3:
                    return bits[2], bits[1]
                if len(bits) == 2:
                    return bits[1], bits[0]
    except OSError:
        pass
    return path.name, ''


def _scan_faster_whisper(root: Path, *, source: str, max_dirs: int = 200) -> list[dict[str, Any]]:
    """Discover faster-whisper model directories (contain ``model.bin``).

    faster-whisper models are CTranslate2 whisper snapshots: a directory
    holding ``model.bin`` plus ``config.json`` / ``tokenizer.json``. They run
    on the ``faster-whisper`` runtime and can be imported into the Console
    library. Only *whisper* model dirs are picked up — other CTranslate2
    snapshots (e.g. NLLB / M2M translation packages) are excluded.
    """
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for path in root.rglob('model.bin'):
            if len(rows) >= max_dirs:
                break
            parent = path.parent
            if not _is_faster_whisper_model_dir(parent):
                continue
            key = str(parent).lower()
            if key in seen:
                continue
            seen.add(key)
            display, publisher = _faster_whisper_display_name(parent)
            rows.append({
                'id': display.replace('_', '-').lower()[:120],
                'path': str(parent),
                'filename': display,
                'label': display,
                'arch': 'whisper',
                'params': _guess_params(display),
                'publisher': publisher or _publisher(parent),
                'quant': _guess_quant(display) if _QUANT_RE.search(display) else 'f16',
                'size_gb': _size_gb(path),
                'modified': _modified_label(parent),
                'source': source,
                'kind': 'dir',
                'runtime_id': 'faster-whisper',
                'capabilities': ['instruct', 'stt'],
            })
    except OSError:
        pass
    return rows


def _scan_vibevoice(root: Path, *, source: str, max_dirs: int = 120) -> list[dict[str, Any]]:
    """Discover Microsoft VibeVoice TTS model directories.

    VibeVoice models are Transformers safetensors directories (config.json +
    model.safetensors + preprocessor_config.json) whose config declares the
    ``vibevoice_streaming`` model type. They run on the ``vibevoice`` runtime.
    """
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for config in root.rglob('config.json'):
            if len(rows) >= max_dirs:
                break
            parent = config.parent
            key = str(parent).lower()
            if key in seen:
                continue
            seen.add(key)
            if not (parent / 'model.safetensors').is_file():
                continue
            try:
                data = json.loads(config.read_text(encoding='utf-8', errors='replace'))
            except (OSError, ValueError):
                continue
            model_type = str(data.get('model_type') or '')
            architectures = str(data.get('architectures') or '')
            if 'vibevoice' not in (model_type + ' ' + architectures).lower():
                continue
            display = parent.name
            rows.append({
                'id': display.replace('_', '-').lower()[:120],
                'path': str(parent),
                'filename': display,
                'label': display,
                'arch': 'vibevoice',
                'params': _guess_params(display),
                'publisher': _publisher(parent),
                'quant': 'f16',
                'size_gb': _size_gb(parent / 'model.safetensors'),
                'modified': _modified_label(parent),
                'source': source,
                'kind': 'dir',
                'runtime_id': 'vibevoice',
                'capabilities': ['instruct', 'tts'],
            })
    except OSError:
        pass
    return rows


def _collapse_split_shards(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Represent a multi-file GGUF model as one row with its combined size."""
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in models:
        path = Path(str(row.get('path') or ''))
        match = _SPLIT_SHARD_RE.match(path.name)
        if not match:
            continue
        groups[(str(path.parent).lower(), match.group('prefix').lower(), int(match.group('total')))].append(row)

    hidden: set[int] = set()
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: int(_SPLIT_SHARD_RE.match(Path(str(row.get('path') or '')).name).group('part')))
        survivor = rows[0]
        files = [str(row.get('path') or '') for row in rows]
        known_sizes = [float(row['size_gb']) for row in rows if row.get('size_gb') is not None]
        survivor['split_files'] = files
        survivor['split_count'] = len(files)
        survivor['split_total'] = int(_SPLIT_SHARD_RE.match(Path(files[0]).name).group('total'))
        survivor['size_gb'] = round(sum(known_sizes), 2) if known_sizes else None
        survivor['label'] = survivor.get('label') or survivor.get('filename')
        for row in rows[1:]:
            hidden.add(id(row))

    return [row for row in models if id(row) not in hidden]


def _resolve_stack_pair(server: dict[str, Any], *, cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    stack = resolve_model_stack(server, cfg=cfg)
    target = next((row for row in stack if row.get('role') == 'target'), None)
    draft = next((row for row in stack if str(row.get('role') or '').startswith('draft')), None)
    return target, draft


def _server_catalog_row(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any],
    enabled: bool | None = None,
) -> dict[str, Any]:
    target, draft = _resolve_stack_pair(server, cfg=cfg)
    path = Path(str(target.get('path') or '')) if target else None
    caps = ['instruct']
    profile = str(server.get('profile') or '')
    if draft:
        caps.append('dflash')
    elif profile == 'gemma-12-ar':
        caps.append('ar')
    else:
        caps.append('ar')
    if 'gemma' in str(server.get('model_id') or '').lower():
        caps.append('tools')
    from core.vision_setup import resolve_mmproj_path

    _append_vision_capability(caps, path, mmproj_path=resolve_mmproj_path(server, cfg=cfg))
    _append_reasoning_capability(caps, str(server.get('label') or server.get('model_id') or ''))
    draft_path = str(draft.get('path') or '') if draft else ''
    draft_path_obj = Path(draft_path) if draft_path else None
    is_enabled = enabled if enabled is not None else server.get('enabled', True) is not False
    has_dflash = 'dflash' in caps
    target_ready = bool(path and path.is_file())
    draft_ready = bool(draft_path_obj and draft_path_obj.is_file())
    if has_dflash:
        loadable = is_enabled and target_ready and draft_ready
    else:
        loadable = is_enabled and target_ready
    return {
        'id': str(server.get('model_id') or server.get('id')),
        'server_id': str(server.get('id') or ''),
        'label': str(server.get('label') or server.get('model_id') or ''),
        'profile': str(server.get('profile') or ''),
        'port': int(server.get('port') or 0),
        'loadable': loadable,
        'path': str(target.get('path') or '') if target else '',
        'filename': path.name if path and path.name else '',
        'arch': _guess_arch(str(server.get('label') or path.name if path else '')),
        'params': _guess_params(str(server.get('label') or path.name if path else '')),
        'publisher': _publisher(path) if path else 'dflash',
        'quant': _guess_quant(path.name if path else str(server.get('label') or '')),
        'size_gb': target.get('size_gb') if target else _size_gb(path) if path else None,
        'modified': _modified_label(path) if path and path.is_file() else '—',
        'source': 'dflash-profile',
        'capabilities': caps,
        'reasoning': 'reasoning' in caps,
        'context_max': _context_max_for_profile(str(server.get('profile') or '')),
        'draft_label': draft.get('label') if draft else '',
        'draft_path': draft_path,
        'draft_filename': draft_path_obj.name if draft_path_obj else '',
        'draft_size_gb': _size_gb(draft_path_obj) if draft_path_obj and draft_path_obj.is_file() else None,
        'draft_quant': _guess_quant(draft_path_obj.name) if draft_path_obj else '',
        'load_settings': server.get('load_settings') or {},
        'inference_settings': server.get('inference_settings') or {},
        'context_size': server.get('context_size'),
        'gpu_layers_max': 128,
        'dflash_stack': has_dflash,
        'stack_status': 'ready' if is_enabled and has_dflash else ('disabled' if has_dflash else ''),
    }


def _normalize_path_key(path_text: str) -> str:
    text = str(path_text or '').strip()
    if not text:
        return ''
    try:
        return str(Path(text).resolve()).lower()
    except OSError:
        return text.lower()


def _registered_stack_targets(config: dict[str, Any], *, cfg: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for server in list_servers(config):
        target, draft = _resolve_stack_pair(normalize_server(server), cfg=cfg)
        if not draft or not target:
            continue
        path_key = _normalize_path_key(str(target.get('path') or ''))
        if path_key:
            paths.add(path_key)
    return paths


def _capable_stack_row(target: dict[str, Any]) -> dict[str, Any]:
    from core.stack_match import suggest_stack_label

    path = Path(str(target.get('path') or ''))
    draft_path = str(target.get('draft_path') or '').strip()
    draft_path_obj = Path(draft_path) if draft_path else None
    label = suggest_stack_label(path) if path.is_file() else str(target.get('label') or path.name)
    caps = ['instruct', 'dflash']
    _append_vision_capability(caps, path)
    _append_reasoning_capability(caps, label)
    # The source reflects where the target file physically lives. A capable
    # target inside the Console's own models folder is a Console DFlash model
    # (dflash-stack); files living under other libraries keep their provider
    # source (e.g. lmstudio) so they are grouped as external models.
    source = str(target.get('source') or '').strip().lower()
    if source in ('dflash', 'dflash-profile', 'dflash-stack', ''):
        source = 'dflash-stack'
    return {
        'id': f"stack-capable:{path.stem.replace('_', '-').lower()[:96]}",
        'server_id': '',
        'label': label,
        'profile': '',
        'port': 0,
        'loadable': False,
        'path': str(path),
        'filename': path.name,
        'arch': target.get('arch') or _guess_arch(path.name),
        'params': target.get('params') or _guess_params(path.name),
        'publisher': target.get('publisher') or _publisher(path),
        'quant': target.get('quant') or _guess_quant(path.name),
        'size_gb': target.get('size_gb'),
        'modified': target.get('modified') or (_modified_label(path) if path.is_file() else '—'),
        'source': source,
        'capabilities': caps,
        'reasoning': 'reasoning' in caps,
        'context_max': 131072,
        'draft_label': target.get('draft_filename') or '',
        'draft_path': draft_path,
        'draft_filename': draft_path_obj.name if draft_path_obj else str(target.get('draft_filename') or ''),
        'draft_size_gb': target.get('draft_size_gb'),
        'draft_quant': _guess_quant(draft_path_obj.name) if draft_path_obj else '',
        'load_settings': {},
        'inference_settings': {},
        'context_size': 8192,
        'gpu_layers_max': 128,
        'dflash_stack': True,
        'stack_status': 'unregistered',
        'match_score': target.get('match_score'),
    }


def _dflash_stack_supplement(config: dict[str, Any], catalog: dict[str, dict[str, Any]], *, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from core.stack_match import list_capable_targets

    rows: list[dict[str, Any]] = []
    registered_targets = _registered_stack_targets(config, cfg=cfg)
    catalog_server_ids = {str(row.get('server_id') or '') for row in catalog.values()}

    for server in list_servers(config):
        if server.get('enabled', True) is not False:
            continue
        normalized = normalize_server(server)
        server_id = str(normalized.get('id') or '')
        if server_id in catalog_server_ids:
            continue
        _, draft = _resolve_stack_pair(normalized, cfg=cfg)
        if not draft:
            continue
        rows.append(_server_catalog_row(normalized, cfg=cfg, enabled=False))

    seen_targets = set(registered_targets)
    for target in list_capable_targets(cfg=config).get('targets') or []:
        path_key = _normalize_path_key(str(target.get('path') or ''))
        if not path_key or path_key in seen_targets:
            continue
        seen_targets.add(path_key)
        rows.append(_capable_stack_row(target))

    return rows


_DUPLICATE_SAMPLE_BYTES = 64 * 1024


def _duplicate_file_fingerprint(path: Path) -> str | None:
    """Return a cheap content fingerprint for files sharing a name and size."""
    try:
        stat = path.stat()
        with path.open('rb') as handle:
            digest = hashlib.blake2b(digest_size=16)
            digest.update(handle.read(_DUPLICATE_SAMPLE_BYTES))
            if stat.st_size > _DUPLICATE_SAMPLE_BYTES:
                handle.seek(max(0, stat.st_size - _DUPLICATE_SAMPLE_BYTES))
                digest.update(handle.read(_DUPLICATE_SAMPLE_BYTES))
        return f'{stat.st_size}:{digest.hexdigest()}'
    except OSError:
        return None


def _collapse_identical_files(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one catalog row when the same file exists in multiple scan roots."""
    candidates: dict[tuple[str, int], list[int]] = defaultdict(list)
    for index, row in enumerate(models):
        path_text = str(row.get('path') or '').strip()
        filename = str(row.get('filename') or Path(path_text).name).strip().lower()
        if not path_text or not filename:
            continue
        try:
            size = Path(path_text).stat().st_size
        except OSError:
            continue
        candidates[(filename, size)].append(index)

    hidden: set[int] = set()
    for indices in candidates.values():
        if len(indices) < 2:
            continue
        fingerprints: dict[str, list[int]] = defaultdict(list)
        for index in indices:
            fingerprint = _duplicate_file_fingerprint(Path(str(models[index].get('path') or '')))
            if fingerprint:
                fingerprints[fingerprint].append(index)
        for matching in fingerprints.values():
            if len(matching) < 2:
                continue
            # Catalog order already prefers configured profiles and the first
            # configured/scanned root, so keep that row as the canonical entry.
            survivor = matching[0]
            paths = [str(models[index].get('path') or '') for index in matching]
            models[survivor]['duplicate_group'] = (
                f"dup:{str(models[survivor].get('filename') or '').lower()}"
            )
            models[survivor]['duplicate_count'] = len(paths)
            models[survivor]['duplicate_paths'] = paths
            models[survivor]['duplicate_identical'] = True
            hidden.update(matching[1:])

    return [row for index, row in enumerate(models) if index not in hidden]


def _mark_duplicate_files(models: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in models:
        path_text = str(row.get('path') or '').strip()
        if not path_text:
            continue
        name = str(row.get('filename') or Path(path_text).name).lower()
        if not name:
            continue
        groups[name].append(row)
    for name, group in groups.items():
        if len(group) < 2:
            continue
        group_id = f'dup:{name}'
        paths = [str(item.get('path') or '') for item in group]
        for row in group:
            row['duplicate_group'] = group_id
            row['duplicate_count'] = len(group)
            row['duplicate_paths'] = paths
            row['duplicate_identical'] = False


def _annotate_path_status(row: dict[str, Any]) -> None:
    path_text = str(row.get('path') or '').strip()
    if not path_text:
        row['path_missing'] = True
        row['loadable'] = False
        return
    path = Path(path_text)
    # faster-whisper models are directories (model.bin + config.json + ...),
    # so a directory path is present when the directory itself exists.
    if str(row.get('kind') or '').lower() == 'dir' or str(row.get('runtime_id') or '') == 'faster-whisper':
        missing = not path.is_dir()
    else:
        missing = not path.is_file()
    row['path_missing'] = missing
    if missing:
        row['loadable'] = False
    draft_path = str(row.get('draft_path') or '').strip()
    if draft_path:
        draft_missing = not Path(draft_path).is_file()
        row['draft_path_missing'] = draft_missing
        if draft_missing and row.get('dflash_stack'):
            row['loadable'] = False
    if int(row.get('split_count') or 0) > 1:
        row['loadable'] = False


def _annotate_runtime_fields(row: dict[str, Any]) -> None:
    """Phase 0: add modality/runtime_id/kind/flags to a catalog row.

    Modality is inferred from the engine mode/profile plus the same filename
    heuristics the Models tab previously used, so the backend ``modality`` is
    authoritative and filter chips wire to it (not client-side guesses).
    Every current row maps to llama-server; non-GGUF formats get wired to their
    adapter in later phases when the corresponding adapter ships.
    """
    caps = list(row.get('capabilities') or [])
    profile = str(row.get('profile') or '').lower()
    engine_mode = str(row.get('engine_mode') or '').lower()
    haystack = ' '.join(
        str(row.get(key) or '')
        for key in ('label', 'filename', 'path', 'publisher', 'id')
    ).lower()
    if engine_mode == 'embedding' or profile in EMBEDDING_PROFILES:
        modality = 'embedding'
    elif 'ocr' in caps or re.search(r'ocr|chandra|ovis|paddleocr|olmocr', haystack):
        modality = 'ocr'
    elif 'vision' in caps or re.search(r'vision|multimodal|[-_]vl[-_]|mmproj|image', haystack):
        modality = 'vision'
    elif re.search(r'whisper|speech|asr|parakeet|wav2vec|faster[-_]whisper', haystack):
        modality = 'speech-to-text'
    elif re.search(r'text.?to.?speech|tts|piper|kokoro|bark|vibevoice', haystack):
        modality = 'text-to-speech'
    elif re.search(r'translat|nllb|madlad|seamless|tower', haystack):
        modality = 'translation'
    elif re.search(r'embed|embedding|nomic|bge[-_]|e5[-_]|gte[-_]', haystack):
        modality = 'embedding'
    else:
        modality = 'llm'
    row.setdefault('modality', modality)
    runtime_id = 'llama-server'
    if modality == 'text-to-speech':
        # VibeVoice TTS models are safetensors dirs (config.json declares the
        # vibevoice_streaming model_type) and run on the vibevoice runtime;
        # other TTS models map to piper.
        stt_path = str(row.get('path') or '')
        vv_is_dir = False
        try:
            vv_cfg = Path(stt_path).expanduser() / 'config.json'
            if vv_cfg.is_file():
                vv_data = json.loads(vv_cfg.read_text(encoding='utf-8', errors='replace'))
                vv_text = (str(vv_data.get('model_type') or '') + ' ' + str(vv_data.get('architectures') or '')).lower()
                vv_is_dir = 'vibevoice' in vv_text and (Path(stt_path) / 'model.safetensors').is_file()
        except (OSError, ValueError):
            vv_is_dir = False
        if vv_is_dir:
            runtime_id = 'vibevoice'
            row['kind'] = 'dir'
        else:
            runtime_id = 'piper'
    elif modality == 'speech-to-text':
        # faster-whisper models are directories holding model.bin (CTranslate2
        # snapshots); whisper.cpp models are single .gguf/.bin files. Pick the
        # right STT runtime so load/import dispatch to the correct adapter.
        stt_path = str(row.get('path') or '')
        try:
            stt_path_obj = Path(stt_path).expanduser()
            stt_is_dir = stt_path_obj.is_dir() and (stt_path_obj / 'model.bin').is_file()
        except OSError:
            stt_is_dir = False
        if stt_is_dir:
            runtime_id = 'faster-whisper'
            row['kind'] = 'dir'
        else:
            runtime_id = 'stt'
    elif modality == 'ocr':
        from core.gguf_meta import read_gguf_architecture
        from core.ocr_setup import GLMOCR_TRANSFORMERS_REPO, llama_server_supports_glmocr

        ocr_path = str(row.get('path') or '')
        arch = read_gguf_architecture(ocr_path) if ocr_path else str(row.get('arch') or '').lower()
        if arch == 'glmocr' and not llama_server_supports_glmocr():
            runtime_id = 'transformers'
            row['hf_repo'] = GLMOCR_TRANSFORMERS_REPO
            row['plain_gguf'] = False
            row['kind'] = 'dir'
    row.setdefault('runtime_id', runtime_id)
    row.setdefault('kind', 'file')
    row.setdefault('catalog_visible', True)
    row.setdefault('downloadable', True)
    row.setdefault('runnable', row.get('loadable') is True)
    size_gb = row.get('size_gb')
    if isinstance(size_gb, (int, float)) and size_gb > 0:
        row.setdefault('size_bytes', int(size_gb * (1024 ** 3)))
    row.setdefault('estimated_vram_mb', None)
    row.setdefault('runtime_min_version', '')
    row.setdefault('family', str(row.get('arch') or row.get('label') or '').strip())
    row.setdefault('task', {
        'embedding': 'embed',
        'vision': 'vision',
        'ocr': 'ocr',
        'speech-to-text': 'transcribe',
        'text-to-speech': 'speech',
        'translation': 'translate',
    }.get(modality, 'chat'))


def _mark_stack_path_access(models: list[dict[str, Any]], config: dict[str, Any]) -> None:
    roots: list[Path] = []
    for root in allowed_model_roots(config):
        try:
            roots.append(root.expanduser().resolve())
        except OSError:
            continue
    for row in models:
        path_text = str(row.get('path') or '').strip()
        if not path_text or not roots:
            row['stack_path_allowed'] = False
            continue
        try:
            path = Path(path_text).expanduser().resolve()
            row['stack_path_allowed'] = any(path.is_relative_to(root) for root in roots)
        except (OSError, ValueError):
            row['stack_path_allowed'] = False


def _context_max_for_profile(profile: str) -> int:
    if profile in ('gemma-chat', 'gemma-ar', 'gemma-12-ar', 'gemma-12-dflash'):
        return 262144
    if profile in ('qwen-dflash', 'qwen-ar'):
        return 32768
    if profile == 'bonsai-spec':
        return 16384
    if profile == 'bonsai':
        return 8192
    return 131072


def _profile_catalog(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        row = _server_catalog_row(normalize_server(server), cfg=config)
        catalog[row['server_id']] = row
    return catalog


def _build_models_payload(
    config: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    extras: list[dict[str, Any]],
    *,
    partial: bool = False,
) -> dict[str, Any]:
    models = list(catalog.values()) + sorted(extras, key=lambda r: (r.get('label') or '').lower())
    models = _collapse_split_shards(models)
    models = _collapse_identical_files(models)
    _mark_duplicate_files(models)
    for row in models:
        _annotate_path_status(row)
    _mark_stack_path_access(models, config)
    for row in models:
        _annotate_runtime_fields(row)
    models.sort(key=lambda r: (0 if r.get('loadable') else 1, (r.get('label') or '').lower()))
    total_gb = round(sum(float(r.get('size_gb') or 0) for r in models), 2)
    loadable_count = sum(1 for r in models if r.get('loadable'))
    libraries = get_model_libraries(config)
    download_dir = get_download_dir(config)
    payload: dict[str, Any] = {
        'success': True,
        'models': models,
        'models_dir': str(download_dir),
        'model_libraries': libraries,
        'storage_presets': storage_presets(),
        'total_count': len(models),
        'total_size_gb': total_gb,
        'loadable_count': loadable_count,
    }
    if partial:
        payload['partial'] = True
    return payload


def invalidate_model_catalog_cache() -> None:
    global _CATALOG_CACHE, _CATALOG_CACHE_AT, _CATALOG_CACHE_KEY
    global _CATALOG_CACHE_PLAIN, _CATALOG_CACHE_PLAIN_AT, _CATALOG_CACHE_PLAIN_KEY
    _CATALOG_CACHE = None
    _CATALOG_CACHE_AT = 0.0
    _CATALOG_CACHE_KEY = ''
    _CATALOG_CACHE_PLAIN = None
    _CATALOG_CACHE_PLAIN_AT = 0.0
    _CATALOG_CACHE_PLAIN_KEY = ''


def warm_model_catalog(*, cfg: dict[str, Any] | None = None) -> None:
    """Pre-scan local GGUF libraries so the first UI request is fast."""
    list_local_models(cfg=cfg, scan_disk=True, force_refresh=True)


def start_model_catalog_refresh_loop(*, interval_seconds: float = 300.0) -> None:
    """Keep the local model snapshot fresh without blocking API requests."""
    global _CATALOG_REFRESH_LOOP_STARTED
    with _CATALOG_REFRESH_LOCK:
        if _CATALOG_REFRESH_LOOP_STARTED:
            return
        _CATALOG_REFRESH_LOOP_STARTED = True

    def refresh_loop() -> None:
        while True:
            time.sleep(max(60.0, float(interval_seconds)))
            try:
                _schedule_catalog_refresh(load_config(), include_dflash_stacks=True)
            except Exception:
                pass

    threading.Thread(
        target=refresh_loop,
        daemon=True,
        name='model-catalog-refresh-loop',
    ).start()


def list_local_models(
    *,
    cfg: dict[str, Any] | None = None,
    scan_disk: bool = True,
    force_refresh: bool = False,
    include_dflash_stacks: bool = True,
) -> dict[str, Any]:
    global _CATALOG_CACHE, _CATALOG_CACHE_AT, _CATALOG_CACHE_KEY
    global _CATALOG_CACHE_PLAIN, _CATALOG_CACHE_PLAIN_AT, _CATALOG_CACHE_PLAIN_KEY
    config = cfg or load_config()
    catalog = _profile_catalog(config)
    cache_key = _catalog_cache_key(config)

    if not scan_disk:
        return _build_models_payload(config, catalog, [], partial=True)

    now = time.time()
    if include_dflash_stacks:
        if (
            not force_refresh
            and _CATALOG_CACHE
            and _CATALOG_CACHE_KEY == cache_key
            and (now - _CATALOG_CACHE_AT) < _CATALOG_TTL_SECONDS
        ):
            return _CATALOG_CACHE
        if (
            not force_refresh
            and _CATALOG_CACHE
            and _CATALOG_CACHE_KEY == cache_key
        ):
            stale = dict(_CATALOG_CACHE)
            stale['cached'] = True
            stale['stale'] = True
            stale['cache_age_seconds'] = round(max(0.0, now - _CATALOG_CACHE_AT), 1)
            _schedule_catalog_refresh(config, include_dflash_stacks=True)
            return stale
    elif (
        not force_refresh
        and _CATALOG_CACHE_PLAIN
        and _CATALOG_CACHE_PLAIN_KEY == cache_key
        and (now - _CATALOG_CACHE_PLAIN_AT) < _CATALOG_TTL_SECONDS
    ):
        return _CATALOG_CACHE_PLAIN
    elif (
        not force_refresh
        and _CATALOG_CACHE_PLAIN
        and _CATALOG_CACHE_PLAIN_KEY == cache_key
    ):
        stale = dict(_CATALOG_CACHE_PLAIN)
        stale['cached'] = True
        stale['stale'] = True
        stale['cache_age_seconds'] = round(max(0.0, now - _CATALOG_CACHE_PLAIN_AT), 1)
        _schedule_catalog_refresh(config, include_dflash_stacks=False)
        return stale

    if not force_refresh:
        persisted = _read_persisted_catalog(
            cache_key,
            include_dflash_stacks=include_dflash_stacks,
        )
        if persisted is not None:
            if include_dflash_stacks:
                _CATALOG_CACHE = persisted
                _CATALOG_CACHE_AT = now
                _CATALOG_CACHE_KEY = cache_key
            else:
                _CATALOG_CACHE_PLAIN = persisted
                _CATALOG_CACHE_PLAIN_AT = now
                _CATALOG_CACHE_PLAIN_KEY = cache_key
            # The first request after process startup gets the previous scan
            # immediately; the disk scan happens off the request thread.
            _schedule_catalog_refresh(config, include_dflash_stacks=include_dflash_stacks)
            return persisted
        # Never block API workers on a full disk scan — return profile rows
        # immediately and finish the library scan in the background.
        if not _CATALOG_REFRESHING:
            _schedule_catalog_refresh(config, include_dflash_stacks=include_dflash_stacks)
        partial = _build_models_payload(config, catalog, [], partial=True)
        partial['stale'] = True
        partial['cache_age_seconds'] = 0
        return partial

    scanned: list[dict[str, Any]] = []
    for root, source in disk_scan_roots(config):
        scanned.extend(_scan_gguf(root, source=source))
        scanned.extend(_scan_faster_whisper(root, source=source))
        scanned.extend(_scan_vibevoice(root, source=source))

    extras: list[dict[str, Any]] = []
    known_paths = {str(row.get('path') or '').lower() for row in catalog.values()}
    for row in scanned:
        path_key = str(row.get('path') or '').lower()
        if path_key in known_paths:
            continue
        caps = list(row.get('capabilities') or [])
        if 'instruct' not in caps:
            caps.insert(0, 'instruct')
        if 'llm' not in caps:
            caps.append('llm')
        row['server_id'] = ''
        row['label'] = row.get('filename') or row.get('id')
        row['profile'] = ''
        row['port'] = 0
        row['loadable'] = True
        row['context_max'] = 131072
        row['context_size'] = 8192
        row['load_settings'] = {}
        row['inference_settings'] = {}
        row['gpu_layers_max'] = 128
        row['capabilities'] = caps
        row['dflash_stack'] = False
        row['stack_status'] = ''
        row['plain_gguf'] = True
        extras.append(row)
        known_paths.add(path_key)

    # Models installed under Ollama (manifests + blobs) are not .gguf files,
    # so the disk scan above cannot see them; add them explicitly.
    extras.extend(_scan_ollama_models())

    if not include_dflash_stacks:
        payload = _build_models_payload(config, catalog, extras)
        _CATALOG_CACHE_PLAIN = payload
        _CATALOG_CACHE_PLAIN_AT = now
        _CATALOG_CACHE_PLAIN_KEY = cache_key
        _write_persisted_catalog(payload, cache_key=cache_key, include_dflash_stacks=False)
        return payload

    stack_rows = _dflash_stack_supplement(config, catalog, cfg=config)
    # Prefer stack cards over plain GGUF rows for the same target path.
    stack_paths = {str(row.get('path') or '').lower() for row in stack_rows if row.get('path')}
    if stack_paths:
        extras = [
            row for row in extras
            if str(row.get('path') or '').lower() not in stack_paths
        ]
    payload = _build_models_payload(config, catalog, stack_rows + extras)
    _CATALOG_CACHE = payload
    _CATALOG_CACHE_AT = now
    _CATALOG_CACHE_KEY = cache_key
    _write_persisted_catalog(payload, cache_key=cache_key, include_dflash_stacks=True)
    return payload
