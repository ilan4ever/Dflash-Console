"""Tests for the batch-embed helpers (Phase 4)."""

from __future__ import annotations

import json

from core.embedding_server import embed_batch, embed_rows_to_jsonl


def test_embed_rows_to_jsonl_roundtrip():
    rows = [
        {'index': 0, 'text': 'hello world', 'embedding': [0.1, 0.2], 'dimensions': 2},
        {'index': 1, 'text': 'second item', 'embedding': [0.3, 0.4], 'dimensions': 2},
    ]
    payload = embed_rows_to_jsonl(rows)
    lines = [line for line in payload.strip().splitlines() if line]
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed['text'] == 'hello world'
    assert parsed['embedding'] == [0.1, 0.2]


def test_embed_batch_maps_vectors_by_index(monkeypatch):
    server = {'id': 'nomic-embed', 'api_url': 'http://127.0.0.1:8891/v1', 'model_id': 'nomic-embed-text'}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self._payload).encode('utf-8')

    captured = {}

    def fake_urlopen(request, timeout=0):
        captured['url'] = request.full_url
        captured['body'] = json.loads(request.data.decode('utf-8'))
        return FakeResponse({
            'model': 'nomic-embed-text',
            'data': [
                {'object': 'embedding', 'index': 0, 'embedding': [1.0, 0.0, 0.0]},
                {'object': 'embedding', 'index': 1, 'embedding': [0.0, 1.0, 0.0]},
            ],
            'usage': {'total_tokens': 12},
        })

    monkeypatch.setattr('core.embedding_server.urllib.request.urlopen', fake_urlopen)
    result = embed_batch(server, items=[{'text': 'alpha'}, {'text': 'beta'}])
    assert result['success'] is True
    assert len(result['rows']) == 2
    assert result['rows'][0]['embedding'] == [1.0, 0.0, 0.0]
    assert result['rows'][1]['embedding'] == [0.0, 1.0, 0.0]
    assert result['rows'][0]['dimensions'] == 3
    # Upstream received both texts in one batch call.
    assert captured['body']['input'] == ['alpha', 'beta']
    assert captured['url'] == 'http://127.0.0.1:8891/v1/embeddings'


def test_embed_batch_rejects_all_blank_items(monkeypatch):
    server = {'id': 'nomic-embed', 'api_url': 'http://127.0.0.1:8891/v1'}

    def fake_urlopen(request, timeout=0):  # pragma: no cover
        raise AssertionError('urlopen must not be called')

    monkeypatch.setattr('core.embedding_server.urllib.request.urlopen', fake_urlopen)
    result = embed_batch(server, items=[{'text': '   '}, {'text': ''}])
    assert result['success'] is False
    assert 'no text items' in result['error']
