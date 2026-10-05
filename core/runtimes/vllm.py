"""vLLM runtime adapter (server mode).

Starts the official OpenAI-compatible vLLM server on loopback. The engine is
installed on demand into ``runtimes/vllm/venv`` so the Windows installer stays
small. Load starts a new process for one model; unload stops it.
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

from core.config import ROOT, suggest_runtime_port
from core.runtimes.base import EXECUTION_MODE_SERVER, MODALITY_LLM, RUNTIME_VLLM

VLLM_BUNDLE = ROOT / 'runtimes' / 'vllm'
VLLM_VENV_PY = VLLM_BUNDLE / 'venv' / 'Scripts' / 'python.exe'
VLLM_MANIFEST = VLLM_BUNDLE / 'manifest.json'
LOG_DIR = ROOT / 'logs' / 'runtimes'
VLLM_LOG = LOG_DIR / 'vllm.log'
VLLM_CHAT_TEMPLATE = Path(__file__).resolve().parent / 'qwen_vl_chat_template.jinja'
VLLM_PROCESS_TOKEN = f'runtimes{os.sep}vllm{os.sep}venv'

_VLLM_INSTALLED_CACHE: tuple[float, bool] = (0.0, False)
_VLLM_INSTALLED_CACHE_TTL = 300.0

PRESETS: dict[str, dict[str, Any]] = {
    'fast': {'gpu_memory_utilization': 0.70, 'max_model_len': 4096},
    'balanced': {'gpu_memory_utilization': 0.85, 'max_model_len': 8192},
    'long': {'gpu_memory_utilization': 0.90, 'max_model_len': 32768},
}

_STATE_LOCK = threading.Lock()
_SERVED_ID_CACHE: tuple[float, int, str] = (0.0, 0, '')
_ORPHAN_CACHE: tuple[float, int, str] = (0.0, 0, '')
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


def is_compact_vision_model(model_path: Path) -> bool:
    """Small OCR / vision folders should not reserve an entire GPU."""
    name = model_path.name.lower()
    return 'ocr' in name or 'navi' in name


def model_needs_chat_template(model_path: Path) -> bool:
    """True when the folder has no chat template for vLLM to apply."""
    if (model_path / 'chat_template.jinja').is_file():
        return False
    config_path = model_path / 'tokenizer_config.json'
    try:
        data = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True
    if not isinstance(data, dict):
        return True
    return not str(data.get('chat_template') or '').strip()


def fit_gpu_memory_utilization(text: str, current: float) -> float | None:
    """Lower the vLLM memory request so it fits the free memory in a startup error."""
    match = re.search(r'Free memory on device \S+ \(([\d.]+)/([\d.]+) GiB\)', str(text or ''))
    if not match or 'desired GPU memory utilization' not in str(text or ''):
        return None
    free_gb = float(match.group(1))
    total_gb = float(match.group(2))
    if total_gb <= 0:
        return None
    fitted = round((free_gb / total_gb) * 0.92, 2)
    fitted = max(0.20, min(fitted, round(float(current) - 0.02, 2)))
    if fitted >= float(current) - 0.01:
        return None
    return fitted


def _orphaned_server() -> tuple[int, str]:
    """A vLLM process can keep serving after the Console that started it is gone."""
    global _ORPHAN_CACHE
    now = time.time()
    cached_at, cached_port, cached_model = _ORPHAN_CACHE
    if cached_port and (now - cached_at) < 5.0:
        return cached_port, cached_model
    try:
        blob = VLLM_LOG.read_text(encoding='utf-8', errors='replace')[-50000:]
    except OSError:
        return 0, ''
    urls = re.findall(r'Starting vLLM server on http://[^:\s]+:(\d+)', blob)
    if not urls:
        return 0, ''
    port = int(urls[-1])
    if not _tcp_open(_HOST, port, timeout=0.3):
        return 0, ''
    served = ''
    try:
        with urllib.request.urlopen(f'http://{_HOST}:{port}/v1/models', timeout=2.0) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
        data = payload.get('data') if isinstance(payload, dict) else None
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and str(row.get('id') or '').strip():
                    served = str(row.get('id') or '').strip()
                    break
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return 0, ''
    windows = served
    if served.startswith('/mnt/') and len(served) > 6:
        rest = served[7:].replace('/', '\\')
        windows = f'{served[5].upper()}:\\{rest}'
    _ORPHAN_CACHE = (now, port, windows)
    return port, windows


def parse_vllm_load_progress(text: str) -> dict[str, Any]:
    """Turn the vLLM log tail into a 1–99 percent the Engines card can show."""
    blob = str(text or '')
    if not blob.strip():
        return {'pct': 1, 'detail': 'Starting vLLM…', 'phase': 'start'}
    if 'Starting vLLM server' in blob[-2500:] or 'Application startup complete' in blob[-2000:]:
        return {'pct': 96, 'detail': 'Opening the vision server…', 'phase': 'ready'}
    full = [int(value) for value in re.findall(r'Capturing CUDA graphs \(FULL\):\s+(\d+)%', blob)]
    if full:
        return {
            'pct': min(95, 70 + int(full[-1] * 0.25)),
            'detail': 'Preparing the GPU…',
            'phase': 'compile',
        }
    piece = [int(value) for value in re.findall(r'Capturing CUDA graphs \(PIECEWISE\):\s+(\d+)%', blob)]
    if piece:
        return {
            'pct': min(70, 40 + int(piece[-1] * 0.30)),
            'detail': 'Preparing the GPU…',
            'phase': 'compile',
        }
    shards = [int(value) for value in re.findall(r'Loading safetensors checkpoint shards:\s+(\d+)%', blob)]
    if shards:
        return {
            'pct': max(2, min(40, int(shards[-1] * 0.40))),
            'detail': 'Reading model weights…',
            'phase': 'weights',
        }
    return {'pct': 2, 'detail': 'Starting vLLM…', 'phase': 'start'}


def _vllm_log_tail(limit: int = 12000) -> str:
    try:
        data = VLLM_LOG.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    return data[-limit:]


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
        if not running or port <= 0 or not model:
            remembered_port = int(manifest.get('listen_port') or 0)
            remembered_model = str(manifest.get('active_model') or '').strip()
            if remembered_port > 0 and remembered_model and _tcp_open(_HOST, remembered_port, timeout=0.4):
                port = remembered_port
                model = remembered_model
                running = True
            else:
                adopted_port, adopted_model = _orphaned_server()
                if adopted_port and adopted_model:
                    port = adopted_port
                    model = adopted_model
                    running = True
        served = self._served_model_id(port) if running and port else ''
        ready = bool(running and model and served)
        progress: dict[str, Any] = {}
        if not ready:
            from core.load_activity import active_model_loads

            loading = any(
                str(job.get('runtime_id') or job.get('server_id') or '') == 'vllm'
                for job in active_model_loads()
            )
            if loading:
                progress = parse_vllm_load_progress(_vllm_log_tail())
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': running,
            'inference_ready': ready,
            'port': port,
            'host': _HOST,
            'api_url': f'http://{_HOST}:{port}' if port else '',
            'active_model': model,
            'served_model_id': served,
            'load_progress': progress,
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
        # API shutdown must not kill an OCR server this process did not start.
        # Unload still stops an adopted engine.
        proc_alive = False
        with _STATE_LOCK:
            proc = _PROCESS
            proc_alive = proc is not None and proc.poll() is None
        if not proc_alive:
            return {'success': True, 'stopped': False, 'runtime_id': self.runtime_id}
        self._terminate()
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def _already_serving(self, path_obj: Path) -> bool:
        """Skip a restart when this folder is already answering requests."""
        health = self.health()
        if health.get('inference_ready') is not True or health.get('running') is not True:
            return False
        active = str(health.get('active_model') or '').strip()
        if not active:
            return False
        try:
            same = Path(active).expanduser().resolve() == path_obj.resolve()
        except OSError:
            same = Path(active).name.lower() == path_obj.name.lower()
        if not same:
            return False
        if is_compact_vision_model(path_obj) and self._read_manifest().get('ocr_fast_launch') is not True:
            return False
        if not model_needs_chat_template(path_obj):
            return True
        return self._read_manifest().get('chat_template_applied') is True

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
        if self._already_serving(path_obj):
            health = self.health()
            _log_line(f'model already loaded: {path_obj.name}')
            return {
                'success': True,
                'loaded': True,
                'already_loaded': True,
                'runtime_id': self.runtime_id,
                'model': str(path_obj),
                'preset': str(health.get('preset') or 'balanced'),
                'port': health.get('port'),
                'host': _HOST,
                'api_url': str(health.get('api_url') or ''),
                'how_to_use': 'POST /api/runtimes/vllm/v1/chat/completions',
            }
        preset_name = str((model or {}).get('preset') or (model or {}).get('load_settings', {}).get('preset') or 'balanced')
        if preset_name not in PRESETS:
            preset_name = 'balanced'
        settings = dict(PRESETS[preset_name])
        # A 3 GB OCR folder was reserving about 40 GB and then spending minutes
        # building CUDA graphs. Keep a small cache and start without that step.
        self._ocr_fast_launch = is_compact_vision_model(path_obj)
        if self._ocr_fast_launch:
            settings['gpu_memory_utilization'] = 0.20
            settings['max_model_len'] = 4096
            settings['enforce_eager'] = True
            settings['generation_config'] = 'vllm'
        extra = (model or {}).get('load_settings') or {}
        if isinstance(extra, dict):
            if extra.get('gpu_memory_utilization') is not None:
                settings['gpu_memory_utilization'] = float(extra['gpu_memory_utilization'])
            if extra.get('max_model_len') is not None:
                settings['max_model_len'] = int(extra['max_model_len'])
        requested_gpu = str((model or {}).get('gpu_device') or '').strip().lower()
        if requested_gpu not in ('', 'auto', 'automatic', 'default'):
            settings['gpu_device'] = requested_gpu
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
        global _ACTIVE_MODEL, _SERVED_ID_CACHE
        self._terminate()
        with _STATE_LOCK:
            _ACTIVE_MODEL = ''
        _SERVED_ID_CACHE = (0.0, 0, '')
        self._chat_template_applied = False
        try:
            self.write_manifest()
        except OSError:
            pass
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
        with _STATE_LOCK:
            if _PORT and _ACTIVE_MODEL:
                payload['listen_port'] = int(_PORT)
                payload['active_model'] = str(_ACTIVE_MODEL)
        if getattr(self, '_chat_template_applied', False):
            payload['chat_template_applied'] = True
        if getattr(self, '_ocr_fast_launch', False):
            payload['ocr_fast_launch'] = True
        VLLM_MANIFEST.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        return VLLM_MANIFEST

    def _served_model_id(self, port: int) -> str:
        """Id vLLM will accept. WSL serves a Linux path, not the Windows path."""
        global _SERVED_ID_CACHE
        port = int(port or 0)
        if port <= 0:
            return ''
        now = time.time()
        cached_at, cached_port, cached_id = _SERVED_ID_CACHE
        if cached_id and cached_port == port and (now - cached_at) < 3.0:
            return cached_id
        try:
            with urllib.request.urlopen(f'http://{_HOST}:{port}/v1/models', timeout=2.0) as resp:
                payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            return ''
        data = payload.get('data') if isinstance(payload, dict) else None
        served = ''
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and str(row.get('id') or '').strip():
                    served = str(row.get('id') or '').strip()
                    break
        if served:
            _SERVED_ID_CACHE = (now, port, served)
        return served

    def _ensure_wsl_qwen2_head_dim(self, distro: str, python: str) -> None:
        """Honor config head_dim for models like TeleOCR.

        Stock vLLM sets head size to hidden_size / num_heads. TeleOCR's text
        tower is 1024 wide with 16 heads but a head size of 128, so vLLM
        aborts while building rotary embeddings.
        """
        patch = Path(__file__).resolve().parent / 'patch_qwen2_head_dim.py'
        if not patch.is_file():
            return
        wsl_patch = self._windows_to_wsl_path(patch)
        try:
            subprocess.run(
                ['wsl', '-d', distro, '-u', 'root', '--', python, wsl_patch],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

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
        self._chat_template_applied = False
        if model_needs_chat_template(model_path) and VLLM_CHAT_TEMPLATE.is_file():
            template_path = (
                self._windows_to_wsl_path(VLLM_CHAT_TEMPLATE)
                if backend == 'wsl'
                else str(VLLM_CHAT_TEMPLATE)
            )
            args.extend(['--chat-template', template_path])
            self._chat_template_applied = True
        if settings.get('enforce_eager'):
            args.append('--enforce-eager')
        if settings.get('generation_config'):
            args.extend(['--generation-config', str(settings['generation_config'])])
        if backend == 'wsl':
            distro = str(manifest.get('wsl_distro') or 'Ubuntu')
            python = str(manifest.get('wsl_python') or self.python())
            self._ensure_wsl_qwen2_head_dim(distro, python)
            gpu = str(settings.get('gpu_device') or '').strip().lower()
            # WSL2 can pin memory, but vLLM leaves it off unless this is set.
            # Without it the engine dies with "UVA is not available" even when
            # an NVIDIA GPU is present.
            env_args = [
                'VLLM_WSL2_ENABLE_PIN_MEMORY=1',
                'CUDA_DEVICE_ORDER=PCI_BUS_ID',
                # FlashInfer's sampler tries to JIT-compile with nvcc, which
                # this WSL install does not have. The PyTorch sampler works.
                'VLLM_USE_FLASHINFER_SAMPLER=0',
            ]
            if gpu not in ('', 'auto', 'automatic', 'default'):
                env_args.append(f'CUDA_VISIBLE_DEVICES={gpu}')
            cmd = ['wsl', '-d', distro, '--', 'env', *env_args, python, *args]
        else:
            cmd = [self.python(), *args]
        popen_kwargs: dict[str, Any] = {'cwd': str(VLLM_BUNDLE)}
        gpu = str(settings.get('gpu_device') or '').strip().lower()
        if gpu not in ('', 'auto', 'automatic', 'default'):
            env = os.environ.copy()
            env['CUDA_VISIBLE_DEVICES'] = gpu
            popen_kwargs['env'] = env
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        retried_for_memory = False
        profile_retries = 0
        while True:
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
                        tail = VLLM_LOG.read_text(encoding='utf-8', errors='replace')[-6000:]
                    except OSError:
                        pass
                    fitted = None if retried_for_memory else fit_gpu_memory_utilization(
                        tail,
                        float(settings.get('gpu_memory_utilization') or 0.85),
                    )
                    if fitted is not None:
                        retried_for_memory = True
                        settings['gpu_memory_utilization'] = fitted
                        for command in (args, cmd):
                            try:
                                util_at = command.index('--gpu-memory-utilization')
                            except ValueError:
                                continue
                            command[util_at + 1] = str(fitted)
                        _log_line(
                            f'GPU is busy — retrying vLLM using {int(fitted * 100)}% of the card'
                        )
                        break
                    if 'Error in memory profiling' in tail and profile_retries < 2:
                        profile_retries += 1
                        _log_line('GPU memory shifted while starting — trying again')
                        time.sleep(2.0)
                        break
                    reason = 'An NVIDIA GPU is required.'
                    memory = re.search(r'ValueError: (Free memory on device.+)', tail)
                    if memory:
                        reason = memory.group(1).strip()
                    else:
                        for line in reversed(tail.splitlines()):
                            for marker in ('RuntimeError:', 'AssertionError:', 'ValueError:'):
                                if marker in line:
                                    reason = line.split(marker, 1)[-1].strip() or reason
                                    break
                            else:
                                continue
                            break
                    return {
                        'success': False,
                        'error': f'vLLM exited before it was ready. {reason} See logs/runtimes/vllm.log.',
                        'detail': tail,
                    }
                time.sleep(1.0)
            else:
                self._terminate()
                return {'success': False, 'error': f'vLLM did not become ready on port {port} within 15 minutes'}
            # Memory retry: the failed process has already exited. Start again on the same port.

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
        # The Windows parent can exit and leave the Linux engine holding VRAM.
        manifest = self._read_manifest()
        if str(manifest.get('backend') or '').lower() == 'wsl':
            distro = str(manifest.get('wsl_distro') or 'Ubuntu')
            try:
                subprocess.run(
                    ['wsl', '-d', distro, '--', 'pkill', '-f', 'vllm.entrypoints.openai.api_server'],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
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
