"""VibeVoice (Microsoft) realtime TTS runtime adapter (server mode).

Wraps a long-lived Python worker (``runtimes/vibevoice/server.py``) that loads a
Microsoft VibeVoice realtime TTS model (a Transformers safetensors directory)
and serves a minimal loopback HTTP API. The Console adapter proxies the
OpenAI-shaped ``POST /v1/audio/speech`` onto the worker's ``/synthesize``
endpoint, exactly like the Piper adapter but with a persistent server process.

The worker runs under a dedicated venv (``runtimes/vibevoice/venv``) built with
torch (CUDA) + transformers + the ``vibevoice`` package (Microsoft's repo cloned
to ``runtimes/vibevoice/repo``). Voice presets (cached KV prompts for different
speakers/languages) live under ``runtimes/vibevoice/voices/*.pt``.

Process identity: ``runtimes/vibevoice/server.py`` appears in the worker command
line, so ``managed_process_identity`` / ``server.ps1`` can adopt, stop, and clean
up orphaned workers without mistaking a foreign python process.
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
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_TEXT_TO_SPEECH, RUNTIME_VIBEVOICE

VV_BUNDLE = ROOT / 'runtimes' / 'vibevoice'
VV_SERVER = VV_BUNDLE / 'server.py'
VV_VENV_PY = VV_BUNDLE / 'venv' / 'Scripts' / 'python.exe'
VV_VOICES = VV_BUNDLE / 'voices'
VV_MANIFEST = VV_BUNDLE / 'manifest.json'

LOG_DIR = ROOT / 'logs' / 'runtimes'
VV_LOG = LOG_DIR / 'vibevoice.log'

# Identity token: a path-segment substring in the command line of our worker.
VV_PROCESS_TOKEN = f'runtimes{os.sep}vibevoice{os.sep}server.py'

_DEFAULT_SETTINGS: dict[str, Any] = {
    'device': 'auto',
    'voice': 'en-Carter_man',
    'cfg_scale': 1.5,
    'ddpm_steps': 5,
}

_STATE_LOCK = threading.Lock()
_PROFILE: dict[str, Any] = {}
_PROCESS: subprocess.Popen | None = None
_ACTIVE_MODEL = ''
_DEVICE = ''
_VOICE = ''
_PORT = 0
_HOST = '127.0.0.1'


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with VV_LOG.open('a', encoding='utf-8') as handle:
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


class VibeVoiceRuntimeAdapter:
    runtime_id = RUNTIME_VIBEVOICE
    modalities = (MODALITY_TEXT_TO_SPEECH,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = (VV_PROCESS_TOKEN,)

    # -- install / health ---------------------------------------------------

    @staticmethod
    def is_installed() -> bool:
        return VV_SERVER.is_file() and VV_VENV_PY.is_file()

    def python(self) -> str:
        return str(VV_VENV_PY) if VV_VENV_PY.is_file() else sys.executable

    def health(self) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
            model = _ACTIVE_MODEL
            device = _DEVICE
            voice = _VOICE
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': running,
            'port': port,
            'active_model': model,
            'device': device,
            'voice': voice,
            'voices': [p.stem for p in VV_VOICES.glob('*.pt')] if VV_VOICES.is_dir() else [],
            'bundle': str(VV_BUNDLE),
            'python': self.python(),
            'worker': str(VV_SERVER),
        }

    def list_voices(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not VV_VOICES.is_dir():
            return rows
        try:
            for path in sorted(VV_VOICES.glob('*.pt')):
                rows.append({
                    'id': path.stem,
                    'label': path.stem.replace('-', ' ').replace('_', ' ').title(),
                    'path': str(path),
                    'size_bytes': path.stat().st_size,
                })
        except OSError:
            pass
        return rows

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            _PROFILE.clear()
            _PROFILE.update(dict(profile or {}))
        if not self.is_installed():
            return {
                'success': False,
                'error': 'VibeVoice runtime is not installed under runtimes/vibevoice/ (venv missing)',
            }
        if not VV_SERVER.is_file():
            return {'success': False, 'error': 'VibeVoice worker script missing under runtimes/vibevoice/server.py'}
        self._ensure_worker()
        self.write_manifest()
        _log_line('VibeVoice adapter started (server mode)')
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        self._terminate()
        _log_line('VibeVoice adapter stopped')
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        global _ACTIVE_MODEL, _DEVICE, _VOICE
        model_path = str((model or {}).get('path') or '').strip()
        if not model_path:
            return {'success': False, 'error': 'model path is required to load a VibeVoice model'}
        path_obj = Path(model_path).expanduser().resolve()
        if not path_obj.is_dir() or not (path_obj / 'config.json').is_file():
            return {'success': False, 'error': f'VibeVoice model must be a directory with config.json: {model_path}'}
        if not (path_obj / 'model.safetensors').is_file():
            return {'success': False, 'error': f'model.safetensors not found in VibeVoice model directory: {model_path}'}
        worker = self._ensure_worker()
        if not worker.get('success'):
            return worker
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'VibeVoice worker is not running'}

        with _STATE_LOCK:
            profile = dict(_PROFILE)
        if not profile:
            try:
                from core.config import ensure_runtime_entry, load_config
                entry = ensure_runtime_entry(self.runtime_id, label='VibeVoice TTS', cfg=load_config())
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
        for key in _DEFAULT_SETTINGS:
            if key in ('device',):
                continue
            if key in profile and profile[key] is not None and profile[key] != '':
                settings[key] = profile[key]
        policy = str(profile.get('device_policy') or 'auto').strip().lower()
        if settings.get('device') in ('auto', '', None):
            settings['device'] = {'gpu': 'cuda', 'cpu': 'cpu'}.get(policy, 'auto')

        payload = dict(settings)
        payload['model_dir'] = str(path_obj)
        result = self._request('POST', '/load', payload, timeout=600.0)
        if not result.get('success'):
            return {'success': False, 'error': result.get('error') or 'VibeVoice load failed'}
        with _STATE_LOCK:
            _ACTIVE_MODEL = str(path_obj)
            _DEVICE = str(result.get('device') or settings.get('device') or '')
            _VOICE = str(result.get('voice') or settings.get('voice') or '')
        _log_line(f'VibeVoice model loaded: {path_obj.name} (device={_DEVICE} voice={_VOICE})')
        return {
            'success': True,
            'loaded': True,
            'runtime_id': self.runtime_id,
            'model': str(path_obj),
            'device': _DEVICE,
            'voice': _VOICE,
            'port': port,
            'host': _HOST,
            'api_url': f'http://{_HOST}:{port}',
        }

    def unload(self) -> dict[str, Any]:
        global _ACTIVE_MODEL, _DEVICE, _VOICE
        self._request('POST', '/unload', {})
        with _STATE_LOCK:
            _ACTIVE_MODEL = ''
            _DEVICE = ''
            _VOICE = ''
        self._terminate()
        _log_line('VibeVoice model unloaded; worker stopped')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/audio/speech']

    # -- worker lifecycle ---------------------------------------------------

    def _ensure_worker(self) -> dict[str, Any]:
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
            return {'success': False, 'error': 'VibeVoice runtime is not installed'}
        port = int(_PROFILE.get('port') or 0)
        if port <= 0:
            port = suggest_runtime_port()
        host = '127.0.0.1'
        python = self.python()
        cmd = [python, str(VV_SERVER), '--host', host, '--port', str(port)]
        popen_kwargs: dict[str, Any] = {'cwd': str(VV_BUNDLE)}
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = VV_LOG.open('a', encoding='utf-8')
        popen_kwargs['stdout'] = log_file
        popen_kwargs['stderr'] = subprocess.STDOUT
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            log_file.close()
            return {'success': False, 'error': f'could not start VibeVoice worker: {exc}'}
        log_file.close()

        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if _tcp_open(host, port):
                with _STATE_LOCK:
                    _PROCESS = proc
                    _PORT = port
                _log_line(f'VibeVoice worker up on {host}:{port}')
                return {'success': True, 'port': port}
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        try:
            proc.kill()
        except OSError:
            pass
        _log_line(f'VibeVoice worker failed to become ready (port {port})')
        return {'success': False, 'error': f'VibeVoice worker did not become ready on port {port}'}

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

    def _request(self, method: str, path: str, payload: Any = None, *, headers: dict[str, str] | None = None, timeout: float = 600.0) -> dict[str, Any]:
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'VibeVoice worker is not running'}
        url = f'http://{_HOST}:{port}{path}'
        data: bytes | None = None
        request_headers: dict[str, str] = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            request_headers.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:500] if exc.fp else str(exc)
            _log_line(f'worker {method} {path} HTTP {exc.code}: {detail}')
            return {'success': False, 'error': f'VibeVoice worker returned HTTP {exc.code}: {detail}'}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log_line(f'worker {method} {path} error: {exc}')
            return {'success': False, 'error': f'VibeVoice worker request failed: {exc}'}
        ctype = resp.headers.get('Content-Type', '') if hasattr(resp, 'headers') else ''
        try:
            if 'application/json' in ctype or raw[:1] == b'{':
                decoded = json.loads(raw.decode('utf-8', errors='replace'))
                if isinstance(decoded, dict):
                    return decoded
            return {'success': True, 'audio': raw}
        except (ValueError, UnicodeDecodeError):
            return {'success': True, 'audio': raw}

    # -- synthesis ----------------------------------------------------------

    def synthesize(self, text: str, *, voice: str = '', speed: float = 1.0) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
            active_voice = _VOICE
        if not running or port <= 0:
            return {'success': False, 'error': 'VibeVoice is not running (load a model first)'}
        if not text or not text.strip():
            return {'success': False, 'error': 'input text is required'}
        payload: dict[str, Any] = {'text': text}
        if voice:
            payload['voice'] = voice
        if speed and speed > 0:
            # VibeVoice has no speed control; cfg_scale stays at its loaded value.
            pass
        result = self._request('POST', '/synthesize', payload, timeout=600.0)
        if not result.get('success'):
            return result
        audio = result.get('audio') or b''
        if not audio:
            return {'success': False, 'error': 'worker returned no audio'}
        _log_line(f'synthesize ok text_chars={len(text)} wav_bytes={len(audio)}')
        return {
            'success': True,
            'audio': audio,
            'media_type': 'audio/wav',
            'voice': voice or active_voice,
            'sample_rate': 24000,
        }

    # -- manifest -----------------------------------------------------------

    def write_manifest(self) -> Path:
        try:
            payload = {
                'version': 1,
                'runtime_id': self.runtime_id,
                'worker': str(VV_SERVER),
                'python': self.python(),
                'voices_dir': str(VV_VOICES),
                'voices': [p.stem for p in VV_VOICES.glob('*.pt')] if VV_VOICES.is_dir() else [],
                'execution_mode': self.execution_mode,
                'generated_by': 'core.runtimes.vibevoice',
            }
            temporary = VV_MANIFEST.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            temporary.replace(VV_MANIFEST)
        except OSError:
            pass
        return VV_MANIFEST


# Module-level singleton used by the registry.
vibevoice_adapter = VibeVoiceRuntimeAdapter()
