"""Tests for the unified components hub catalog."""

from core.components_hub import BUNDLE_REVISIONS, list_components_payload


def test_component_row_prefers_installed_over_stale_error(monkeypatch):
    monkeypatch.setattr(
        'core.components_hub._on_demand_status',
        lambda runtime_id: {
            'installed': True,
            'status': 'error',
            'error': 'stale PowerShell noise from an old install attempt',
        },
    )
    row = next(entry for entry in list_components_payload()['components'] if entry['id'] == 'freetoken')
    assert row['installed'] is True
    assert row['status'] == 'installed'
    assert row['install_error'] == ''

def test_list_components_payload_shape():
    payload = list_components_payload()
    assert payload['success'] is True
    assert isinstance(payload['components'], list)
    assert len(payload['components']) >= 3
    vllm = next(row for row in payload['components'] if row['id'] == 'vllm')
    assert vllm['install_mode'] == 'on_demand'
    assert vllm['category'] == 'llm_engine'
    assert 'attention_count' in payload
    assert isinstance(payload['bundle_revisions'], dict)
    assert payload['bundle_revisions']['vllm'] == BUNDLE_REVISIONS['vllm']
