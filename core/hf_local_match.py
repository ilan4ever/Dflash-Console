"""Match Hugging Face repo files to locally installed model files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import load_config
from core.local_models import list_local_models
from core.model_paths import enabled_scan_roots, get_model_libraries
from core.stack_match import _param_token


def _repo_parts(repo_id: str) -> tuple[str, str] | None:
    repo = str(repo_id or '').strip().strip('/')
    if not repo or '/' not in repo:
        return None
    author, repo_name = repo.split('/', 1)
    if not author or not repo_name:
        return None
    return author, repo_name


def _library_labels(cfg: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in get_model_libraries(cfg):
        path = str(row.get('path') or '').strip()
        if not path:
            continue
        try:
            key = str(Path(path).expanduser().resolve()).lower()
        except OSError:
            continue
        labels[key] = str(row.get('label') or row.get('id') or 'Models')
    return labels


def _library_label_for_path(path: Path, labels: dict[str, str], fallback: str = 'Local') -> str:
    resolved = str(path.resolve()).lower()
    best = ''
    best_label = fallback
    for root, label in labels.items():
        if resolved.startswith(root) and len(root) > len(best):
            best = root
            best_label = label
    return best_label


def _row_from_path(
    path: Path,
    *,
    match_type: str,
    labels: dict[str, str],
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = str((row or {}).get('source') or 'library')
    return {
        'path': str(path.resolve()),
        'filename': path.name,
        'library_label': _library_label_for_path(path, labels, fallback=source),
        'source': source,
        'size_gb': (row or {}).get('size_gb'),
        'match_type': match_type,
        'loadable': bool((row or {}).get('loadable')),
        'server_id': str((row or {}).get('server_id') or ''),
    }


def find_local_matches(repo_id: str, filename: str, *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return local installs for a Hugging Face repo file, strongest matches first."""
    config = cfg or load_config()
    parts = _repo_parts(repo_id)
    target_name = Path(str(filename or '').strip()).name
    if not parts or not target_name:
        return []

    author, repo_name = parts
    rel_suffix = f'{author}/{repo_name}/{target_name}'.replace('\\', '/').lower()
    norm_name = target_name.lower()
    repo_slug = repo_name.lower().replace('_', '-')
    labels = _library_labels(config)

    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_match(path: Path, match_type: str, row: dict[str, Any] | None = None) -> None:
        if not path.is_file():
            return
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        matches.append(_row_from_path(path, match_type=match_type, labels=labels, row=row))

    catalog = list_local_models(cfg=config)
    catalog_by_path = {
        str(row.get('path') or '').lower(): row
        for row in (catalog.get('models') or [])
        if row.get('path')
    }

    for root, *_rest in enabled_scan_roots(config):
        expected = root.expanduser() / author / repo_name / target_name
        row = catalog_by_path.get(str(expected.resolve()).lower()) if expected.is_file() else None
        add_match(expected, 'exact_path', row)

    for row in catalog.get('models') or []:
        path_text = str(row.get('path') or '').strip()
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_file():
            continue
        if Path(path.name).name.lower() != norm_name:
            continue
        normalized = path.as_posix().lower()
        if normalized.endswith(rel_suffix):
            add_match(path, 'hf_layout', row)
            continue
        if f'/{author.lower()}/' in normalized and repo_slug.replace('-', '') in normalized.replace('-', ''):
            add_match(path, 'repo_path', row)
            continue
        if str(row.get('publisher') or '').lower() == author.lower() and repo_slug.replace('-', '') in normalized.replace('-', ''):
            add_match(path, 'publisher_repo', row)

    priority = {'exact_path': 0, 'hf_layout': 1, 'repo_path': 2, 'publisher_repo': 3}
    matches.sort(key=lambda item: (priority.get(str(item.get('match_type')), 99), item.get('path') or ''))
    return matches


