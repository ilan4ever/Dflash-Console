from __future__ import annotations

from core.diagnostics_bundle import build_diagnostics_bundle, bundle_to_text, redact_mapping
from core.support_journal import redact_mapping as journal_redact_mapping


def test_redact_mapping_hides_secrets():
    payload = {'api_token': 'secret', 'ui_port': 8900}
    out = redact_mapping(payload)
    assert out['api_token'] == '[redacted]'
    assert out['ui_port'] == 8900


def test_bundle_to_text_includes_header(monkeypatch):
    monkeypatch.setattr('core.diagnostics_bundle.get_status_report_payload', lambda **kwargs: {
        'loaded': {'loaded': []},
        'engines': {'servers': []},
    })
    monkeypatch.setattr('core.diagnostics_bundle.get_console_logs_payload', lambda **kwargs: {'errors': []})
    monkeypatch.setattr('core.diagnostics_bundle.get_support_meta', lambda: {'first_run_at': '2026-01-01'})
    monkeypatch.setattr('core.diagnostics_bundle.read_journal_lines', lambda **kwargs: ['2026 line'])

    bundle = build_diagnostics_bundle(boot_id='boot1', user_note='broken load')
    text = bundle_to_text(bundle)
    assert 'DFlash Console diagnostic report' in text
    assert 'boot1' in text
    assert 'broken load' in text
    assert journal_redact_mapping({'x': 1})['x'] == 1
