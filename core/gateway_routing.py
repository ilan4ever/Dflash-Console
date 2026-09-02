"""Resolve OpenAI gateway model names to Console engine profiles."""

from __future__ import annotations

from typing import Any

from core.config import is_embedding_server, list_runtimes, list_servers

_GENERIC_MODEL_IDS = frozenset({'', 'model', 'default', 'browse'})


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


def enabled_chat_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    servers = [s for s in list_servers(cfg) if s.get('enabled', True) and not is_embedding_server(s)]
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
        wanted = str(cfg.get('gateway_server_id') or '').strip()
        if wanted:
            for server in servers:
                if str(server.get('id') or '') == wanted:
                    return server
        return servers[0]

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
    return [s for s in list_servers(cfg) if s.get('enabled', True) and not is_embedding_server(s)]
