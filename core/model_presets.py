"""Generate llama-server router preset INI files for DFlash Console servers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import (
    get_dflash_root,
    normalize_hardware_settings,
    normalize_load_settings,
)
from core.dflash_generation import infer_dflash_generation, spec_draft_n_max
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
    'translategemma': ('q4_0', 'q4_0'),
}


def profile_uses_jinja(profile: str | None) -> bool:
    """TranslateGemma uses a structured template; llama-server jinja breaks on normal chat."""
    return str(profile or '').strip().lower() != 'translategemma'


def profile_requires_draft(profile: str | None) -> bool:
    """Return whether a profile is required to run speculative decoding."""
    name = str(profile or '').strip().lower()
    return name in {'gemma-chat', 'gemma-12-dflash', 'qwen-dflash', 'bonsai-spec'} or (
        'dflash' in name or 'dspark' in name
    )


def server_draft_path_on_disk(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> str:
    """Return a draft GGUF path when the configured stack has one on disk."""
    draft_path = str(server.get('draft_path') or '').strip()
    if draft_path and Path(draft_path).expanduser().is_file():
        return str(Path(draft_path).expanduser().resolve())
    if not profile_requires_draft(server.get('profile')):
        return ''
    try:
        stack = resolve_model_stack(server, cfg=cfg)
    except (OSError, ValueError):
        stack = []
    draft = next(
        (row for row in stack if str(row.get('role') or '').startswith('draft')),
        {},
    )
    path = str(draft.get('path') or '').strip()
    if path and Path(path).expanduser().is_file():
        return str(Path(path).expanduser().resolve())
    return ''


DEFAULT_MODEL_CONTEXT_MAX = 131072


def context_max_for_gguf_path(path: str | Path) -> int:
    """Maximum context tokens the GGUF model reports (or a conservative identity guess)."""
    from core.gguf_meta import read_gguf_context_length

    reported = read_gguf_context_length(path)
    if reported and reported >= 2048:
        return int(reported)
    return DEFAULT_MODEL_CONTEXT_MAX


def context_max_for_server(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> int:
    """Context ceiling for this engine's on-disk target model."""
    target = server_target_path_on_disk(server, cfg=cfg)
    if target:
        return context_max_for_gguf_path(target)
    path = str(server.get('path') or server.get('adhoc_model_path') or '').strip()
    if path and Path(path).expanduser().is_file():
        return context_max_for_gguf_path(path)
    return DEFAULT_MODEL_CONTEXT_MAX


