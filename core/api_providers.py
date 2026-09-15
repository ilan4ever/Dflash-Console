"""External OpenAI-compatible cloud API providers.

Config shape (``config.json`` -> ``api_providers``)::

    [
      {
        "id": "deepseek",
        "label": "DeepSeek",
        "enabled": true,
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "models": [
          {"id": "deepseek-flash", "active": true, "label": "DeepSeek-V4.1-Flash"},
          {"id": "deepseek-v4-pro", "active": false, "label": "DeepSeek-V4-Pro"}
        ],
        "available_models": ["deepseek-flash", "deepseek-v4-pro"]
      }
    ]

Backward compatibility: ``models`` as a list of strings is treated as all-active
and migrated to object form on load/save.

API keys may also be supplied via environment (e.g. ``DEEPSEEK_API_KEY``), which
overrides a stored key when present. Raw keys are never logged or returned by
public APIs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Official OpenAI-compatible chat models (DeepSeek API docs).
DEEPSEEK_DEFAULT_MODELS: tuple[str, ...] = (
    'deepseek-flash',
    'deepseek-v4-pro',
)

# Friendly display names for known DeepSeek API model ids (docs).
# Used when a model entry has no custom ``label``.
DEEPSEEK_MODEL_LABELS: dict[str, str] = {
    'deepseek-flash': 'DeepSeek-V4.1-Flash',
    'deepseek-v4-pro': 'DeepSeek-V4-Pro',
}

DEEPSEEK_DEFAULT_BASE_URL = 'https://api.deepseek.com'

PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    'deepseek': {
        'id': 'deepseek',
        'label': 'DeepSeek',
        'default_base_url': DEEPSEEK_DEFAULT_BASE_URL,
        'default_models': list(DEEPSEEK_DEFAULT_MODELS),
        'env_api_key': 'DEEPSEEK_API_KEY',
        'docs_url': 'https://api-docs.deepseek.com/',
    },
    'openai': {
        'id': 'openai',
        'label': 'OpenAI',
        'default_base_url': 'https://api.openai.com/v1',
        'default_models': [],
        'env_api_key': 'OPENAI_API_KEY',
        'docs_url': 'https://platform.openai.com/docs/api-reference',
    },
    'groq': {
        'id': 'groq',
        'label': 'Groq',
        'default_base_url': 'https://api.groq.com/openai/v1',
        'default_models': [],
        'env_api_key': 'GROQ_API_KEY',
        'docs_url': 'https://console.groq.com/docs/overview',
    },
    'openrouter': {
        'id': 'openrouter',
        'label': 'OpenRouter',
        'default_base_url': 'https://openrouter.ai/api/v1',
        'default_models': [],
        'env_api_key': 'OPENROUTER_API_KEY',
        'docs_url': 'https://openrouter.ai/docs',
    },
    'together': {
        'id': 'together',
        'label': 'Together AI',
        'default_base_url': 'https://api.together.xyz/v1',
        'default_models': [],
        'env_api_key': 'TOGETHER_API_KEY',
        'docs_url': 'https://docs.together.ai/docs/inference-rest',
    },
    'fireworks': {
        'id': 'fireworks',
        'label': 'Fireworks',
        'default_base_url': 'https://api.fireworks.ai/inference/v1',
        'default_models': [],
        'env_api_key': 'FIREWORKS_API_KEY',
        'docs_url': 'https://docs.fireworks.ai/api-reference/post-chatcompletions',
    },
    'mistral': {
        'id': 'mistral',
        'label': 'Mistral',
        'default_base_url': 'https://api.mistral.ai/v1',
        'default_models': [],
        'env_api_key': 'MISTRAL_API_KEY',
        'docs_url': 'https://docs.mistral.ai/api/',
    },
}

_MASK_PLACEHOLDER = '********'


def known_provider_ids() -> frozenset[str]:
    return frozenset(PROVIDER_CATALOG.keys())


def list_provider_catalog(*, configured_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Public catalog presets for the Add Provider wizard (no secrets)."""
    configured = {str(x or '').strip().lower() for x in (configured_ids or set())}
    rows: list[dict[str, Any]] = []
    for provider_id, meta in PROVIDER_CATALOG.items():
        rows.append({
            'id': provider_id,
            'label': str(meta.get('label') or provider_id),
            'default_base_url': str(meta.get('default_base_url') or ''),
            'default_models': list(meta.get('default_models') or []),
            'env_api_key': str(meta.get('env_api_key') or ''),
            'docs_url': str(meta.get('docs_url') or ''),
            'already_configured': provider_id in configured,
            'custom': False,
        })
    return rows


