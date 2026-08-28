"""Transformers / PyTorch Hugging Face model runtime adapter (server mode).

Wraps ``runtimes/transformers/server.py`` — a long-lived Python worker that loads
HF model directories (config.json + safetensors) and serves OpenAI-shaped chat
completions. The bundle is installed on demand from Settings; the Electron
installer stays lightweight.
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
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_LLM, RUNTIME_TRANSFORMERS

TF_BUNDLE = ROOT / 'runtimes' / 'transformers'
TF_SERVER = TF_BUNDLE / 'server.py'
TF_VENV_PY = TF_BUNDLE / 'venv' / 'Scripts' / 'python.exe'
TF_MANIFEST = TF_BUNDLE / 'manifest.json'

LOG_DIR = ROOT / 'logs' / 'runtimes'
TF_LOG = LOG_DIR / 'transformers.log'

TF_PROCESS_TOKEN = f'runtimes{os.sep}transformers{os.sep}server.py'

_DEFAULT_SETTINGS: dict[str, Any] = {
    'device': 'auto',
    'torch_dtype': 'auto',
    'trust_remote_code': False,
    'max_new_tokens': 512,
}

_STATE_LOCK = threading.Lock()
_PROFILE: dict[str, Any] = {}
_PROCESS: subprocess.Popen | None = None
_ACTIVE_MODEL = ''
_DEVICE = ''
_TORCH_DTYPE = ''
_ARCH = ''
_PORT = 0
_HOST = '127.0.0.1'


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with TF_LOG.open('a', encoding='utf-8') as handle:
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


def is_transformers_model_dir(path: str | Path) -> bool:
    """True when ``path`` is a loadable HF transformers model directory."""
    try:
        target = Path(str(path)).expanduser().resolve()
    except OSError:
        return False
    if not target.is_dir():
        return False
    config = target / 'config.json'
    if not config.is_file():
        return False
    try:
        data = json.loads(config.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        data = {}
    model_type = str(data.get('model_type') or '').lower()
    architectures = ' '.join(str(x) for x in (data.get('architectures') or [])).lower()
    if 'vibevoice' in model_type or 'vibevoice' in architectures:
        return False
    if (target / 'model.bin').is_file() and 'whisper' in (model_type + ' ' + architectures):
        return False
    if (target / 'model.safetensors').is_file():
        return True
    if any(target.glob('model-*.safetensors')):
        return True
    if (target / 'pytorch_model.bin').is_file():
        return True
    return bool(model_type or architectures)


class TransformersRuntimeAdapter:
    runtime_id = RUNTIME_TRANSFORMERS
    modalities = (MODALITY_LLM,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = (TF_PROCESS_TOKEN,)

    @staticmethod
    def is_installed() -> bool:
        return TF_SERVER.is_file() and TF_VENV_PY.is_file()

    def python(self) -> str:
        return str(TF_VENV_PY) if TF_VENV_PY.is_file() else sys.executable

    def health(self) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
            model = _ACTIVE_MODEL
            device = _DEVICE
            dtype = _TORCH_DTYPE
            arch = _ARCH
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': running,
            'port': port,
            'host': _HOST,
            'api_url': f'http://{_HOST}:{port}' if port else '',
            'active_model': model,
            'device': device,
            'torch_dtype': dtype,
            'arch': arch,
            'bundle': str(TF_BUNDLE),
            'python': self.python(),
            'worker': str(TF_SERVER),
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            _PROFILE.clear()
            _PROFILE.update(dict(profile or {}))
        if not self.is_installed():
            return {
                'success': False,
                'error': 'Transformers runtime is not installed.',
                'requires_install': True,
                'runtime_id': self.runtime_id,
            }
        self._ensure_worker()
        self.write_manifest()
        _log_line('Transformers adapter started (server mode)')
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        self._terminate()
        _log_line('Transformers adapter stopped')
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        global _ACTIVE_MODEL, _DEVICE, _TORCH_DTYPE, _ARCH
        model_path = str((model or {}).get('path') or '').strip()
        if not model_path:
            return {'success': False, 'error': 'model path is required'}
        path_obj = Path(model_path).expanduser().resolve()
        if not is_transformers_model_dir(path_obj):
            return {'success': False, 'error': f'not a Transformers model directory: {model_path}'}
        worker = self._ensure_worker()
        if not worker.get('success'):
            return worker
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'Transformers worker is not running'}

        with _STATE_LOCK:
            profile = dict(_PROFILE)
        if not profile:
            try:
                from core.config import ensure_runtime_entry, load_config
                entry = ensure_runtime_entry(self.runtime_id, label='Transformers (PyTorch)', cfg=load_config())
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
        if 'glm-ocr' in model_path.lower() or 'glm_ocr' in model_path.lower():
            settings['trust_remote_code'] = True
        policy = str(profile.get('device_policy') or 'auto').strip().lower()
        if settings.get('device') in ('auto', '', None):
            settings['device'] = {'gpu': 'cuda', 'cpu': 'cpu'}.get(policy, 'auto')

        payload = dict(settings)
        payload['model_dir'] = str(path_obj)
        result = self._request('POST', '/load', payload, timeout=900.0)
        if not result.get('success'):
            return {'success': False, 'error': result.get('error') or 'Transformers load failed'}
        with _STATE_LOCK:
            _ACTIVE_MODEL = str(path_obj)
            _DEVICE = str(result.get('device') or settings.get('device') or '')
            _TORCH_DTYPE = str(result.get('torch_dtype') or settings.get('torch_dtype') or '')
            _ARCH = str(result.get('arch') or '')
        _log_line(f'model loaded: {path_obj.name} device={_DEVICE} arch={_ARCH}')
        return {
            'success': True,
            'loaded': True,
            'runtime_id': self.runtime_id,
            'model': str(path_obj),
            'device': _DEVICE,
            'torch_dtype': _TORCH_DTYPE,
            'arch': _ARCH,
            'port': port,
            'host': _HOST,
            'api_url': f'http://{_HOST}:{port}',
            'how_to_use': 'POST /api/runtimes/transformers/v1/chat/completions',
        }

    def unload(self) -> dict[str, Any]:
        global _ACTIVE_MODEL, _DEVICE, _TORCH_DTYPE, _ARCH
        self._request('POST', '/unload', {})
        with _STATE_LOCK:
            _ACTIVE_MODEL = ''
            _DEVICE = ''
            _TORCH_DTYPE = ''
            _ARCH = ''
        self._terminate()
        _log_line('model unloaded; worker stopped')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/chat/completions']

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
        if not running or port <= 0:
            return {'success': False, 'error': 'Transformers runtime is not running (load a model first)'}
        result = self._request('POST', '/v1/chat/completions', payload, timeout=600.0)
        if result.get('error'):
            message = result['error']
            if isinstance(message, dict):
                message = message.get('message') or str(message)
            return {'success': False, 'error': str(message)}
        return {'success': True, **result}

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
            return {'success': False, 'error': 'Transformers runtime is not installed', 'requires_install': True, 'runtime_id': self.runtime_id}
        port = int(_PROFILE.get('port') or 0)
        if port <= 0:
            port = suggest_runtime_port()
        host = '127.0.0.1'
        python = self.python()
        cmd = [python, str(TF_SERVER), '--host', host, '--port', str(port)]
        popen_kwargs: dict[str, Any] = {'cwd': str(TF_BUNDLE)}
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = TF_LOG.open('a', encoding='utf-8')
        popen_kwargs['stdout'] = log_file
        popen_kwargs['stderr'] = subprocess.STDOUT
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            log_file.close()
            return {'success': False, 'error': f'could not start Transformers worker: {exc}'}
        log_file.close()

        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if _tcp_open(host, port):
                with _STATE_LOCK:
                    _PROCESS = proc
                    _PORT = port
                _log_line(f'worker up on {host}:{port}')
                return {'success': True, 'port': port}
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        try:
            proc.kill()
        except OSError:
            pass
        return {'success': False, 'error': f'Transformers worker did not become ready on port {port}'}

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

    def _request(self, method: str, path: str, payload: Any = None, *, timeout: float = 600.0) -> dict[str, Any]:
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'Transformers worker is not running'}
        url = f'http://{_HOST}:{port}{path}'
        data: bytes | None = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:2000] if exc.fp else str(exc)
            _log_line(f'worker {method} {path} HTTP {exc.code}: {detail}')
            try:
                decoded = json.loads(detail)
                if isinstance(decoded, dict):
                    return decoded
            except (ValueError, UnicodeDecodeError):
                pass
            return {'success': False, 'error': f'Transformers worker returned HTTP {exc.code}: {detail}'}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {'success': False, 'error': f'Transformers worker request failed: {exc}'}
        try:
            decoded = json.loads(raw.decode('utf-8', errors='replace'))
            if isinstance(decoded, dict):
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
        return {'success': True, 'raw': raw}

    def write_manifest(self) -> Path:
        try:
            # Keep the installer-stamped bundle_revision so the components hub
            # does not report a false "Update available" after the first start.
            existing: dict[str, Any] = {}
            try:
                existing = json.loads(TF_MANIFEST.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                existing = {}
            revision = existing.get('bundle_revision')
            if not revision:
                from core.components_hub import BUNDLE_REVISIONS

                revision = int(BUNDLE_REVISIONS.get(self.runtime_id, 0) or 0)
            payload = {
                'version': 1,
                'bundle_revision': int(revision or 0),
                'runtime_id': self.runtime_id,
                'worker': str(TF_SERVER),
                'python': self.python(),
                'execution_mode': self.execution_mode,
                'generated_by': 'core.runtimes.transformers_hf',
            }
            temporary = TF_MANIFEST.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            temporary.replace(TF_MANIFEST)
        except OSError:
            pass
        return TF_MANIFEST


transformers_adapter = TransformersRuntimeAdapter()