def _extract_hf_repo_from_path(path: Path, config: dict[str, Any]) -> str | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    for root, *_rest in enabled_scan_roots(config):
        try:
            root_resolved = root.expanduser().resolve()
            rel = resolved.relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if len(rel.parts) >= 3 and rel.suffix.lower() == '.gguf':
            author, repo_name = rel.parts[0], rel.parts[1]
            if author and repo_name:
                return f'{author}/{repo_name}'
    return None


def _path_dflash_hf_repo(path: Path, config: dict[str, Any]) -> str | None:
    hf_repo = _extract_hf_repo_from_path(path, config)
    if not hf_repo:
        return None
    repo_l = hf_repo.lower()
    name_l = path.name.lower()
    if 'dflash' in repo_l or 'dspark' in repo_l or 'dflash' in name_l or 'dspark' in name_l:
        return hf_repo
    return None


def _flat_stack_text(stack: dict[str, Any]) -> str:
    parts = [str(stack.get('label') or '')]
    for key in ('draft_path', 'path'):
        path_text = str(stack.get(key) or '').strip()
        if path_text:
            parts.append(Path(path_text).stem)
    return ' '.join(parts).lower()


def _repo_blob(repo_id: str, title: str = '', tags: list[str] | None = None) -> str:
    tag_text = ' '.join(str(tag or '') for tag in (tags or []))
    return f'{repo_id} {title} {tag_text}'.lower()


def _flat_stack_matches_repo(stack: dict[str, Any], blob: str) -> bool:
    from core.dflash_generation import infer_dflash_generation, repo_dflash_generation

    stack_text = _flat_stack_text(stack)
    stack_paths = [
        Path(str(stack.get(key) or ''))
        for key in ('draft_path', 'path')
        if str(stack.get(key) or '').strip()
    ]
    draft_paths = [
        path for path in stack_paths
        if 'dflash' in path.name.lower() or 'dspark' in path.name.lower()
    ]
    repo_generation = repo_dflash_generation(blob)
    if draft_paths:
        draft_generations = {infer_dflash_generation(path) for path in draft_paths}
        if repo_generation == 'dflash2' and 'dflash2' not in draft_generations:
            return False
        if repo_generation == 'dflash1' and draft_generations == {'dflash2'}:
            return False

    stack_param = next((_param_token(path.name) for path in stack_paths if path.name), None)
    if stack_param:
        param_flat = stack_param.replace('.', '').replace('b', '')
        blob_flat = blob.replace('-', '').replace('_', '').replace('.', '')
        if param_flat not in blob_flat:
            return False

    for family in ('gemma', 'qwen', 'deepseek', 'bonsai'):
        if family in stack_text and family not in blob:
            return False

    if 'qwen' in stack_text:
        version = re.search(r'qwen[\s._-]?(\d+(?:\.\d+)?)', stack_text, re.I)
        if version:
            ver = version.group(1)
            ver_flat = ver.replace('.', '')
            blob_flat = blob.replace('-', '').replace('_', '').replace('.', '')
            if f'qwen{ver_flat}' not in blob_flat and ver not in blob:
                return False
            if ver == '3.5' and ('qwen36' in blob_flat or 'qwen3.6' in blob):
                return False
            if ver == '3.6' and ('qwen35' in blob_flat or 'qwen3.5' in blob):
                return False

    if 'gemma' in stack_text:
        version = re.search(r'gemma[\s._-]?(\d+)', stack_text, re.I)
        if version:
            ver = version.group(1)
            blob_flat = blob.replace('-', '').replace('_', '').replace('.', '')
            if f'gemma{ver}' not in blob_flat and ver not in blob:
                return False

    return True


