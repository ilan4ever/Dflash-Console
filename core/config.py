"""Load and persist DFlash Console configuration."""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / 'config.json'
_CONFIG_SAVE_LOCK = threading.Lock()

VALID_PROFILES = frozenset({
    'gemma-chat',
    'gemma-ar',
    'gemma-12-ar',
    'gemma-12-dflash',
    'qwen-dflash',
    'qwen-ar',
    'bonsai',
    'bonsai-spec',
    'generic-ar',
    'nomic-embed',
})

EMBEDDING_PROFILES = frozenset({'nomic-embed'})

DEFAULT_LOAD_SETTINGS: dict[str, Any] = {
    'gpu_layers': 99,
    'cpu_threads': 9,
    'eval_batch_size': 2048,
    'physical_batch_size': 512,
    'flash_attention': True,
    'parallel_slots': 4,
}

DEFAULT_INFERENCE_SETTINGS: dict[str, Any] = {
    'temperature': 0.7,
    'top_p': 0.9,
    'top_k': 40,
    'repeat_penalty': 1.1,
    'max_tokens': 4096,
}

DEFAULT_HARDWARE_SETTINGS: dict[str, Any] = {
    # Prefer the fastest/largest GPU as a whole model — never auto layer-split.
    # Layer-split across PCIe (4090+TITAN) destroys decode speed.
    'gpu_strategy': 'single_largest',
    'enabled_gpu_indices': [],
    'limit_offload_dedicated_vram': True,
    'offload_kv_cache_to_gpu': True,
}

SPECULATIVE_PROFILES = frozenset({'gemma-chat', 'gemma-12-dflash', 'qwen-dflash', 'bonsai-spec'})


def is_embedding_server(entry: dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    mode = str(entry.get('engine_mode') or '').strip().lower()
    profile = str(entry.get('profile') or '').strip().lower()
    return mode == 'embedding' or profile in EMBEDDING_PROFILES


def is_loopback_host(value: Any) -> bool:
    host = str(value or '').strip().lower()
    if host in {'', 'localhost'}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        raise ValueError('config.json must be a JSON object')
    try:
        ui_port = int(cfg.get('ui_port') or 8900)
    except (TypeError, ValueError) as exc:
        raise ValueError('ui_port must be an integer') from exc
    if not 1 <= ui_port <= 65535:
        raise ValueError('ui_port must be between 1 and 65535')

    servers = cfg.get('servers') or []
    if not isinstance(servers, list):
        raise ValueError('servers must be a list')
    seen_ids: set[str] = set()
    seen_ports: dict[int, str] = {ui_port: 'ui_port'}
    for index, row in enumerate(servers):
        if not isinstance(row, dict):
            raise ValueError(f'servers[{index}] must be an object')
        server_id = str(row.get('id') or '').strip()
        if not server_id:
            raise ValueError(f'servers[{index}].id is required')
        if server_id in seen_ids:
            raise ValueError(f'duplicate server id: {server_id}')
        seen_ids.add(server_id)
        try:
            port = int(row.get('port') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'servers[{index}].port must be an integer') from exc
        if not 1 <= port <= 65535:
            raise ValueError(f'servers[{index}].port must be between 1 and 65535')
        if port in seen_ports:
            raise ValueError(f'port {port} is already used by {seen_ports[port]}')
        seen_ports[port] = server_id
        if not is_loopback_host(row.get('host')):
            raise ValueError(f'servers[{index}].host must be loopback-only')

    return cfg


DEFAULT_ENGINE_PORTS = (8090, 8091, 8092, 8093, 8094, 8095, 8096, 8097)


def normalize_load_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_LOAD_SETTINGS)
    flash = raw.get('flash_attention')
    return {
        'gpu_layers': max(0, min(999, int(raw.get('gpu_layers') or DEFAULT_LOAD_SETTINGS['gpu_layers']))),
        'cpu_threads': max(1, min(64, int(raw.get('cpu_threads') or DEFAULT_LOAD_SETTINGS['cpu_threads']))),
        'eval_batch_size': max(32, min(8192, int(raw.get('eval_batch_size') or DEFAULT_LOAD_SETTINGS['eval_batch_size']))),
        'physical_batch_size': max(32, min(8192, int(raw.get('physical_batch_size') or DEFAULT_LOAD_SETTINGS['physical_batch_size']))),
        'flash_attention': flash is not False,
        'parallel_slots': max(1, min(16, int(raw.get('parallel_slots') or DEFAULT_LOAD_SETTINGS['parallel_slots']))),
    }