def _slug_provider_id(value: Any) -> str:
    text = str(value or '').strip().lower().replace('_', '-')
    return ''.join(ch for ch in text if ch.isalnum() or ch == '-')[:64]


def _model_id_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get('id') or item.get('model') or item.get('model_id') or '').strip()
    return str(item or '').strip()


def default_model_label(model_id: str) -> str:
    """Default friendly name for an API model id (DeepSeek map, else the id)."""
    token = str(model_id or '').strip()
    if not token:
        return ''
    mapped = DEEPSEEK_MODEL_LABELS.get(token.lower())
    return mapped or token


def resolve_model_label(item: Any) -> str:
    """Friendly label for a model entry; empty/null label uses defaults."""
    if isinstance(item, dict):
        token = _model_id_of(item)
        raw = item.get('label')
        if raw is not None and str(raw).strip():
            return str(raw).strip()
        return default_model_label(token)
    return default_model_label(str(item or '').strip())


def normalize_model_entries(
    raw: Any,
    *,
    fallback: list[str] | None = None,
    default_active: bool | None = None,
    previous_active: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize ``models`` to ``[{id, active, label?}, ...]``.

    - String lists (legacy) become active=True for each id.
    - Object lists keep ``active`` when present.
    - When ``previous_active`` is provided (e.g. after fetch), ids in that set
      stay active; others follow ``default_active`` (False if omitted).
    - ``label`` is the user-editable friendly name. Missing labels are migrated
      from the DeepSeek default map (or the API id). Explicit empty/null means
      "use default" and is omitted from the stored entry.
    """
    prev = {str(x).strip().lower() for x in (previous_active or set()) if str(x).strip()}
    force_default = default_active is not None or previous_active is not None

    items: list[Any]
    if isinstance(raw, list) and raw:
        items = raw
    elif fallback:
        items = list(fallback)
    else:
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        token = _model_id_of(item)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        if isinstance(item, dict) and 'active' in item and not force_default:
            active = item.get('active') is True
        elif force_default:
            if key in prev:
                active = True
            elif default_active is True:
                active = True
            else:
                active = False
        elif isinstance(item, dict):
            active = item.get('active') is True
        else:
            # Legacy string entry => treat as active.
            active = True
        entry = {'id': token, 'active': active}
        if isinstance(item, dict) and item.get('manual') is True:
            entry['manual'] = True
        if isinstance(item, dict) and 'label' in item:
            cleaned = str(item.get('label') or '').strip()
            if cleaned:
                entry['label'] = cleaned
            # Explicit empty/null => omit (resolve via default map / id).
        else:
            # Migrate legacy string rows / objects lacking label.
            entry['label'] = default_model_label(token)
        out.append(entry)
    return out


def active_model_ids(provider: dict[str, Any]) -> list[str]:
    """Return model ids marked active (gateway / library advertising)."""
    models = provider.get('models')
    if not isinstance(models, list):
        return []
    # Legacy string list: all active.
    if models and all(not isinstance(m, dict) for m in models):
        out: list[str] = []
        seen: set[str] = set()
        for item in models:
            token = str(item or '').strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out
    out2: list[str] = []
    seen2: set[str] = set()
    for item in models:
        if isinstance(item, dict):
            token = _model_id_of(item)
            if not token or item.get('active') is not True:
                continue
        else:
            token = str(item or '').strip()
            if not token:
                continue
        key = token.lower()
        if key in seen2:
            continue
        seen2.add(key)
        out2.append(token)
    return out2


def _clean_available_models(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = _model_id_of(item) if isinstance(item, dict) else str(item or '').strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _clean_available_models_meta(raw: Any) -> dict[str, dict[str, Any]]:
    """Keep a small id -> upstream metadata map for UI subtitles."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        token = str(key or '').strip()
        if not token or not isinstance(value, dict):
            continue
        meta: dict[str, Any] = {}
        for field in ('owned_by', 'object', 'created', 'name', 'description', 'display_name'):
            if field in value and value.get(field) is not None:
                meta[field] = value.get(field)
        # Preserve any other small scalar extras (skip huge blobs).
        for field, val in value.items():
            if field in meta or field == 'id':
                continue
            if isinstance(val, (str, int, float, bool)) and field not in meta:
                if isinstance(val, str) and len(val) > 240:
                    continue
                meta[field] = val
        if meta:
            out[token] = meta
    return out


def meta_from_upstream_rows(upstream: list[Any] | None) -> dict[str, dict[str, Any]]:
    """Build available_models_meta from OpenAI-style ``data[]`` rows."""
    meta: dict[str, dict[str, Any]] = {}
    for row in upstream or []:
        if not isinstance(row, dict):
            continue
        token = str(row.get('id') or '').strip()
        if not token:
            continue
        extras = {k: v for k, v in row.items() if k != 'id'}
        cleaned = _clean_available_models_meta({token: extras})
        if token in cleaned:
            meta[token] = cleaned[token]
    return meta


def apply_fetched_models(
    provider: dict[str, Any],
    fetched_ids: list[str],
    *,
    upstream: list[Any] | None = None,
    models_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge a live ``/models`` fetch into provider models + available_models cache.

    Newly fetched ids default inactive; previously active ids stay active.
    First-time DeepSeek with no prior active selection pre-activates official
    defaults when present in the fetch. Previously active ids missing from the
    fetch are kept and tagged ``manual`` so the UI can show a custom badge.
    """
    provider_id = str(provider.get('id') or '').strip().lower()
    prev_active = {m.lower() for m in active_model_ids(provider)}
    fetched = []
    seen: set[str] = set()
    for item in fetched_ids or []:
        token = str(item or '').strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        fetched.append(token)

    # First-time DeepSeek: empty prior selection -> pre-check official defaults.
    if provider_id == 'deepseek' and not prev_active:
        defaults = {m.lower() for m in DEEPSEEK_DEFAULT_MODELS}
        present_defaults = {m.lower() for m in fetched if m.lower() in defaults}
        if present_defaults:
            prev_active = present_defaults

    models = normalize_model_entries(
        fetched,
        previous_active=prev_active,
        default_active=False,
    )

    prior_rows = [
        m for m in (provider.get('models') or [])
        if isinstance(m, dict)
    ]
    prior_labels = {
        str(m.get('id') or '').strip().lower(): str(m.get('label') or '').strip()
        for m in prior_rows
        if str(m.get('label') or '').strip()
    }
    prior_manual = {
        str(m.get('id') or '').strip().lower()
        for m in prior_rows
        if m.get('manual') is True
    }

    # Keep any previously active ids that were not in the fetch (still listed).
    known = {m['id'].lower() for m in models}
    for token in active_model_ids(provider):
        key = token.lower()
        if key in known:
            continue
        kept = {'id': token, 'active': True, 'manual': True}
        if key in prior_labels:
            kept['label'] = prior_labels[key]
        else:
            kept['label'] = default_model_label(token)
        models.append(kept)
        known.add(key)

    # Preserve prior manual flags + custom labels for ids still present.
    fetched_keys = {x.lower() for x in fetched}
    for row in models:
        key = row['id'].lower()
        if key in prior_manual and key not in fetched_keys:
            row['manual'] = True
        if key in prior_labels:
            row['label'] = prior_labels[key]

    out = dict(provider)
    out['models'] = models
    out['available_models'] = list(fetched)
    if models_meta is not None:
        out['available_models_meta'] = _clean_available_models_meta(models_meta)
    elif upstream is not None:
        out['available_models_meta'] = meta_from_upstream_rows(upstream)
    out['models_fetched'] = True
    return out


def normalize_api_provider(raw: Any, *, existing_ids: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('api provider must be an object')
    provider_id = _slug_provider_id(raw.get('id') or raw.get('provider_id'))
    if not provider_id:
        raise ValueError('api provider id is required')
    taken = existing_ids or set()
    if provider_id in taken:
        raise ValueError(f'duplicate api provider id: {provider_id}')

    catalog = PROVIDER_CATALOG.get(provider_id, {})
    label = str(raw.get('label') or catalog.get('label') or provider_id).strip() or provider_id
    default_base = str(catalog.get('default_base_url') or '').strip() or 'https://api.openai.com/v1'
    base_url = str(raw.get('base_url') or default_base).strip().rstrip('/')
    if not base_url:
        base_url = default_base
    if not (base_url.startswith('http://') or base_url.startswith('https://')):
        raise ValueError('api provider base_url must be http(s)')

    # Prefer explicit models. Do not auto-seed checklist from catalog defaults —
    # defaults are activation hints after a successful /models fetch only.
    if 'models' in raw and raw.get('models') is not None:
        models = normalize_model_entries(raw.get('models'), fallback=[])
    else:
        models = []

    available_models = _clean_available_models(raw.get('available_models'))
    if not available_models:
        # Seed cache from known model ids so the UI has something to show.
        available_models = [m['id'] for m in models]

    available_models_meta = _clean_available_models_meta(raw.get('available_models_meta'))
    models_fetched = raw.get('models_fetched') is True

    api_key = str(raw.get('api_key') or '').strip()

    return {
        'id': provider_id,
        'label': label,
        'enabled': raw.get('enabled') is True,
        'api_key': api_key,
        'base_url': base_url,
        'models': models,
        'available_models': available_models,
        'available_models_meta': available_models_meta,
        'models_fetched': models_fetched,
    }


def normalize_api_providers(raw: Any) -> list[dict[str, Any]]:
    """Normalize provider list. Does not auto-inject catalog presets.

    Presets appear in the Add Provider wizard until the user adds them.
    Existing DeepSeek (or other) entries in config are preserved and migrated.
    """
    rows: list[Any]
    if raw is None:
        rows = []
    elif isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        # Allow map form: { "deepseek": { ... } }
        rows = []
        for key, value in raw.items():
            if isinstance(value, dict):
                rows.append({**value, 'id': value.get('id') or key})
            else:
                rows.append({'id': key})
    else:
        raise ValueError('api_providers must be a list or object')

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            normalized = normalize_api_provider(row, existing_ids=seen)
        except ValueError as exc:
            raise ValueError(f'api_providers[{index}]: {exc}') from exc
        seen.add(normalized['id'])
        out.append(normalized)
    return out


def ensure_api_providers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    providers = normalize_api_providers(cfg.get('api_providers'))
    cfg['api_providers'] = providers
    return providers


def mask_api_key(api_key: str) -> str:
    key = str(api_key or '').strip()
    if not key:
        return ''
    if len(key) <= 4:
        return _MASK_PLACEHOLDER
    return f'{_MASK_PLACEHOLDER}{key[-4:]}'


def resolve_provider_api_key(provider: dict[str, Any]) -> str:
    """Return the effective API key (env override wins). Never log the result."""
    provider_id = str(provider.get('id') or '').strip().lower()
    catalog = PROVIDER_CATALOG.get(provider_id) or {}
    env_name = str(catalog.get('env_api_key') or '').strip()
    if env_name:
        env_value = str(os.environ.get(env_name) or '').strip()
        if env_value:
            return env_value
    return str(provider.get('api_key') or '').strip()


def provider_has_api_key(provider: dict[str, Any]) -> bool:
    return bool(resolve_provider_api_key(provider))


def public_api_provider(provider: dict[str, Any]) -> dict[str, Any]:
    """Safe view for Settings / GET APIs — never includes the raw key."""
    provider_id = str(provider.get('id') or '').strip().lower()
    catalog = PROVIDER_CATALOG.get(provider_id) or {}
    stored = str(provider.get('api_key') or '').strip()
    env_name = str(catalog.get('env_api_key') or '').strip()
    env_present = bool(env_name and str(os.environ.get(env_name) or '').strip())
    effective = resolve_provider_api_key(provider)
    models = normalize_model_entries(provider.get('models'), fallback=[])
    available = _clean_available_models(provider.get('available_models'))
    if not available:
        available = [m['id'] for m in models]
    available_meta = _clean_available_models_meta(provider.get('available_models_meta'))
    active = [m['id'] for m in models if m.get('active') is True]
    return {
        'id': provider_id,
        'label': str(provider.get('label') or catalog.get('label') or provider_id),
        'enabled': provider.get('enabled') is True,
        'base_url': str(provider.get('base_url') or catalog.get('default_base_url') or ''),
        'models': models,
        'available_models': available,
        'available_models_meta': available_meta,
        'models_fetched': provider.get('models_fetched') is True,
        'active_models': active,
        'active_model_count': len(active),
        'api_key_set': bool(stored),
        'api_key_preview': mask_api_key(stored) if stored else '',
        'env_api_key': env_name,
        'env_api_key_present': env_present,
        'ready': provider.get('enabled') is True and bool(effective),
        'docs_url': str(catalog.get('docs_url') or ''),
        'default_models': list(catalog.get('default_models') or []),
        'default_base_url': str(catalog.get('default_base_url') or ''),
        'is_catalog': provider_id in PROVIDER_CATALOG,
    }


def list_public_api_providers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [public_api_provider(row) for row in ensure_api_providers(cfg)]


def redact_api_providers_in_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of cfg with api_providers keys redacted for API responses."""
    out = dict(cfg)
    redacted: list[dict[str, Any]] = []
    for row in ensure_api_providers(cfg):
        public = public_api_provider(row)
        redacted.append({
            'id': public['id'],
            'label': public['label'],
            'enabled': public['enabled'],
            'base_url': public['base_url'],
            'models': public['models'],
            'available_models': public['available_models'],
            'available_models_meta': public.get('available_models_meta') or {},
            'models_fetched': public.get('models_fetched') is True,
            'active_models': public['active_models'],
            'api_key': public['api_key_preview'],
            'api_key_set': public['api_key_set'],
            'env_api_key': public['env_api_key'],
            'env_api_key_present': public['env_api_key_present'],
            'ready': public['ready'],
        })
    out['api_providers'] = redacted
    return out


def merge_api_provider_patch(
    existing: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge a Settings patch onto one provider.

    ``api_key`` omitted / null / mask placeholder keeps the stored key.
    Empty string clears the stored key.
    """
    merged = dict(existing)
    if 'enabled' in patch:
        merged['enabled'] = patch.get('enabled') is True
    if 'label' in patch and patch.get('label') is not None:
        merged['label'] = str(patch.get('label') or '').strip() or existing.get('label')
    if 'base_url' in patch and patch.get('base_url') is not None:
        merged['base_url'] = str(patch.get('base_url') or '').strip().rstrip('/')
    if 'models' in patch and patch.get('models') is not None:
        merged['models'] = patch.get('models')
    if 'available_models' in patch and patch.get('available_models') is not None:
        merged['available_models'] = patch.get('available_models')
    if 'available_models_meta' in patch and patch.get('available_models_meta') is not None:
        merged['available_models_meta'] = patch.get('available_models_meta')
    if 'models_fetched' in patch:
        merged['models_fetched'] = patch.get('models_fetched') is True
    if 'api_key' in patch:
        incoming = patch.get('api_key')
        if incoming is None:
            pass
        else:
            text = str(incoming).strip()
            if text == '' or text == _MASK_PLACEHOLDER or text.startswith(_MASK_PLACEHOLDER):
                if text == '':
                    merged['api_key'] = ''
                # mask / placeholder -> keep existing
            else:
                merged['api_key'] = text
    return normalize_api_provider(merged)


def upsert_api_providers(cfg: dict[str, Any], patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = {str(row.get('id') or ''): dict(row) for row in ensure_api_providers(cfg)}
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        provider_id = _slug_provider_id(patch.get('id'))
        if not provider_id:
            continue
        base = current.get(provider_id) or normalize_api_provider({'id': provider_id})
        current[provider_id] = merge_api_provider_patch(base, patch)
    # Preserve catalog order then any extras.
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider_id in PROVIDER_CATALOG:
        if provider_id in current:
            ordered.append(current[provider_id])
            seen.add(provider_id)
    for provider_id, row in current.items():
        if provider_id not in seen:
            ordered.append(row)
    cfg['api_providers'] = normalize_api_providers(ordered)
    return cfg['api_providers']


def delete_api_provider(cfg: dict[str, Any], provider_id: str) -> bool:
    """Remove a provider from config. Returns True if something was removed."""
    wanted = _slug_provider_id(provider_id)
    if not wanted:
        return False
    providers = ensure_api_providers(cfg)
    next_rows = [row for row in providers if str(row.get('id') or '') != wanted]
    if len(next_rows) == len(providers):
        return False
    cfg['api_providers'] = next_rows
    return True


def enabled_providers_with_keys(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in ensure_api_providers(cfg):
        if row.get('enabled') is not True:
            continue
        if not provider_has_api_key(row):
            continue
        out.append(row)
    return out



def with_api_label(text: str) -> str:
    """Append a stable `(API)` suffix unless one is already present."""
    base = str(text or '').strip()
    if not base:
        return 'API'
    if re.search(r'\(\s*API\s*\)\s*$', base, re.I):
        return base
    return f'{base} (API)'


def cloud_model_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat list of advertised cloud model rows for gateway / catalog (active only)."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider in enabled_providers_with_keys(cfg):
        provider_id = str(provider.get('id') or '')
        label = str(provider.get('label') or provider_id)
        for model_id in active_model_ids(provider):
            token = str(model_id or '').strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            model_label = token
            for item in (provider.get('models') or []):
                if isinstance(item, dict) and _model_id_of(item).lower() == key:
                    model_label = resolve_model_label(item)
                    break
            else:
                model_label = default_model_label(token)
            entries.append({
                'id': token,
                'label': model_label,
                'provider_id': provider_id,
                'provider_label': label,
                'owned_by': provider_id,
                'base_url': str(provider.get('base_url') or ''),
            })
    return entries


def resolve_cloud_provider_for_model(cfg: dict[str, Any], model: str) -> dict[str, Any] | None:
    requested = str(model or '').strip()
    if not requested:
        return None
    wanted = requested.lower()
    for provider in enabled_providers_with_keys(cfg):
        for model_id in active_model_ids(provider):
            if str(model_id or '').strip().lower() == wanted:
                return provider
    return None


def models_endpoint_candidates(base_url: str) -> list[str]:
    """Build GET /models URL candidates for an OpenAI-compatible base URL."""
    base = str(base_url or '').strip().rstrip('/')
    if not base:
        return []
    if base.endswith('/v1'):
        return [f'{base}/models']
    return [f'{base}/models', f'{base}/v1/models']


def chat_completions_url(provider: dict[str, Any]) -> str:
    base = str(provider.get('base_url') or '').strip().rstrip('/')
    if not base:
        base = DEEPSEEK_DEFAULT_BASE_URL
    # DeepSeek docs use https://api.deepseek.com/chat/completions (no /v1).
    # Accept either style from Settings.
    if base.endswith('/v1'):
        return f'{base}/chat/completions'
    return f'{base}/chat/completions'


def catalog_rows_for_providers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows shaped like ``/api/models`` entries for Playground pickers (active only)."""
    rows: list[dict[str, Any]] = []
    for entry in cloud_model_entries(cfg):
        model_id = entry['id']
        provider_id = entry['provider_id']
        provider_label = str(entry.get('provider_label') or provider_id or 'API')
        owned_by = str(entry.get('owned_by') or provider_id or '').strip() or provider_id
        # Display title uses friendly label; routing fields keep the real API id.
        label = str(entry.get('label') or default_model_label(model_id) or model_id)
        rows.append({
            'id': model_id,
            'model_id': model_id,
            'label': label,
            'filename': model_id,
            'path': '',
            'source': str(provider_id or 'api'),
            'source_label': provider_label,
            'library_label': provider_label,
            'provider': provider_label,
            'owned_by': owned_by,
            'publisher': owned_by,
            'author': owned_by,
            # Family / backend columns — cloud has no local GGUF arch.
            'arch': 'Cloud API',
            'family': 'Cloud API',
            'backend': 'Cloud API',
            'runtime_label': 'Cloud API',
            # Scale: unknown remote params — never invent fake param counts.
            'params': 'API',
            'modality': 'llm',
            'task': 'chat',
            'loadable': False,
            'cloud': True,
            'cloud_provider': provider_id,
            'provider_id': provider_id,
            'chat_model_key': f'cloud::{provider_id}::{model_id}',
            'server_id': f'cloud:{provider_id}',
            'capabilities': [],
            'always_ready': True,
            'api': True,
            'name': with_api_label(label),
            # Disk / recency are N/A for remote API models.
            'size_gb': None,
            'modified': 'Cloud',
        })
    return rows
