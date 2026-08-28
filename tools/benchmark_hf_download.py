#!/usr/bin/env python3
"""Compare Hugging Face download throughput: 1 vs N parallel range connections."""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.huggingface import (  # noqa: E402
    HF_BASE,
    _DOWNLOAD_CHUNK,
    _hf_download_headers,
    _probe_hf_download,
    _split_byte_ranges,
)

# Same family as typical Console downloads; first 80 MiB only.
REPO_ID = 'bartowski/Qwen3.8-27B-GGUF'
FILENAME = 'Qwen3.8-27B-Q6_K_L.gguf'
TEST_BYTES = 80 * 1024 * 1024


def _download_range_to_file(
    url: str,
    headers: dict[str, str],
    dest: Path,
    start: int,
    end: int,
) -> int:
    req_headers = dict(headers)
    req_headers['Range'] = f'bytes={start}-{end}'
    req = urllib.request.Request(url, headers=req_headers)
    written = 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        with dest.open('r+b') as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = resp.read(min(_DOWNLOAD_CHUNK, remaining))
                if not chunk:
                    raise OSError(f'range {start}-{end} ended early')
                handle.write(chunk)
                nbytes = len(chunk)
                written += nbytes
                remaining -= nbytes
    return written


def _run_single(url: str, headers: dict[str, str], dest: Path, end: int) -> float:
    dest.unlink(missing_ok=True)
    with dest.open('wb') as handle:
        handle.truncate(end + 1)
    started = time.perf_counter()
    _download_range_to_file(url, headers, dest, 0, end)
    elapsed = time.perf_counter() - started
    return (end + 1) / elapsed


def _run_parallel(url: str, headers: dict[str, str], dest: Path, end: int, connections: int) -> float:
    dest.unlink(missing_ok=True)
    total = end + 1
    with dest.open('wb') as handle:
        handle.truncate(total)
    ranges = _split_byte_ranges(total, connections)
    errors: list[BaseException] = []

    def worker(start: int, stop: int) -> None:
        try:
            _download_range_to_file(url, headers, dest, start, stop)
        except BaseException as exc:
            errors.append(exc)

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=item, daemon=True) for item in ranges]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    if errors:
        raise errors[0]
    return total / elapsed


def _mbps(bps: float) -> float:
    return bps / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description='Benchmark HF download throughput')
    parser.add_argument('--mib', type=int, default=80, help='MiB to download per test (default 80)')
    args = parser.parse_args()
    test_bytes = max(8, args.mib) * 1024 * 1024

    url = f'{HF_BASE}/{REPO_ID}/resolve/main/{urllib.parse.quote(FILENAME, safe="/")}'
    headers = _hf_download_headers()
    final_url, file_total, ranged = _probe_hf_download(url, headers)
    if not ranged or file_total <= 0:
        print('HF file does not support ranged downloads — parallel mode unavailable.')
        return 1

    end = min(test_bytes, file_total) - 1
    print(f'Benchmark target: {REPO_ID}/{FILENAME}')
    print(f'Window: {_mbps(end + 1):.1f} MiB (file total {_mbps(file_total):.1f} MiB)')
    print(f'Ranged CDN URL: {final_url[:80]}...')
    print()

    configs = [
        ('1 connection (single stream)', lambda dest: _run_single(final_url, headers, dest, end)),
        ('2 parallel ranges', lambda dest: _run_parallel(final_url, headers, dest, end, 2)),
        ('4 parallel ranges (Console default for ~80MB+)', lambda dest: _run_parallel(final_url, headers, dest, end, 4)),
        ('6 parallel ranges (Console max for 256MB+)', lambda dest: _run_parallel(final_url, headers, dest, end, 6)),
        ('8 parallel ranges (test only)', lambda dest: _run_parallel(final_url, headers, dest, end, 8)),
    ]

    results: list[tuple[str, float]] = []
    with tempfile.TemporaryDirectory(prefix='hf-bench-') as tmp:
        tmp_path = Path(tmp)
        for label, fn in configs:
            dest = tmp_path / 'sample.part'
            try:
                bps = fn(dest)
                results.append((label, bps))
                print(f'{label:42} {_mbps(bps):6.2f} MiB/s')
            except Exception as exc:
                print(f'{label:42} FAILED: {exc}')
            time.sleep(1.0)

    if not results:
        return 1

    best_label, best_bps = max(results, key=lambda row: row[1])
    single_bps = next((bps for label, bps in results if label.startswith('1 connection')), None)
    print()
    if single_bps:
        gain = (best_bps / single_bps - 1.0) * 100.0
        print(f'Best: {best_label} at {_mbps(best_bps):.2f} MiB/s')
        print(f'Vs single stream: {gain:+.1f}%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