def normalize_inference_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_INFERENCE_SETTINGS)
    return {
        'temperature': max(0.0, min(2.0, float(raw.get('temperature') if raw.get('temperature') is not None else DEFAULT_INFERENCE_SETTINGS['temperature']))),
        'top_p': max(0.0, min(1.0, float(raw.get('top_p') if raw.get('top_p') is not None else DEFAULT_INFERENCE_SETTINGS['top_p']))),
        'top_k': max(0, min(200, int(raw.get('top_k') if raw.get('top_k') is not None else DEFAULT_INFERENCE_SETTINGS['top_k']))),
        'repeat_penalty': max(1.0, min(2.0, float(raw.get('repeat_penalty') if raw.get('repeat_penalty') is not None else DEFAULT_INFERENCE_SETTINGS['repeat_penalty']))),
        'max_tokens': max(256, min(32768, int(raw.get('max_tokens') if raw.get('max_tokens') is not None else DEFAULT_INFERENCE_SETTINGS['max_tokens']))),
    }


def normalize_hardware_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    strategy = str(raw.get('gpu_strategy') or DEFAULT_HARDWARE_SETTINGS['gpu_strategy']).strip().lower()
    if strategy not in ('single_largest', 'split_evenly', 'split_by_vram'):
        strategy = DEFAULT_HARDWARE_SETTINGS['gpu_strategy']
    enabled_raw = raw.get('enabled_gpu_indices')
    enabled_indices: list[int] = []
    if isinstance(enabled_raw, list):
        for item in enabled_raw:
            try:
                enabled_indices.append(int(item))
            except (TypeError, ValueError):
                continue
    return {
        'gpu_strategy': strategy,
        'enabled_gpu_indices': enabled_indices,
        'limit_offload_dedicated_vram': raw.get('limit_offload_dedicated_vram') is not False,
        'offload_kv_cache_to_gpu': raw.get('offload_kv_cache_to_gpu') is not False,
    }


