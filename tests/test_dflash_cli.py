from __future__ import annotations

from dflash_cli.cli import build_parser, main
from dflash_cli.commands import (
    _cmd_shim_text,
    _parse_setting_pair,
    _profile_function_text,
    _settings_patch_body,
)
from dflash_cli.http import resolve_base_url
from dflash_cli.render import format_table
from dflash_cli.resolve import pick_model, pick_node, score_name


def test_resolve_base_url_prefers_flag(monkeypatch):
    monkeypatch.delenv('DFLASH_URL', raising=False)
    monkeypatch.delenv('DFLASH_PORT', raising=False)
    assert resolve_base_url('http://127.0.0.1:8999', 8900) == 'http://127.0.0.1:8999'
    assert resolve_base_url(None, 8123) == 'http://127.0.0.1:8123'


def test_score_and_pick_model():
    rows = [
        {'id': 'gemma-31b-dflash', 'label': 'Gemma 31B', 'filename': 'gemma.gguf'},
        {'id': 'qwen3-5-27b', 'label': 'Qwen 27B DFlash', 'filename': 'qwen.gguf'},
    ]
    assert score_name('gemma', 'Gemma 31B') >= 80
    picked = pick_model('qwen', rows)
    assert picked['id'] == 'qwen3-5-27b'


def test_format_table_includes_headers():
    text = format_table(
        [{'name': 'Gemma', 'size': '16 GB'}],
        [('NAME', 'name'), ('SIZE', 'size')],
    )
    assert 'NAME' in text
    assert 'Gemma' in text


def test_parser_accepts_list_flags():
    args = build_parser().parse_args(['list', '--loaded', '--ollama', '--type', 'ocr', '--json'])
    assert args.command == 'list'
    assert args.loaded is True
    assert args.ollama is True
    assert args.type == 'ocr'
    assert args.json is True


def test_help_exits_zero():
    assert main(['help']) == 0
    assert main(['-h']) == 0


def test_install_helpers_point_at_repo():
    assert 'python -m dflash_cli' in _cmd_shim_text()
    assert 'function dflash' in _profile_function_text()
    assert 'BEGIN DFLASH CLI' in _profile_function_text()


def test_parser_accepts_new_commands():
    parser = build_parser()
    embed = parser.parse_args(['embed', 'hello world', '--engine', 'nomic'])
    assert embed.command == 'embed'
    assert embed.text == ['hello world']
    assert embed.engine == 'nomic'
    delete = parser.parse_args(['delete', 'qwen', '--yes'])
    assert delete.command == 'delete'
    assert delete.yes is True
    nodes = parser.parse_args(['nodes', 'add', 'http://127.0.0.1:8901', '--label', 'Lab'])
    assert nodes.action == 'add'
    assert nodes.target == 'http://127.0.0.1:8901'
    settings = parser.parse_args(['settings', '--set', 'ui_port=8900'])
    assert settings.set_pair == 'ui_port=8900'


def test_help_lists_new_commands(capsys):
    assert main(['help']) == 0
    out = capsys.readouterr().out
    assert 'dflash embed' in out
    assert 'dflash delete' in out
    assert 'dflash nodes' in out
    assert 'dflash settings' in out


def test_settings_helpers():
    key, value = _parse_setting_pair('ui_port=8901')
    assert key == 'ui_port'
    assert value == 8901
    assert _settings_patch_body('download_settings.parallel_connections', 4) == {
        'download_settings': {'parallel_connections': 4}
    }


def test_pick_node():
    rows = [
        {'id': 'lab', 'label': 'Lab PC', 'base_url': 'http://10.0.0.5:8900'},
        {'id': 'office', 'label': 'Office', 'base_url': 'http://10.0.0.8:8900'},
    ]
    assert pick_node('lab', rows)['id'] == 'lab'
