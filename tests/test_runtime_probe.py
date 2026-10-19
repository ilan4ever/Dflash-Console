from __future__ import annotations

import json

from core import runtime


def test_router_detection_reads_props_without_posting_unload(monkeypatch):
    calls: list[tuple[str, str]] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({'role': 'router'}).encode()

    def open_url(request, timeout):
        calls.append((request.get_method(), request.full_url))
        return Response()

    monkeypatch.setattr(runtime.urllib.request, 'urlopen', open_url)

    assert runtime.router_unload_available('http://127.0.0.1:8099/v1') is True
    assert calls == [('GET', 'http://127.0.0.1:8099/props')]