def normalize_ui_layout(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    result: dict[str, Any] = {}
    for key, lo, hi in (
        ('sidenav_width', 120, 480),
        ('inspector_width', 180, 720),
        ('logs_height', 80, 900),
        ('hf_search_left_width', 160, 720),
    ):
        if key not in raw:
            continue
        try:
            result[key] = max(lo, min(hi, int(raw[key])))
        except (TypeError, ValueError):
            continue
    if 'logs_hidden' in raw:
        result['logs_hidden'] = raw.get('logs_hidden') is True
    if 'sidenav_hidden' in raw:
        result['sidenav_hidden'] = raw.get('sidenav_hidden') is True
    for key in ('engines_show_dflash', 'engines_show_external'):
        if key in raw:
            result[key] = raw.get(key) is not False
    if 'engines_card_filter' in raw:
        val = str(raw.get('engines_card_filter') or '').strip().lower()
        if val in ('both', 'dflash', 'external'):
            result['engines_card_filter'] = val
    elif 'engines_show_dflash' in raw or 'engines_show_external' in raw:
        show_dflash = raw.get('engines_show_dflash') is not False
        show_external = raw.get('engines_show_external') is not False
        if show_dflash and show_external:
            result['engines_card_filter'] = 'both'
        elif show_dflash:
            result['engines_card_filter'] = 'dflash'
        elif show_external:
            result['engines_card_filter'] = 'external'
        else:
            result['engines_card_filter'] = 'both'
    table_raw = raw.get('table_columns')
    if isinstance(table_raw, dict):
        table_columns: dict[str, dict[str, int]] = {}
        for table_key, cols in table_raw.items():
            if not isinstance(cols, dict):
                continue
            safe_key = str(table_key).strip()
            if not safe_key:
                continue
            normalized_cols: dict[str, int] = {}
            for col_id, width in cols.items():
                try:
                    value = int(width)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    normalized_cols[str(col_id)] = value
            if normalized_cols:
                table_columns[safe_key] = normalized_cols
        if table_columns:
            result['table_columns'] = table_columns
    valid_views = {'chat', 'server', 'models', 'devices', 'docs', 'catalog', 'settings'}
    if 'active_view' in raw:
        view = str(raw.get('active_view') or '').strip()
        if view in valid_views:
            result['active_view'] = view
    if 'inspector_collapsed' in raw:
        result['inspector_collapsed'] = raw.get('inspector_collapsed') is True
    valid_inspector_tabs = {'info', 'load'}
    if 'inspector_tab' in raw:
        tab = str(raw.get('inspector_tab') or '').strip()
        if tab in valid_inspector_tabs:
            result['inspector_tab'] = tab
    valid_settings_panels = {
        'ws-checkpoints', 'ws-locations', 'hw-system', 'hw-gpus', 'hw-strategy',
        'hw-live', 'gw-network', 'gw-behavior', 'gw-preset', 'int-mcp',
    }
    if 'settings_panel' in raw:
        panel = str(raw.get('settings_panel') or '').strip()
        if panel in valid_settings_panels:
            result['settings_panel'] = panel
    return result


def get_dflash_root(cfg: dict[str, Any] | None = None) -> Path:
    config = cfg if cfg is not None else load_config()
    raw = (
        os.environ.get('DFLASH_ROOT_OVERRIDE')
        or config.get('dflash_root')
        or os.environ.get('DFLASH_ROOT')
        or r'C:\dev\Dflash'
    )
    return Path(str(raw)).resolve()


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {'ui_port': 8900, 'dflash_root': r'C:\dev\Dflash', 'servers': []}
    with CONFIG_PATH.open(encoding='utf-8') as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError('config.json must be a JSON object')
    servers = data.get('servers')
    if not isinstance(servers, list):
        data['servers'] = []
    data['hardware_settings'] = normalize_hardware_settings(data.get('hardware_settings'))
    if 'ui_layout' in data:
        data['ui_layout'] = normalize_ui_layout(data.get('ui_layout'))
    return validate_config(data)


def suggest_server_port(*, cfg: dict[str, Any] | None = None) -> int:
    config = cfg or load_config()
    used = {int(config.get('ui_port') or 8900)}
    used.update(int(row.get('port') or 0) for row in list_servers(config) if row.get('port'))
    for port in DEFAULT_ENGINE_PORTS:
        if port not in used:
            return port
    return max(used) + 1 if used else 8090


def save_config(cfg: dict[str, Any]) -> None:
    validate_config(cfg)
    payload = json.dumps(cfg, indent=2) + '\n'
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ''
    with _CONFIG_SAVE_LOCK:
        with _config_file_lock():
            try:
                fd, temp_name = tempfile.mkstemp(
                    prefix=f'.{CONFIG_PATH.name}.',
                    suffix='.tmp',
                    dir=str(CONFIG_PATH.parent),
                    text=True,
                )
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, CONFIG_PATH)
                temp_name = ''
            finally:
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass


