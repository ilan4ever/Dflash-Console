"""faster-whisper (CTranslate2) STT runtime adapter (server mode).

Wraps a long-lived Python worker (``runtimes/faster-whisper/server.py``) that
loads faster-whisper models — **model directories** containing ``model.bin``
(a CTranslate2 snapshot) — and serves a minimal loopback HTTP API. The Console
adapter proxies the OpenAI-shaped ``POST /v1/audio/transcriptions`` onto the
worker's ``/transcribe`` endpoint, exactly like the whisper.cpp adapter.

The worker runs under a dedicated venv (``runtimes/faster-whisper/venv``)
built with a Python version that ships ``ctranslate2`` wheels; if that venv is
missing the adapter falls back to the Console's own interpreter when
``faster_whisper`` is importable there.

Process identity: ``runtimes/faster-whisper/server.py`` appears in the worker
command line, so ``managed_process_identity`` / ``server.ps1`` can adopt, stop,
and clean up orphaned workers without mistaking a foreign python process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT, suggest_runtime_port
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_SPEECH_TO_TEXT, RUNTIME_FASTER_WHISPER

FW_BUNDLE = ROOT / 'runtimes' / 'faster-whisper'
FW_SERVER = FW_BUNDLE / 'server.py'
FW_VENV_PY = FW_BUNDLE / 'venv' / 'Scripts' / 'python.exe'
FW_MANIFEST = FW_BUNDLE / 'manifest.json'

LOG_DIR = ROOT / 'logs' / 'runtimes'
FW_LOG = LOG_DIR / 'faster-whisper.log'

# Identity token: a path-segment substring in the command line of our worker.
FW_PROCESS_TOKEN = f'runtimes{os.sep}faster-whisper{os.sep}server.py'

# Default STT inference/load settings. ``device``/``compute_type`` default to
# auto so faster-whisper picks GPU (float16) when a CUDA device is present.
_DEFAULT_SETTINGS: dict[str, Any] = {
    'device': 'auto',
    'compute_type': 'auto',
    'language': '',
    'task': 'transcribe',
    'beam_size': 5,
    'vad_filter': False,
    'temperature': 0.0,
    'cpu_threads': 0,
    'num_workers': 0,
}

_STATE_LOCK = threading.Lock()
_PROFILE: dict[str, Any] = {}
_PROCESS: subprocess.Popen | None = None
_ACTIVE_MODEL = ''
_DEVICE = ''
_COMPUTE_TYPE = ''
_PORT = 0
_HOST = '127.0.0.1'


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with FW_LOG.open('a', encoding='utf-8') as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


def _tcp_open(host: str, port: int, *, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def is_faster_whisper_dir(path: str | Path) -> bool:
    """True when ``path`` is a directory containing a faster-whisper ``model.bin``."""
    try:
        target = Path(str(path)).expanduser().resolve()
    except OSError:
        return False
    if not target.is_dir():
        return False
    if (target / 'model.bin').is_file():
        return True
    # HF snapshot layout: some snapshots keep model.bin at the top level, but
    # also accept a nested models--<org>--<name> snapshot directory directly.
    return False


class FasterWhisperRuntimeAdapter:
    runtime_id = RUNTIME_FASTER_WHISPER
    modalities = (MODALITY_SPEECH_TO_TEXT,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = (FW_PROCESS_TOKEN,)

    # -- install / health ---------------------------------------------------

    @staticmethod
    def is_installed() -> bool:
        if FW_SERVER.is_file() and FW_VENV_PY.is_file():
            return True
        # Fallback: the Console's own interpreter may already have faster_whisper.
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def python(self) -> str:
        """The interpreter used to run the worker (dedicated venv preferred)."""
        if FW_VENV_PY.is_file():
            return str(FW_VENV_PY)
        return sys.executable

    def health(self) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
            model = _ACTIVE_MODEL
            device = _DEVICE
            compute_type = _COMPUTE_TYPE
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': running,
            'port': port,
            'active_model': model,
            'device': device,
            'compute_type': compute_type,
            'bundle': str(FW_BUNDLE),
            'python': self.python(),
            'worker': str(FW_SERVER),
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            _PROFILE.clear()
            _PROFILE.update(dict(profile or {}))
        if not self.is_installed():
            return {
                'success': False,
                'error': 'faster-whisper runtime is not installed under runtimes/faster-whisper/ (venv with faster-whisper missing)',
            }
        if not FW_SERVER.is_file():
            return {'success': False, 'error': 'faster-whisper worker script missing under runtimes/faster-whisper/server.py'}
        self._ensure_worker()
        self.write_manifest()
        _log_line('faster-whisper adapter started (server mode)')
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        self._terminate()
        _log_line('faster-whisper adapter stopped')
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        global _ACTIVE_MODEL, _DEVICE, _COMPUTE_TYPE
        model_path = str((model or {}).get('path') or '').strip()
        if not model_path:
            return {'success': False, 'error': 'model path is required to load a faster-whisper model'}
        path_obj = Path(model_path).expanduser().resolve()
        if path_obj.is_file() and path_obj.suffix.lower() == '.gguf':
            return {
                'success': False,
                'error': 'this is a GGUF model — use the whisper.cpp (STT) runtime instead of faster-whisper',
            }
        if not path_obj.is_dir():
            return {'success': False, 'error': f'faster-whisper model must be a directory, not found: {model_path}'}
        if not (path_obj / 'model.bin').is_file():
            return {'success': False, 'error': f'model.bin not found in faster-whisper model directory: {model_path}'}
        worker = self._ensure_worker()
        if not worker.get('success'):
            return worker
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'faster-whisper worker is not running'}

        # When loaded through /api/models/load the adapter's start() was never
        # called with the config profile, so pull the persisted runtime profile
        # (device policy + STT settings) from config.runtimes[].
        with _STATE_LOCK:
            profile = dict(_PROFILE)
        if not profile:
            try:
                from core.config import ensure_runtime_entry, load_config
                entry = ensure_runtime_entry(self.runtime_id, label='Faster-Whisper STT', cfg=load_config())
                if isinstance(entry, dict):
                    with _STATE_LOCK:
                        _PROFILE.clear()
                        _PROFILE.update(entry)
                    profile = dict(entry)
            except Exception:
                pass

        settings = dict(_DEFAULT_SETTINGS)
        load_settings = (model or {}).get('load_settings') or {}
        if isinstance(load_settings, dict):
            for key, value in load_settings.items():
                if key in _DEFAULT_SETTINGS and value is not None:
                    settings[key] = value
        # The runtime profile (config runtimes[]) carries persisted STT settings.
        for key in _DEFAULT_SETTINGS:
            if key in ('device',):
                continue
            if key in profile and profile[key] is not None and profile[key] != '':
                settings[key] = profile[key]
        # device_policy on the runtime profile maps to the faster-whisper device.
        policy = str(profile.get('device_policy') or 'auto').strip().lower()
        if settings.get('device') in ('auto', '', None):
            settings['device'] = {'gpu': 'cuda', 'cpu': 'cpu'}.get(policy, 'auto')
        if settings.get('compute_type') in ('auto', '', None):
            if settings['device'] == 'cuda':
                settings['compute_type'] = 'float16'
            elif settings['device'] == 'cpu':
                settings['compute_type'] = 'int8'

        payload = dict(settings)
        payload['model_dir'] = str(path_obj)
        result = self._request('POST', '/load', payload)
        if not result.get('success'):
            return {'success': False, 'error': result.get('error') or 'faster-whisper load failed'}
        with _STATE_LOCK:
            _ACTIVE_MODEL = str(path_obj)
            _DEVICE = str(result.get('device') or settings.get('device') or '')
            _COMPUTE_TYPE = str(result.get('compute_type') or settings.get('compute_type') or '')
        _log_line(f'faster-whisper model loaded: {path_obj.name} (device={_DEVICE} compute_type={_COMPUTE_TYPE})')
        return {
            'success': True,
            'loaded': True,
            'runtime_id': self.runtime_id,
            'model': str(path_obj),
            'device': _DEVICE,
            'compute_type': _COMPUTE_TYPE,
            'port': port,
            'host': _HOST,
            'api_url': f'http://{_HOST}:{port}',
        }

    def unload(self) -> dict[str, Any]:
        global _ACTIVE_MODEL, _DEVICE, _COMPUTE_TYPE
        self._request('POST', '/unload', {})
        with _STATE_LOCK:
            _ACTIVE_MODEL = ''
            _DEVICE = ''
            _COMPUTE_TYPE = ''
        self._terminate()
        _log_line('faster-whisper model unloaded; worker stopped')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/audio/transcriptions']

    # -- worker lifecycle ---------------------------------------------------

    def _ensure_worker(self) -> dict[str, Any]:
        """Start the worker if it is not already running; returns success."""
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
        if running and port > 0:
            return {'success': True, 'port': port}
        return self._start_server()

    def _start_server(self) -> dict[str, Any]:
        global _PROCESS, _PORT
        self._terminate()
        if not self.is_installed():
            return {'success': False, 'error': 'faster-whisper runtime is not installed'}
        port = int(_PROFILE.get('port') or 0)
        if port <= 0:
            port = suggest_runtime_port()
        host = '127.0.0.1'
        python = self.python()
        cmd = [python, str(FW_SERVER), '--host', host, '--port', str(port)]
        popen_kwargs: dict[str, Any] = {'cwd': str(FW_BUNDLE)}
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = FW_LOG.open('a', encoding='utf-8')
        popen_kwargs['stdout'] = log_file
        popen_kwargs['stderr'] = subprocess.STDOUT
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            log_file.close()
            return {'success': False, 'error': f'could not start faster-whisper worker: {exc}'}
        log_file.close()

        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            if _tcp_open(host, port):
                with _STATE_LOCK:
                    _PROCESS = proc
                    _PORT = port
                _log_line(f'faster-whisper worker up on {host}:{port}')
                return {'success': True, 'port': port}
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        try:
            proc.kill()
        except OSError:
            pass
        _log_line(f'faster-whisper worker failed to become ready (port {port})')
        return {'success': False, 'error': f'faster-whisper worker did not become ready on port {port}'}

    def _terminate(self) -> None:
        global _PROCESS, _PORT
        with _STATE_LOCK:
            proc = _PROCESS
            _PROCESS = None
            _PORT = 0
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == 'win32':
                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                else:
                    os.killpg(proc.pid, 15)
                proc.wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass

    # -- HTTP to the worker -------------------------------------------------

    def _request(self, method: str, path: str, payload: Any = None, *, headers: dict[str, str] | None = None, timeout: float = 240.0) -> dict[str, Any]:
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'faster-whisper worker is not running'}
        url = f'http://{_HOST}:{port}{path}'
        data: bytes | None = None
        request_headers: dict[str, str] = dict(headers or {})
        if payload is not None:
            if isinstance(payload, bytes):
                data = payload
                request_headers.setdefault('Content-Type', 'application/octet-stream')
            else:
                data = json.dumps(payload).encode('utf-8')
                request_headers.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:400] if exc.fp else str(exc)
            _log_line(f'worker {method} {path} HTTP {exc.code}: {detail}')
            return {'success': False, 'error': f'faster-whisper worker returned HTTP {exc.code}: {detail}'}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log_line(f'worker {method} {path} error: {exc}')
            return {'success': False, 'error': f'faster-whisper worker request failed: {exc}'}
        ctype = resp.headers.get('Content-Type', '') if hasattr(resp, 'headers') else ''
        try:
            if 'application/json' in ctype or raw[:1] == b'{':
                decoded = json.loads(raw.decode('utf-8', errors='replace'))
                if isinstance(decoded, dict):
                    return decoded
            return {'success': True, 'text': raw.decode('utf-8', errors='replace')}
        except (ValueError, UnicodeDecodeError):
            return {'success': True, 'text': raw.decode('utf-8', errors='replace')}

    # -- transcribe ---------------------------------------------------------

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = 'audio.wav',
        language: str = '',
        response_format: str = 'json',
    ) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
        if not running or port <= 0:
            return {'success': False, 'error': 'faster-whisper is not running (load a model first)'}
        if not audio:
            return {'success': False, 'error': 'audio data is required'}
        headers = {'X-Response-Format': response_format or 'json'}
        if language:
            headers['X-Language'] = language
        with _STATE_LOCK:
            task = str(_PROFILE.get('task') or _DEFAULT_SETTINGS['task'])
        if task:
            headers['X-Task'] = task
        result = self._request('POST', '/transcribe', audio, headers=headers)
        if not result.get('success'):
            return result
        text = str(result.get('text') or '')
        _log_line(f'transcribe ok audio_bytes={len(audio)} text_chars={len(text)}')
        return {'success': True, 'text': text}

    # -- manifest -----------------------------------------------------------

    def write_manifest(self) -> Path:
        try:
            payload = {
                'version': 1,
                'runtime_id': self.runtime_id,
                'worker': str(FW_SERVER),
                'python': self.python(),
                'execution_mode': self.execution_mode,
                'generated_by': 'core.runtimes.faster_whisper',
            }
            temporary = FW_MANIFEST.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            temporary.replace(FW_MANIFEST)
        except OSError:
            pass
        return FW_MANIFEST


# Module-level singleton used by the registry.
faster_whisper_adapter = FasterWhisperRuntimeAdapter()
