"""Build redacted diagnostic bundles for bug reports."""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from typing import Any

from core.api_introspection import get_console_logs_payload
from core.config import ROOT, load_config
from core.status_report import get_status_report_payload
from core.support_journal import (
    get_support_meta,
    read_journal_lines,
    redact_mapping,
)
from core.version import APP_VERSION


def _environment_line() -> str:
    return f'{platform.system()} {platform.release()}, Python {platform.python_version()}'


def _summary_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    return redact_mapping({
        'ui_port': cfg.get('ui_port'),
        'gateway_port': cfg.get('gateway_port'),
        'hardware_settings': cfg.get('hardware_settings') or {},
        'download_settings': cfg.get('download_settings') or {},
        'gpu_performance_mode': cfg.get('gpu_performance_mode'),
        'context_auto_grow': cfg.get('context_auto_grow'),
        'runtime_stop_others_on_load': cfg.get('runtime_stop_others_on_load'),
        'keep_models_loaded_on_exit': cfg.get('keep_models_loaded_on_exit'),
        'server_count': len(cfg.get('servers') or []),
        'runtime_count': len(cfg.get('runtimes') or []),
        'library_count': len(cfg.get('model_libraries') or []),
    })


def build_diagnostics_bundle(
    *,
    cfg: dict[str, Any] | None = None,
    boot_id: str = '',
    boot_at: float = 0.0,
    shell_version: str = '',
    tail: int = 400,
    user_note: str = '',
    reproduction: str = '',
) -> dict[str, Any]:
    config = cfg or load_config()
    meta = get_support_meta()
    report = get_status_report_payload(cfg=config, include_external=True)
    logs = get_console_logs_payload(cfg=config, tail=tail, errors_only=False)
    journal = read_journal_lines(tail=min(tail, 600))
    errors = [row for row in (logs.get('errors') or []) if isinstance(row, dict)][-80:]

    bundle = {
        'success': True,
        'report_id': uuid.uuid4().hex[:16],
        'generated_at': time.time(),
        'app': {
            'name': 'DFlash Console',
            'version': APP_VERSION,
            'boot_id': boot_id,
            'boot_at': boot_at,
            'shell_version': shell_version or os.environ.get('DFLASH_CONSOLE_SHELL_VERSION', ''),
            'dev_server': bool((ROOT / '.git').is_dir() and not shell_version),
            'console_root': str(ROOT),
        },
        'environment': _environment_line(),
        'support_meta': redact_mapping(meta),
        'settings': _summary_settings(config),
        'status_report': redact_mapping(report),
        'recent_errors': errors,
        'journal_tail': journal,
        'log_errors_only': [row.get('line') for row in errors if row.get('line')],
        'user_note': str(user_note or '').strip()[:4000],
        'reproduction': str(reproduction or '').strip()[:4000],
        'private_upload_configured': bool(
            str(config.get('support_upload_url') or os.environ.get('DFLASH_SUPPORT_UPLOAD_URL') or 'https://onevoiceai.in/wp-json/dflash/v1/report').strip()
            and str(
                config.get('support_report_token')
                or os.environ.get('DFLASH_REPORT_TOKEN')
                or os.environ.get('DFLASH_UPDATE_TOKEN')
                or ''
            ).strip()
        ),
    }
    return bundle


def bundle_to_text(bundle: dict[str, Any]) -> str:
    app = bundle.get('app') or {}
    meta = bundle.get('support_meta') or {}
    settings = bundle.get('settings') or {}
    loaded = ((bundle.get('status_report') or {}).get('loaded') or {}).get('loaded') or []
    lines = [
        '=== DFlash Console diagnostic report ===',
        f'Report ID: {bundle.get("report_id")}',
        f'Generated: {bundle.get("generated_at")}',
        f'Version: {app.get("version")}',
        f'Boot ID: {app.get("boot_id")}',
        f'Environment: {bundle.get("environment")}',
        f'Console root: {app.get("console_root")}',
        '',
        '--- Install / usage ---',
        f'first_run_at: {meta.get("first_run_at")}',
        f'first_server_at: {meta.get("first_server_at")}',
        f'session_count: {meta.get("session_count")}',
        f'shell_version: {app.get("shell_version") or "n/a"}',
        '',
        '--- Settings (redacted) ---',
        json.dumps(settings, indent=2, sort_keys=True),
        '',
        '--- Currently loaded ---',
    ]
    if loaded:
        for row in loaded[:40]:
            if not isinstance(row, dict):
                continue
            label = row.get('label') or row.get('server_id') or row.get('runtime_id') or 'model'
            model = row.get('active_model_id') or row.get('active_model') or row.get('model_path') or ''
            lines.append(f'- {label}: {model}')
    else:
        lines.append('(none)')

    history = meta.get('model_load_history') or []
    lines.extend(['', '--- Model activity (recent) ---'])
    if history:
        for row in history[-30:]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f'{row.get("at")} {row.get("event")} server={row.get("server_id")} '
                f'model={row.get("model_id")} client={row.get("client")} {row.get("detail") or ""}'.strip()
            )
    else:
        lines.append('(none)')

    if bundle.get('user_note'):
        lines.extend(['', '--- User note ---', str(bundle.get('user_note'))])
    if bundle.get('reproduction'):
        lines.extend(['', '--- Reproduction steps ---', str(bundle.get('reproduction'))])

    err_lines = bundle.get('log_errors_only') or []
    lines.extend(['', '--- Recent error lines ---'])
    lines.extend(err_lines[-40:] if err_lines else ['(none)'])

    journal = bundle.get('journal_tail') or []
    lines.extend(['', '--- Support journal ---'])
    lines.extend(journal[-120:] if journal else ['(empty)'])

    lines.append('')
    lines.append('=== End of diagnostic report ===')
    return '\n'.join(lines)


def github_issue_prefill(bundle: dict[str, Any]) -> dict[str, str]:
    app = bundle.get('app') or {}
    meta = bundle.get('support_meta') or {}
    return {
        'version': str(app.get('version') or APP_VERSION),
        'environment': str(bundle.get('environment') or _environment_line()),
        'reproduction': str(bundle.get('reproduction') or 'See attached diagnostic report (copied to clipboard).'),
        'expected': 'Describe what you expected.',
        'actual': str(bundle.get('user_note') or 'See diagnostic report for details.'),
        'report_id': str(bundle.get('report_id') or ''),
        'boot_id': str(app.get('boot_id') or ''),
        'first_run_at': str(meta.get('first_run_at') or ''),
    }
