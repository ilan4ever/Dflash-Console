from __future__ import annotations

from dflash_cli.cli import build_parser, main
from dflash_cli.commands import _cmd_shim_text, _profile_function_text
from dflash_cli.http import resolve_base_url
from dflash_cli.render import format_table
from dflash_cli.resolve import pick_model, score_name


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
