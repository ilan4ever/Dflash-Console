"""Match Hugging Face repo files to locally installed model files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import load_config
from core.local_models import list_local_models
from core.model_paths import enabled_scan_roots, get_model_libraries


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

    for root, _source in enabled_scan_roots(config):
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
