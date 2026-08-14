"""Shared catalog load dispatch used by API routes and HF install."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from core.config import (
    get_server,
    is_embedding_server,
    list_servers,
    load_config,
    normalize_inference_settings,
    normalize_load_settings,
    normalize_server,
)
from core.engine_state import note_engine_loaded
from core.local_models import list_local_models
from core.memory_guardrails import assess_load
from core.server_boot import load_server_checkpoint


def execute_catalog_load(
    *,
    path: str | None = None,
    model_id: str | None = None,
    server_id: str | None = None,
    context_size: int | None = None,
    load_settings: dict[str, Any] | None = None,
    inference_settings: dict[str, Any] | None = None,
    loaded_by: str = 'api',
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load a model from the local catalog by path or model_id."""
    config = cfg or load_config()
    catalog = list_local_models(cfg=config)
    models = catalog.get('models') or []
    resolved_path = str(path or '').strip()
    target = None
    if resolved_path:
        target = next(
            (m for m in models if isinstance(m, dict) and str(m.get('path') or '') == resolved_path),
            None,
        )
    if target is None and model_id:
        mid = str(model_id).strip().lower()
        target = next(
            (
                m for m in models
                if isinstance(m, dict) and (
                    str(m.get('model_id') or '').lower() == mid
                    or str(m.get('ollama_model') or '').lower() == mid
                    or str(m.get('id') or '').lower() == mid
                    or str(m.get('label') or '').lower() == mid
                )
            ),
            None,
        )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail='model not found in the local catalog (use path or model_id from GET /api/models)',
        )

    modality = str(target.get('modality') or 'llm')
    runtime_id = str(target.get('runtime_id') or 'llama-server')
    resolved_path = str(target.get('path') or resolved_path)

    from core.ocr_setup import resolve_glmocr_load

    glmocr = resolve_glmocr_load(resolved_path, target, cfg=config)
    if isinstance(glmocr, dict) and glmocr.get('success'):
        resolved_path = str(glmocr.get('path') or resolved_path)
        runtime_id = str(glmocr.get('runtime_id') or runtime_id)
        modality = str(glmocr.get('modality') or modality)
    elif isinstance(glmocr, dict) and glmocr.get('error'):
        raise HTTPException(status_code=400, detail=str(glmocr.get('error')))

    from core.runtimes import get_runtime_adapter

    if runtime_id == 'stt':
        adapter = get_runtime_adapter('stt')
        result = adapter.load({'path': resolved_path})
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'STT load failed')
        return {
            'success': True,
            'modality': modality,
            'runtime_id': 'stt',
            'loaded': True,
            'path': resolved_path,
            'how_to_use': 'POST /api/runtimes/stt/v1/audio/transcriptions (multipart: file=<audio>)',
            **result,
        }
    if runtime_id == 'faster-whisper':
        adapter = get_runtime_adapter('faster-whisper')
        model_payload: dict[str, Any] = {'path': resolved_path}
        if load_settings:
            model_payload['load_settings'] = dict(load_settings)
        result = adapter.load(model_payload)
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'faster-whisper load failed')
        return {
            'success': True,
            'modality': modality,
            'runtime_id': 'faster-whisper',
            'loaded': True,
            'path': resolved_path,
            'how_to_use': 'POST /api/runtimes/faster-whisper/v1/audio/transcriptions (multipart: file=<audio>)',
            **result,
        }
    if runtime_id == 'piper':
        adapter = get_runtime_adapter('piper')
        result = adapter.load({'path': resolved_path})
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'TTS load failed')
        return {
            'success': True,
            'modality': modality,
            'runtime_id': 'piper',
            'loaded': True,
            'path': resolved_path,
            'how_to_use': 'POST /api/runtimes/piper/v1/audio/speech {"input": "...", "voice": "en_US-lessac-medium"}',
            **result,
        }
    if runtime_id == 'vibevoice':
        adapter = get_runtime_adapter('vibevoice')
        model_payload = {'path': resolved_path}
        if load_settings:
            model_payload['load_settings'] = dict(load_settings)
        result = adapter.load(model_payload)
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'VibeVoice load failed')
        return {
            'success': True,
            'modality': modality,
            'runtime_id': 'vibevoice',
            'loaded': True,
            'path': resolved_path,
            'how_to_use': 'POST /api/runtimes/vibevoice/v1/audio/speech {"input": "...", "voice": "en-Carter_man"}',
            **result,
        }
    if runtime_id == 'transformers':
        adapter = get_runtime_adapter('transformers')
        model_payload = {'path': resolved_path}
        if load_settings:
            model_payload['load_settings'] = dict(load_settings)
        result = adapter.load(model_payload)
        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error') or 'Transformers load failed')
        return {
            'success': True,
            'modality': modality,
            'runtime_id': 'transformers',
            'loaded': True,
            'path': resolved_path,
            'how_to_use': 'POST /api/runtimes/transformers/v1/chat/completions',
            **result,
        }

    server = None
    if server_id:
        server = get_server(config, server_id)
        if server is None:
            raise HTTPException(status_code=404, detail=f'unknown server_id: {server_id}')
    else:
        candidates = [s for s in list_servers(config) if s.get('enabled', True)]
        if modality == 'embedding':
            server = next((s for s in candidates if is_embedding_server(s)), None)
        else:
            server = next((s for s in candidates if not is_embedding_server(s)), None)
    if server is None:
        raise HTTPException(status_code=409, detail=f'no enabled server can run a {modality} model — pass server_id')

    server = normalize_server(dict(server))
    candidate = {**server, 'adhoc_model_path': resolved_path}
    if context_size is not None:
        candidate['context_size'] = int(context_size)
    if load_settings:
        candidate['load_settings'] = normalize_load_settings(load_settings)
    if inference_settings:
        candidate['inference_settings'] = normalize_inference_settings(inference_settings)

    check = assess_load(candidate, cfg=config)
    if check.get('level') == 'block':
        raise HTTPException(status_code=400, detail=str(check.get('message') or 'insufficient VRAM'))
    result = load_server_checkpoint(server, cfg=config, model_path=resolved_path, model_id=model_id)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error') or 'load failed')
    note_engine_loaded(str(server.get('id') or ''), loaded_by=loaded_by)
    if modality == 'embedding':
        how_to_use = f'POST /api/servers/{server["id"]}/v1/embeddings {{"input": ["text", ...]}}'
    else:
        how_to_use = f'POST /api/servers/{server["id"]}/v1/chat/completions'
    return {
        'success': True,
        'modality': modality,
        'runtime_id': 'llama-server',
        'server_id': str(server.get('id') or ''),
        'loaded': True,
        'path': resolved_path,
        'how_to_use': how_to_use,
        **result,
    }
