"""Machine-readable API discovery, installed catalog, and console logs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.api_access_log import is_error_line, list_api_calls, read_access_log_file
from core.config import list_servers, load_config
from core.local_models import list_local_models
from core.model_discovery import summarize_library_path
from core.model_paths import storage_presets
from core.version import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'logs'

_CONSOLE_LOG_FILES = {
    'console': LOG_DIR / 'console-server.log',
    'console_err': LOG_DIR / 'console-server.err.log',
    'startup': LOG_DIR / 'startup.log',
}


def list_app_endpoints(app: Any, *, console_base: str = '') -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for route in getattr(app, 'routes', []) or []:
        methods = getattr(route, 'methods', None)
        path = getattr(route, 'path', None)
        if not methods or not path:
            continue
        for method in sorted(methods - {'HEAD', 'OPTIONS'}):
            endpoint = getattr(route, 'endpoint', None)
            doc_lines = str(getattr(endpoint, '__doc__', '') or '').strip().splitlines() if endpoint else []
            doc = doc_lines[0] if doc_lines else ''
            rows.append({
                'method': method,
                'path': path,
                'name': str(getattr(route, 'name', '') or ''),
                'summary': str(getattr(route, 'summary', None) or doc or ''),
                'tags': list(getattr(route, 'tags', None) or []),
            })
    rows.sort(key=lambda row: (row['path'], row['method']))
    base = console_base.rstrip('/')
    return {
        'success': True,
        'app': 'DFlash Console',
        'version': APP_VERSION,
        'count': len(rows),
        'openapi_url': f'{base}/openapi.json' if base else '/openapi.json',
        'swagger_url': f'{base}/docs' if base else '/docs',
        'endpoints': rows,
    }


def _model_in_library(model: dict[str, Any], library: dict[str, Any]) -> bool:
    model_path = str(model.get('path') or '').strip()
    root = str(library.get('path') or '').strip()
    if not model_path or not root:
        return False
    try:
        return Path(model_path).expanduser().resolve().is_relative_to(Path(root).expanduser().resolve())
    except (OSError, ValueError):
        return model_path.lower().startswith(root.lower())


def get_installed_payload(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    models_payload = list_local_models(cfg=config)
    models = list(models_payload.get('models') or [])
    libraries = list(models_payload.get('model_libraries') or [])
    preset_map = {row['id']: row for row in storage_presets()}

    providers: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for library in libraries:
        preset = str(library.get('preset') or 'custom')
        preset_meta = preset_map.get(preset, preset_map.get('custom', {}))
        stats = summarize_library_path(str(library.get('path') or ''), preset)
        lib_models = [row for row in models if _model_in_library(row, library)]
        for row in lib_models:
            path_key = str(row.get('path') or '').lower()
            if path_key:
                assigned.add(path_key)
        providers.append({
            'id': str(library.get('id') or ''),
            'label': str(library.get('label') or preset_meta.get('label') or library.get('id') or ''),
            'preset': preset,
            'preset_label': str(preset_meta.get('label') or preset),
            'description': str(preset_meta.get('description') or ''),
            'path': str(library.get('path') or ''),
            'enabled': library.get('enabled', True) is not False,
            'model_count': int(stats.get('model_count') or len(lib_models)),
            'total_size_gb': stats.get('total_size_gb'),
            'models': lib_models,
        })

    unassigned = [
        row for row in models
        if str(row.get('path') or '').lower() not in assigned and str(row.get('path') or '').strip()
    ]
    if unassigned:
        providers.append({
            'id': 'other',
            'label': 'Other local models',
            'preset': 'custom',
            'preset_label': 'Custom folder',
            'description': 'Models found outside configured libraries',
            'path': '',
            'enabled': True,
            'model_count': len(unassigned),
            'total_size_gb': round(sum(float(row.get('size_gb') or 0) for row in unassigned), 2),
            'models': unassigned,
        })

    return {
        'success': True,
        'models': models,
        'providers': providers,
        'model_libraries': libraries,
        'storage_presets': models_payload.get('storage_presets') or storage_presets(),
        'models_dir': models_payload.get('models_dir'),
        'total_count': int(models_payload.get('total_count') or len(models)),
        'total_size_gb': models_payload.get('total_size_gb'),
        'loadable_count': models_payload.get('loadable_count'),
    }


def _read_log_source(path: Path, *, tail: int) -> dict[str, Any]:
    limit = max(1, min(int(tail or 200), 5000))
    if not path.is_file():
        return {'path': str(path), 'exists': False, 'lines': [], 'total_lines': 0}
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return {'path': str(path), 'exists': True, 'lines': [], 'total_lines': 0}
    return {
        'path': str(path),
        'exists': True,
        'total_lines': len(lines),
        'lines': lines[-limit:],
    }


def get_console_logs_payload(
    *,
    cfg: dict[str, Any] | None = None,
    tail: int = 200,
    errors_only: bool = False,
    include_engines: bool = True,
    include_api_calls: bool = True,
) -> dict[str, Any]:
    config = cfg or load_config()
    sources: dict[str, Any] = {}

    for key, path in _CONSOLE_LOG_FILES.items():
        sources[key] = _read_log_source(path, tail=tail)

    sources['api_access_file'] = read_access_log_file(tail=tail)

    if include_engines:
        engine_logs: dict[str, Any] = {}
        for server in list_servers(config):
            server_id = str(server.get('id') or '').strip()
            if not server_id:
                continue
            engine_logs[server_id] = _read_log_source(LOG_DIR / f'{server_id}.log', tail=tail)
        sources['engines'] = engine_logs

    error_lines: list[dict[str, Any]] = []
    for source_name, payload in sources.items():
        if source_name == 'engines' and isinstance(payload, dict):
            for engine_id, engine_payload in payload.items():
                for line in engine_payload.get('lines') or []:
                    if is_error_line(line):
                        error_lines.append({'source': f'engine:{engine_id}', 'line': line})
            continue
        for line in (payload or {}).get('lines') or []:
            if is_error_line(line):
                error_lines.append({'source': source_name, 'line': line})

    api_calls = list_api_calls(tail=tail, errors_only=errors_only) if include_api_calls else []
    for row in api_calls:
        if row.get('error'):
            error_lines.append({
                'source': 'api_call',
                'line': f'{row.get("method")} {row.get("path")} -> {row.get("status")}: {row.get("error")}',
            })

    if errors_only:
        for key in list(sources.keys()):
            if key == 'engines':
                filtered: dict[str, Any] = {}
                for engine_id, engine_payload in (sources.get('engines') or {}).items():
                    lines = [line for line in (engine_payload.get('lines') or []) if is_error_line(line)]
                    filtered[engine_id] = {**engine_payload, 'lines': lines}
                sources['engines'] = filtered
            else:
                payload = sources.get(key) or {}
                sources[key] = {
                    **payload,
                    'lines': [line for line in (payload.get('lines') or []) if is_error_line(line)],
                }

    return {
        'success': True,
        'logs_dir': str(LOG_DIR),
        'tail': max(1, min(int(tail or 200), 5000)),
        'errors_only': bool(errors_only),
        'sources': sources,
        'errors': error_lines[-max(1, min(int(tail or 200), 5000)):],
        'api_calls': api_calls,
    }
