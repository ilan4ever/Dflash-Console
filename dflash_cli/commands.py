"""CLI command implementations."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from core.config import PACKAGE_ROOT, ROOT, ensure_console_data_root, is_embedding_server, is_source_checkout
from dflash_cli import __version__
from dflash_cli.http import ConsoleClient, ConsoleError
from dflash_cli.render import emit, emit_json, fail, format_table, yes_no
from dflash_cli.resolve import pick_engine, pick_model, pick_node, pick_runtime

BIN_DIR = ROOT / 'bin'


def cmd_help() -> int:
    emit(MAIN_HELP)
    return 0


def cmd_version(client: ConsoleClient, *, as_json: bool) -> int:
    payload = {'cli': __version__, 'url': client.base_url}
    try:
        health = client.get('/api/health')
        payload['server'] = health.get('version')
        payload['app'] = health.get('app')
        payload['online'] = True
    except ConsoleError:
        payload['online'] = False
    if as_json:
        return emit_json(payload)
    emit(f"dflash {payload['cli']}")
    if payload.get('online'):
        emit(f"server {payload.get('server')}  {client.base_url}")
    else:
        emit(f'server offline  {client.base_url}')
    return 0


def cmd_status(client: ConsoleClient, *, as_json: bool) -> int:
    health = client.get('/api/health')
    loaded = client.get('/api/status/loaded')
    if as_json:
        return emit_json({'health': health, 'loaded': loaded})
    emit(f"{health.get('app')} v{health.get('version')}  online")
    emit(f"url     {client.base_url}")
    emit(f"loaded  {loaded.get('count') or 0}")
    return 0


def cmd_list(client: ConsoleClient, args: Any) -> int:
    sources = _list_source_filters(args)
    query: dict[str, Any] = {
        'quick': 1 if args.quick else 0,
        'refresh': 1 if args.refresh else 0,
    }
    if len(sources) == 1:
        query['source'] = sources[0]
    data = client.get('/api/models', **query)
    models = list(data.get('models') or [])
    if len(sources) > 1:
        from core.local_models import model_matches_source

        models = [row for row in models if any(model_matches_source(row, source) for source in sources)]
    if args.loaded:
        loaded_ids = _loaded_ids(client)
        models = [row for row in models if _model_key(row) in loaded_ids]
    if args.type and args.type != 'all':
        models = [row for row in models if str(row.get('modality') or 'llm') == args.type]
    if args.filter:
        needle = str(args.filter).lower()
        models = [
            row for row in models
            if needle in ' '.join(
                str(row.get(key) or '') for key in ('id', 'label', 'filename', 'path', 'publisher', 'source')
            ).lower()
        ]
    if args.json:
        return emit_json({'success': True, 'count': len(models), 'models': models})
    if not models:
        emit('No models match.')
        return 0
    rows = [{
        'name': row.get('label') or row.get('id'),
        'id': row.get('id') or row.get('server_id') or '',
        'source': _source_label(row),
        'size': _size(row.get('size_gb')),
        'type': row.get('modality') or 'llm',
        'quant': row.get('quant') or '-',
        'ready': yes_no(row.get('loadable')),
    } for row in models]
    emit(format_table(rows, [
        ('NAME', 'name'), ('ID', 'id'), ('SOURCE', 'source'),
        ('SIZE', 'size'), ('TYPE', 'type'), ('QUANT', 'quant'), ('READY', 'ready'),
    ]))
    if not args.quiet:
        emit(f'\n{len(rows)} model{"s" if len(rows) != 1 else ""}')
    return 0


def _list_source_filters(args: Any) -> list[str]:
    sources: list[str] = []
    if getattr(args, 'ollama', False):
        sources.append('ollama')
    if getattr(args, 'lmstudio', False):
        sources.append('lmstudio')
    if getattr(args, 'dflash', False):
        sources.append('dflash')
    if getattr(args, 'vllm', False):
        sources.append('vllm')
    if getattr(args, 'transformers', False):
        sources.append('transformers')
    extra = str(getattr(args, 'source', '') or '').strip()
    if extra and extra.lower() != 'all':
        sources.append(extra)
    return sources


def _source_label(row: dict[str, Any]) -> str:
    raw = str(row.get('source') or '').strip().lower()
    if row.get('dflash_stack') or raw in {'dflash', 'dflash-profile', 'dflash-stack'}:
        return 'dflash'
    if raw == 'lmstudio':
        return 'lmstudio'
    if raw == 'ollama':
        return 'ollama'
    return raw or 'library'


def cmd_ps(client: ConsoleClient, *, as_json: bool) -> int:
    data = client.get('/api/status/loaded')
    if as_json:
        return emit_json(data)
    rows = []
    for row in data.get('loaded') or []:
        rows.append({
            'name': row.get('label') or row.get('active_model') or row.get('server_id'),
            'kind': row.get('kind') or row.get('runtime_id') or 'engine',
            'model': row.get('active_model_id') or row.get('active_model') or '-',
            'status': row.get('status') or '-',
            'url': row.get('api_url') or '',
        })
    if not rows:
        emit('Nothing loaded.')
        return 0
    emit(format_table(rows, [
        ('NAME', 'name'), ('KIND', 'kind'), ('MODEL', 'model'),
        ('STATUS', 'status'), ('URL', 'url'),
    ]))
    return 0


def cmd_engines(client: ConsoleClient, *, as_json: bool) -> int:
    data = client.get('/api/servers')
    servers = list(data.get('servers') or data.get('all_servers') or [])
    if as_json:
        return emit_json(data)
    rows = [{
        'name': row.get('label') or row.get('id'),
        'id': row.get('id'),
        'port': row.get('port'),
        'status': row.get('status') or ('on' if row.get('enabled', True) else 'off'),
        'model': row.get('active_model_id') or row.get('model_id') or '-',
    } for row in servers]
    if not rows:
        emit('No engines configured.')
        return 0
    emit(format_table(rows, [
        ('NAME', 'name'), ('ID', 'id'), ('PORT', 'port'),
        ('STATUS', 'status'), ('MODEL', 'model'),
    ]))
    return 0


def cmd_runtimes(client: ConsoleClient, *, as_json: bool) -> int:
    data = client.get('/api/runtimes')
    if as_json:
        return emit_json(data)
    rows = []
    for row in data.get('runtimes') or []:
        rows.append({
            'name': row.get('label') or row.get('id'),
            'id': row.get('runtime_id') or row.get('id'),
            'status': 'running' if row.get('running') else 'ready',
            'model': row.get('active_model') or '-',
        })
    if not rows:
        emit('No extra runtimes.')
        return 0
    emit(format_table(rows, [
        ('NAME', 'name'), ('ID', 'id'), ('STATUS', 'status'), ('MODEL', 'model'),
    ]))
    return 0


def cmd_show(client: ConsoleClient, name: str, *, as_json: bool) -> int:
    model = pick_model(name, client.get('/api/models', quick=1).get('models') or [])
    if as_json:
        return emit_json(model)
    emit(model.get('label') or model.get('id'))
    for key, title in (
        ('id', 'id'),
        ('path', 'path'),
        ('modality', 'type'),
        ('quant', 'quant'),
        ('size_gb', 'size'),
        ('server_id', 'engine'),
        ('source', 'source'),
        ('modified', 'updated'),
    ):
        value = model.get(key)
        if value not in (None, ''):
            emit(f'  {title:8} {value}{" GB" if key == "size_gb" else ""}')
    return 0


def cmd_load(client: ConsoleClient, name: str, args: Any) -> int:
    model = pick_model(name, client.get('/api/models', quick=1).get('models') or [])
    body: dict[str, Any] = {'path': model.get('path'), 'model_id': model.get('id')}
    if args.engine:
        engine = pick_engine(args.engine, _engines(client))
        body['server_id'] = engine.get('id')
    elif model.get('server_id'):
        body['server_id'] = model.get('server_id')
    result = client.post('/api/models/load', body)
    if args.json:
        return emit_json(result)
    emit(f"Loaded {model.get('label') or name}")
    return 0


def cmd_unload(client: ConsoleClient, name: str, *, as_json: bool) -> int:
    engines = _engines(client)
    try:
        engine = pick_engine(name, engines)
        result = client.post(f"/api/servers/{engine['id']}/unload")
    except ValueError:
        runtime = pick_runtime(name, (client.get('/api/runtimes').get('runtimes') or []))
        result = client.post(f"/api/runtimes/{runtime.get('runtime_id') or runtime.get('id')}/unload")
    if as_json:
        return emit_json(result)
    emit(f'Unloaded {name}')
    return 0


def cmd_start(client: ConsoleClient, name: str, *, as_json: bool) -> int:
    engine = pick_engine(name, _engines(client, all_servers=True))
    result = client.post(f"/api/servers/{engine['id']}/start")
    if as_json:
        return emit_json(result)
    emit(f"Started {engine.get('label') or engine.get('id')}")
    return 0


def cmd_stop(client: ConsoleClient, name: str, *, as_json: bool) -> int:
    engine = pick_engine(name, _engines(client, all_servers=True))
    result = client.post(f"/api/servers/{engine['id']}/stop")
    if as_json:
        return emit_json(result)
    emit(f"Stopped {engine.get('label') or engine.get('id')}")
    return 0


def cmd_chat(client: ConsoleClient, args: Any) -> int:
    prompt = ' '.join(args.prompt).strip()
    if not prompt:
        return fail('Usage: dflash chat [-e ENGINE] "your message"')
    engine_id = args.engine
    if not engine_id:
        loaded = client.get('/api/status/loaded').get('engines') or []
        if loaded:
            engine_id = loaded[0].get('server_id')
        else:
            engines = _engines(client)
            engine_id = (engines[0] or {}).get('id') if engines else ''
    if not engine_id:
        return fail('No engine available. Start one with: dflash start <engine>')
    engine = pick_engine(str(engine_id), _engines(client, all_servers=True))
    body = {
        'model': engine.get('id'),
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'max_tokens': args.max_tokens,
    }
    result = client.post(
        f"/api/servers/{engine['id']}/v1/chat/completions",
        body,
        timeout=max(30.0, float(args.timeout or 30)),
    )
    if args.json:
        return emit_json(result)
    text = ''
    choices = result.get('choices') if isinstance(result, dict) else None
    if choices:
        text = ((choices[0] or {}).get('message') or {}).get('content') or ''
    emit(text or str(result))
    return 0


def cmd_search(client: ConsoleClient, args: Any) -> int:
    data = client.get(
        '/api/hf/search',
        q=args.query,
        limit=args.limit,
        category=args.category,
        sort=args.sort,
    )
    models = list(data.get('models') or [])
    if args.json:
        return emit_json(data)
    rows = [{
        'name': row.get('label') or row.get('id'),
        'repo': row.get('id'),
        'size': row.get('size_label') or _size(row.get('size_gb')),
        'downloads': row.get('downloads_label') or row.get('downloads') or '-',
        'local': yes_no(row.get('local_ready')),
    } for row in models]
    if not rows:
        emit('No catalog matches.')
        return 0
    emit(format_table(rows, [
        ('NAME', 'name'), ('REPO', 'repo'), ('SIZE', 'size'),
        ('DOWNLOADS', 'downloads'), ('LOCAL', 'local'),
    ]))
    if not args.quiet:
        emit('\nDownload a file with:  dflash pull <repo> --file <filename>')
    return 0


def cmd_pull(client: ConsoleClient, args: Any) -> int:
    target = str(args.target or '').strip()
    if not target:
        return fail('Usage: dflash pull <repo-or-search> [--file NAME]')
    if args.install or '/' not in target:
        body = {
            'query': target if '/' not in target else None,
            'repo_id': target if '/' in target else None,
            'filename': args.file,
            'load': bool(args.load),
            'wait': True,
            'library_id': args.library,
        }
        result = client.post('/api/hf/install', {key: value for key, value in body.items() if value is not None}, timeout=3600)
        if args.json:
            return emit_json(result)
        emit(result.get('message') or f'Installed {target}')
        return 0
    if not args.file:
        detail = client.get(f'/api/hf/models/{target}')
        files = list(detail.get('download_files') or detail.get('gguf_files') or [])
        if args.json:
            return emit_json(detail)
        if not files:
            return fail(f'No downloadable files in {target}. Pass --file NAME.')
        emit(f'{target} files:')
        for item in files[:20]:
            if isinstance(item, dict):
                emit(f"  {item.get('filename') or item.get('name')}  {item.get('size_label') or ''}")
            else:
                emit(f'  {item}')
        emit('\nThen:  dflash pull {target} --file <filename>')
        return 0
    result = client.post('/api/hf/download', {
        'repo_id': target,
        'filename': args.file,
        'library_id': args.library,
    })
    if args.json:
        return emit_json(result)
    job_id = result.get('job_id')
    emit(f"Downloading {args.file}  job {job_id}")
    if args.wait and job_id:
        return _wait_job(client, str(job_id), as_json=False)
    return 0


def cmd_downloads(client: ConsoleClient, args: Any) -> int:
    data = client.get('/api/hf/downloads', active=1 if args.active else 0, discover=1)
    jobs = list(data.get('jobs') or [])
    if args.active:
        jobs = [job for job in jobs if job.get('status') == 'downloading']
    elif args.history:
        jobs = [job for job in jobs if job.get('status') != 'downloading']
    if args.range and args.range != 'all':
        days = float(args.range)
        cutoff = time.time() - days * 86400
        jobs = [job for job in jobs if float(job.get('finished_at') or job.get('started_at') or 0) >= cutoff]
    if args.json:
        return emit_json({'success': True, 'jobs': jobs, 'count': len(jobs)})
    if args.clear:
        result = client.delete('/api/hf/downloads')
        emit(f"Cleared {result.get('cleared') or 0} history items")
        return 0
    if args.remove:
        result = client.delete(f'/api/hf/downloads/{args.remove}')
        emit(f"Removed {result.get('cleared') or args.remove}")
        return 0
    rows = [{
        'name': job.get('filename') or job.get('repo_id'),
        'repo': job.get('repo_id') or '',
        'status': 'downloading' if job.get('status') == 'downloading' else (job.get('origin') or job.get('status')),
        'progress': f"{int(job.get('progress') or 0)}%" if job.get('status') == 'downloading' else '-',
    } for job in jobs]
    if not rows:
        emit('No downloads.')
        return 0
    emit(format_table(rows, [
        ('NAME', 'name'), ('REPO', 'repo'), ('STATUS', 'status'), ('PROGRESS', 'progress'),
    ]))
    return 0


def cmd_logs(client: ConsoleClient, args: Any) -> int:
    if args.engine:
        engine = pick_engine(args.engine, _engines(client, all_servers=True))
        data = client.get(f"/api/logs/{engine['id']}", tail=args.tail)
        lines = data.get('lines') or []
    else:
        data = client.get('/api/console/logs', tail=args.tail, errors_only=1 if args.errors else 0)
        lines = data.get('lines') or data.get('log') or []
    if args.json:
        return emit_json(data)
    if isinstance(lines, str):
        emit(lines)
        return 0
    for line in lines[-args.tail:]:
        emit(str(line).rstrip())
    return 0


def cmd_hardware(client: ConsoleClient, *, as_json: bool) -> int:
    data = client.get('/api/hardware')
    if as_json:
        return emit_json(data)
    gpus = data.get('gpus') or []
    for gpu in gpus:
        emit(
            f"{gpu.get('display_name') or gpu.get('name')}  "
            f"VRAM {gpu.get('vram_used_gb') or 0}/{gpu.get('vram_total_gb') or 0} GB  "
            f"{gpu.get('vram_percent') or 0}%"
        )
    if not gpus:
        emit('No GPUs reported.')
    return 0


def cmd_stats(client: ConsoleClient, *, as_json: bool) -> int:
    data = client.get('/api/system-stats')
    if as_json:
        return emit_json(data)
    emit(f"CPU {data.get('cpu_percent')}%   RAM {data.get('ram_percent')}%  ({data.get('ram_used_gb')} / {data.get('ram_total_gb')} GB)")
    for gpu in data.get('gpus') or []:
        emit(
            f"{gpu.get('display_name') or gpu.get('name')}  "
            f"load {gpu.get('load_percent')}%  "
            f"VRAM {gpu.get('vram_used_gb')}/{gpu.get('vram_total_gb')} GB"
        )
    return 0


def cmd_report(client: ConsoleClient, *, as_json: bool) -> int:
    data = client.get('/api/status/report')
    if as_json:
        return emit_json(data)
    system = data.get('system') or {}
    emit(f"CPU {system.get('cpu_percent')}%   RAM {system.get('ram_percent')}%")
    loaded = (data.get('loaded') or {}).get('count') or 0
    engines = len((data.get('engines') or {}).get('servers') or [])
    emit(f'engines {engines}   loaded {loaded}')
    return 0


def cmd_api(client: ConsoleClient, args: Any) -> int:
    method = str(args.method or 'GET').upper()
    path = args.path if str(args.path).startswith('/') else '/' + str(args.path)
    body = None
    if args.body:
        import json
        body = json.loads(args.body)
    result = client.request(method, path, body=body)
    return emit_json(result)


def cmd_open(client: ConsoleClient) -> int:
    webbrowser.open(client.base_url + '/')
    emit(client.base_url + '/')
    return 0


def cmd_embed(client: ConsoleClient, args: Any) -> int:
    texts = [part for part in (args.text or []) if str(part).strip()]
    if args.file:
        path = Path(str(args.file)).expanduser()
        if not path.is_file():
            return fail(f'File not found: {path}')
        texts.extend(
            line.strip()
            for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()
        )
    if not texts:
        return fail('Usage: dflash embed [-e ENGINE] "text"   or   dflash embed --file notes.txt')
    engine = _pick_embed_engine(client, getattr(args, 'engine', None))
    result = client.post(
        f"/api/servers/{engine['id']}/v1/embeddings",
        {'input': texts if len(texts) > 1 else texts[0], 'model': engine.get('id')},
        timeout=max(30.0, float(args.timeout or 30)),
    )
    if args.json:
        return emit_json(result)
    rows = result.get('data') or []
    emit(f"{engine.get('label') or engine.get('id')}  {len(rows)} vector{'s' if len(rows) != 1 else ''}")
    for row in rows:
        vector = row.get('embedding') or []
        preview = ', '.join(f'{float(value):.4f}' for value in vector[:8])
        extra = f' … +{len(vector) - 8}' if len(vector) > 8 else ''
        emit(f"  [{row.get('index', 0)}] dim {len(vector)}  [{preview}{extra}]")
    return 0


def cmd_delete(client: ConsoleClient, args: Any) -> int:
    model = pick_model(args.name, client.get('/api/models').get('models') or [])
    label = str(model.get('label') or model.get('id') or args.name)
    if not args.yes:
        answer = input(f'Delete {label} from disk? [y/N] ').strip().lower()
        if answer not in {'y', 'yes'}:
            emit('Canceled')
            return 0
    query = {
        'path': model.get('path') or '',
        'source': model.get('source') or '',
        'model_id': model.get('id') or '',
        'server_id': model.get('server_id') or '',
    }
    result = client.delete('/api/models/file', **query)
    if args.json:
        return emit_json(result)
    emit(f"Deleted {result.get('model') or label}")
    return 0


def cmd_nodes(client: ConsoleClient, args: Any) -> int:
    action = str(args.action or 'list').lower()
    if action == 'add':
        url = str(args.target or '').strip()
        if not url:
            return fail('Usage: dflash nodes add http://host:8900 [--label NAME] [--token TOKEN]')
        label = str(args.label or '').strip() or _label_from_url(url)
        body: dict[str, Any] = {'label': label, 'base_url': url, 'enabled': True}
        if args.token:
            body['api_token'] = args.token
        result = client.post('/api/nodes', body)
        if args.json:
            return emit_json(result)
        node = result.get('node') or {}
        emit(f"Added {node.get('label') or label}  {node.get('base_url') or url}")
        return 0
    if action in {'remove', 'rm'}:
        name = str(args.target or args.label or '').strip()
        if not name:
            return fail('Usage: dflash nodes remove <name>')
        node = pick_node(name, client.get('/api/nodes').get('nodes') or [])
        result = client.delete(f"/api/nodes/{node['id']}")
        if args.json:
            return emit_json(result)
        emit(f"Removed {node.get('label') or node.get('id')}")
        return 0
    if action == 'health':
        name = str(args.target or '').strip()
        if not name:
            return fail('Usage: dflash nodes health <name>')
        node = pick_node(name, client.get('/api/nodes').get('nodes') or [])
        result = client.post(f"/api/nodes/{node['id']}/health")
        if args.json:
            return emit_json(result)
        status = 'online' if result.get('online') else (result.get('status') or 'offline')
        emit(f"{node.get('label') or node.get('id')}  {status}  {result.get('remote_version') or ''}".rstrip())
        return 0
    data = client.get('/api/nodes', fresh=1 if args.fresh else 0)
    if args.json:
        return emit_json(data)
    rows = [{
        'name': row.get('label') or row.get('id'),
        'id': row.get('id') or '',
        'url': row.get('base_url') or '',
        'status': row.get('status') or ('online' if row.get('online') else 'offline'),
        'version': row.get('remote_version') or '-',
    } for row in data.get('nodes') or []]
    if not rows:
        emit('No remote nodes. Add one with:  dflash nodes add http://host:8900 --label NAME')
        return 0
    emit(format_table(rows, [
        ('NAME', 'name'), ('ID', 'id'), ('URL', 'url'),
        ('STATUS', 'status'), ('VERSION', 'version'),
    ]))
    return 0


def cmd_settings(client: ConsoleClient, args: Any) -> int:
    if args.set_pair:
        key, value = _parse_setting_pair(args.set_pair)
        body = _settings_patch_body(key, value)
        result = client.request('PUT', '/api/config', body=body)
        if args.json:
            return emit_json(result)
        emit(f'Set {key} = {value}')
        return 0
    data = client.get('/api/config')
    config = data.get('config') or {}
    if args.get_key:
        value = _settings_get(config, args.get_key)
        if args.json:
            return emit_json({'key': args.get_key, 'value': value})
        emit(_format_setting(value))
        return 0
    summary = _settings_summary(config)
    if args.json:
        return emit_json(summary)
    for key, value in summary.items():
        emit(f'{key:24} {_format_setting(value)}')
    return 0


def cmd_serve(args: Any) -> int:
    port = int(args.port or 8900)
    if _already_up(port):
        emit(f'Already running at http://127.0.0.1:{port}')
        return 0
    root = ensure_console_data_root()
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PACKAGE_ROOT) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env['DFLASH_CONSOLE_ROOT'] = str(root)
    if not env.get('DFLASH_ROOT'):
        env['DFLASH_ROOT'] = str(root)
    emit(f'Starting DFlash Console on http://127.0.0.1:{port}')
    emit(f'data    {root}')
    return subprocess.call(
        [sys.executable, '-m', 'uvicorn', 'api.app:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=str(root),
        env=env,
    )


def cmd_install(*, as_json: bool) -> int:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    added_path = _add_user_path(BIN_DIR)
    shim = _install_windowsapps_shim()
    profile = _install_powershell_profile()
    _add_current_process_path(BIN_DIR)
    if shim:
        _add_current_process_path(shim.parent)
    payload = {
        'bin': str(BIN_DIR),
        'path_added': added_path,
        'shim': str(shim) if shim else '',
        'profile': str(profile) if profile else '',
    }
    if as_json:
        return emit_json(payload)
    emit('dflash is ready in this PowerShell window.')
    emit('Try:  dflash list')
    if profile:
        emit(f'Also registered in your PowerShell profile: {profile}')
    return 0


_SETTINGS_KEYS = {
    'ui_port',
    'gateway_port',
    'gateway_server_id',
    'dflash_root',
    'models_root',
    'cpu_slow_warn',
    'runtime_stop_others_on_load',
    'context_auto_grow',
    'context_max',
}
_SETTINGS_NESTED = {
    'download_settings': {'parallel_connections'},
    'hardware_settings': {
        'gpu_strategy',
        'max_vram_usage_gb',
        'limit_offload_dedicated_vram',
        'offload_kv_cache_to_gpu',
    },
}


def _pick_embed_engine(client: ConsoleClient, name: str | None) -> dict[str, Any]:
    engines = _engines(client, all_servers=True)
    embedders = [row for row in engines if is_embedding_server(row)]
    if name:
        return pick_engine(name, embedders or engines)
    if embedders:
        return embedders[0]
    raise ValueError('No embedding engine. Load one with: dflash load <embed-model>')


def _label_from_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url if '://' in url else f'http://{url}')
    host = parsed.hostname or url
    port = parsed.port
    return f'{host}:{port}' if port else host


def _parse_setting_pair(raw: str) -> tuple[str, Any]:
    if '=' not in str(raw):
        raise ValueError('Usage: dflash settings --set KEY=VALUE')
    key, text = str(raw).split('=', 1)
    key = key.strip()
    if not key:
        raise ValueError('Usage: dflash settings --set KEY=VALUE')
    return key, _parse_setting_value(text.strip())


def _parse_setting_value(text: str) -> Any:
    lowered = text.lower()
    if lowered in {'true', 'yes', 'on'}:
        return True
    if lowered in {'false', 'no', 'off'}:
        return False
    try:
        if text.startswith(('+', '-')) or text.isdigit():
            return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _settings_patch_body(key: str, value: Any) -> dict[str, Any]:
    if key in _SETTINGS_KEYS:
        return {key: value}
    if '.' in key:
        parent, child = key.split('.', 1)
        allowed = _SETTINGS_NESTED.get(parent)
        if allowed and child in allowed:
            return {parent: {child: value}}
    raise ValueError(
        f'Unknown setting {key!r}. Try: ui_port, gateway_port, dflash_root, '
        'download_settings.parallel_connections'
    )


def _settings_get(config: dict[str, Any], key: str) -> Any:
    if _is_secret_setting(key):
        return '(hidden)'
    if '.' in key:
        parent, child = key.split('.', 1)
        row = config.get(parent)
        if isinstance(row, dict):
            return row.get(child)
        return None
    return config.get(key)


def _settings_summary(config: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        'ui_port',
        'gateway_port',
        'dflash_root',
        'models_root',
        'gateway_server_id',
        'context_auto_grow',
        'context_max',
    ):
        if key in config:
            summary[key] = config.get(key)
    hardware = config.get('hardware_settings') or {}
    if isinstance(hardware, dict) and hardware.get('gpu_strategy'):
        summary['hardware_settings.gpu_strategy'] = hardware.get('gpu_strategy')
    downloads = config.get('download_settings') or {}
    if isinstance(downloads, dict) and downloads.get('parallel_connections') is not None:
        summary['download_settings.parallel_connections'] = downloads.get('parallel_connections')
    summary['engines'] = len(config.get('servers') or [])
    summary['nodes'] = len(config.get('remote_nodes') or [])
    summary['libraries'] = len(config.get('model_libraries') or [])
    return summary


def _format_setting(value: Any) -> str:
    if value is None:
        return '-'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return str(value)


def _is_secret_setting(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ('token', 'password', 'secret'))


def _engines(client: ConsoleClient, *, all_servers: bool = False) -> list[dict[str, Any]]:
    data = client.get('/api/servers/profiles')
    key = 'all_servers' if all_servers else 'servers'
    return list(data.get(key) or data.get('servers') or [])


def _loaded_ids(client: ConsoleClient) -> set[str]:
    keys: set[str] = set()
    for row in client.get('/api/status/loaded').get('loaded') or []:
        for key in ('server_id', 'id', 'active_model_id', 'active_model', 'label'):
            value = str(row.get(key) or '').strip().lower()
            if value:
                keys.add(value)
    return keys


def _model_key(row: dict[str, Any]) -> str:
    return str(row.get('id') or row.get('server_id') or row.get('label') or '').strip().lower()


def _size(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '-'
    if number <= 0:
        return '-'
    if number < 1:
        return f'{int(number * 1024)} MB'
    return f'{number:.1f} GB'


def _wait_job(client: ConsoleClient, job_id: str, *, as_json: bool) -> int:
    while True:
        data = client.get(f'/api/hf/download/{job_id}')
        job = data.get('job') or data
        status = str(job.get('status') or '')
        if as_json:
            emit_json(job)
        else:
            emit(f"{status}  {job.get('progress') or 0}%  {job.get('filename') or ''}")
        if status in {'done', 'error'}:
            return 0 if status == 'done' else fail(job.get('error') or 'download failed')
        time.sleep(1.5)


def _already_up(port: int) -> bool:
    try:
        ConsoleClient(f'http://127.0.0.1:{port}', timeout=2).get('/api/health')
        return True
    except ConsoleError:
        return False


def _cmd_shim_text() -> str:
    if is_source_checkout():
        root = str(ROOT.resolve())
        return (
            '@echo off\r\n'
            'setlocal\r\n'
            f'set "ROOT={root}"\r\n'
            'set "PYTHONPATH=%ROOT%"\r\n'
            'if not defined DFLASH_ROOT set "DFLASH_ROOT=%ROOT%"\r\n'
            'python -m dflash_cli %*\r\n'
            'exit /b %ERRORLEVEL%\r\n'
        )
    return (
        '@echo off\r\n'
        'setlocal\r\n'
        f'"{sys.executable}" -m dflash_cli %*\r\n'
        'exit /b %ERRORLEVEL%\r\n'
    )


def _profile_function_text() -> str:
    if is_source_checkout():
        root = str(ROOT.resolve()).replace("'", "''")
        return (
            '# BEGIN DFLASH CLI\n'
            'function dflash {\n'
            '    $env:PYTHONPATH = \'' + root + '\'\n'
            '    if (-not $env:DFLASH_ROOT) { $env:DFLASH_ROOT = \'' + root + '\' }\n'
            '    & python -m dflash_cli @args\n'
            '}\n'
            '# END DFLASH CLI\n'
        )
    exe = str(Path(sys.executable).resolve()).replace("'", "''")
    return (
        '# BEGIN DFLASH CLI\n'
        'function dflash {\n'
        f"    & '{exe}' -m dflash_cli @args\n"
        '}\n'
        '# END DFLASH CLI\n'
    )


def _install_windowsapps_shim() -> Path | None:
    if os.name != 'nt':
        return None
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    folder = Path(local) / 'Microsoft' / 'WindowsApps'
    folder.mkdir(parents=True, exist_ok=True)
    shim = folder / 'dflash.cmd'
    shim.write_text(_cmd_shim_text(), encoding='utf-8')
    return shim


def _install_powershell_profile() -> Path | None:
    if os.name != 'nt':
        return None
    documents = Path.home() / 'Documents'
    profiles = [
        documents / 'PowerShell' / 'Microsoft.PowerShell_profile.ps1',
        documents / 'WindowsPowerShell' / 'Microsoft.PowerShell_profile.ps1',
    ]
    block = _profile_function_text()
    written: Path | None = None
    for profile in profiles:
        profile.parent.mkdir(parents=True, exist_ok=True)
        current = profile.read_text(encoding='utf-8') if profile.is_file() else ''
        if '# BEGIN DFLASH CLI' in current and '# END DFLASH CLI' in current:
            start = current.index('# BEGIN DFLASH CLI')
            end = current.index('# END DFLASH CLI') + len('# END DFLASH CLI')
            current = current[:start].rstrip() + '\n\n' + block + current[end:].lstrip('\n')
        else:
            current = current.rstrip() + ('\n\n' if current.strip() else '') + block
        profile.write_text(current, encoding='utf-8')
        written = profile
    return written


def _add_current_process_path(folder: Path) -> None:
    target = str(folder.resolve())
    current = os.environ.get('PATH') or ''
    parts = [part for part in current.split(os.pathsep) if part]
    if any(os.path.normcase(part) == os.path.normcase(target) for part in parts):
        return
    os.environ['PATH'] = target + os.pathsep + current


def _add_user_path(folder: Path) -> bool:
    target = str(folder.resolve())
    if os.name != 'nt':
        emit(f'Add this folder to PATH: {target}', err=True)
        return False
    import winreg

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
    try:
        current, _kind = winreg.QueryValueEx(key, 'Path')
    except FileNotFoundError:
        current = ''
    parts = [part for part in str(current).split(';') if part]
    if any(os.path.normcase(part) == os.path.normcase(target) for part in parts):
        return False
    parts.append(target)
    winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, ';'.join(parts))
    return True


MAIN_HELP = '''DFlash Console CLI

Talk to the local Console server from PowerShell, cmd, or any terminal.

Usage:
  dflash <command> [flags]

Getting started
  dflash status              Server health
  dflash list                Local models
  dflash ps                  Models loaded right now
  dflash engines             Engine profiles
  dflash chat "hello"        Send a prompt

Models
  dflash list [--ollama] [--lmstudio] [--dflash] [--vllm] [--transformers] [--source NAME]
  dflash list [--loaded] [--type llm] [--filter qwen] [--refresh]
  dflash show <name>         Details for one model
  dflash load <name>         Load a model
  dflash unload <name>       Unload an engine or runtime
  dflash start <engine>      Start an engine
  dflash stop <engine>       Stop an engine
  dflash delete <name>       Remove a local model from disk
  dflash embed "text"        Turn text into vectors

Catalog
  dflash search <query>      Search Hugging Face
  dflash pull <repo> --file <name.gguf>
  dflash pull "qwen 3.8" --install
  dflash downloads [--active] [--range 7]

Machine
  dflash hardware            GPUs
  dflash stats               CPU, RAM, VRAM
  dflash report              Full status
  dflash logs [--engine NAME] [--errors]
  dflash nodes               Remote Consoles
  dflash settings            Show or change settings

Server
  dflash serve               Start the Console if it is not running
  dflash open                Open the UI
  dflash api GET /api/health Raw HTTP helper
  dflash install             Add `dflash` to your user PATH

Global flags
  -u, --url URL              Console URL (or $env:DFLASH_URL)
  -p, --port N               Port (default 8900)
  -j, --json                 JSON output
  -q, --quiet                Less text
  --timeout SEC              HTTP timeout

Names can be short. `dflash load gemma` matches Gemma 31B if that is unique.
The Console must be running, except for: help, version, serve, install.
'''
