"""Normalize OpenAI multimodal chat bodies and prepare llama-server vision engines."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from core.config import get_server, load_config, normalize_server
from core.model_presets import profile_requires_draft, preset_path_for, write_server_preset
from core.runtime import tcp_port_open
from core.vision_setup import resolve_mmproj_path, wire_vision

_DATA_URL_RE = re.compile(
    r'^data:(?P<mime>image/[\w.+-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+)$',
    re.I,
)


def chat_messages_contain_images(body: dict[str, Any]) -> bool:
    messages = body.get('messages')
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get('type') or '').strip().lower()
            if part_type in {'image_url', 'image'}:
                return True
    return False


def _normalize_image_url(url: str) -> str:
    token = str(url or '').strip()
    if not token:
        return ''
    if token.startswith(('http://', 'https://')):
        return token
    if _DATA_URL_RE.match(token):
        match = _DATA_URL_RE.match(token)
        assert match is not None
        mime = match.group('mime')
        data = re.sub(r'\s+', '', match.group('data'))
        return f'data:{mime};base64,{data}'
    compact = re.sub(r'\s+', '', token)
    if re.fullmatch(r'[A-Za-z0-9+/=]+', compact):
        try:
            base64.b64decode(compact, validate=True)
        except Exception:
            return token
        return f'data:image/png;base64,{compact}'
    return token


def _normalize_content_part(part: dict[str, Any]) -> dict[str, Any]:
    part_type = str(part.get('type') or '').strip().lower()
    if part_type == 'image_url':
        image_url = part.get('image_url')
        if isinstance(image_url, dict):
            url = _normalize_image_url(str(image_url.get('url') or ''))
            detail = image_url.get('detail')
            normalized: dict[str, Any] = {'type': 'image_url', 'image_url': {'url': url}}
            if detail is not None:
                normalized['image_url']['detail'] = detail
            return normalized
        if isinstance(image_url, str):
            return {'type': 'image_url', 'image_url': {'url': _normalize_image_url(image_url)}}
    if part_type == 'image':
        raw = part.get('image') or part.get('data') or part.get('url')
        if isinstance(raw, str):
            return {'type': 'image_url', 'image_url': {'url': _normalize_image_url(raw)}}
    if part_type == 'text':
        text = part.get('text')
        if isinstance(text, str):
            return {'type': 'text', 'text': text}
    return dict(part)


def normalize_multimodal_chat_body(body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return a copy of the chat body with normalized image_url parts."""
    if not isinstance(body, dict):
        return {}, False
    messages = body.get('messages')
    if not isinstance(messages, list):
        return dict(body), False
    changed = False
    normalized_messages: list[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            normalized_messages.append(msg)
            continue
        content = msg.get('content')
        if not isinstance(content, list):
            normalized_messages.append(msg)
            continue
        parts = [_normalize_content_part(part) for part in content if isinstance(part, dict)]
        if parts != content:
            changed = True
        normalized_messages.append({**msg, 'content': parts})
    if not changed:
        return dict(body), False
    return {**body, 'messages': normalized_messages}, True


def vision_capability(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.vision_setup import resolve_mmproj_path, server_supports_vision_chat

    ready = server_supports_vision_chat(server, cfg=cfg)
    mmproj = str(resolve_mmproj_path(server, cfg=cfg) or '').strip() if ready else ''
    path = Path(mmproj).expanduser() if mmproj else None
    resolved = str(path.resolve()) if ready and path is not None and path.is_file() else ''
    return {
        'supports_vision': ready,
        'imageInput': ready,
        'mmproj_path': resolved,
    }


def _preset_has_mmproj(server_id: str) -> bool:
    path = preset_path_for(server_id)
    if not path.is_file():
        return False
    try:
        return 'mmproj =' in path.read_text(encoding='utf-8')
    except OSError:
        return False


def _preset_has_draft(server_id: str) -> bool:
    path = preset_path_for(server_id)
    if not path.is_file():
        return False
    try:
        return 'model-draft' in path.read_text(encoding='utf-8')
    except OSError:
        return False


def _model_entry_has_draft(entry: dict[str, Any]) -> bool:
    status = entry.get('status') if isinstance(entry.get('status'), dict) else {}
    args = status.get('args') if isinstance(status, dict) else None
    if isinstance(args, list):
        joined = ' '.join(str(part) for part in args).lower()
        if '--model-draft' in joined or ' -model-draft ' in f' {joined} ':
            return True
    preset = str((status or {}).get('preset') or '')
    lowered = preset.lower()
    return 'model-draft' in lowered or 'model-draft =' in lowered


def _model_entry_has_mmproj(entry: dict[str, Any]) -> bool:
    status = entry.get('status') if isinstance(entry.get('status'), dict) else {}
    args = status.get('args') if isinstance(status, dict) else None
    if isinstance(args, list):
        joined = ' '.join(str(part) for part in args).lower()
        if '--mmproj' in joined or ' -mmproj ' in f' {joined} ':
            return True
    preset = str((status or {}).get('preset') or '')
    lowered = preset.lower()
    if 'mmproj =' in lowered or 'mmproj=' in lowered:
        return True
    return False


def live_loaded_has_mmproj(server: dict[str, Any]) -> bool | None:
    """Whether the currently loaded worker was launched with a projector.

    Returns None when the engine is unreachable or the checkpoint is unloaded.
    Architecture.input_modalities is not enough — llama.cpp reports image
    support from GGUF metadata even when ``--mmproj`` was never passed.
    """
    from core.runtime import _fetch_models_payload, _model_state

    api_url = str(server.get('api_url') or '').strip()
    if not api_url:
        return None
    entries = _fetch_models_payload(api_url)
    if not entries:
        return None
    loaded = [
        entry
        for entry in entries
        if _model_state(entry) in {'loaded', 'running'}
    ]
    if not loaded:
        return None
    return any(_model_entry_has_mmproj(entry) for entry in loaded)


def router_registration_flags(api_url: str, load_id: str) -> dict[str, Any]:
    """Return mmproj/draft flags for a router-registered checkpoint id."""
    from core.gateway_routing import normalize_model_token
    from core.runtime import _fetch_models_payload

    wanted = normalize_model_token(load_id)
    for entry in _fetch_models_payload(api_url):
        entry_id = str(entry.get('id') or '').strip()
        if normalize_model_token(entry_id) != wanted and entry_id != load_id:
            continue
        return {
            'registered': True,
            'mmproj': _model_entry_has_mmproj(entry),
            'draft': _model_entry_has_draft(entry),
        }
    return {'registered': False, 'mmproj': None, 'draft': None}


def router_registration_stale(
    server: dict[str, Any],
    *,
    load_id: str,
    server_id: str | None = None,
) -> bool:
    """True when the live router registered a model with different mmproj/draft than the preset."""
    sid = str(server_id or server.get('id') or '').strip()
    api_url = str(server.get('api_url') or '').strip()
    if not sid or not api_url or not load_id:
        return False
    flags = router_registration_flags(api_url, load_id)
    if not flags.get('registered'):
        return False
    preset_mmproj = _preset_has_mmproj(sid)
    preset_draft = _preset_has_draft(sid)
    reg_mmproj = flags.get('mmproj')
    reg_draft = flags.get('draft')
    if reg_mmproj is not None and bool(reg_mmproj) != preset_mmproj:
        return True
    if reg_draft is not None and bool(reg_draft) != preset_draft:
        return True
    return False


def ensure_vision_ready_for_chat(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Wire a local mmproj, refresh the router preset, and reload if vision was missing."""
    config = cfg or load_config()
    entry = normalize_server(server)
    server_id = str(entry.get('id') or '').strip()
    cap = vision_capability(entry, cfg=config)
    if not cap['supports_vision']:
        return {
            'success': False,
            'reason': 'no_mmproj',
            'error': (
                f'The DFlash Console engine for {entry.get("label") or server_id} '
                'does not have a vision projector (mmproj). Place an mmproj GGUF next to '
                'the model file or add vision support from the Models tab.'
            ),
            **cap,
        }

    target_path = str(entry.get('target_path') or '').strip()
    if profile_requires_draft(entry.get('profile')):
        draft_path = str(entry.get('draft_path') or '').strip()
        if not draft_path or not Path(draft_path).expanduser().is_file():
            repair_action = 'attach_draft' if target_path else 'choose_target'
            message = (
                'This DFlash profile requires a matching draft accelerator before '
                'vision chat can start.'
            )
            return {
                'success': False,
                'reason': 'dflash_repair_required',
                'reason_code': 'draft-required' if not draft_path else 'missing-draft',
                'error': message,
                'message': message,
                'target_path': target_path,
                'draft_path': draft_path,
                'repair': {
                    'action': repair_action,
                    'server_id': server_id,
                    'target_path': target_path,
                    'current_draft_path': '',
                },
                **cap,
            }
    mmproj_path = cap['mmproj_path']
    explicit = str(entry.get('mmproj_path') or '').strip()
    if not explicit or Path(explicit).expanduser().resolve() != Path(mmproj_path):
        wired = wire_vision(
            model_path=target_path,
            mmproj_path=mmproj_path,
            server_id=server_id or None,
            cfg=config,
        )
        if not wired.get('success'):
            return {
                'success': False,
                'reason': 'wire_failed',
                'error': str(wired.get('error') or 'could not wire vision projector'),
                **cap,
            }
        config = load_config()
        entry = normalize_server(get_server(config, server_id) or entry)

    had_mmproj = _preset_has_mmproj(server_id)
    write_server_preset(entry, cfg=config)
    live_mmproj = live_loaded_has_mmproj(entry)
    needs_reload = (not had_mmproj) or (live_mmproj is False)

    host = str(entry.get('host') or '127.0.0.1')
    port = int(entry.get('port') or 0)
    api_url = str(entry.get('api_url') or '')
    if needs_reload and port > 0 and tcp_port_open(host, port):
        from core.runtime import stop_server
        from core.server_boot import listener_is_managed_engine, load_server_checkpoint

        if listener_is_managed_engine(host, port):
            stop_server(port=port, host=host, api_url=api_url or None)
            reload = load_server_checkpoint(entry, cfg=config)
            if not reload.get('success'):
                return {
                    'success': False,
                    'reason': 'reload_failed',
                    'error': str(reload.get('error') or 'could not reload engine with vision projector'),
                    **cap,
                }
            return {'success': True, 'reloaded': True, **cap}

    return {'success': True, 'reloaded': False, **cap}
