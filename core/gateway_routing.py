"""Resolve OpenAI gateway model names to Console engine profiles."""

from __future__ import annotations

from typing import Any

from core.config import is_embedding_server, list_runtimes, list_servers

_GENERIC_MODEL_IDS = frozenset({'', 'model', 'default', 'browse'})

# Cursor validates custom model names client-side before any HTTP call. These
# OpenAI-style ids are accepted in Cursor Settings and route to the default
# gateway chat engine (see resolve_chat_server).
CURSOR_COMPAT_MODEL_IDS = frozenset({
    'gpt-4',
    'gpt-4-turbo',
    'gpt-4o',
    'gpt-4o-mini',
})


def is_cursor_compat_model_id(model: str) -> bool:
    return str(model or '').strip().lower() in CURSOR_COMPAT_MODEL_IDS


def default_gateway_chat_server(cfg: dict[str, Any]) -> dict[str, Any]:
    """Default chat engine for empty model or Cursor-compat aliases."""
    servers = enabled_chat_servers(cfg)
    if not servers:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail='no enabled chat engine available')
    wanted = str(cfg.get('gateway_server_id') or '').strip()
    if wanted:
        for server in servers:
            if str(server.get('id') or '') == wanted:
                return server
    return servers[0]


def model_ids_compatible(requested: str, active: str) -> bool:
    """True when a client model name refers to the loaded checkpoint (LM Studio style)."""
    req = normalize_model_token(requested)
    act = normalize_model_token(active)
    if not req or not act:
        return True
    if req == act:
        return True
    return act.startswith(f'{req}-') or req.startswith(f'{act}-')


def normalize_model_token(value: str) -> str:
    """Normalize client model ids for tolerant matching (LM Studio style)."""
    text = str(value or '').strip().lower()
    if not text:
        return ''
    text = text.replace('\\', '/').replace('.', '-').replace('_', '-')
    while '--' in text:
        text = text.replace('--', '-')
    text = text.strip('-')
    if text.endswith('-dflash'):
        text = text[: -len('-dflash')].strip('-')
    return text


def catalog_model_id(server: dict[str, Any]) -> str:
    """Client-facing id advertised on GET /v1/models."""
    model_id = str(server.get('model_id') or '').strip()
    if model_id and model_id.lower() not in _GENERIC_MODEL_IDS:
        return model_id.replace('.', '-').replace('_', '-')
    server_id = str(server.get('id') or '').strip()
    normalized = normalize_model_token(server_id)
    return normalized or server_id


def gateway_public_model_ids(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> list[str]:
    """All OpenAI ``id`` strings to advertise for one engine (canonical first)."""
    primary = catalog_model_id(server)
    ordered: list[str] = [primary]
    seen = {primary.lower()}

    def add(raw: str) -> None:
        token = str(raw or '').strip()
        if not token:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(token)

    model_id = str(server.get('model_id') or '').strip()
    server_id = str(server.get('id') or '').strip()
    advertise_dflash = False
    try:
        from core.model_presets import server_dflash_stack_ready

        advertise_dflash = server_dflash_stack_ready(server, cfg=cfg)
    except Exception:
        advertise_dflash = False

    def is_dflash_id(token: str) -> bool:
        return token.lower().endswith('-dflash')

    if model_id and model_id.replace('.', '-').replace('_', '-').lower() != primary.lower():
        if advertise_dflash or not is_dflash_id(model_id):
            add(model_id)
    if server_id and server_id.lower() != primary.lower():
        if advertise_dflash or not is_dflash_id(server_id):
            add(server_id)
    for alias in sorted(gateway_model_aliases(server, cfg=cfg)):
        if alias.lower() in seen:
            continue
        if normalize_model_token(alias) != normalize_model_token(primary):
            continue
        if is_dflash_id(alias) and not advertise_dflash:
            continue
        add(alias)
    return ordered


def gateway_model_aliases(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> set[str]:
    """All model strings that should route to this engine."""
    aliases: set[str] = set()
    server_id = str(server.get('id') or '').strip()
    model_id = str(server.get('model_id') or '').strip()
    for raw in (server_id, model_id):
        token = str(raw or '').strip()
        if token:
            aliases.add(token)
            normalized = normalize_model_token(token)
            if normalized:
                aliases.add(normalized)
    if server_id.endswith('-dflash'):
        aliases.add(server_id[: -len('-dflash')].strip('-'))
    try:
        from core.model_stack import resolve_model_stack

        for row in resolve_model_stack(server, cfg=cfg):
            alias_id = str(row.get('id') or '').strip()
            if alias_id and str(row.get('role') or '') == 'alias':
                aliases.add(alias_id)
                normalized = normalize_model_token(alias_id)
                if normalized:
                    aliases.add(normalized)
    except ValueError:
        pass
    return {alias for alias in aliases if alias}


def _advertised_engine_server(server: dict[str, Any], *, cfg: dict[str, Any]) -> bool:
    """True when this engine profile should appear on the OpenAI gateway."""
    if is_embedding_server(server):
        return server.get('enabled', True) is not False
    from core.model_presets import server_target_path_on_disk

    return bool(server_target_path_on_disk(server, cfg=cfg))


def enabled_chat_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    servers = [
        s for s in list_servers(cfg)
        if _advertised_engine_server(s, cfg=cfg) and not is_embedding_server(s)
    ]
    known = {str(row.get('id') or '') for row in servers}
    for runtime in list_runtimes(cfg):
        runtime_id = str(runtime.get('runtime_id') or '').strip()
        if runtime.get('enabled', True) is False or runtime_id not in {'vllm', 'transformers', 'freetoken'}:
            continue
        if runtime_id in known:
            continue
        servers.append({
            **runtime,
            'id': runtime_id,
            'runtime_id': runtime_id,
            'model_id': str(runtime.get('default_model') or ''),
        })
    return servers


def resolve_chat_server(cfg: dict[str, Any], model: str) -> dict[str, Any]:
    """Pick the engine profile for an inbound chat ``model`` name."""
    from fastapi import HTTPException

    servers = enabled_chat_servers(cfg)
    if not servers:
        raise HTTPException(status_code=503, detail='no enabled chat engine available')

    requested = str(model or '').strip()
    if not requested:
        return default_gateway_chat_server(cfg)

    if is_cursor_compat_model_id(requested):
        return default_gateway_chat_server(cfg)

    token = normalize_model_token(requested)
    matches: list[dict[str, Any]] = []
    for server in servers:
        alias_tokens = {normalize_model_token(alias) for alias in gateway_model_aliases(server, cfg=cfg)}
        if token in alias_tokens or requested in gateway_model_aliases(server, cfg=cfg):
            matches.append(server)

    if not matches:
        raise HTTPException(
            status_code=404,
            detail={
                'error': {
                    'message': f"The model '{requested}' does not exist or is not enabled.",
                    'type': 'invalid_request_error',
                    'code': 'model_not_found',
                    'param': 'model',
                }
            },
        )
    if len(matches) > 1:
        labels = ', '.join(
            f"{row.get('label') or row.get('id')}"
            for row in matches[:6]
        )
        raise HTTPException(
            status_code=400,
            detail={
                'error': {
                    'message': f"The model '{requested}' is ambiguous. Matches: {labels}",
                    'type': 'invalid_request_error',
                    'code': 'model_ambiguous',
                    'param': 'model',
                }
            },
        )
    return matches[0]


def list_gateway_chat_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s for s in list_servers(cfg)
        if _advertised_engine_server(s, cfg=cfg) and not is_embedding_server(s)
    ]
