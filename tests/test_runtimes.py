"""Tests for the multi-modal runtime adapter registry (Phase 0)."""

from __future__ import annotations

from core.runtimes import (
    EXECUTION_MODE_CLI,
    NoopRuntimeAdapter,
    get_runtime_adapter,
    list_runtime_adapters,
    register_runtime_adapter,
    runtime_process_identity_tokens,
)


def test_noop_adapter_implements_contract():
    adapter = NoopRuntimeAdapter()
    assert adapter.runtime_id == 'noop'
    assert adapter.execution_mode == EXECUTION_MODE_CLI
    assert isinstance(adapter.process_identity_tokens, tuple)

    health = adapter.health()
    assert health['ok'] is True
    assert health['running'] is False
    assert adapter.start({})['success'] is True
    assert adapter.stop()['success'] is True
    assert adapter.load({})['success'] is True
    assert adapter.unload()['success'] is True
    assert adapter.openai_routes() == []


def test_registry_lists_noop_and_can_fetch_it():
    runtime_ids = {adapter.runtime_id for adapter in list_runtime_adapters()}
    assert 'noop' in runtime_ids
    assert get_runtime_adapter('noop') is not None
    assert get_runtime_adapter('does-not-exist') is None
    assert get_runtime_adapter('') is None


def test_register_overrides_and_lists():
    class FakeAdapter(NoopRuntimeAdapter):
        runtime_id = 'fake-test'
        process_identity_tokens = ('fake-engine', 'fake-runtime')

    register_runtime_adapter(FakeAdapter())
    assert get_runtime_adapter('fake-test') is not None
    assert 'fake-test' in {a.runtime_id for a in list_runtime_adapters()}


def test_process_identity_tokens_include_llama_and_registered():
    tokens = set(runtime_process_identity_tokens())
    assert 'llama-server' in tokens
    assert 'start_llama_server.ps1' in tokens
    # FakeAdapter registered above contributes its tokens without duplicates.
    assert 'fake-engine' in tokens
    assert 'fake-runtime' in tokens


def test_process_identity_tokens_dedupe_case_insensitively():
    class DupAdapter(NoopRuntimeAdapter):
        runtime_id = 'dup-test'
        process_identity_tokens = ('LLAMA-SERVER', 'fake-engine')

    register_runtime_adapter(DupAdapter())
    tokens = list(runtime_process_identity_tokens())
    lower = [t.lower() for t in tokens]
    assert lower.count('llama-server') == 1
    assert lower.count('fake-engine') == 1


def test_write_bundle_manifests_writes_adapter_manifests(tmp_path, monkeypatch):
    from pathlib import Path

    import core.runtimes.registry as registry

    class ManifAdapter(NoopRuntimeAdapter):
        runtime_id = 'manifest-test'

        def write_manifest(self):
            target = tmp_path / 'runtimes' / self.runtime_id / 'manifest.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"runtime_id": "manifest-test"}', encoding='utf-8')
            return target

    adapter = ManifAdapter()
    register_runtime_adapter(adapter)
    registry.write_bundle_manifests()
    manifest = tmp_path / 'runtimes' / 'manifest-test' / 'manifest.json'
    assert manifest.is_file()
