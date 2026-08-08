"""whisper.cpp ``whisper-server`` STT runtime adapter (server mode).

Wraps the native ``whisper-server.exe`` binary under ``runtimes/stt/``
(per docs/STT-ENGINE-DECISION.md — locked for Phase 2). It is a long-lived
loopback HTTP server exposing the OpenAI-compatible
``POST /v1/audio/transcriptions`` route, so ``execution_mode`` is ``server``.

Process identity: ``runtimes/stt/whisper-server`` appears in the command line
of Console-managed whisper-server processes, so ``managed_process_identity`` /
``server.ps1`` can adopt, stop, and clean up orphaned STT servers without
mistaking a foreign whisper process for ours.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT, suggest_runtime_port
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_SPEECH_TO_TEXT, RUNTIME_STT

STT_BUNDLE = ROOT / 'runtimes' / 'stt'
STT_EXE = STT_BUNDLE / 'whisper-server.exe'
STT_MANIFEST = STT_BUNDLE / 'manifest.json'

LOG_DIR = ROOT / 'logs' / 'runtimes'
STT_LOG = LOG_DIR / 'stt.log'

# Identity token: a path-segment substring in the command line of our server.
STT_PROCESS_TOKEN = f'runtimes{os.sep}stt{os.sep}whisper-server'

_STATE_LOCK = threading.Lock()
_PROFILE: dict[str, Any] = {}
_PROCESS: subprocess.Popen | None = None
_ACTIVE_MODEL: str = ''
_PORT = 0
_HOST = '127.0.0.1'


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with STT_LOG.open('a', encoding='utf-8') as handle:
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


class SttRuntimeAdapter:
    runtime_id = RUNTIME_STT
    modalities = (MODALITY_SPEECH_TO_TEXT,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = (STT_PROCESS_TOKEN,)

    # -- install / health ---------------------------------------------------

    @staticmethod
    def is_installed() -> bool:
        return STT_EXE.is_file()

    def health(self) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
            model = _ACTIVE_MODEL
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': running,
            'port': port,
            'active_model': model,
            'bundle': str(STT_BUNDLE),
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            _PROFILE.clear()
            _PROFILE.update(dict(profile or {}))
        if not self.is_installed():
            return {
                'success': False,
                'error': 'whisper-server is not installed under runtimes/stt/ (see Settings → Install speech runtime)',
            }
        self.write_manifest()
        _log_line('stt adapter started (server mode)')
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        self._terminate()
        _log_line('stt adapter stopped')
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        model_path = str((model or {}).get('path') or '').strip()
        if not model_path:
            return {'success': False, 'error': 'model path is required to start whisper-server'}
        path_obj = Path(model_path)
        if not path_obj.is_file():
            return {'success': False, 'error': f'model not found: {model_path}'}
        return self._start_server(path_obj, cfg_profile=(model or {}))

    def unload(self) -> dict[str, Any]:
        self._terminate()
        _log_line('stt model unloaded; server stopped')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/audio/transcriptions']

    # -- lifecycle ----------------------------------------------------------

    def _terminate(self) -> None:
        global _PROCESS, _ACTIVE_MODEL, _PORT
        with _STATE_LOCK:
            proc = _PROCESS
            _PROCESS = None
            _ACTIVE_MODEL = ''
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

    def _start_server(self, model_path: Path, *, cfg_profile: dict[str, Any]) -> dict[str, Any]:
        global _PROCESS, _ACTIVE_MODEL, _PORT
        self._terminate()
        if not self.is_installed():
            return {'success': False, 'error': 'whisper-server is not installed under runtimes/stt/'}

        port = int(cfg_profile.get('port') or 0)
        if port <= 0:
            port = suggest_runtime_port()
        host = '127.0.0.1'

        cmd = [
            str(STT_EXE),
            '-m', str(model_path),
            '-p', str(port),
            '--host', host,
            '--openai',  # expose OpenAI-compatible /v1/audio/transcriptions
        ]
        popen_kwargs: dict[str, Any] = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.STDOUT,
        }
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = STT_LOG.open('a', encoding='utf-8')
        popen_kwargs['stdout'] = log_file
        popen_kwargs['stderr'] = subprocess.STDOUT
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            log_file.close()
            return {'success': False, 'error': f'could not start whisper-server: {exc}'}
        log_file.close()

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if _tcp_open(host, port):
                with _STATE_LOCK:
                    _PROCESS = proc
                    _ACTIVE_MODEL = str(model_path)
                    _PORT = port
                _log_line(f'whisper-server up on {host}:{port} model={model_path.name}')
                return {
                    'success': True,
                    'loaded': True,
                    'port': port,
                    'host': host,
                    'api_url': f'http://{host}:{port}',
                }
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        try:
            proc.kill()
        except OSError:
            pass
        _log_line(f'whisper-server failed to become ready (port {port})')
        return {'success': False, 'error': f'whisper-server did not become ready on port {port}'}

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
            port = _PORT
            running = _PROCESS is not None and _PROCESS.poll() is None
        if not running or port <= 0:
            return {'success': False, 'error': 'whisper-server is not running (load a model first)'}
        if not audio:
            return {'success': False, 'error': 'audio data is required'}

        url = f'http://{_HOST}:{port}/v1/audio/transcriptions'
        fields: list[tuple[str, str]] = [('model', 'whisper-1')]
        if language:
            fields.append(('language', language))
        if response_format and response_format in ('json', 'text'):
            fields.append(('response_format', response_format))
        boundary = f'----DFlashSTT{os.getpid()}'
        body = _build_multipart(fields, boundary, filename=filename or 'audio.wav', audio=audio)
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
        request = urllib.request.Request(url, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=120.0) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:300] if exc.fp else str(exc)
            _log_line(f'transcribe HTTP {exc.code}: {detail}')
            return {'success': False, 'error': f'whisper-server returned HTTP {exc.code}'}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log_line(f'transcribe error: {exc}')
            return {'success': False, 'error': f'transcription request failed: {exc}'}

        text = ''
        try:
            decoded = json.loads(payload.decode('utf-8', errors='replace'))
            text = str(decoded.get('text') or '')
        except (ValueError, AttributeError):
            text = payload.decode('utf-8', errors='replace').strip()
        _log_line(f'transcribe ok audio_bytes={len(audio)} text_chars={len(text)}')
        return {'success': True, 'text': text}

    def write_manifest(self) -> Path:
        try:
            payload = {
                'version': 1,
                'runtime_id': self.runtime_id,
                'binary': str(STT_EXE),
                'execution_mode': self.execution_mode,
                'generated_by': 'core.runtimes.stt',
            }
            temporary = STT_MANIFEST.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            temporary.replace(STT_MANIFEST)
        except OSError:
            pass
        return STT_MANIFEST


def _build_multipart(fields: list[tuple[str, str]], boundary: str, *, filename: str, audio: bytes) -> bytes:
    lines: list[bytes] = []
    for name, value in fields:
        lines.append(f'--{boundary}\r\n'.encode('utf-8'))
        lines.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8'))
        lines.append(f'{value}\r\n'.encode('utf-8'))
    lines.append(f'--{boundary}\r\n'.encode('utf-8'))
    lines.append(
        f'Content-Disposition: form-data; name="file"; filename="{urllib.parse.quote(filename)}"\r\n'.encode('utf-8')
    )
    lines.append(b'Content-Type: application/octet-stream\r\n\r\n')
    lines.append(audio)
    lines.append(b'\r\n')
    lines.append(f'--{boundary}--\r\n'.encode('utf-8'))
    return b''.join(lines)


# Module-level singleton used by the registry.
stt_adapter = SttRuntimeAdapter()
