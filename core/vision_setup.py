"""Resolve, download, and wire Hugging Face vision projectors (mmproj) for local GGUF models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import get_server, load_config, normalize_server, save_config
from core.local_models import _has_vision_support, invalidate_model_catalog_cache
from core.model_presets import write_server_preset
from core.model_paths import allowed_model_roots

_MMPROJ_RE = re.compile(r'mmproj', re.I)

VISION_CHAT_PROFILES = frozenset({'gemma-12-dflash', 'qwen-dflash', 'gemma-12-ar'})


def server_supports_vision_chat(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> bool:
    """True only for profiles that intentionally run chat vision (mmproj in preset)."""
    if server.get('vision') is False:
        return False
    profile = str(server.get('profile') or '').strip().lower()
    if profile not in VISION_CHAT_PROFILES:
        return False
    mmproj = str(resolve_mmproj_path(server, cfg=cfg) or '').strip()
    return bool(mmproj and Path(mmproj).expanduser().is_file())


def _is_mmproj_name(name: str) -> bool:
    lower = str(name or '').lower()
    return lower.endswith('.gguf') and bool(_MMPROJ_RE.search(lower))


def infer_hf_repo_from_path(path: str | Path) -> str | None:
    """Best-effort repo id from LM Studio-style models/publisher/repo/file layout."""
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    parts = resolved.parts
    for idx, part in enumerate(parts):
        if part.lower() != 'models' or idx + 1 >= len(parts):
            continue
        publisher = parts[idx + 1]
        if idx + 2 < len(parts):
            repo_folder = parts[idx + 2]
            if resolved.parent.name.lower() == repo_folder.lower():
                return f'{publisher}/{repo_folder}'
        folder_hint = str(publisher or '').strip()
        if folder_hint:
            guessed = _guess_vision_repo_from_hint(folder_hint, resolved.name)
            if guessed:
                return guessed
    return _guess_vision_repo_from_hint(str(resolved.parent.name), resolved.name)


def _guess_vision_repo_from_hint(folder_hint: str, filename: str = '') -> str | None:
    haystack = f'{folder_hint}/{filename}'.lower()
    known: list[tuple[str, str]] = [
        ('gemma-4-12b', 'lmstudio-community/gemma-4-12b-it-GGUF'),
        ('gemma-4-31b', 'google/gemma-4-31B-it-qat-q4_0-gguf'),
        ('qwen3.8-27b', 'bartowski/Qwen3.8-27B-GGUF'),
        ('qwen3-8-27b', 'bartowski/Qwen3.8-27B-GGUF'),
    ]
    for token, repo in known:
        if token in haystack:
            return repo
    return None


def _mmproj_siblings(model_path: Path) -> list[Path]:
    if not model_path.is_file():
        return []
    try:
        return sorted(
            sibling
            for sibling in model_path.parent.glob('*.gguf')
            if _is_mmproj_name(sibling.name)
        )
    except OSError:
        return []


def _fetch_mmproj_filenames(repo_id: str) -> list[str]:
    from core.huggingface import _request_json

    repo = str(repo_id or '').strip().strip('/')
    if not repo or '/' not in repo:
        return []
    try:
        import urllib.parse

        payload = _request_json(
            f'https://huggingface.co/api/models/{urllib.parse.quote(repo, safe="/")}',
            timeout=25.0,
        )
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    names: list[str] = []
    for entry in payload.get('siblings') or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('rfilename') or entry.get('path') or '').strip()
        if name and _is_mmproj_name(name):
            names.append(name)
    names.sort(key=_mmproj_rank)
    return names


def _mmproj_rank(name: str) -> tuple[int, int, str]:
    lower = name.lower()
    penalty = 0
    if 'f16' in lower:
        penalty = 0
    elif 'q8_0' in lower or 'q8' in lower:
        penalty = 1
    elif 'q4' in lower:
        penalty = 2
    else:
        penalty = 3
    return (penalty, len(name), lower)


def _is_allowed_model_path(path: Path, cfg: dict[str, Any]) -> bool:
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


def pick_mmproj_filename(repo_id: str, model_path: str | Path) -> str | None:
    names = _fetch_mmproj_filenames(repo_id)
    if not names:
        return None
    target = Path(model_path).name.lower()
    scored: list[tuple[tuple[int, int, str], str]] = []
    for name in names:
        lower = name.lower()
        match_bonus = 0
        if '31b' in target and '31b' in lower:
            match_bonus -= 2
        if '12b' in target and '12b' in lower:
            match_bonus -= 2
        if 'gemma' in target and 'gemma' in lower:
            match_bonus -= 1
        if 'qwen' in target and 'qwen' in lower:
            match_bonus -= 1
        rank = (_mmproj_rank(name)[0] + match_bonus, len(name), lower)
        scored.append((rank, name))
    scored.sort(key=lambda row: row[0])
    return scored[0][1] if scored else names[0]


def resolve_mmproj_path(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> str:
    config = cfg or load_config()
    explicit = str(server.get('mmproj_path') or '').strip()
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_file() and _is_allowed_model_path(explicit_path, config):
            return str(explicit_path.resolve())
    target_path = str(server.get('target_path') or '').strip()
    if not target_path:
        from core.model_stack import resolve_model_stack

        stack = resolve_model_stack(server, cfg=config)
        target = next((row for row in stack if row.get('role') == 'target'), None)
        target_path = str(target.get('path') or '') if target else ''
    if target_path and _is_allowed_model_path(Path(target_path), config):
        siblings = _mmproj_siblings(Path(target_path).expanduser())
        if siblings:
            return str(siblings[0])
    return ''


def vision_plan(*, model_path: str, server_id: str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    path = Path(str(model_path or '').strip()).expanduser().resolve()
    if not path.is_file():
        return {'success': False, 'error': f'model file not found: {model_path}'}
    if not _is_allowed_model_path(path, config):
        return {
            'success': False,
            'error': 'model path not under an allowed model directory',
            'model_path': str(path),
        }

    server = normalize_server(get_server(config, server_id) or {}) if server_id else {}
    if server_id and not server.get('id'):
        return {'success': False, 'error': f'unknown server: {server_id}'}

    explicit = str(server.get('mmproj_path') or '').strip()
    explicit_ok = bool(explicit and Path(explicit).expanduser().is_file())
    local_mmproj = _mmproj_siblings(path)
    if local_mmproj or explicit_ok:
        mmproj = explicit if explicit_ok else str(local_mmproj[0])
        return {
            'success': True,
            'ready': explicit_ok,
            'needs_download': False,
            'model_path': str(path),
            'server_id': server_id or '',
            'mmproj_path': mmproj,
            'filename': Path(mmproj).name,
            'message': 'Vision already available for this model.' if explicit_ok else 'Projector file found locally; ready to wire.',
        }

    if _has_vision_support(path):
        return {
            'success': True,
            'ready': True,
            'model_path': str(path),
            'server_id': server_id or '',
            'mmproj_path': '',
            'message': 'Vision already available for this model.',
        }

    repo_id = infer_hf_repo_from_path(path)
    mmproj_filename = pick_mmproj_filename(repo_id, path) if repo_id else None
    dest = path.parent / Path(mmproj_filename).name if mmproj_filename else None

    if not repo_id or not mmproj_filename:
        return {
            'success': False,
            'error': 'Could not find a matching vision projector on Hugging Face for this model.',
            'model_path': str(path),
            'repo_id': repo_id or '',
        }

    return {
        'success': True,
        'ready': False,
        'needs_download': True,
        'model_path': str(path),
        'server_id': server_id or '',
        'repo_id': repo_id,
        'filename': mmproj_filename,
        'dest_path': str(dest),
        'hf_url': f'https://huggingface.co/{repo_id}/tree/main/{mmproj_filename}',
        'message': f'Will download {mmproj_filename} next to your model.',
    }


def wire_vision(
    *,
    model_path: str,
    mmproj_path: str,
    server_id: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    target = Path(str(model_path or '').strip()).expanduser().resolve()
    projector = Path(str(mmproj_path or '').strip()).expanduser().resolve()
    if not target.is_file():
        return {'success': False, 'error': f'model file not found: {model_path}'}
    if not _is_allowed_model_path(target, config):
        return {'success': False, 'error': 'model path not under an allowed model directory'}
    if not projector.is_file():
        return {'success': False, 'error': f'projector file not found: {mmproj_path}'}
    if not _is_allowed_model_path(projector, config):
        return {'success': False, 'error': 'projector path not under an allowed model directory'}
    if projector.parent != target.parent:
        return {'success': False, 'error': 'projector must be next to the model'}
    if not _is_mmproj_name(projector.name):
        return {'success': False, 'error': 'projector filename must contain mmproj and use GGUF format'}

    if server_id:
        servers = config.get('servers') or []
        updated = False
        for idx, entry in enumerate(servers):
            if not isinstance(entry, dict) or str(entry.get('id') or '') != server_id:
                continue
            configured_target = str(entry.get('target_path') or '').strip()
            if configured_target:
                try:
                    if Path(configured_target).expanduser().resolve() != target:
                        return {'success': False, 'error': 'model does not match the selected server'}
                except OSError:
                    return {'success': False, 'error': 'configured server model path is invalid'}
            merged = {**entry, 'mmproj_path': str(projector.resolve())}
            servers[idx] = merged
            config['servers'] = servers
            save_config(config)
            write_server_preset(normalize_server(merged), cfg=config)
            updated = True
            break
        if not updated:
            return {'success': False, 'error': f'unknown server: {server_id}'}

    invalidate_model_catalog_cache()
    return {
        'success': True,
        'model_path': str(target.resolve()),
        'mmproj_path': str(projector.resolve()),
        'server_id': server_id or '',
        'vision_ready': True,
    }


def wire_vision_after_download(post_action: dict[str, Any]) -> None:
    wire_vision(
        model_path=str(post_action.get('model_path') or ''),
        mmproj_path=str(post_action.get('mmproj_path') or post_action.get('dest_path') or ''),
        server_id=str(post_action.get('server_id') or '').strip() or None,
    )


def ensure_server_vision(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    download: bool = True,
) -> dict[str, Any]:
    """Wire a local mmproj or download one for a server profile."""
    config = cfg or load_config()
    server_id = str(server.get('id') or '').strip()
    target_path = str(server.get('target_path') or '').strip()
    if not server_id or not target_path:
        return {'success': False, 'error': 'server is missing id or target_path', 'server_id': server_id}
    plan = vision_plan(model_path=target_path, server_id=server_id, cfg=config)
    if not plan.get('success'):
        return {'success': False, 'server_id': server_id, 'error': plan.get('error') or 'vision plan failed'}
    if plan.get('ready') and str(plan.get('mmproj_path') or '').strip():
        wired = wire_vision(
            model_path=target_path,
            mmproj_path=str(plan.get('mmproj_path') or ''),
            server_id=server_id,
            cfg=config,
        )
        return {**plan, **wired, 'server_id': server_id}
    if plan.get('needs_download'):
        if not download:
            return {**plan, 'server_id': server_id, 'downloaded': False}
        from core.huggingface import start_download

        post_action = {
            'type': 'wire_vision',
            'model_path': target_path,
            'server_id': server_id,
            'dest_path': plan.get('dest_path') or '',
        }
        downloaded = start_download(
            str(plan.get('repo_id') or ''),
            str(plan.get('filename') or ''),
            dest_path=str(plan.get('dest_path') or ''),
            post_action=post_action,
            cfg=config,
        )
        return {**plan, **downloaded, 'server_id': server_id}
    mmproj = str(plan.get('mmproj_path') or '').strip()
    if mmproj:
        wired = wire_vision(
            model_path=target_path,
            mmproj_path=mmproj,
            server_id=server_id,
            cfg=config,
        )
        return {**plan, **wired, 'server_id': server_id}
    return {**plan, 'server_id': server_id}


def ensure_gemma_qwen_vision_profiles(*, cfg: dict[str, Any] | None = None, download: bool = True) -> list[dict[str, Any]]:
    """Ensure mmproj companions exist for Gemma and Qwen GGUF engine profiles."""
    from core.config import list_servers

    config = cfg or load_config()
    results: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for server in list_servers(config):
        if not isinstance(server, dict) or server.get('enabled') is False:
            continue
        target_path = str(server.get('target_path') or '').strip().lower()
        label = f'{server.get("id")} {server.get("label")}'.lower()
        if not target_path:
            continue
        if not any(token in label or token in target_path for token in ('gemma', 'qwen')):
            continue
        dedupe_key = f'{target_path}|{server.get("id")}'
        if dedupe_key in seen_targets:
            continue
        seen_targets.add(dedupe_key)
        results.append(ensure_server_vision(server, cfg=config, download=download))
    return results