def loadable_catalog_ready_repos(cfg: dict[str, Any] | None = None) -> tuple[set[str], list[dict[str, Any]]]:
    """Return HF repo ids from loadable DFlash stacks plus flat stacks needing signature match."""
    config = cfg or load_config()
    exact: set[str] = set()
    flat: list[dict[str, Any]] = []
    for row in list_local_models(cfg=config).get('models') or []:
        if not row.get('loadable') or not row.get('dflash_stack'):
            continue
        had_dflash_repo = False
        for key in ('draft_path', 'path'):
            path_text = str(row.get(key) or '').strip()
            if not path_text:
                continue
            hf_repo = _path_dflash_hf_repo(Path(path_text), config)
            if hf_repo:
                exact.add(hf_repo.lower())
                had_dflash_repo = True
        if not had_dflash_repo:
            flat.append(row)
    return exact, flat


def is_catalog_ready_to_load(
    repo_id: str,
    *,
    title: str = '',
    tags: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
) -> bool:
    """True when an HF repo corresponds to a loadable DFlash stack on this machine."""
    if not is_dflash_repo(repo_id, tags):
        return False
    config = cfg or load_config()
    repo_l = str(repo_id or '').strip().lower()
    if not repo_l:
        return False
    exact, flat_stacks = loadable_catalog_ready_repos(config)
    if repo_l in exact:
        return True
    blob = _repo_blob(repo_id, title, tags)
    return any(_flat_stack_matches_repo(stack, blob) for stack in flat_stacks)


def is_dflash_repo(repo_id: str, tags: list[str] | None = None) -> bool:
    repo = str(repo_id or '').lower()
    if 'dflash' in repo or 'dspark' in repo:
        return True
    for tag in tags or []:
        text = str(tag or '').lower()
        if 'dflash' in text or 'dspark' in text:
            return True
    return False


def find_repo_local_installs(repo_id: str, *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return local files on disk that belong to a Hugging Face repo."""
    config = cfg or load_config()
    parts = _repo_parts(repo_id)
    if not parts:
        return []

    author, repo_name = parts
    author_l = author.lower()
    repo_slug = repo_name.lower().replace('_', '-')
    repo_slug_flat = repo_slug.replace('-', '')
    labels = _library_labels(config)
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in list_local_models(cfg=config).get('models') or []:
        path_text = str(row.get('path') or '').strip()
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_file():
            continue
        normalized = path.as_posix().lower()
        norm_flat = normalized.replace('-', '').replace('_', '')
        matched = False
        if f'/{author_l}/' in normalized and repo_slug in normalized:
            matched = True
        elif repo_slug_flat and repo_slug_flat in norm_flat and f'/{author_l}/' in normalized:
            matched = True
        elif str(row.get('publisher') or '').lower() == author_l and repo_slug_flat in norm_flat:
            matched = True
        if not matched:
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        matches.append(_row_from_path(path, match_type='repo_scan', labels=labels, row=row))

    if not matches:
        tokens = [
            token for token in re.split(r'[^a-z0-9]+', repo_name.lower())
            if len(token) >= 3 and token not in {'gguf', 'llama', 'cpp', 'model', 'dflash', 'dspark'}
        ]
        for row in list_local_models(cfg=config).get('models') or []:
            path_text = str(row.get('path') or '').strip()
            path = Path(path_text)
            if not path.is_file():
                continue
            normalized = path.as_posix().lower().replace('_', '-')
            if author_l not in normalized and f'/{author_l}/' not in normalized:
                continue
            if repo_slug not in normalized and repo_slug_flat not in normalized.replace('-', ''):
                continue
            if tokens and sum(token in normalized for token in tokens) < min(2, len(tokens)):
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            matches.append(_row_from_path(path, match_type='model_name', labels=labels, row=row))

    return matches


def primary_local_match(repo_id: str, filename: str, *, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = find_local_matches(repo_id, filename, cfg=cfg)
    return rows[0] if rows else None


def local_installs_for_files(repo_id: str, filenames: list[str], *, cfg: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    config = cfg or load_config()
    installs: dict[str, list[dict[str, Any]]] = {}
    for name in filenames:
        fn = Path(str(name or '').strip()).name
        if not fn:
            continue
        installs[fn] = find_local_matches(repo_id, fn, cfg=config)
    return installs
