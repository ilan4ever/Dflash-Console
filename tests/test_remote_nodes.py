from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.config import normalize_remote_node, normalize_remote_nodes
from core.remote_nodes import (
    add_remote_node,
    check_remote_node_health,
    list_remote_nodes,
    node_chat_url,
    node_health_url,
    remove_remote_node,
    save_remote_nodes,
)


def test_normalize_remote_node_requires_url():
    with pytest.raises(ValueError):
        normalize_remote_node({'label': 'Lab'})
    node = normalize_remote_node({'label': 'Lab', 'base_url': 'http://10.0.0.5:8900/'})
    assert node['id'] == 'lab'
    assert node['base_url'] == 'http://10.0.0.5:8900'


def test_normalize_remote_nodes_dedupes_ids():
    rows = normalize_remote_nodes([
        {'id': 'lab', 'label': 'A', 'base_url': 'http://10.0.0.1:8900'},
        {'id': 'lab', 'label': 'B', 'base_url': 'http://10.0.0.2:8900'},
    ])
    assert [row['id'] for row in rows] == ['lab', 'lab-2']


def test_remote_node_urls():
    node = {'base_url': 'http://192.168.1.10:8900'}
    assert node_health_url(node) == 'http://192.168.1.10:8900/api/health'
    assert node_chat_url(node) == 'http://192.168.1.10:8900/v1/chat/completions'


def test_add_and_remove_remote_node(tmp_path, monkeypatch):
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({'ui_port': 8900, 'servers': [], 'remote_nodes': []}), encoding='utf-8')
    monkeypatch.setattr('core.config.CONFIG_PATH', cfg_path)
    monkeypatch.setattr('core.remote_nodes.load_config', lambda cfg=None: json.loads(cfg_path.read_text(encoding='utf-8')))
    monkeypatch.setattr('core.remote_nodes.save_config', lambda cfg: cfg_path.write_text(json.dumps(cfg), encoding='utf-8'))

    node = add_remote_node({'label': 'Remote PC', 'base_url': 'http://127.0.0.1:8901'})
    assert node['id'] == 'remote-pc'
    assert len(list_remote_nodes()) == 1
    assert remove_remote_node(node['id']) is True
    assert list_remote_nodes() == []


def test_check_remote_node_health_online():
    node = {'base_url': 'http://127.0.0.1:8900', 'enabled': True}
    payload = json.dumps({'success': True, 'app': 'DFlash Console', 'version': '0.3.48'}).encode('utf-8')

    class FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch('core.remote_nodes.urllib.request.urlopen', return_value=FakeResp()):
        health = check_remote_node_health(node)
    assert health['online'] is True
    assert health['status'] == 'online'
    assert health['remote_version'] == '0.3.48'