def server_target_path_on_disk(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> str:
    """Return the target GGUF path when it exists on disk."""
    target_path = str(server.get('target_path') or '').strip()
    if target_path and Path(target_path).expanduser().is_file():
        return str(Path(target_path).expanduser().resolve())
    try:
        stack = resolve_model_stack(server, cfg=cfg)
    except (OSError, ValueError):
        stack = []
    target = next((row for row in stack if row.get('role') == 'target'), {})
    path = str(target.get('path') or '').strip()
    if path and Path(path).expanduser().is_file():
        return str(Path(path).expanduser().resolve())
    return ''


def _dflash_pair_preflight(
    target: str,
    draft: str,
) -> dict[str, Any] | None:
    if not target or not draft:
        return None
    from core.stack_match import preflight_dflash_pair

    return preflight_dflash_pair(Path(target), Path(draft))


def server_dflash_draft_usable(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> bool:
    """True when a draft file can be attached without blocking load (including unverified fixtures)."""
    if not profile_requires_draft(server.get('profile')):
        return False
    target = server_target_path_on_disk(server, cfg=cfg)
    draft = server_draft_path_on_disk(server, cfg=cfg)
    preflight = _dflash_pair_preflight(target, draft)
    return bool(preflight and preflight.get('compatible'))


def server_dflash_stack_ready(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> bool:
    """True only when a compatible, metadata-validated DFlash draft is on disk."""
    if not profile_requires_draft(server.get('profile')):
        return False
    target = server_target_path_on_disk(server, cfg=cfg)
    draft = server_draft_path_on_disk(server, cfg=cfg)
    preflight = _dflash_pair_preflight(target, draft)
    return bool(preflight and preflight.get('compatible') and preflight.get('validated'))


def ar_fallback_profile(profile: str | None) -> str:
    lowered = str(profile or '').strip().lower()
    if lowered == 'qwen-dflash':
        return 'qwen-ar'
    if lowered == 'gemma-12-dflash':
        return 'gemma-12-ar'
    if lowered == 'gemma-chat':
        return 'gemma-ar'
    if lowered == 'bonsai-spec':
        return 'bonsai'
    if profile_requires_draft(lowered):
        return 'generic-ar'
    return str(profile or 'generic-ar').strip() or 'generic-ar'


def effective_server_profile(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> str:
    """Profile used for load/preset when a DFlash draft cannot load — fall back to AR."""
    profile = str(server.get('profile') or infer_profile_from_path(server.get('target_path') or '')).strip()
    if not profile_requires_draft(profile):
        return profile
    if server_dflash_draft_usable(server, cfg=cfg):
        return profile
    return ar_fallback_profile(profile)


def _kv_offload_enabled(server: dict[str, Any], hardware: dict[str, Any]) -> bool:
    raw = server.get('load_settings')
    if isinstance(raw, dict) and 'kv_offload' in raw:
        return raw.get('kv_offload') is not False
    load = normalize_load_settings(raw)
    if 'kv_offload' in load:
        return bool(load.get('kv_offload'))
    return hardware.get('offload_kv_cache_to_gpu') is not False


def infer_profile_from_path(path: str | Path) -> str:
    name = Path(path).name.lower()
    if 'translategemma' in name:
        return 'translategemma'
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


def sanitize_preset_model_id(model_id: str, path: str | Path | None = None) -> str:
    """Strip catalog aliases so llama.ini sections stay valid load ids."""
    token = str(model_id or '').strip()
    if token.lower().startswith('library-file:'):
        token = token.split(':', 1)[1].strip()
        if path and (len(token) < 4 or token.lower() in {'it', 'id'}):
            return model_id_from_path(path)
    if token and ':' not in token and '/' not in token and '\\' not in token:
        return token
    if path:
        return model_id_from_path(path)
    return model_id_from_path(token) if token else ''


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
    preset_model_id = sanitize_preset_model_id(
        model_id or server.get('model_id'),
        target_path or server.get('target_path') or server.get('adhoc_model_path'),
    )
    preset_profile = str(profile or effective_server_profile(server, cfg=cfg) or 'gemma-chat').strip()
    if not server_id or not preset_model_id:
        raise ValueError('server id and model_id required')

    load = normalize_load_settings(server.get('load_settings'))
    required_gb = None
    if cfg:
        try:
            from core.memory_guardrails import _estimate_load_gb

            required_gb = _estimate_load_gb(server, cfg)
        except Exception:
            required_gb = None
    launch = resolve_role_gpu_launch_params(
        server.get('gpu_device'),
        model_id=preset_model_id,
        hardware=(cfg or {}).get('hardware_settings'),
        context_size=server.get('context_size'),
        required_gb=required_gb,
    )
    cache_k, cache_v = PROFILE_CACHE_TYPES.get(preset_profile, ('q4_0', 'q4_0'))
    hardware = normalize_hardware_settings((cfg or {}).get('hardware_settings'))

    target_path_resolved = str(target_path or server.get('target_path') or '').strip()
    draft_path_resolved = str(server.get('draft_path') or '').strip()
    if target_path_resolved and not Path(target_path_resolved).expanduser().is_file():
        target_path_resolved = ''
    if draft_path_resolved and not Path(draft_path_resolved).expanduser().is_file():
        draft_path_resolved = ''
    if not profile_requires_draft(preset_profile):
        draft_path_resolved = ''
    if target_path_resolved:
        target = {
            'role': 'target',
            'label': Path(target_path_resolved).name,
            'path': target_path_resolved,
        }
        draft = None
        if use_draft is not False and draft_path_resolved:
            draft_file = Path(draft_path_resolved).expanduser()
            if draft_file.is_file():
                draft = {
                    'role': 'draft-dflash',
                    'label': draft_file.name,
                    'path': str(draft_file),
                }
        if use_draft is not False and profile_requires_draft(preset_profile) and not draft:
            stack = resolve_model_stack({**server, 'target_path': '', 'draft_path': ''}, cfg=cfg)
            draft_row = next(
                (row for row in stack if str(row.get('role') or '').startswith('draft')),
                None,
            )
            draft_path = str(draft_row.get('path') or '').strip() if draft_row else ''
            if draft_path and Path(draft_path).expanduser().is_file():
                draft = {
                    'role': str(draft_row.get('role') or 'draft-dflash'),
                    'label': str(draft_row.get('label') or Path(draft_path).name),
                    'path': draft_path,
                }
    else:
        stack = resolve_model_stack(server, cfg=cfg)
        target = next((row for row in stack if row.get('role') == 'target'), None)
        draft = next((row for row in stack if str(row.get('role') or '').startswith('draft')), None)

    if not target or not target.get('path'):
        raise ValueError(f'target model path missing for profile {preset_profile}')

    configured_profile = str(server.get('profile') or preset_profile).strip()
    if draft and str(draft.get('path') or '').strip():
        pair = _dflash_pair_preflight(str(target.get('path') or ''), str(draft.get('path') or ''))
        if not pair or not pair.get('compatible'):
            draft = None

    if use_draft is False:
        if profile_requires_draft(preset_profile) and server_dflash_draft_usable(server, cfg=cfg):
            raise ValueError(
                f'DFlash profile {configured_profile} cannot disable its required draft accelerator.'
            )
        draft = None

    if not draft and profile_requires_draft(preset_profile):
        preset_profile = ar_fallback_profile(configured_profile)
        cache_k, cache_v = PROFILE_CACHE_TYPES.get(preset_profile, ('q4_0', 'q4_0'))

    if profile_requires_draft(preset_profile):
        if not draft or not str(draft.get('path') or '').strip():
            raise ValueError(
                f'DFlash profile {configured_profile} requires a target model and a draft accelerator.'
            )
        if not Path(str(draft['path'])).expanduser().is_file():
            raise ValueError(
                f'DFlash draft file not found: {draft["path"]}'
            )

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
        f"jinja = {'true' if profile_uses_jinja(preset_profile) else 'false'}",
        'mlock = true',
        f"main-gpu = {int(launch.get('main_gpu') or 0)}",
        f"split-mode = {launch.get('split_mode') or 'none'}",
        *(
            [f"tensor-split = {launch['tensor_split']}"]
            if launch.get('split_mode') != 'none' and launch.get('tensor_split')
            else []
        ),
        f"kv-offload = {'true' if _kv_offload_enabled(server, hardware) else 'false'}",
        f"cache-type-k = {cache_k}",
        f"cache-type-v = {cache_v}",
        f"np = {int(load.get('parallel_slots') or 4)}",
        '',
        f'[{preset_model_id}]',
        f"model = {target['path']}",
        'load-on-startup = false',
    ]
    if not profile_uses_jinja(preset_profile):
        lines.append('jinja = false')

    if draft and draft.get('path'):
        lines.append(f"model-draft = {draft['path']}")
        if preset_profile in ('gemma-chat', 'qwen-dflash', 'gemma-12-dflash'):
            draft_n_max = spec_draft_n_max(draft_path=draft['path'], profile=preset_profile)
            lines.extend([
                'spec-type = draft-dflash',
                f'spec-draft-n-max = {draft_n_max}',
                'ngld = all',
            ])
        elif preset_profile == 'bonsai-spec':
            lines.extend(['spec-type = draft-dspark', 'spec-draft-n-max = 4', 'ngld = all'])

    from core.vision_setup import resolve_mmproj_path, server_supports_vision_chat

    mmproj_path = str(server.get('mmproj_path') or '').strip()
    if not mmproj_path:
        if server_supports_vision_chat(server, cfg=cfg):
            mmproj_path = resolve_mmproj_path(server, cfg=cfg)
    elif not server_supports_vision_chat(server, cfg=cfg):
        mmproj_path = ''
    if mmproj_path and Path(mmproj_path).is_file():
        lines.append(f"mmproj = {mmproj_path}")

    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    path = preset_path_for(server_id)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def clamp_server_context_fields(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clamp saved context fields to the target model's reported maximum."""
    cap = context_max_for_server(server, cfg=cfg)
    out = dict(server)
    ctx_max = max(2048, int(out.get('context_max') or cap))
    out['context_max'] = min(ctx_max, cap)
    ctx_size = max(2048, int(out.get('context_size') or 8192))
    out['context_size'] = min(ctx_size, out['context_max'])
    out['model_context_max'] = cap
    return out


def gpu_layers_max_for(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> int:
    profile = str(server.get('profile') or '')
    if '31' in profile or '31B' in str(server.get('label') or ''):
        return 128
    if '12' in profile or '27' in profile:
        return 96
    return 128
