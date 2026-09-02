"""FreeToken runtime adapter for Windows-hosted WSL2 inference.

FreeToken is a Linux/CUDA serving engine.  The Console keeps its environment
outside the Electron package and starts ``python -m freetoken serve`` through a
configured WSL distribution.  Model paths remain in the Console's Windows
library and are translated to ``/mnt/<drive>/...`` for WSL.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import (
    ROOT,
    normalize_freetoken_settings,
    suggest_runtime_port,
)
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_LLM, RUNTIME_FREETOKEN

FREETOKEN_BUNDLE = ROOT / 'runtimes' / 'freetoken'
FREETOKEN_MANIFEST = FREETOKEN_BUNDLE / 'manifest.json'
FREETOKEN_PROCESS_STATE = FREETOKEN_BUNDLE / 'process.json'
LOG_DIR = ROOT / 'logs' / 'runtimes'
FREETOKEN_LOG = LOG_DIR / 'freetoken.log'

# This token is injected into the WSL command line and is also persisted in the
# process state file. It prevents a foreign FreeToken process from being
# adopted or killed by Console cleanup.
FREETOKEN_PROCESS_TOKEN = 'dflash-console-freetoken'

_STATE_LOCK = threading.Lock()
_PROFILE: dict[str, Any] = {}
_PROCESS: subprocess.Popen | None = None
_ACTIVE_MODEL = ''
_PORT = 0
_HOST = '127.0.0.1'
_WARMING = False
_INFERENCE_READY = False
_LOAD_PROGRESS: dict[str, Any] = {}
_WATCH_STOP = threading.Event()
_WATCH_THREAD: threading.Thread | None = None

_EXPERT_PROGRESS_RE = re.compile(
    r'Loading DSV4 FP4 experts:\s+'
    r'(?P<pct>\d+(?:\.\d+)?)%\|[^|]*\|\s*'
    r'(?P<present>\d+)/(?P<total>\d+)'
    r'(?:\s+\[(?P<elapsed>\d+:\d+)(?:<(?P<remaining>\d+:\d+))?)?',
)


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with FREETOKEN_LOG.open('a', encoding='utf-8') as handle:
            handle.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {text}\n')
    except OSError:
        pass


def _tcp_open(host: str, port: int, *, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        temporary.replace(path)
    except OSError:
        pass


def _safe_pid(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _pid_matches_console_process(pid: int) -> bool:
    if not _pid_is_alive(pid):
        return False
    if sys.platform != 'win32':
        return True
    try:
        result = subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-Command',
                (
                    '$p = Get-CimInstance Win32_Process -Filter '
                    f'"ProcessId = {int(pid)}"; '
                    'if ($p -and $p.CommandLine -match '
                    f'"{FREETOKEN_PROCESS_TOKEN}") {{ exit 0 }} else {{ exit 1 }}'
                ),
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _windows_to_wsl_path(path: str | Path) -> str:
    """Translate a local Windows path to the standard WSL DrvFS path."""
    resolved = Path(path).expanduser().resolve()
    drive = resolved.drive.rstrip(':').lower()
    if not drive:
        raise ValueError(f'FreeToken model path must be on a Windows drive: {path}')
    rest = str(resolved)[len(resolved.drive):].replace('\\', '/')
    return f'/mnt/{drive}{rest}'


def parse_freetoken_log_progress(log_text: str = '') -> dict[str, Any]:
    """Best-effort progress for FreeToken expert bank warmup from log tail."""
    try:
        text = log_text or FREETOKEN_LOG.read_text(encoding='utf-8', errors='replace')
    except OSError:
        text = log_text or ''
    # Ignore progress from an older model load. The warmup server can emit many
    # 503 probe lines while loading, so keep enough of the current cycle to
    # retain the latest tqdm update.
    start_marker = text.lower().rfind('model warming:')
    if start_marker >= 0:
        text = text[start_marker:]
    tail = text[-65536:]
    matches = _EXPERT_PROGRESS_RE.findall(tail)
    if matches:
        match = _EXPERT_PROGRESS_RE.finditer(tail)
        latest = list(match)[-1]
        present = latest.group('present')
        total = latest.group('total')
        present_i = int(present)
        total_i = int(total)
        pct = round((present_i / total_i) * 100, 1) if total_i > 0 else None
        progress: dict[str, Any] = {
            'phase': 'experts',
            'expert_present': present_i,
            'expert_total': total_i,
            'expert_pct': pct,
            'detail': f'Building expert banks {present_i}/{total_i}',
        }
        remaining = latest.group('remaining')
        if remaining:
            minutes, seconds = (int(value) for value in remaining.split(':', 1))
            progress['eta_seconds'] = minutes * 60 + seconds
            progress['eta'] = remaining
        elapsed = latest.group('elapsed')
        if elapsed:
            minutes, seconds = (int(value) for value in elapsed.split(':', 1))
            progress['elapsed_seconds'] = minutes * 60 + seconds
        return progress
    lowered = tail.lower()
    if 'expert banks: slow path' in lowered or 'loading dsv4 fp4 experts' in lowered:
        return {'phase': 'experts', 'detail': 'Building expert banks…'}
    if 'application startup complete' in lowered:
        return {'phase': 'starting', 'detail': 'Starting FreeToken server…'}
    return {}


def probe_freetoken_inference_ready(port: int, *, host: str = _HOST, timeout: float = 8.0) -> bool:
    """Return True when FreeToken accepts chat (not just when HTTP port is open)."""
    if int(port or 0) <= 0:
        return False
    payload = {
        'model': 'warmup-probe',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'max_tokens': 1,
        'temperature': 0,
    }
    url = f'http://{host}:{int(port)}/v1/chat/completions'
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            return 200 <= int(response.status or 0) < 300
    except urllib.error.HTTPError as exc:
        if int(exc.code or 0) == 503:
            return False
        return False
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def is_freetoken_model_dir(path: str | Path) -> bool:
    """Return whether a local path resembles a FreeToken HF checkpoint."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    if not target.is_dir() or not (target / 'config.json').is_file():
        return False
    try:
        entries = tuple(target.glob('*.safetensors'))
    except OSError:
        entries = ()
    return bool(entries or (target / 'model.safetensors').is_file())