@contextmanager
def _config_file_lock() -> Iterator[None]:
    lock_path = CONFIG_PATH.with_name(f'{CONFIG_PATH.name}.lock')
    with lock_path.open('a+b') as handle:
        handle.seek(0)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update_server_runtime(
    server_id: str,
    *,
    engine_on: bool | None = None,
    loaded_by: str | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    entry = get_server(cfg, server_id)
    if not entry:
        return {'engine_on': False}

    changed = False
    if engine_on is not None and (entry.get('engine_on') is True) != bool(engine_on):
        entry['engine_on'] = bool(engine_on)
        changed = True
    if loaded_by is not None:
        label = str(loaded_by or '').strip()
        if label and entry.get('loaded_by') != label:
            entry['loaded_by'] = label
            changed = True
    if changed:
        entry.pop('checkpoint_loaded', None)
        save_config(cfg)

    return {
        'engine_on': entry.get('engine_on') is True,
        'loaded_by': str(entry.get('loaded_by') or '').strip(),
    }


def get_server(cfg: dict[str, Any], server_id: str) -> dict[str, Any] | None:
    for entry in cfg.get('servers') or []:
        if isinstance(entry, dict) and str(entry.get('id') or '') == server_id:
            return entry
    return None


def normalize_server(entry: dict[str, Any]) -> dict[str, Any]:
    port = int(entry.get('port') or 0)
    host = str(entry.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    api_url = str(entry.get('api_url') or f'http://{host}:{port}/v1').strip().rstrip('/')
    idle_minutes = entry.get('idle_unload_minutes')
    if idle_minutes is None:
        idle_seconds = int(entry.get('idle_unload_seconds') or 3600)
        idle_minutes = max(0, round(idle_seconds / 60))
    target_path = str(entry.get('target_path') or '').strip()
    draft_path = str(entry.get('draft_path') or '').strip()
    result = {
        'id': str(entry.get('id') or '').strip(),
        'label': str(entry.get('label') or entry.get('id') or 'Server').strip(),
        'profile': str(entry.get('profile') or 'gemma-chat').strip(),
        'port': port,
        'host': host,
        'api_url': api_url,
        'model_id': str(entry.get('model_id') or '').strip(),
        'gpu_device': str(entry.get('gpu_device') or 'auto').strip().lower() or 'auto',
        'context_size': max(2048, int(entry.get('context_size') or 8192)),
        'idle_unload_minutes': max(0, int(idle_minutes or 0)),
        'enabled': entry.get('enabled', True) is not False,
        'engine_on': entry.get('engine_on') is True,
        'loaded_by': str(entry.get('loaded_by') or '').strip(),
        'load_settings': normalize_load_settings(entry.get('load_settings')),
        'inference_settings': normalize_inference_settings(entry.get('inference_settings')),
    }
    if target_path:
        result['target_path'] = target_path
    if draft_path:
        result['draft_path'] = draft_path
    mmproj_path = str(entry.get('mmproj_path') or '').strip()
    if mmproj_path:
        result['mmproj_path'] = mmproj_path
    engine_mode = str(entry.get('engine_mode') or '').strip().lower()
    profile_name = str(result.get('profile') or '').strip().lower()
    if engine_mode:
        result['engine_mode'] = engine_mode
    elif profile_name in EMBEDDING_PROFILES:
        result['engine_mode'] = 'embedding'
    pooling = str(entry.get('pooling') or '').strip().lower()
    if pooling:
        result['pooling'] = pooling
    elif profile_name in EMBEDDING_PROFILES:
        result['pooling'] = 'mean'
    embed_raw = entry.get('embedding_settings')
    if isinstance(embed_raw, dict):
        result['embedding_settings'] = {
            'dimensions': int(embed_raw.get('dimensions') or 768),
            'parameters': str(embed_raw.get('parameters') or '137M'),
            'architecture': str(embed_raw.get('architecture') or 'nomic-bert'),
            'model_family': str(embed_raw.get('model_family') or 'nomic-embed-text'),
            'model_version': str(embed_raw.get('model_version') or 'v1.5'),
        }
    elif profile_name in EMBEDDING_PROFILES:
        result['embedding_settings'] = {
            'dimensions': 768,
            'parameters': '137M',
            'architecture': 'nomic-bert',
            'model_family': 'nomic-embed-text',
            'model_version': 'v1.5',
        }
    return result


def list_servers(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = cfg or load_config()
    result: list[dict[str, Any]] = []
    for entry in data.get('servers') or []:
        if not isinstance(entry, dict):
            continue
        normalized = normalize_server(entry)
        if normalized['id']:
            result.append(normalized)
    return result


def normalize_model_libraries(raw: Any, *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from core.model_paths import normalize_model_libraries as _normalize

    return _normalize(raw, cfg=cfg or load_config())
