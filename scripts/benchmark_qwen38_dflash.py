#!/usr/bin/env python3
"""Benchmark Qwen3.8-27B autoregressive vs DFlash 1 vs DFlash 2 decode speed.

Runs the same target checkpoint three ways through DFlash Console:
  1. Autoregressive (no draft accelerator)
  2. DFlash 1 speculative decoding
  3. DFlash 2 speculative decoding

Requires the Console API (default http://127.0.0.1:8900) and llama-server engines.
Only one 27B load runs at a time — each mode unloads before the next starts.

Example:
  python scripts/benchmark_qwen38_dflash.py
  python scripts/benchmark_qwen38_dflash.py --runs 5 --max-tokens 512
  python scripts/benchmark_qwen38_dflash.py --list-paths
  python scripts/benchmark_qwen38_dflash.py --dflash2-draft "C:\\path\\to\\Qwen3.8-27B-DFlash2.gguf"
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONSOLE = 'http://127.0.0.1:8900'
DEFAULT_AR_SERVER = 'qwen3-8-27b-q6-k-l'
DEFAULT_DFLASH_SERVER = 'qwen3-8-27b-q6-k-l-dflash'
DEFAULT_MODEL_ID = 'qwen3.8-27b-q6-k-l'
DEFAULT_PROMPT = (
    'Write a detailed technical explanation of GPU matrix multiplication, memory bandwidth, '
    'KV cache layout, and batching strategies for large language model inference. '
    'Use clear sections and continue until you reach the token limit.'
)


@dataclass
class BenchPaths:
    target_path: Path
    dflash1_draft: Path
    dflash2_draft: Path | None
    model_id: str
    ar_server_id: str
    dflash_server_id: str


@dataclass
class RunResult:
    tokens: int
    wall_seconds: float
    tokens_per_second: float
    wall_tokens_per_second: float
    source: str


def _request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    data = None
    headers = {'Accept': 'application/json'}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{method} {url} failed ({exc.code}): {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'{method} {url} failed: {exc}') from exc
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if isinstance(payload, dict) and payload.get('success') is False:
        raise RuntimeError(str(payload.get('error') or payload.get('detail') or payload))
    return payload if isinstance(payload, dict) else {'data': payload}


def _health_ok(console_base: str) -> None:
    payload = _request_json('GET', f'{console_base.rstrip("/")}/api/health', timeout=10)
    if not payload.get('success'):
        raise RuntimeError('Console API health check failed')


def _find_server(servers: list[dict[str, Any]], server_id: str) -> dict[str, Any]:
    for row in servers:
        if str(row.get('id') or '') == server_id:
            return row
    raise RuntimeError(f'server id not found in config: {server_id}')


def _discover_target(servers: list[dict[str, Any]]) -> tuple[Path, str]:
    for row in servers:
        path_text = str(row.get('target_path') or '').strip()
        if not path_text:
            continue
        name = Path(path_text).name.lower()
        if 'qwen3.8' in name.replace('_', '.') or 'qwen3-8' in name:
            model_id = str(row.get('model_id') or DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID
            return Path(path_text).resolve(), model_id
    matches = sorted(Path(ROOT / 'models').rglob('*Qwen3.8-27B*.gguf'))
    for candidate in matches:
        lower = candidate.name.lower()
        if 'dflash' in lower or 'dspark' in lower:
            continue
        return candidate.resolve(), DEFAULT_MODEL_ID
    raise RuntimeError(
        'Could not find Qwen3.8-27B target GGUF. Configure a server with target_path or place '
        'the model under models/.',
    )


def _qwen_version(name: str) -> str | None:
    match = re.search(r'qwen3(?:\.(\d+))?', name.lower())
    if not match:
        return None
    return match.group(1) or '0'


def _scan_local_drafts(target: Path, generation: str) -> list[tuple[float, Path]]:
    from core.dflash_generation import infer_dflash_generation
    from core.stack_match import find_local_accelerators, is_accelerator_path, score_accelerator_pair

    rows = find_local_accelerators(target, dflash_generation=generation, limit=12)
    if rows:
        return [(float(row.get('score') or 0), Path(str(row['path'])).resolve()) for row in rows]

    candidates: list[tuple[float, Path]] = []
    models_root = ROOT / 'models'
    if not models_root.is_dir():
        return candidates
    for candidate in sorted(models_root.rglob('*.gguf')):
        if not candidate.is_file() or not is_accelerator_path(candidate):
            continue
        if candidate.resolve() == target.resolve():
            continue
        accel_gen = infer_dflash_generation(candidate)
        if generation == 'dflash1' and accel_gen == 'dflash2':
            continue
        if generation == 'dflash2' and accel_gen != 'dflash2':
            continue
        score = score_accelerator_pair(target, candidate)
        if score > 0:
            candidates.append((score, candidate.resolve()))
    candidates.sort(key=lambda row: (-row[0], row[1].name.lower()))
    return candidates


def _discover_dflash_draft(target: Path, generation: str) -> Path | None:
    rows = _scan_local_drafts(target, generation)
    return rows[0][1] if rows else None


def _discover_dflash1_draft(target: Path) -> Path:
    draft = _discover_dflash_draft(target, 'dflash1')
    if draft is not None:
        return draft
    raise RuntimeError(
        'No DFlash 1 accelerator found. Download a Qwen3.8-27B DFlash draft from '
        'Model catalog → DFlash 1 Accelerator.',
    )


def _discover_dflash2_draft(target: Path) -> Path | None:
    return _discover_dflash_draft(target, 'dflash2')


def _fetch_match_repo_ids(console_base: str, target: Path, generation: str, *, limit: int = 3) -> list[str]:
    query = urllib.parse.urlencode({
        'target_path': str(target),
        'dflash_generation': generation,
    })
    url = f'{console_base.rstrip("/")}/api/stacks/match?{query}'
    try:
        payload = _request_json('GET', url, timeout=45)
    except RuntimeError:
        return []
    repos: list[str] = []
    for row in list(payload.get('hf_suggestions') or [])[:limit]:
        repo_id = str(row.get('id') or '').strip()
        if repo_id:
            repos.append(repo_id)
    return repos


def _draft_pair_notes(target: Path, draft: Path) -> list[str]:
    from core.stack_match import score_accelerator_pair

    notes: list[str] = []
    score = score_accelerator_pair(target, draft)
    target_ver = _qwen_version(target.name)
    draft_ver = _qwen_version(draft.name)
    if target_ver and draft_ver and target_ver != draft_ver:
        notes.append(
            f'Draft/target generation mismatch: draft is Qwen3.{draft_ver}, target is Qwen3.{target_ver} '
            f'(pair score {score:.2f}). Expect lower speedup than a matched Qwen3.{target_ver} accelerator.',
        )
        return notes
    if score < 8.0:
        notes.append(
            f'Draft/target pair score is only {score:.2f}; a better-matched accelerator may decode faster.',
        )
    return notes


def discover_paths(
    *,
    console_base: str,
    ar_server_id: str,
    dflash_server_id: str,
    target_override: str = '',
    dflash1_override: str = '',
    dflash2_override: str = '',
) -> BenchPaths:
    payload = _request_json('GET', f'{console_base.rstrip("/")}/api/servers', timeout=30)
    servers = list(payload.get('servers') or [])
    if target_override.strip():
        target = Path(target_override).expanduser().resolve()
    else:
        target, _ = _discover_target(servers)
    if not target.is_file():
        raise RuntimeError(f'target model not found: {target}')

    dflash1 = Path(dflash1_override).expanduser().resolve() if dflash1_override.strip() else _discover_dflash1_draft(target)
    if not dflash1.is_file():
        raise RuntimeError(f'DFlash 1 draft not found: {dflash1}')

    if dflash2_override.strip():
        dflash2 = Path(dflash2_override).expanduser().resolve()
    else:
        dflash2 = _discover_dflash2_draft(target)
    if dflash2 is not None and not dflash2.is_file():
        raise RuntimeError(f'DFlash 2 draft not found: {dflash2}')

    ar_server = _find_server(servers, ar_server_id)
    dflash_server = _find_server(servers, dflash_server_id)
    model_id = str(ar_server.get('model_id') or dflash_server.get('model_id') or DEFAULT_MODEL_ID).strip()
    return BenchPaths(
        target_path=target,
        dflash1_draft=dflash1,
        dflash2_draft=dflash2,
        model_id=model_id or DEFAULT_MODEL_ID,
        ar_server_id=ar_server_id,
        dflash_server_id=dflash_server_id,
    )


def _wait_chat_ready(console_base: str, server_id: str, *, timeout_s: float = 900.0) -> None:
    deadline = time.time() + timeout_s
    url = f'{console_base.rstrip("/")}/api/servers/{server_id}/chat-ready'
    while time.time() < deadline:
        payload = _request_json('GET', url, timeout=30)
        if payload.get('ready'):
            return
        time.sleep(2.0)
    raise RuntimeError(f'timed out waiting for chat-ready on {server_id}')


def _unload(console_base: str, server_id: str) -> None:
    url = f'{console_base.rstrip("/")}/api/servers/{server_id}/unload'
    try:
        _request_json('POST', url, timeout=180)
    except RuntimeError:
        pass
    time.sleep(2.0)


def _load_ar(console_base: str, paths: BenchPaths) -> None:
    base = console_base.rstrip('/')
    _unload(base, paths.ar_server_id)
    _unload(base, paths.dflash_server_id)
    listen = _request_json('POST', f'{base}/api/servers/{paths.ar_server_id}/listen', timeout=120)
    if not listen.get('success', True):
        raise RuntimeError(str(listen.get('error') or 'listen failed'))
    load = _request_json(
        'POST',
        f'{base}/api/servers/{paths.ar_server_id}/load',
        body={'model_path': str(paths.target_path), 'model_id': paths.model_id},
        timeout=900,
    )
    if not load.get('success', True):
        raise RuntimeError(str(load.get('error') or 'load failed'))
    _wait_chat_ready(base, paths.ar_server_id)


def _replace_draft(console_base: str, server_id: str, draft_path: Path) -> None:
    url = f'{console_base.rstrip("/")}/api/stacks/{server_id}/replace-draft'
    _request_json('POST', url, body={'draft_path': str(draft_path)}, timeout=60)


def _load_dflash(console_base: str, paths: BenchPaths, draft_path: Path) -> None:
    base = console_base.rstrip('/')
    _unload(base, paths.ar_server_id)
    _unload(base, paths.dflash_server_id)
    _replace_draft(base, paths.dflash_server_id, draft_path)
    listen = _request_json('POST', f'{base}/api/servers/{paths.dflash_server_id}/listen', timeout=120)
    if not listen.get('success', True):
        raise RuntimeError(str(listen.get('error') or 'listen failed'))
    load = _request_json('POST', f'{base}/api/servers/{paths.dflash_server_id}/load', timeout=900)
    if not load.get('success', True):
        raise RuntimeError(str(load.get('error') or 'load failed'))
    _wait_chat_ready(base, paths.dflash_server_id)


def _run_completion(
    console_base: str,
    server_id: str,
    *,
    model_id: str,
    prompt: str,
    max_tokens: int,
) -> RunResult:
    url = f'{console_base.rstrip("/")}/api/servers/{server_id}/v1/chat/completions'
    body = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'temperature': 0.7,
        'stream': False,
    }
    started = time.time()
    payload = _request_json('POST', url, body=body, timeout=600)
    wall = max(time.time() - started, 0.001)
    usage = payload.get('usage') if isinstance(payload.get('usage'), dict) else {}
    timings = payload.get('timings') if isinstance(payload.get('timings'), dict) else {}
    tokens = int(usage.get('completion_tokens') or 0)
    tps = float(timings.get('predicted_per_second') or 0.0)
    wall_tps = (tokens / wall) if tokens > 0 else 0.0
    source = 'timings.predicted_per_second'
    if tps <= 0 and tokens > 0:
        tps = wall_tps
        source = 'completion_tokens / wall_time'
    return RunResult(
        tokens=tokens,
        wall_seconds=wall,
        tokens_per_second=tps,
        wall_tokens_per_second=wall_tps,
        source=source,
    )


def _benchmark_mode(
    console_base: str,
    server_id: str,
    *,
    model_id: str,
    prompt: str,
    max_tokens: int,
    runs: int,
    warmup: int,
) -> list[RunResult]:
    results: list[RunResult] = []
    total = warmup + runs
    for index in range(total):
        label = 'warmup' if index < warmup else f'run {index - warmup + 1}/{runs}'
        print(f'    {label}…', flush=True)
        result = _run_completion(
            console_base,
            server_id,
            model_id=model_id,
            prompt=prompt,
            max_tokens=max_tokens,
        )
        if index >= warmup:
            results.append(result)
        print(
            f'      {result.tokens} tok · {result.tokens_per_second:.1f} t/s '
            f'({result.wall_tokens_per_second:.1f} wall) · '
            f'{result.wall_seconds:.1f}s ({result.source})',
            flush=True,
        )
    return results


def _summarize(label: str, results: list[RunResult]) -> dict[str, Any]:
    speeds = [row.tokens_per_second for row in results if row.tokens_per_second > 0]
    wall_speeds = [row.wall_tokens_per_second for row in results if row.wall_tokens_per_second > 0]
    tokens = [row.tokens for row in results]
    if not speeds:
        return {
            'mode': label,
            'runs': len(results),
            'average_tps': 0.0,
            'median_tps': 0.0,
            'average_wall_tps': 0.0,
            'median_wall_tps': 0.0,
            'tokens': tokens,
        }
    return {
        'mode': label,
        'runs': len(results),
        'average_tps': round(statistics.mean(speeds), 2),
        'median_tps': round(statistics.median(speeds), 2),
        'min_tps': round(min(speeds), 2),
        'max_tps': round(max(speeds), 2),
        'average_wall_tps': round(statistics.mean(wall_speeds), 2) if wall_speeds else 0.0,
        'median_wall_tps': round(statistics.median(wall_speeds), 2) if wall_speeds else 0.0,
        'tokens': tokens,
    }


def _print_preflight(
    paths: BenchPaths,
    *,
    console_base: str,
    dflash2_skipped: bool,
) -> None:
    print('\n=== Preflight ===', flush=True)
    print(f'target     : {paths.target_path.name}', flush=True)
    print(f'dflash1    : {paths.dflash1_draft.name}', flush=True)
    for note in _draft_pair_notes(paths.target_path, paths.dflash1_draft):
        print(f'WARNING    : {note}', flush=True)
    dflash1_repos = _fetch_match_repo_ids(console_base, paths.target_path, 'dflash1')
    if dflash1_repos and _qwen_version(paths.dflash1_draft.name) != _qwen_version(paths.target_path.name):
        print(f'suggested  : better DFlash 1 match on Hugging Face: {", ".join(dflash1_repos)}', flush=True)
    if paths.dflash2_draft:
        print(f'dflash2    : {paths.dflash2_draft.name}', flush=True)
        for note in _draft_pair_notes(paths.target_path, paths.dflash2_draft):
            print(f'WARNING    : {note}', flush=True)
    elif dflash2_skipped:
        dflash2_repos = _fetch_match_repo_ids(console_base, paths.target_path, 'dflash2')
        repo_hint = ', '.join(dflash2_repos[:3]) if dflash2_repos else 'Model catalog → DFlash 2 Accelerator'
        print(f'dflash2    : not installed locally — download {repo_hint}', flush=True)


def _print_summary(
    rows: list[dict[str, Any]],
    *,
    dflash2_skipped: bool,
) -> None:
    print('\n=== Qwen3.8-27B decode speed summary ===')
    baseline = next((row['average_wall_tps'] for row in rows if row['mode'] == 'autoregressive'), 0.0) or 0.0
    for row in rows:
        avg = float(row.get('average_tps') or 0)
        wall_avg = float(row.get('average_wall_tps') or 0)
        speedup = (wall_avg / baseline) if baseline > 0 and row['mode'] != 'autoregressive' else 1.0
        extra = f' · {speedup:.2f}x vs AR (wall)' if row['mode'] != 'autoregressive' and baseline > 0 else ''
        print(
            f"{row['mode']:<16} engine {avg:.1f} t/s · wall {wall_avg:.1f} t/s "
            f"({row.get('runs', 0)} runs){extra}",
        )

    dflash1 = next((row for row in rows if row['mode'] == 'dflash1'), None)
    dflash2 = next((row for row in rows if row['mode'] == 'dflash2'), None)
    if dflash1 and dflash2:
        d1 = float(dflash1.get('average_wall_tps') or 0)
        d2 = float(dflash2.get('average_wall_tps') or 0)
        if d1 > 0 and d2 > 0:
            print(f'\nDFlash 2 vs DFlash 1: {d2 / d1:.2f}x wall speed ({d2:.1f} vs {d1:.1f} t/s)')
    elif dflash2_skipped:
        print('\nDFlash 2 was not tested — no local DFlash 2 draft. Install one to compare DFlash 1 vs DFlash 2.')


def run_benchmark(args: argparse.Namespace) -> int:
    console_base = str(args.console_url).rstrip('/')
    _health_ok(console_base)
    paths = discover_paths(
        console_base=console_base,
        ar_server_id=args.ar_server,
        dflash_server_id=args.dflash_server,
        target_override=args.target,
        dflash1_override=args.dflash1_draft,
        dflash2_override=args.dflash2_draft,
    )

    if args.list_paths:
        print(json.dumps({
            'target_path': str(paths.target_path),
            'dflash1_draft': str(paths.dflash1_draft),
            'dflash2_draft': str(paths.dflash2_draft) if paths.dflash2_draft else None,
            'model_id': paths.model_id,
            'ar_server_id': paths.ar_server_id,
            'dflash_server_id': paths.dflash_server_id,
        }, indent=2))
        return 0

    modes: list[tuple[str, Path | None, str]] = [
        ('autoregressive', None, paths.ar_server_id),
        ('dflash1', paths.dflash1_draft, paths.dflash_server_id),
    ]
    dflash2_skipped = not paths.dflash2_draft and 'dflash2' not in args.skip_modes
    if paths.dflash2_draft:
        modes.append(('dflash2', paths.dflash2_draft, paths.dflash_server_id))
    elif dflash2_skipped:
        if args.require_dflash2:
            repos = _fetch_match_repo_ids(console_base, paths.target_path, 'dflash2')
            hint = repos[0] if repos else 'incoai/Qwen3.8-27B-DFlash2-GGUF'
            raise RuntimeError(
                f'DFlash 2 draft is required but not installed. Download {hint} '
                'from Model catalog → DFlash 2 Accelerator, or pass --dflash2-draft.',
            )

    _print_preflight(paths, console_base=console_base, dflash2_skipped=dflash2_skipped)

    summaries: list[dict[str, Any]] = []
    original_dflash_draft: Path | None = None
    try:
        dflash_payload = _request_json('GET', f'{console_base}/api/servers', timeout=30)
        dflash_row = _find_server(list(dflash_payload.get('servers') or []), paths.dflash_server_id)
        draft_text = str(dflash_row.get('draft_path') or '').strip()
        if draft_text:
            original_dflash_draft = Path(draft_text).resolve()

        for mode, draft_path, server_id in modes:
            if mode in args.skip_modes:
                print(f'\nSkipping {mode} (--skip-modes)', flush=True)
                continue
            print(f'\n--- {mode} ---', flush=True)
            if mode == 'autoregressive':
                print(f'  target: {paths.target_path}', flush=True)
                _load_ar(console_base, paths)
            else:
                assert draft_path is not None
                print(f'  target: {paths.target_path}', flush=True)
                print(f'  draft : {draft_path}', flush=True)
                _load_dflash(console_base, paths, draft_path)
            results = _benchmark_mode(
                console_base,
                server_id,
                model_id=paths.model_id,
                prompt=args.prompt,
                max_tokens=args.max_tokens,
                runs=args.runs,
                warmup=args.warmup,
            )
            summaries.append(_summarize(mode, results))
    finally:
        if original_dflash_draft and original_dflash_draft.is_file():
            try:
                _replace_draft(console_base, paths.dflash_server_id, original_dflash_draft)
            except RuntimeError:
                pass
        _unload(console_base, paths.ar_server_id)
        _unload(console_base, paths.dflash_server_id)

    _print_summary(summaries, dflash2_skipped=dflash2_skipped)
    if args.json:
        print(json.dumps({'modes': summaries}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--console-url', default=DEFAULT_CONSOLE)
    parser.add_argument('--ar-server', default=DEFAULT_AR_SERVER)
    parser.add_argument('--dflash-server', default=DEFAULT_DFLASH_SERVER)
    parser.add_argument('--target', default='', help='Override target GGUF path')
    parser.add_argument('--dflash1-draft', default='', help='Override DFlash 1 draft GGUF path')
    parser.add_argument('--dflash2-draft', default='', help='Override DFlash 2 draft GGUF path')
    parser.add_argument('--runs', type=int, default=3, help='Measured runs per mode (default 3)')
    parser.add_argument('--warmup', type=int, default=1, help='Warmup runs per mode (default 1)')
    parser.add_argument('--max-tokens', type=int, default=256)
    parser.add_argument('--prompt', default=DEFAULT_PROMPT)
    parser.add_argument('--list-paths', action='store_true', help='Print discovered paths and exit')
    parser.add_argument('--json', action='store_true', help='Print JSON summary at the end')
    parser.add_argument(
        '--require-dflash2',
        action='store_true',
        help='Fail instead of skipping when no local DFlash 2 draft is installed',
    )
    parser.add_argument(
        '--skip-modes',
        action='append',
        default=[],
        choices=['autoregressive', 'dflash1', 'dflash2'],
        help='Skip one or more modes',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_benchmark(args)
    except RuntimeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
