"""OCR model load helpers (GLM-OCR mmproj wiring, preset hints)."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, load_config
from core.gguf_meta import read_gguf_architecture
from core.model_paths import get_download_dir

GLMOCR_HF_REPO = 'ggml-org/GLM-OCR-GGUF'
GLMOCR_TRANSFORMERS_REPO = 'zai-org/GLM-OCR'
GLMOCR_MMPROJ_FILENAME = 'mmproj-GLM-OCR-Q8_0.gguf'
HF_BASE = 'https://huggingface.co'


def glmocr_transformers_dir(*, cfg: dict[str, Any] | None = None) -> Path:
    config = cfg or load_config()
    return get_download_dir(config) / 'zai-org' / 'GLM-OCR'


def ensure_glmocr_transformers_model(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Download zai-org/GLM-OCR (PyTorch) when the local Ollama GGUF cannot run on llama-server."""
    from core.runtimes.transformers_hf import is_transformers_model_dir

    dest = glmocr_transformers_dir(cfg=cfg)
    if is_transformers_model_dir(dest):
        return {'success': True, 'path': str(dest.resolve()), 'downloaded': False}

    root = get_dflash_root(cfg)
    python = root / 'runtimes' / 'transformers' / 'venv' / 'Scripts' / 'python.exe'
    if not python.is_file():
        return {
            'success': False,
            'error': 'Transformers runtime is not installed. Open Settings → Downloads & engines to install it.',
        }
    dest.parent.mkdir(parents=True, exist_ok=True)
    script = (
        'import os,sys\n'
        'from huggingface_hub import snapshot_download\n'
        'repo=sys.argv[1]\n'
        'dest=sys.argv[2]\n'
        'token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")\n'
        'snapshot_download(repo_id=repo, local_dir=dest, local_dir_use_symlinks=False, token=token)\n'
        'print(dest)\n'
    )
    try:
        import subprocess

        result = subprocess.run(
            [str(python), '-c', script, GLMOCR_TRANSFORMERS_REPO, str(dest)],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'success': False, 'error': f'GLM-OCR download failed: {exc}'}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        return {
            'success': False,
            'error': detail or f'Could not download {GLMOCR_TRANSFORMERS_REPO} from Hugging Face',
        }
    if not is_transformers_model_dir(dest):
        return {'success': False, 'error': f'download finished but model is incomplete: {dest}'}
    return {'success': True, 'path': str(dest.resolve()), 'downloaded': True}


def resolve_glmocr_load(path: str | Path, row: dict[str, Any] | None = None, *, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """When a catalog row points at a GLM-OCR GGUF blob, switch to the Transformers HF install."""
    target = Path(str(path or '')).expanduser()
    arch = read_gguf_architecture(target) if target.is_file() else ''
    meta = row or {}
    label = str(meta.get('label') or meta.get('model_id') or '').lower()
    if arch != 'glmocr' and 'glm-ocr' not in label and meta.get('hf_repo') != GLMOCR_TRANSFORMERS_REPO:
        return None
    if not llama_server_supports_glmocr():
        ensured = ensure_glmocr_transformers_model(cfg=cfg)
        if not ensured.get('success'):
            return ensured
        return {
            'success': True,
            'path': ensured['path'],
            'runtime_id': 'transformers',
            'modality': 'ocr',
            'message': 'GLM-OCR runs on the Transformers runtime (PyTorch).',
        }
    return None


def glmocr_mmproj_cache_dir(*, cfg: dict[str, Any] | None = None) -> Path:
    config = cfg or load_config()
    root = get_download_dir(config) / 'ocr' / 'projectors'
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_glmocr_mmproj_path(*, cfg: dict[str, Any] | None = None) -> Path:
    return glmocr_mmproj_cache_dir(cfg=cfg) / GLMOCR_MMPROJ_FILENAME


def _download_hf_file(repo_id: str, filename: str, dest: Path) -> None:
    url = f'{HF_BASE}/{repo_id}/resolve/main/{urllib.parse.quote(filename, safe="/")}'
    headers = {'User-Agent': 'DFlash-Console/0.1'}
    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=600) as resp:
        with tmp.open('wb') as handle:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    tmp.replace(dest)


def ensure_glmocr_mmproj(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ensure the GLM-OCR vision projector is present under the Console model library."""
    dest = resolve_glmocr_mmproj_path(cfg=cfg)
    if dest.is_file() and dest.stat().st_size > 1024:
        return {'success': True, 'mmproj_path': str(dest.resolve()), 'downloaded': False}
    try:
        _download_hf_file(GLMOCR_HF_REPO, GLMOCR_MMPROJ_FILENAME, dest)
    except Exception as exc:
        return {
            'success': False,
            'error': (
                f'Could not download {GLMOCR_MMPROJ_FILENAME} from {GLMOCR_HF_REPO}: {exc}'
            ),
        }
    if not dest.is_file():
        return {'success': False, 'error': f'download finished but file missing: {dest}'}
    return {'success': True, 'mmproj_path': str(dest.resolve()), 'downloaded': True}


def ocr_load_hints(model_path: str | Path, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return preset hints for OCR GGUF loads (mmproj path, flash-attn off, etc.)."""
    path = Path(str(model_path or '')).expanduser()
    if not path.is_file():
        return {'success': False, 'error': f'model file not found: {model_path}'}
    arch = read_gguf_architecture(path)
    if arch != 'glmocr':
        return {'success': True, 'architecture': arch, 'ocr': False}
    mmproj = ensure_glmocr_mmproj(cfg=cfg)
    if not mmproj.get('success'):
        return mmproj
    return {
        'success': True,
        'ocr': True,
        'architecture': arch,
        'mmproj_path': mmproj.get('mmproj_path') or '',
        'flash_attention': False,
        'context_size': 12000,
        'message': 'GLM-OCR requires a vision projector and flash-attn off.',
    }


def llama_server_supports_glmocr() -> bool:
    """True only when the bundled llama.dll actually contains the glmocr arch."""
    root = get_dflash_root()
    dll = root / 'llama.cpp' / 'build' / 'bin' / 'Release' / 'llama.dll'
    if not dll.is_file():
        return False
    try:
        return b'glmocr' in dll.read_bytes()
    except OSError:
        return False
