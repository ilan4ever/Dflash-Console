"""Argument parser and dispatch for the DFlash CLI."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from dflash_cli.commands import (
    cmd_api,
    cmd_chat,
    cmd_delete,
    cmd_downloads,
    cmd_embed,
    cmd_engines,
    cmd_hardware,
    cmd_help,
    cmd_install,
    cmd_list,
    cmd_load,
    cmd_logs,
    cmd_nodes,
    cmd_open,
    cmd_ps,
    cmd_pull,
    cmd_report,
    cmd_runtimes,
    cmd_search,
    cmd_serve,
    cmd_settings,
    cmd_show,
    cmd_start,
    cmd_stats,
    cmd_status,
    cmd_stop,
    cmd_unload,
    cmd_version,
)
from dflash_cli.http import ConsoleClient, ConsoleError, resolve_base_url
from dflash_cli.render import fail


def _shared_flags() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument('-u', '--url', help='Console URL, or set DFLASH_URL')
    shared.add_argument('-p', '--port', type=int, help='Console port (default 8900)')
    shared.add_argument('-j', '--json', action='store_true', help='Print JSON')
    shared.add_argument('-q', '--quiet', action='store_true', help='Less output')
    shared.add_argument('--timeout', type=float, default=30.0, help='HTTP timeout seconds')
    return shared


def build_parser() -> argparse.ArgumentParser:
    shared = _shared_flags()
    parser = argparse.ArgumentParser(
        prog='dflash',
        description='Talk to the DFlash Console server from the terminal.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Run `dflash help` for examples. Names can be short and unique.',
        parents=[shared],
    )
    parser.set_defaults(handler='help')

    sub = parser.add_subparsers(dest='command')

    def add(name: str, **kwargs: object) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[shared], **kwargs)

    add('help', help='Show command overview')
    add('version', help='CLI and server version')
    add('status', help='Server health and loaded count')
    add('ps', aliases=['loaded'], help='Models loaded right now')
    add('engines', help='Engine profiles')
    add('runtimes', help='Speech and other runtimes')
    add('hardware', aliases=['gpu', 'gpus'], help='GPU memory')
    add('stats', help='CPU, RAM, and VRAM')
    add('report', help='Full machine report')
    add('open', aliases=['ui'], help='Open the Console in a browser')
    add('install', help='Add dflash to your user PATH')

    listing = add('list', aliases=['ls', 'models'], help='List every local model the Console can see')
    listing.add_argument('--loaded', action='store_true', help='Only loaded models')
    listing.add_argument('--dflash', action='store_true', help='DFlash stacks and Console library models')
    listing.add_argument('--ollama', action='store_true', help='Only Ollama models on this PC')
    listing.add_argument('--lmstudio', action='store_true', help='Only LM Studio library models')
    listing.add_argument('--vllm', action='store_true', help='Hugging Face models that can run on vLLM')
    listing.add_argument('--transformers', action='store_true', help='Hugging Face models that can run on Transformers')
    listing.add_argument('--source', help='Filter by source: ollama, lmstudio, dflash, library, vllm, transformers')
    listing.add_argument('--type', default='all', help='Filter by type, e.g. llm or ocr')
    listing.add_argument('--filter', help='Text filter')
    listing.add_argument('--quick', action='store_true', help='Engine profiles only, skip the full disk library')
    listing.add_argument('--refresh', action='store_true', help='Rescan model folders')

    show = add('show', help='Show one model')
    show.add_argument('name', help='Model name or id')

    load = add('load', help='Load a model')
    load.add_argument('name', help='Model name or id')
    load.add_argument('-e', '--engine', help='Engine to load onto')

    unload = add('unload', help='Unload an engine or runtime')
    unload.add_argument('name', help='Engine, runtime, or model name')

    start = add('start', help='Start an engine')
    start.add_argument('name', help='Engine name or id')

    stop = add('stop', help='Stop an engine')
    stop.add_argument('name', help='Engine name or id')

    chat = add('chat', help='Send a prompt')
    chat.add_argument('prompt', nargs='+', help='Message to send')
    chat.add_argument('-e', '--engine', help='Engine id or label')
    chat.add_argument('--max-tokens', type=int, default=512)

    embed = add('embed', help='Turn text into vectors')
    embed.add_argument('text', nargs='*', help='Text to embed')
    embed.add_argument('-e', '--engine', help='Embedding engine id or label')
    embed.add_argument('--file', help='Read one item per line from a file')

    delete = add('delete', aliases=['rm'], help='Delete a local model from disk')
    delete.add_argument('name', help='Model name or id')
    delete.add_argument('-y', '--yes', action='store_true', help='Do not ask for confirmation')

    nodes = add('nodes', help='List or manage remote Console nodes')
    nodes.add_argument(
        'action',
        nargs='?',
        default='list',
        choices=['list', 'add', 'remove', 'rm', 'health'],
        help='list, add, remove, or health',
    )
    nodes.add_argument('target', nargs='?', help='URL when adding, or node name')
    nodes.add_argument('--label', help='Display name when adding a node')
    nodes.add_argument('--token', help='API token when adding a node')
    nodes.add_argument('--fresh', action='store_true', help='Refresh node health')

    settings = add('settings', aliases=['config'], help='Show or change Console settings')
    settings.add_argument('--get', dest='get_key', help='Read one key, e.g. ui_port')
    settings.add_argument('--set', dest='set_pair', help='Write KEY=VALUE, e.g. ui_port=8900')

    search = add('search', help='Search Hugging Face')
    search.add_argument('query', help='Search text')
    search.add_argument('--limit', type=int, default=10)
    search.add_argument('--category', default='all-gguf')
    search.add_argument('--sort', default='downloads')

    pull = add('pull', aliases=['download'], help='Download a model')
    pull.add_argument('target', help='Repo id or search text')
    pull.add_argument('--file', help='Exact filename to download')
    pull.add_argument('--library', help='Library id')
    pull.add_argument('--install', action='store_true', help='Search, download, and set up')
    pull.add_argument('--load', action='store_true', help='Load after install')
    pull.add_argument('--wait', action='store_true', help='Wait until the file finishes')

    downloads = add('downloads', help='Current and last downloads')
    downloads.add_argument('--active', action='store_true', help='Only current transfers')
    downloads.add_argument('--history', action='store_true', help='Only finished items')
    downloads.add_argument('--range', default='all', help='Days: 1, 7, 30, or all')
    downloads.add_argument('--clear', action='store_true', help='Clear finished history')
    downloads.add_argument('--remove', help='Remove one history id')

    logs = add('logs', help='Console or engine logs')
    logs.add_argument('-e', '--engine', help='Engine log instead of console log')
    logs.add_argument('--tail', type=int, default=40)
    logs.add_argument('--errors', action='store_true')

    api = add('api', help='Call any Console HTTP route')
    api.add_argument('method', help='GET, POST, DELETE, ...')
    api.add_argument('path', help='API path, e.g. /api/models')
    api.add_argument('--body', help='JSON body')

    serve = add('serve', help='Start the Console if it is not running')
    serve.add_argument('-P', '--serve-port', dest='serve_port', type=int, default=None, help='Listen port')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {'-h', '--help'}:
        return cmd_help()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 1)

    command = (args.command or 'help').lower()
    if command == 'help':
        return cmd_help()

    if command == 'serve':
        args.port = args.serve_port or args.port or 8900
        return cmd_serve(args)

    if command == 'install':
        return cmd_install(as_json=args.json)

    client = ConsoleClient(resolve_base_url(args.url, args.port), timeout=args.timeout)
    try:
        if command == 'version':
            return cmd_version(client, as_json=args.json)
        if command == 'status':
            return cmd_status(client, as_json=args.json)
        if command in {'list', 'ls', 'models'}:
            return cmd_list(client, args)
        if command in {'ps', 'loaded'}:
            return cmd_ps(client, as_json=args.json)
        if command == 'engines':
            return cmd_engines(client, as_json=args.json)
        if command == 'runtimes':
            return cmd_runtimes(client, as_json=args.json)
        if command == 'show':
            return cmd_show(client, args.name, as_json=args.json)
        if command == 'load':
            return cmd_load(client, args.name, args)
        if command == 'unload':
            return cmd_unload(client, args.name, as_json=args.json)
        if command == 'start':
            return cmd_start(client, args.name, as_json=args.json)
        if command == 'stop':
            return cmd_stop(client, args.name, as_json=args.json)
        if command == 'chat':
            return cmd_chat(client, args)
        if command == 'search':
            return cmd_search(client, args)
        if command in {'pull', 'download'}:
            return cmd_pull(client, args)
        if command == 'downloads':
            return cmd_downloads(client, args)
        if command == 'logs':
            return cmd_logs(client, args)
        if command in {'hardware', 'gpu', 'gpus'}:
            return cmd_hardware(client, as_json=args.json)
        if command == 'stats':
            return cmd_stats(client, as_json=args.json)
        if command == 'report':
            return cmd_report(client, as_json=args.json)
        if command == 'api':
            return cmd_api(client, args)
        if command in {'open', 'ui'}:
            return cmd_open(client)
        if command == 'embed':
            return cmd_embed(client, args)
        if command in {'delete', 'rm'}:
            return cmd_delete(client, args)
        if command == 'nodes':
            return cmd_nodes(client, args)
        if command in {'settings', 'config'}:
            return cmd_settings(client, args)
        return fail(f'Unknown command {command!r}. Run: dflash help')
    except ConsoleError as exc:
        return fail(str(exc))
    except ValueError as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        return fail('Canceled', 130)


if __name__ == '__main__':
    raise SystemExit(main())
