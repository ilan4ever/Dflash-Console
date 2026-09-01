"""vLLM runtime adapter (server mode).

Starts the official OpenAI-compatible vLLM server on loopback. The engine is
installed on demand into ``runtimes/vllm/venv`` so the Windows installer stays
small. Load starts a new process for one model; unload stops it.
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
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_LLM, RUNTIME_VLLM

VLLM_BUNDLE = ROOT / 'runtimes' / 'vllm'
VLLM_VENV_PY = VLLM_BUNDLE / 'venv' / 'Scripts' / 'python.exe'
VLLM_MANIFEST = VLLM_BUNDLE / 'manifest.json'
LOG_DIR = ROOT / 'logs' / 'runtimes'
VLLM_LOG = LOG_DIR / 'vllm.log'
VLLM_PROCESS_TOKEN = f'runtimes{os.sep}vllm{os.sep}venv'

_VLLM_INSTALLED_CACHE: tuple[float, bool] = (0.0, False)
_VLLM_INSTALLED_CACHE_TTL = 300.0

PRESETS: dict[str, dict[str, Any]] = {
    'fast': {'gpu_memory_utilization': 0.70, 'max_model_len': 4096},
    'balanced': {'gpu_memory_utilization': 0.85, 'max_model_len': 8192},
    'long': {'gpu_memory_utilization': 0.90, 'max_model_len': 32768},
}

_STATE_LOCK = threading.Lock()
_PROCESS: subprocess.Popen | None = None
_ACTIVE_MODEL = ''
_PORT = 0
_HOST = '127.0.0.1'
_PRESET = 'balanced'


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with VLLM_LOG.open('a', encoding='utf-8') as handle:
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


def is_vllm_model_dir(path: str | Path) -> bool:
    from core.runtimes.transformers_hf import is_transformers_model_dir

    return is_transformers_model_dir(path)


def _read_manifest_file() -> dict[str, Any]:
    if not VLLM_MANIFEST.is_file():
        return {}
    try:
        data = json.loads(VLLM_MANIFEST.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def verify_vllm_installation() -> tuple[bool, str]:
    """Return (installed, detail). Detail is empty when installed."""
    manifest = _read_manifest_file()
    if not manifest:
        if VLLM_MANIFEST.is_file():
            return False, 'vLLM manifest exists but could not be read (invalid JSON).'
        return False, 'vLLM manifest is missing — install did not finish writing runtimes/vllm/manifest.json.'

    backend = str(manifest.get('backend') or '').strip().lower()
    if not backend:
        python_hint = str(manifest.get('wsl_python') or manifest.get('python') or '').strip()
        if python_hint.startswith('/'):
            backend = 'wsl'
    if backend == 'wsl':
        distro = str(manifest.get('wsl_distro') or '').strip()
        python = str(manifest.get('wsl_python') or '').strip()
        if not distro or not python:
            return False, 'vLLM WSL manifest is incomplete (missing distro or python path).'
        try:
            proc = subprocess.run(
                ['wsl', '-d', distro, '--', python, '-c', 'import vllm'],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, 'Timed out verifying vLLM import in WSL (first import can take up to a minute).'
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f'Could not run WSL verification: {exc}'
        if proc.returncode == 0:
            return True, ''
        detail = (proc.stderr or proc.stdout or '').strip()
        return False, detail or 'vLLM import failed inside WSL.'

    python = str(manifest.get('python') or '').strip()
    native_py = python if python and Path(python).is_file() else str(VLLM_VENV_PY)
    if not Path(native_py).is_file():
        return False, 'Native vLLM Python environment is missing.'
    try:
        proc = subprocess.run(
            [native_py, '-c', 'import vllm'],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, 'Timed out verifying vLLM import on Windows.'
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f'Could not verify native vLLM import: {exc}'
    if proc.returncode == 0:
        return True, ''
    detail = (proc.stderr or proc.stdout or '').strip()
    return False, detail or 'vLLM import failed in the Windows environment.'


class VllmRuntimeAdapter:
    runtime_id = RUNTIME_VLLM
    modalities = (MODALITY_LLM,)
    execution_mode = EXECUTION_MODE_SERVER
    process_identity_tokens = (VLLM_PROCESS_TOKEN, 'vllm.entrypoints')

    def python(self) -> str:
        manifest = self._read_manifest()
        if str(manifest.get('backend') or '') == 'wsl':
            return str(manifest.get('wsl_python') or '')
        if str(manifest.get('python') or '') and Path(str(manifest.get('python'))).is_file():
            return str(manifest.get('python'))
        return str(VLLM_VENV_PY) if VLLM_VENV_PY.is_file() else sys.executable

    def _read_manifest(self) -> dict[str, Any]:
        return _read_manifest_file()

    @staticmethod
    def is_installed() -> bool:
        if VLLM_MANIFEST.is_file():
            return True
        global _VLLM_INSTALLED_CACHE
        now = time.time()
        cached_at, cached_ok = _VLLM_INSTALLED_CACHE
        if (now - cached_at) < _VLLM_INSTALLED_CACHE_TTL:
            return cached_ok
        ok, _detail = verify_vllm_installation()
        _VLLM_INSTALLED_CACHE = (now, ok)
        return ok

    def health(self) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
            model = _ACTIVE_MODEL
            preset = _PRESET
        manifest = self._read_manifest()
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
            'preset': preset,
            'presets': list(PRESETS),
            'backend': str(manifest.get('backend') or ('native' if VLLM_VENV_PY.is_file() else '')),
            'bundle': str(VLLM_BUNDLE),
            'python': self.python(),
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not self.is_installed():
            return {
                'success': False,
                'error': 'vLLM is not installed yet.',
                'requires_install': True,
                'runtime_id': self.runtime_id,
            }
        return {'success': True, 'started': False, 'message': 'Load a model to start the vLLM engine'}

    def stop(self) -> dict[str, Any]:
        return self.unload()

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        global _ACTIVE_MODEL, _PRESET
        if not self.is_installed():
            return {
                'success': False,
                'error': 'vLLM is not installed yet.',
                'requires_install': True,
                'runtime_id': self.runtime_id,
            }
        model_path = str((model or {}).get('path') or '').strip()
        if not model_path:
            return {'success': False, 'error': 'model path is required'}
        path_obj = Path(model_path).expanduser().resolve()
        if not is_vllm_model_dir(path_obj):
            return {'success': False, 'error': f'not a Hugging Face model folder: {model_path}'}
        preset_name = str((model or {}).get('preset') or (model or {}).get('load_settings', {}).get('preset') or 'balanced')
        if preset_name not in PRESETS:
            preset_name = 'balanced'
        settings = dict(PRESETS[preset_name])
        extra = (model or {}).get('load_settings') or {}
        if isinstance(extra, dict):
            if extra.get('gpu_memory_utilization') is not None:
                settings['gpu_memory_utilization'] = float(extra['gpu_memory_utilization'])
            if extra.get('max_model_len') is not None:
                settings['max_model_len'] = int(extra['max_model_len'])
        started = self._start_server(path_obj, settings)
        if not started.get('success'):
            return started
        with _STATE_LOCK:
            _ACTIVE_MODEL = str(path_obj)
            _PRESET = preset_name
        self.write_manifest()
        _log_line(f'model loaded: {path_obj.name} preset={preset_name}')
        return {
            'success': True,
            'loaded': True,
            'runtime_id': self.runtime_id,
            'model': str(path_obj),
            'preset': preset_name,
            'port': started.get('port'),
            'host': _HOST,
            'api_url': f'http://{_HOST}:{started.get("port")}',
            'how_to_use': 'POST /api/runtimes/vllm/v1/chat/completions',
        }

    def unload(self) -> dict[str, Any]:
        global _ACTIVE_MODEL
        self._terminate()
        with _STATE_LOCK:
            _ACTIVE_MODEL = ''
        _log_line('vLLM engine stopped')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/chat/completions', '/v1/models']

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            running = _PROCESS is not None and _PROCESS.poll() is None
            port = _PORT
        if not running or port <= 0:
            return {'success': False, 'error': 'vLLM is not running. Load a model first.'}
        return self._request('POST', '/v1/chat/completions', payload, timeout=600.0)

    def write_manifest(self) -> Path:
        VLLM_BUNDLE.mkdir(parents=True, exist_ok=True)
        existing = _read_manifest_file()
        revision = existing.get('bundle_revision')
        if not revision:
            try:
                from core.components_hub import BUNDLE_REVISIONS

                revision = int(BUNDLE_REVISIONS.get(self.runtime_id, 0) or 0)
            except Exception:
                revision = 0
        backend = str(existing.get('backend') or '').strip().lower()
        if not backend and str(existing.get('wsl_python') or existing.get('python') or '').startswith('/'):
            backend = 'wsl'
        payload: dict[str, Any] = {
            'version': int(existing.get('version') or 1),
            'bundle_revision': int(revision or 0),
            'runtime_id': self.runtime_id,
            'execution_mode': self.execution_mode,
            'backend': backend or ('native' if VLLM_VENV_PY.is_file() else ''),
            'generated_by': str(existing.get('generated_by') or 'core.runtimes.vllm'),
        }
        if payload['backend'] == 'wsl':
            payload['wsl_distro'] = str(existing.get('wsl_distro') or 'Ubuntu')
            payload['wsl_python'] = str(existing.get('wsl_python') or existing.get('python') or self.python())
            payload['python'] = ''
        else:
            payload['python'] = str(existing.get('python') or self.python())
            payload['wsl_distro'] = str(existing.get('wsl_distro') or '')
            payload['wsl_python'] = str(existing.get('wsl_python') or '')
        VLLM_MANIFEST.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        return VLLM_MANIFEST

    def _windows_to_wsl_path(self, path: Path) -> str:
        resolved = path.expanduser().resolve()
        drive = resolved.drive.rstrip(':').lower()
        rest = str(resolved)[len(resolved.drive):].replace('\\', '/')
        return f'/mnt/{drive}{rest}'

    def _start_server(self, model_path: Path, settings: dict[str, Any]) -> dict[str, Any]:
        global _PROCESS, _PORT
        self._terminate()
        port = suggest_runtime_port()
        host = '127.0.0.1'
        manifest = self._read_manifest()
        backend = str(manifest.get('backend') or 'native')
        args = [
            '-m',
            'vllm.entrypoints.openai.api_server',
            '--model',
            self._windows_to_wsl_path(model_path) if backend == 'wsl' else str(model_path),
            '--host',
            '0.0.0.0' if backend == 'wsl' else host,
            '--port',
            str(port),
            '--gpu-memory-utilization',
            str(settings.get('gpu_memory_utilization') or 0.85),
            '--max-model-len',
            str(int(settings.get('max_model_len') or 8192)),
        ]
        if backend == 'wsl':
            distro = str(manifest.get('wsl_distro') or 'Ubuntu')
            python = str(manifest.get('wsl_python') or self.python())
            cmd = ['wsl', '-d', distro, '--', python, *args]
        else:
            cmd = [self.python(), *args]
        popen_kwargs: dict[str, Any] = {'cwd': str(VLLM_BUNDLE)}
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = VLLM_LOG.open('a', encoding='utf-8')
        popen_kwargs['stdout'] = log_file
        popen_kwargs['stderr'] = subprocess.STDOUT
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            log_file.close()
            return {'success': False, 'error': f'could not start vLLM: {exc}'}
        log_file.close()
        deadline = time.monotonic() + 900.0
        while time.monotonic() < deadline:
            if _tcp_open(host, port, timeout=1.5):
                with _STATE_LOCK:
                    _PROCESS = proc
                    _PORT = port
                _log_line(f'vLLM up on {host}:{port}')
                return {'success': True, 'port': port}
            if proc.poll() is not None:
                tail = ''
                try:
                    tail = VLLM_LOG.read_text(encoding='utf-8', errors='replace')[-1200:]
                except OSError:
                    pass
                return {
                    'success': False,
                    'error': 'vLLM exited before it was ready. An NVIDIA GPU is required. See logs/runtimes/vllm.log.',
                    'detail': tail,
                }
            time.sleep(1.0)
        self._terminate()
        return {'success': False, 'error': f'vLLM did not become ready on port {port} within 15 minutes'}

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
                        timeout=15,
                        check=False,
                    )
                else:
                    proc.terminate()
                proc.wait(timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass

    def _request(self, method: str, path: str, payload: Any = None, *, timeout: float = 600.0) -> dict[str, Any]:
        with _STATE_LOCK:
            port = _PORT
        if port <= 0:
            return {'success': False, 'error': 'vLLM is not running'}
        url = f'http://{_HOST}:{port}{path}'
        data = None
        headers = {'Accept': 'application/json'}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode('utf-8', errors='replace')
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                parsed = {'error': raw or str(exc)}
            if isinstance(parsed, dict) and parsed.get('error'):
                return {'success': False, **parsed}
            return {'success': False, 'error': parsed.get('error') or raw or str(exc)}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {'success': False, 'error': str(exc)}
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            return {'success': False, 'error': raw or 'invalid JSON from vLLM'}
        if isinstance(parsed, dict):
            parsed.setdefault('success', True)
            return parsed
        return {'success': True, 'data': parsed}
