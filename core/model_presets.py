"""Generate llama-server router preset INI files for DFlash Console servers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import get_dflash_root, normalize_load_settings
from core.gpu_devices import resolve_role_gpu_launch_params
from core.model_stack import resolve_model_stack

ROOT = Path(__file__).resolve().parent.parent
PRESET_DIR = ROOT / 'logs' / 'presets'

PROFILE_CACHE_TYPES = {
    'gemma-chat': ('q4_0', 'q4_0'),
    'gemma-ar': ('q4_0', 'q4_0'),
    'gemma-12-ar': ('q4_0', 'q4_0'),
    'gemma-12-dflash': ('q4_0', 'q4_0'),
    'qwen-dflash': ('q8_0', 'q8_0'),
    'qwen-ar': ('q8_0', 'q8_0'),
    'generic-ar': ('q4_0', 'q4_0'),
}


def infer_profile_from_path(path: str | Path) -> str:
    name = Path(path).name.lower()
    if 'qwen' in name or 'deepseek' in name:
        return 'qwen-ar'
    if 'bonsai' in name:
        return 'bonsai'
    if 'gemma' in name:
        return 'gemma-ar'
    return 'generic-ar'


def model_id_from_path(path: str | Path) -> str:
    stem = Path(path).stem
    return stem.replace('_', '-').lower()[:80]


def preset_path_for(server_id: str) -> Path:
    return PRESET_DIR / f'{server_id}.ini'


def write_server_preset(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    target_path: str | None = None,
    model_id: str | None = None,
    profile: str | None = None,
    use_draft: bool | None = None,
) -> Path:
    server_id = str(server.get('id') or '').strip()
    preset_model_id = str(model_id or server.get('model_id') or '').strip()
    preset_profile = str(profile or server.get('profile') or 'gemma-chat').strip()
    if not server_id or not preset_model_id:
        raise ValueError('server id and model_id required')

    load = normalize_load_settings(server.get('load_settings'))
    launch = resolve_role_gpu_launch_params(
        server.get('gpu_device'),
        model_id=preset_model_id,
        hardware=(cfg or {}).get('hardware_settings'),
    )
    cache_k, cache_v = PROFILE_CACHE_TYPES.get(preset_profile, ('q4_0', 'q4_0'))

    target_path_resolved = str(target_path or '').strip()
    if target_path_resolved:
        target = {
            'role': 'target',
            'label': Path(target_path_resolved).name,
            'path': target_path_resolved,
        }
        draft = None
    else:
        stack = resolve_model_stack(server, cfg=cfg)
        target = next((row for row in stack if row.get('role') == 'target'), None)
        draft = next((row for row in stack if str(row.get('role') or '').startswith('draft')), None)

    if not target or not target.get('path'):
        raise ValueError(f'target model path missing for profile {preset_profile}')

    if use_draft is False:
        draft = None

    lines = [
        'version = 1',
        '',
        '[*]',
        f"c = {int(server.get('context_size') or 8192)}",
        f"n-gpu-layers = {int(load.get('gpu_layers') or 99)}",
        f"t = {int(load.get('cpu_threads') or 9)}",
        f"b = {int(load.get('eval_batch_size') or 2048)}",
        f"ub = {int(load.get('physical_batch_size') or 512)}",
        f"fa = {'on' if load.get('flash_attention', True) else 'off'}",
        'jinja = true',
        'mlock = true',
        f"main-gpu = {int(launch.get('main_gpu') or 0)}",
        f"split-mode = {launch.get('split_mode') or 'none'}",
        f"cache-type-k = {cache_k}",
        f"cache-type-v = {cache_v}",
        'np = 1',
        '',
        f'[{preset_model_id}]',
        f"model = {target['path']}",
        'load-on-startup = false',
    ]

    if draft and draft.get('path'):
        lines.append(f"model-draft = {draft['path']}")
        if preset_profile in ('gemma-chat', 'qwen-dflash', 'gemma-12-dflash'):
            lines.extend(['spec-type = draft-dflash', 'spec-draft-n-max = 8'])
        elif preset_profile == 'bonsai-spec':
            lines.extend(['spec-type = draft-dspark', 'spec-draft-n-max = 4', 'ngld = 999'])

    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    path = preset_path_for(server_id)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def gpu_layers_max_for(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> int:
    profile = str(server.get('profile') or '')
    if '31' in profile or '31B' in str(server.get('label') or ''):
        return 128
    if '12' in profile or '27' in profile:
        return 96
    return 128