class FreeTokenRuntimeAdapter:
    runtime_id = RUNTIME_FREETOKEN
    modalities = (MODALITY_LLM,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = (FREETOKEN_PROCESS_TOKEN,)

    @staticmethod
    def _manifest() -> dict[str, Any]:
        return _read_json(FREETOKEN_MANIFEST)

    @classmethod
    def is_installed(cls) -> bool:
        manifest = cls._manifest()
        return bool(
            str(manifest.get('backend') or '').lower() == 'wsl'
            and str(manifest.get('wsl_distro') or '').strip()
            and str(manifest.get('wsl_python') or '').strip()
            and str(manifest.get('wsl_ft') or '').strip()
        )

    def _profile(self) -> dict[str, Any]:
        with _STATE_LOCK:
            profile = dict(_PROFILE)
        if profile:
            return profile
        try:
            from core.config import list_runtimes, load_config

            for entry in list_runtimes(load_config()):
                if str(entry.get('runtime_id') or '') == self.runtime_id:
                    with _STATE_LOCK:
                        _PROFILE.update(entry)
                    return dict(entry)
        except Exception:
            pass
        return {}

    def _process_snapshot(self) -> tuple[bool, int, str, int]:
        with _STATE_LOCK:
            proc = _PROCESS
            port = _PORT
            model = _ACTIVE_MODEL
        if proc is not None and proc.poll() is None:
            return True, proc.pid, model, port

        state = _read_json(FREETOKEN_PROCESS_STATE)
        pid = _safe_pid(state.get('pid'))
        state_port = _safe_pid(state.get('port'))
        state_model = str(state.get('model') or '').strip()
        if pid and _pid_matches_console_process(pid) and state_port and _tcp_open(_HOST, state_port, timeout=0.25):
            return True, pid, state_model, state_port
        return False, 0, '', 0

    def health(self) -> dict[str, Any]:
        global _INFERENCE_READY, _WARMING, _LOAD_PROGRESS
        running, pid, model, port = self._process_snapshot()
        manifest = self._manifest()
        settings = normalize_freetoken_settings(self._profile().get('freetoken_settings'))
        with _STATE_LOCK:
            warming = bool(_WARMING)
            inference_ready = bool(_INFERENCE_READY)
            load_progress = dict(_LOAD_PROGRESS)
        if running and not inference_ready:
            fresh_progress = parse_freetoken_log_progress()
            if fresh_progress:
                load_progress = fresh_progress
            phase = str(load_progress.get('phase') or '').strip().lower()
            detail = str(load_progress.get('detail') or '').strip().lower()
            if phase in {'experts', 'starting'} or 'expert bank' in detail:
                warming = True
                with _STATE_LOCK:
                    _WARMING = True
                    _LOAD_PROGRESS = dict(load_progress)
            elif load_progress and not warming:
                warming = True
                with _STATE_LOCK:
                    _WARMING = True
                    _LOAD_PROGRESS = dict(load_progress)
        if running and not inference_ready and not warming:
            if probe_freetoken_inference_ready(port, timeout=2.0):
                with _STATE_LOCK:
                    _WARMING = False
                    _INFERENCE_READY = True
                    inference_ready = True
                    load_progress = {'phase': 'ready', 'detail': 'Ready for chat'}
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': running,
            'warming': running and warming and not inference_ready,
            'inference_ready': running and inference_ready,
            'load_progress': load_progress if running and (warming or not inference_ready) else {},
            'pid': pid,
            'port': port,
            'host': _HOST,
            'api_url': f'http://{_HOST}:{port}' if port else '',
            'active_model': model,
            'backend': str(manifest.get('backend') or ''),
            'wsl_distro': str(manifest.get('wsl_distro') or ''),
            'wsl_python': str(manifest.get('wsl_python') or ''),
            'wsl_ft': str(manifest.get('wsl_ft') or ''),
            'bundle': str(FREETOKEN_BUNDLE),
            'settings': settings,
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            _PROFILE.clear()
            _PROFILE.update(dict(profile or {}))
        if not self.is_installed():
            return {
                'success': False,
                'error': 'FreeToken is not installed. Install the WSL runtime from Settings first.',
                'requires_install': True,
                'runtime_id': self.runtime_id,
            }
        self.write_manifest()
        return {
            'success': True,
            'started': False,
            'runtime_id': self.runtime_id,
            'message': 'Load a model to start the FreeToken engine',
        }

    def stop(self) -> dict[str, Any]:
        self._terminate()
        _log_line('FreeToken adapter stopped')
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        global _ACTIVE_MODEL, _WARMING, _INFERENCE_READY, _LOAD_PROGRESS, _PORT, _PROCESS
        if not self.is_installed():
            return {
                'success': False,
                'error': 'FreeToken is not installed. Install the WSL runtime from Settings first.',
                'requires_install': True,
                'runtime_id': self.runtime_id,
            }
        model_path = str((model or {}).get('path') or '').strip()
        if not model_path:
            return {'success': False, 'error': 'model path is required'}
        path_obj = Path(model_path).expanduser().resolve()
        if not is_freetoken_model_dir(path_obj):
            return {
                'success': False,
                'error': 'FreeToken requires a Hugging Face SafeTensors model directory',
            }
        profile = self._profile()
        settings = normalize_freetoken_settings(profile.get('freetoken_settings'))
        overrides = (model or {}).get('load_settings')
        if isinstance(overrides, dict):
            settings = normalize_freetoken_settings({**settings, **overrides})
        started = self._start_server(path_obj, profile=profile, settings=settings)
        if not started.get('success'):
            return started
        if started.get('warming'):
            with _STATE_LOCK:
                if not _INFERENCE_READY:
                    _WARMING = True
                    if not _LOAD_PROGRESS:
                        _LOAD_PROGRESS = {
                            'phase': 'starting',
                            'detail': 'Starting FreeToken server…',
                        }
        with _STATE_LOCK:
            _ACTIVE_MODEL = str(path_obj)
            port = int(_PORT or started.get('port') or 0)
            proc = _PROCESS
        if proc is not None and port > 0:
            self._start_warmup_watch(proc, port)
            if probe_freetoken_inference_ready(port):
                with _STATE_LOCK:
                    _WARMING = False
                    _INFERENCE_READY = True
                    _LOAD_PROGRESS = {'phase': 'ready', 'detail': 'Ready for chat'}
        with _STATE_LOCK:
            warming = bool(_WARMING and not _INFERENCE_READY)
            inference_ready = bool(_INFERENCE_READY)
            load_progress = dict(_LOAD_PROGRESS)
        self._write_process_state(
            pid=int(started.get('pid') or 0),
            port=int(started.get('port') or 0),
            model=str(path_obj),
        )
        self.write_manifest()
        if inference_ready:
            _log_line(f'model loaded: {path_obj.name}')
        else:
            _log_line(f'model warming: {path_obj.name}')
        return {
            'success': True,
            'loaded': inference_ready,
            'warming': warming,
            'load_progress': load_progress,
            'runtime_id': self.runtime_id,
            'model': str(path_obj),
            'port': started.get('port'),
            'host': _HOST,
            'api_url': f'http://{_HOST}:{started.get("port")}',
            'settings': settings,
            'how_to_use': 'POST /api/servers/freetoken/v1/chat/completions',
        }

    def unload(self) -> dict[str, Any]:
        self._terminate()
        _log_line('FreeToken model unloaded; server stopped')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/chat/completions', '/v1/responses', '/v1/models']

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        running, _pid, _model, port = self._process_snapshot()
        health = self.health()
        if not running or port <= 0:
            return {'success': False, 'error': 'FreeToken is not running. Load a model first.'}
        if health.get('warming') and not health.get('inference_ready'):
            progress = health.get('load_progress') or {}
            detail = str(progress.get('detail') or 'FreeToken is still warming up expert banks')
            return {'success': False, 'error': detail, 'warming': True, 'load_progress': progress}
        return self._request('POST', '/v1/chat/completions', payload, port=port, timeout=3600.0)

    def _start_server(
        self,
        model_path: Path,
        *,
        profile: dict[str, Any],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        global _PROCESS, _PORT
        self._terminate()
        manifest = self._manifest()
        distro = str(manifest.get('wsl_distro') or '').strip()
        ft = str(manifest.get('wsl_ft') or '').strip()
        if not distro or not ft:
            return {'success': False, 'error': 'FreeToken WSL installation manifest is incomplete'}

        port = int(profile.get('port') or 0)
        if port <= 0:
            port = suggest_runtime_port()
        model_wsl = _windows_to_wsl_path(model_path)
        cmd = [
            'wsl',
            '-d',
            distro,
            '--',
            'env',
            f'DFLASH_CONSOLE_RUNTIME={FREETOKEN_PROCESS_TOKEN}',
            ft,
            'serve',
            '--model',
            model_wsl,
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
        ]
        gpu = str(profile.get('gpu_device') or '').strip()
        if gpu and gpu.lower() not in {'auto', 'default'}:
            cmd.extend(['--gpu', gpu])
        self._append_settings(cmd, settings)

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = FREETOKEN_LOG.open('a', encoding='utf-8')
        popen_kwargs: dict[str, Any] = {
            'cwd': str(FREETOKEN_BUNDLE),
            'stdout': log_file,
            'stderr': subprocess.STDOUT,
        }
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            log_file.close()
            return {'success': False, 'error': f'could not start FreeToken through WSL: {exc}'}
        log_file.close()
        deadline = time.monotonic() + 900.0
        while time.monotonic() < deadline:
            if _tcp_open(_HOST, port, timeout=1.5):
                with _STATE_LOCK:
                    global _PROCESS, _PORT, _ACTIVE_MODEL, _WARMING, _INFERENCE_READY, _LOAD_PROGRESS
                    _PROCESS = proc
                    _PORT = port
                    _ACTIVE_MODEL = str(model_path)
                    _WARMING = True
                    _INFERENCE_READY = False
                    _LOAD_PROGRESS = {'phase': 'starting', 'detail': 'Starting FreeToken server…'}
                self._write_process_state(pid=proc.pid, port=port, model=str(model_path))
                _log_line(f'FreeToken HTTP up on {_HOST}:{port} distro={distro} (warming)')
                return {'success': True, 'pid': proc.pid, 'port': port, 'warming': True}
            if proc.poll() is not None:
                detail = self._log_tail()
                return {
                    'success': False,
                    'error': 'FreeToken exited before becoming ready. Check logs/runtimes/freetoken.log.',
                    'detail': detail,
                }
            time.sleep(1.0)
        self._terminate_process(proc)
        return {'success': False, 'error': f'FreeToken did not become ready on port {port} within 15 minutes'}

    @staticmethod
    def _append_settings(cmd: list[str], settings: dict[str, Any]) -> None:
        value = settings.get('moe_backend')
        if value and value != 'auto':
            cmd.extend(['--moe-backend', str(value)])
        for key, flag in (
            ('memory_ratio', '--memory-ratio'),
            ('max_running_requests', '--max-running-requests'),
            ('max_output_tokens', '--max-output-tokens'),
            ('max_prefill_length', '--max-prefill-length'),
            ('cuda_graph_max_bs', '--cuda-graph-max-bs'),
            ('max_seq_len_override', '--max-seq-len-override'),
            ('moe_cache_size', '--moe-cache-size'),
            ('moe_cache_rate', '--moe-cache-rate'),
            ('moe_cpu_threads', '--moe-cpu-threads'),
            ('moe_cpu_layers', '--moe-cpu-layers'),
            ('moe_hybrid_max_fetch', '--moe-hybrid-max-fetch'),
        ):
            current = settings.get(key)
            if current not in (None, '', 0, 0.0):
                cmd.extend([flag, str(current)])
        if settings.get('moe_cache_auto') is True:
            cmd.append('--moe-cache-auto')
        if settings.get('moe_prefill_hit_d2d') is True:
            cmd.append('--moe-prefill-hit-d2d')
        if settings.get('disable_moe_prefill_overlap') is True:
            cmd.append('--disable-moe-prefill-overlap')
        if settings.get('enable_cache_report') is True:
            cmd.append('--enable-cache-report')

    def _start_warmup_watch(self, proc: subprocess.Popen, port: int) -> None:
        global _WATCH_THREAD
        self._stop_warmup_watch()
        _WATCH_STOP.clear()

        def watch() -> None:
            global _WARMING, _INFERENCE_READY, _LOAD_PROGRESS
            while not _WATCH_STOP.wait(2.0):
                if proc.poll() is not None:
                    with _STATE_LOCK:
                        _WARMING = False
                        _INFERENCE_READY = False
                    return
                progress = parse_freetoken_log_progress()
                ready = probe_freetoken_inference_ready(port)
                with _STATE_LOCK:
                    if progress:
                        _LOAD_PROGRESS = progress
                    if ready:
                        _WARMING = False
                        _INFERENCE_READY = True
                        _LOAD_PROGRESS = {'phase': 'ready', 'detail': 'Ready for chat'}
                        _log_line('FreeToken inference ready')
                        return

        _WATCH_THREAD = threading.Thread(
            target=watch,
            name='freetoken-warmup-watch',
            daemon=True,
        )
        _WATCH_THREAD.start()

    def _stop_warmup_watch(self) -> None:
        global _WATCH_THREAD
        _WATCH_STOP.set()
        if _WATCH_THREAD and _WATCH_THREAD.is_alive():
            _WATCH_THREAD.join(timeout=2.0)
        _WATCH_STOP.clear()
        _WATCH_THREAD = None

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        port: int,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        url = f'http://{_HOST}:{int(port)}{path}'
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            _log_line(f'FreeToken {method} {path} HTTP {exc.code}: {detail[:500]}')
            try:
                parsed = json.loads(detail) if detail else {}
            except ValueError:
                parsed = {}
            return {
                'success': False,
                'error': parsed.get('error') if isinstance(parsed, dict) else detail,
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {'success': False, 'error': f'FreeToken request failed: {exc}'}
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            return {'success': False, 'error': raw or 'invalid JSON from FreeToken'}
        return parsed if isinstance(parsed, dict) else {'success': True, 'data': parsed}

    def _write_process_state(self, *, pid: int, port: int, model: str) -> None:
        _write_json(
            FREETOKEN_PROCESS_STATE,
            {'version': 1, 'pid': int(pid), 'port': int(port), 'model': model},
        )

    def _log_tail(self) -> str:
        try:
            return FREETOKEN_LOG.read_text(encoding='utf-8', errors='replace')[-2000:]
        except OSError:
            return ''

    def _terminate_process(self, proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == 'win32':
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            else:
                proc.terminate()
            proc.wait(timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    def _terminate(self) -> None:
        global _PROCESS, _ACTIVE_MODEL, _PORT, _WARMING, _INFERENCE_READY, _LOAD_PROGRESS
        self._stop_warmup_watch()
        with _STATE_LOCK:
            proc = _PROCESS
            _PROCESS = None
            _ACTIVE_MODEL = ''
            _PORT = 0
            _WARMING = False
            _INFERENCE_READY = False
            _LOAD_PROGRESS = {}
        if proc is not None:
            self._terminate_process(proc)
        else:
            state = _read_json(FREETOKEN_PROCESS_STATE)
            pid = _safe_pid(state.get('pid'))
            if pid and _pid_matches_console_process(pid) and sys.platform == 'win32':
                try:
                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(pid)],
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
        try:
            FREETOKEN_PROCESS_STATE.unlink()
        except OSError:
            pass

    def write_manifest(self) -> Path:
        existing = self._manifest()
        revision = existing.get('bundle_revision')
        if not revision:
            try:
                from core.components_hub import BUNDLE_REVISIONS

                revision = int(BUNDLE_REVISIONS.get(self.runtime_id, 0) or 0)
            except Exception:
                revision = 0
        payload = {
            'version': 1,
            'bundle_revision': int(revision or 0),
            'runtime_id': self.runtime_id,
            'execution_mode': self.execution_mode,
            'backend': str(existing.get('backend') or 'wsl'),
            'wsl_distro': str(existing.get('wsl_distro') or ''),
            'wsl_python': str(existing.get('wsl_python') or ''),
            'wsl_ft': str(existing.get('wsl_ft') or ''),
            'generated_by': 'core.runtimes.freetoken',
        }
        _write_json(FREETOKEN_MANIFEST, payload)
        return FREETOKEN_MANIFEST


freetoken_adapter = FreeTokenRuntimeAdapter()
