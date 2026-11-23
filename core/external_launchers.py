"""Start, stop and size external model servers from the Console.

Strata (github.com/Niko1221/Strata) serves its model from its own process, not
from the Console.  This module keeps a small registry of such launchers so any
app can ask the Console to:

  * start the model and wait until it answers,
  * see whether it is running and how many requests it serves at once,
  * change that number (``--parallel N``) and restart it,
  * stop it again and free the graphics card.

The registry is a JSON file beside the Console (``external_launchers.json``) so
the settings survive restarts.  A launcher is discovered automatically from a
running Strata server, or from a Strata checkout on this PC, and can always be
registered by hand through the API.

Colibri (github.com/JustVugg/colibri) is the second launcher kind.  Its
``coli serve`` (a Python OpenAI-compatible server) starts one of Colibri's
engines (``glm53.exe`` for GLM-5.3-Flash, ...) and streams MoE experts from
the GGUF on disk.  A Colibri launcher names the GGUF it serves
(``model_path``) and the model directory ``coli import`` writes under the Colibri install
(``C:\\dev\\colibri\\models``, never beside the GGUF on ``D:\\models``);
the Console runs that import once before the first start.
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
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS_PATH = ROOT / 'external_launchers.json'
LOG_DIR = ROOT / 'logs'

_LOCK = threading.Lock()
_STARTING: dict[str, float] = {}

# Where a Strata checkout usually lives on a PC that has one.
_STRATA_ROOTS = (Path('C:/dev/Strata'), Path.home() / 'Strata', Path.home() / 'Documents' / 'Strata')

# Where Colibri's release archive is unpacked on a PC that has one.
_COLIBRI_ROOTS = (Path('C:/dev/colibri'), Path.home() / 'colibri')
# The engines a Colibri server starts; each is a separate .exe beside ``coli``.
COLIBRI_ENGINE_EXES = (
    'glm53', 'colibri', 'qwen36', 'qwen38', 'deepseek_v4', 'deepseek_v41',
    'kimi_k3', 'inkling', 'mimo', 'olmoe',
)
COLIBRI_DEFAULT_PORT = 8120

MIN_PARALLEL = 1
MAX_PARALLEL = 8
DEFAULT_WAIT_S = 300.0


def _hidden() -> dict[str, Any]:
    """Run a helper (query) without a window; its output is read by the caller."""
    if sys.platform != 'win32':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        'startupinfo': startupinfo,
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    }


def _detached() -> dict[str, Any]:
    """Start the model server with its own hidden console.

    A plain DETACHED_PROCESS leaves the engine without a console, and Windows
    then ends it with STATUS_CONTROL_C_EXIT (0xC000013A) as soon as the parent
    goes away.  Its own hidden console keeps it and the engine it starts alive.
    """
    if sys.platform != 'win32':
        return {'start_new_session': True}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        'startupinfo': startupinfo,
        'creationflags': (
            getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
            | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        ),
    }


def _http_json(url: str, *, timeout: float = 2.0, method: str = 'GET') -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(url, method=method, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8', 'replace'))
    except Exception:
        return None


def launcher_kind(entry: dict[str, Any] | None) -> str:
    """``strata``, ``colibri`` or the saved kind of a launcher."""
    if not isinstance(entry, dict):
        return ''
    kind = str(entry.get('kind') or '').strip().lower()
    if kind:
        return kind
    return str(entry.get('id') or '').strip().lower()


def launcher_runtime(entry: dict[str, Any] | None) -> dict[str, str]:
    """Display fields of the runtime behind a launcher (Engines card, badges)."""
    kind = launcher_kind(entry)
    if kind == 'colibri':
        return {
            'runtime_id': 'colibri',
            'runtime_label': 'Colibri',
            'source_label': 'Colibri local',
            'component_key': 'dflash.colibri',
            'component_label': 'Colibri engine',
        }
    return {
        'runtime_id': 'strata',
        'runtime_label': 'Strata',
        'source_label': 'Strata local',
        'component_key': 'dflash.strata',
        'component_label': 'Strata engine',
    }


def health_loaded(entry: dict[str, Any] | None, health: dict[str, Any] | None) -> bool:
    """Whether a launcher's /health answer means the model can take requests.

    Strata reports ``loaded: true``.  Colibri's ``coli serve`` answers
    ``{"status": "ok"}`` once its engine is up, and 503 while it starts.
    """
    if not isinstance(health, dict):
        return False
    if health.get('loaded'):
        return True
    if launcher_kind(entry) == 'colibri':
        return str(health.get('status') or '').strip().lower() == 'ok'
    return False


def _dflash_ownership_check(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Ask a running DFlash Console before starting a Strata launcher.

    The check is cooperative: if no Console is listening, Strata remains
    usable as a standalone runtime. A live Console's 409 response is returned
    to the caller so it can explain the ownership conflict.
    """
    if str(entry.get('kind') or entry.get('id') or '').strip().lower() != 'strata':
        return None
    base = str(
        entry.get('dflash_console_url')
        or os.environ.get('DFLASH_CONSOLE_URL')
        or 'http://127.0.0.1:8900'
    ).strip().rstrip('/')
    if not base:
        return None
    config = _config_data(entry)
    gpu = str(config.get('gpu_device') or config.get('gpu') or 'auto').strip()
    payload = json.dumps({
        'gpu_device': gpu or 'auto',
        'model_id': launcher_model_id(entry),
        'model_path': launcher_model_path(entry),
        'runtime_id': 'strata',
    }).encode('utf-8')
    request = urllib.request.Request(
        f'{base}/api/gpu/ownership/check',
        data=payload,
        method='POST',
        headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.5) as response:
            return json.loads(response.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            return None
        try:
            payload = json.loads(exc.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            payload = {}
        detail = payload.get('detail') if isinstance(payload, dict) else {}
        return {
            'success': False,
            'error': (detail or {}).get('error') if isinstance(detail, dict) else 'gpu_ownership_conflict',
            'message': (detail or {}).get('message') if isinstance(detail, dict) else 'GPU ownership conflict',
            'detail': detail,
        }
    except Exception:
        return None


def _system_exe(name: str) -> str:
    """Resolve a Windows tool even when this process was started with a bare PATH.

    The Console can be launched by a supervisor that only has its own venv on
    PATH.  ``powershell``/``taskkill`` then fail to resolve and the launcher
    would report no pids and refuse to stop the model.
    """
    found = shutil.which(name)
    if found:
        return found
    root = os.environ.get('SystemRoot') or r'C:\Windows'
    for candidate in (
        Path(root) / 'System32' / name,
        Path(root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / name,
    ):
        if candidate.is_file():
            return str(candidate)
    return name


def _read_registry() -> list[dict[str, Any]]:
    try:
        data = json.loads(LAUNCHERS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return []
    rows = data.get('launchers') if isinstance(data, dict) else data
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _write_registry(rows: list[dict[str, Any]]) -> None:
    try:
        LAUNCHERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHERS_PATH.write_text(
            json.dumps({'launchers': rows}, indent=1) + '\n',
            encoding='utf-8',
        )
    except Exception:
        pass


def _find_strata_config(root: Path) -> Path | None:
    try:
        candidates = sorted(
            (path for path in root.glob('strata-*.json') if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return None
    return candidates[0] if candidates else None


def discover_strata_launcher() -> dict[str, Any] | None:
    """A launcher for the Strata checkout on this PC, or None."""
    for root in _STRATA_ROOTS:
        server = root / 'serve' / 'server.py'
        if not server.is_file():
            continue
        config = _find_strata_config(root)
        if config is None:
            continue
        python = root / '.venv' / 'Scripts' / 'python.exe'
        if not python.is_file():
            python = root / '.venv' / 'bin' / 'python'
        if not python.is_file():
            python = Path(sys.executable)
        port = 8080
        model_name = ''
        try:
            cfg = json.loads(config.read_text(encoding='utf-8'))
            port = int(cfg.get('port') or 8080)
            model_name = str(cfg.get('model_name') or '')
        except Exception:
            pass
        label = f'Strata · {model_name}' if model_name else 'Strata'
        return {
            'id': 'strata',
            'label': label,
            'kind': 'strata',
            'command': str(python),
            'args': [str(server), '--engine', 'strata', '--config', str(config), '--port', str(port)],
            'cwd': str(root),
            'config': str(config),
            'port': port,
            'model_id': model_name,
            'health_url': f'http://127.0.0.1:{port}/health',
            'models_url': f'http://127.0.0.1:{port}/v1/models',
            'parallel': MIN_PARALLEL,
            'parallel_flag': '--parallel',
        }
    return None


def colibri_root() -> Path | None:
    """The folder holding Colibri's ``coli`` launcher and engines, or None."""
    env = str(os.environ.get('COLIBRI_HOME') or '').strip()
    for root in ([Path(env)] if env else []) + list(_COLIBRI_ROOTS):
        try:
            if (root / 'coli').is_file() and any((root / f'{name}.exe').is_file() for name in COLIBRI_ENGINE_EXES):
                return root
        except OSError:
            continue
    return None


def _colibri_python() -> str:
    """A real Python 3 for ``coli`` (stdlib only), never the Store alias stub."""
    for candidate in (
        os.environ.get('COLIBRI_PYTHON') or '',
        r'C:\Python314\python.exe',
        r'C:\Python313\python.exe',
        r'C:\Python312\python.exe',
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    found = shutil.which('py')
    if found:
        return found
    return sys.executable


def colibri_model_dir_for(gguf_path: str | Path) -> Path:
    """The directory ``coli import`` writes for a GGUF (first shard of a split).

    Import artifacts (config, tokenizer, gguf.json) live under the Colibri
    install, never next to the GGUF.  The weights stay in the model library
    on ``D:\\models``; ``coli import --mode link`` only points at them.
    """
    path = Path(str(gguf_path))
    stem = re.sub(r'-00001-of-\d{5}\.gguf$', '', path.name, flags=re.IGNORECASE)
    stem = re.sub(r'\.gguf$', '', stem, flags=re.IGNORECASE)
    root = colibri_root() or Path(r'C:\dev\colibri')
    return root / 'models' / f'{stem}.colibri'


def _colibri_slug(text: str) -> str:
    slug = re.sub(r'[^a-z0-9._-]+', '-', str(text or '').strip().lower()).strip('-')
    return slug[:64] or 'model'


def colibri_launcher_for_gguf(
    gguf_path: str | Path,
    *,
    port: int = COLIBRI_DEFAULT_PORT,
    context: int = 32768,
    gpu: int = 0,
    gpu2: int | None = 1,
    launcher_id: str = '',
) -> dict[str, Any] | None:
    """A Colibri launcher entry that serves one GGUF (first shard of a split).

    The engine runs on Vulkan: the GLM-5.3-Flash engine (glm53.exe) has no
    CUDA build on Windows.  ``gpu`` is the primary Vulkan device (its expert
    tier and dense chain), ``gpu2`` a second card for more experts.
    """
    root = colibri_root()
    if root is None:
        return None
    gguf = Path(str(gguf_path))
    model_dir = colibri_model_dir_for(gguf)
    stem = model_dir.name[:-len('.colibri')]
    model_id = _colibri_slug(stem)
    entry_id = launcher_id or f'colibri-{model_id}'[:80]
    env = {
        'COLI_VULKAN': '1',
        'COLI_CUDA': '0',
        'COLI_VK_DEV': str(int(gpu)),
        # Keep room on the card for the Console's other engines.
        'COLI_VK_TIER_RESERVE_GB': '4',
    }
    if gpu2 is not None and int(gpu2) != int(gpu):
        env['COLI_VK_DEV2'] = str(int(gpu2))
        env['COLI_VK_RESERVE2_GB'] = '2'
    return {
        'id': entry_id,
        'label': f'Colibri \u00b7 {stem}',
        'kind': 'colibri',
        'command': _colibri_python(),
        'args': [
            str(root / 'coli'), 'serve',
            '--model', str(model_dir),
            '--host', '127.0.0.1',
            '--port', str(int(port)),
            '--model-id', model_id,
            '--ctx', str(int(context)),
        ],
        'cwd': str(root),
        'port': int(port),
        'model_id': model_id,
        'model_path': str(gguf),
        'model_dir': str(model_dir),
        'max_context': int(context),
        'gpu': int(gpu),
        'env': env,
        'health_url': f'http://127.0.0.1:{int(port)}/health',
        'models_url': f'http://127.0.0.1:{int(port)}/v1/models',
        'parallel': MIN_PARALLEL,
        'parallel_flag': '--kv-slots',
    }


def register_colibri_launcher(gguf_path: str, **options: Any) -> dict[str, Any]:
    """Save (or refresh) the Colibri launcher that serves ``gguf_path``.

    Settings the user changed on an existing entry (slots, who loaded it) are
    kept; the command line is rebuilt from the current Colibri folder.
    """
    wanted = {key: value for key, value in options.items() if value is not None or key == 'gpu2'}
    entry = colibri_launcher_for_gguf(gguf_path, **wanted)
    if entry is None:
        return {'success': False, 'error': 'Colibri is not installed (no coli launcher found in C:\\dev\\colibri)'}
    existing = get_launcher(entry['id'])
    if existing:
        for key in ('parallel', 'loaded_by', 'start_timeout_s'):
            if existing.get(key) is not None:
                entry[key] = existing[key]
    entry.setdefault('start_timeout_s', 900)
    return {'success': True, 'launcher': save_launcher(entry)}


def launchers() -> list[dict[str, Any]]:
    """Saved launchers plus a discovered Strata entry when no file exists yet."""
    rows = _read_registry()
    if not rows:
        discovered = discover_strata_launcher()
        if discovered is not None:
            rows = [discovered]
            _write_registry(rows)
    return rows


def launcher_model_id(entry: dict[str, Any] | None) -> str:
    """The model a launcher serves, from the saved entry or its Strata config."""
    if not isinstance(entry, dict):
        return ''
    for key in ('model_id', 'api_model_id', 'model'):
        value = str(entry.get(key) or '').strip()
        if value:
            return value
    config = str(entry.get('config') or '').strip()
    if config:
        try:
            data = json.loads(Path(config).read_text(encoding='utf-8'))
            if isinstance(data, dict):
                for key in ('model_name', 'model_id', 'model'):
                    value = str(data.get(key) or '').strip()
                    if value:
                        return value
        except Exception:
            pass
    label = str(entry.get('label') or '').strip()
    if '\u00b7' in label:
        tail = label.split('\u00b7', 1)[1].strip()
        if tail:
            return tail
    return ''


def _config_data(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    config = str(entry.get('config') or '').strip()
    if not config:
        return {}
    try:
        data = json.loads(Path(config).read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def launcher_context_size(entry: dict[str, Any] | None) -> int:
    """The context length configured for the launcher's engine."""
    try:
        saved = int((entry or {}).get('max_context') or 0)
    except (TypeError, ValueError):
        saved = 0
    if saved > 0:
        return saved
    data = _config_data(entry)
    for key in ('max_context', 'context_size', 'context'):
        try:
            value = int(data.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    for key in ('args', 'args_written'):
        args = data.get(key)
        if not isinstance(args, list):
            continue
        for index, arg in enumerate(args):
            if str(arg) != '--max-context' or index + 1 >= len(args):
                continue
            try:
                value = int(args[index + 1])
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return 0


def launcher_model_path(entry: dict[str, Any] | None) -> str:
    """The target model file named by the launcher config, when it exists."""
    saved = str((entry or {}).get('model_path') or '').strip()
    if saved and Path(saved).is_file():
        return saved
    data = _config_data(entry)
    for key in ('args', 'args_written'):
        args = data.get(key)
        if not isinstance(args, list):
            continue
        for index, arg in enumerate(args):
            if str(arg) != '--native' or index + 1 >= len(args):
                continue
            path = str(args[index + 1]).strip()
            if path and Path(path).is_file():
                return path
    return ''


def launcher_for_process(*, process_name: str = '', command_line: str = '') -> dict[str, Any] | None:
    """The registered launcher that owns a running process, if any.

    Strata's engine is ``strata.exe``; its server is a Python process whose
    command line names ``--engine strata`` and the launcher config.  Both must
    resolve to the same Console-managed engine instead of an external app.
    """
    name = str(process_name or '').strip().lower()
    cmd = str(command_line or '').strip().lower().replace('/', '\\')
    hay = f'{name} {cmd}'
    if not hay.strip():
        return None
    for entry in launchers():
        entry_id = str(entry.get('id') or '').strip().lower()
        kind = str(entry.get('kind') or '').strip().lower()
        if kind == 'colibri':
            port = str(entry.get('port') or '').strip()
            root = str(entry.get('cwd') or '').strip().lower().replace('/', '\\').rstrip('\\')
            exe = re.search(r'(?:^|[\\/])(' + '|'.join(COLIBRI_ENGINE_EXES) + r')\.exe$', name)
            if exe and root and (root + '\\') in cmd:
                # The engine itself: its command line names the Colibri folder.
                return dict(entry)
            if exe and not cmd and colibri_root() is not None:
                return dict(entry)
            if 'coli' in cmd and ' serve' in cmd and port and (f'--port {port}' in cmd or f'--port={port}' in cmd):
                return dict(entry)
            continue
        if entry_id == 'strata' or kind == 'strata':
            if (
                re.search(r'(?:^|[\\/])strata(?:-vision)?\.exe(?:\s|$)', name)
                or '--engine strata' in cmd
                or '\\strata\\serve\\server.py' in cmd
            ):
                return dict(entry)
            continue
        config = str(entry.get('config') or '').strip().lower().replace('/', '\\')
        if config and config in cmd:
            return dict(entry)
        args = entry.get('args') if isinstance(entry.get('args'), list) else []
        script = next(
            (str(arg) for arg in args if str(arg).lower().replace('/', '\\').endswith('server.py')),
            '',
        )
        port = str(entry.get('port') or '').strip()
        if script and script.lower().replace('/', '\\') in cmd:
            if not port or f'--port {port}' in cmd or f'--port={port}' in cmd:
                return dict(entry)
    return None


def get_launcher(launcher_id: str) -> dict[str, Any] | None:
    wanted = str(launcher_id or '').strip().lower()
    for row in launchers():
        if str(row.get('id') or '').strip().lower() == wanted:
            return dict(row)
    return None


def save_launcher(entry: dict[str, Any]) -> dict[str, Any]:
    """Add or replace a launcher (by id) and keep the registry on disk."""
    entry = {**entry, 'id': str(entry.get('id') or '').strip() or 'external'}
    rows = [row for row in launchers() if str(row.get('id') or '').lower() != entry['id'].lower()]
    rows.append(entry)
    _write_registry(rows)
    return entry


def set_parallel(launcher_id: str, parallel: int) -> dict[str, Any] | None:
    entry = get_launcher(launcher_id)
    if entry is None:
        return None
    entry['parallel'] = _clamp_parallel(parallel)
    return save_launcher(entry)


def _clamp_parallel(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return MIN_PARALLEL
    return max(MIN_PARALLEL, min(MAX_PARALLEL, number))


def _processes_for(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Processes that belong to this launcher (Windows: one WMI query)."""
    config = str(entry.get('config') or '')
    script = ''
    args = entry.get('args') if isinstance(entry.get('args'), list) else []
    for arg in args:
        if str(arg).lower().endswith('server.py'):
            script = str(arg)
            break
    port = str(entry.get('port') or '')
    if sys.platform != 'win32':
        return []
    if launcher_kind(entry) == 'colibri':
        coli = next((str(arg) for arg in args if str(arg).lower().replace('/', '\\').endswith('\\coli')), '')
        if not coli or not port:
            return []
        pattern = coli.replace("'", "''")
        query = (
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -like 'py*' "
            f"-and $_.CommandLine -like '*{pattern}*' -and $_.CommandLine -like '* serve *' "
            f"-and $_.CommandLine -like '*--port {port}*' "
            "} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        )
    elif config:
        pattern = config.replace("'", "''")
        query = (
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -like 'python*' -and $_.CommandLine -like '*server.py*' "
            f"-and $_.CommandLine -like '*{pattern}*' "
            "} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        )
    elif script:
        pattern = script.replace("'", "''")
        query = (
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -like 'python*' "
            f"-and $_.CommandLine -like '*{pattern}*' -and $_.CommandLine -like '*--port {port}*' "
            "} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        )
    else:
        return []
    try:
        result = subprocess.run(
            [_system_exe('powershell'), '-NoProfile', '-NonInteractive', '-Command', query],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            **_hidden(),
        )
        raw = (result.stdout or '').strip()
        if not raw:
            return []
        data = json.loads(raw)
        rows = data if isinstance(data, list) else [data]
        return [row for row in rows if isinstance(row, dict)]
    except Exception:
        return []


def _start_timeout(entry: dict[str, Any] | None, requested: float) -> float:
    """A launcher may need longer than the default to answer (Colibri fills
    its expert tier from a 100 GB GGUF); ``start_timeout_s`` raises the wait."""
    try:
        saved = float((entry or {}).get('start_timeout_s') or 0)
    except (TypeError, ValueError):
        saved = 0.0
    return max(float(requested), saved)


def status(entry: dict[str, Any], *, probe: bool = True) -> dict[str, Any]:
    """Running state, model and request slots of a launcher."""
    health = _http_json(str(entry.get('health_url') or ''), timeout=2.0) if probe else None
    pids = [int(row.get('ProcessId')) for row in _processes_for(entry) if row.get('ProcessId')]
    loaded = health_loaded(entry, health)
    if loaded:
        # The server answers, so a pending start marker is stale.
        _STARTING.pop(str(entry.get('id') or ''), None)
    starting_since = _STARTING.get(str(entry.get('id') or ''))
    start_window = _start_timeout(entry, DEFAULT_WAIT_S)
    return {
        **entry,
        'running': health is not None,
        'loaded': loaded,
        'model': str((health or {}).get('model') or (launcher_model_id(entry) if loaded else '')),
        'max_context': (health or {}).get('max_context') or (health or {}).get('context_tokens') or (entry.get('max_context') if loaded else None),
        'images': (health or {}).get('images'),
        'pids': pids,
        'starting': bool(starting_since) and (time.time() - (starting_since or 0) < start_window),
    }


def list_with_status() -> list[dict[str, Any]]:
    return [status(entry) for entry in launchers()]


def _args_with_parallel(entry: dict[str, Any], parallel: int) -> list[str]:
    args = [str(arg) for arg in (entry.get('args') or [])]
    flag = str(entry.get('parallel_flag') or '--parallel')
    cleaned: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == flag:
            skip_next = True
            continue
        cleaned.append(arg)
    if parallel > 1:
        cleaned += [flag, str(parallel)]
    return cleaned


def note_launcher_client(
    launcher_id: str,
    client_label: Any,
    *,
    allow_console: bool = False,
) -> dict[str, Any] | None:
    """Remember which app asked the Console to start this launcher."""
    label = str(client_label or '').strip()
    if not label or label.lower() == 'unknown api client':
        return None
    if not allow_console and label.lower() == 'dflash console':
        return None
    entry = get_launcher(launcher_id)
    if entry is None or str(entry.get('loaded_by') or '').strip() == label:
        return entry
    entry = {**entry, 'loaded_by': label}
    save_launcher(entry)
    return entry


def _apply_gpu_device(entry: dict[str, Any], gpu_device: Any) -> bool:
    """Write a chosen GPU index into the launcher config.

    ``auto`` and an empty value leave the file alone. The first ``gpu`` field
    is the engine card; a later ``gpu: true`` inside vision must stay.
    @returns whether the saved card changed, so a running server restarts on it.
    """
    text = str(gpu_device or '').strip().lower()
    if text in ('', 'auto') or not text.isdigit():
        return False
    path = Path(str(entry.get('config') or '').strip())
    if not path.is_file():
        return False
    try:
        original = path.read_text(encoding='utf-8')
    except Exception:
        return False
    updated, count = re.subn(
        r'("gpu"\s*:\s*)(-?\d+|true|false|"[^"]*")',
        lambda match: f'{match.group(1)}{int(text)}',
        original,
        count=1,
    )
    if count == 0 or updated == original:
        return False
    path.write_text(updated, encoding='utf-8')
    return True


def prepare_colibri_model(entry: dict[str, Any], *, timeout_s: float = 900.0) -> dict[str, Any]:
    """Run ``coli import`` once so the GGUF has its Colibri model directory.

    The import reads the GGUF header and writes config, tokenizer, chat
    template and ``gguf.json`` (a pointer to the file); the weights stay where
    they are (``--mode link``).
    """
    model_dir = Path(str(entry.get('model_dir') or ''))
    gguf = Path(str(entry.get('model_path') or ''))
    if model_dir and (model_dir / 'config.json').is_file() and (model_dir / 'gguf.json').is_file():
        return {'success': True, 'imported': False, 'model_dir': str(model_dir)}
    if not gguf.is_file():
        return {'success': False, 'error': f'model file is not on disk yet: {gguf}'}
    match = re.search(r'-(\d{5})-of-(\d{5})\.gguf$', gguf.name, re.IGNORECASE)
    if match:
        total = int(match.group(2))
        prefix = gguf.name[: match.start()]
        missing = [
            f'{prefix}-{index:05d}-of-{total:05d}.gguf'
            for index in range(1, total + 1)
            if not (gguf.parent / f'{prefix}-{index:05d}-of-{total:05d}.gguf').is_file()
        ]
        if missing:
            return {'success': False, 'error': 'model download is not complete: missing ' + ', '.join(missing)}
    coli = next(
        (str(arg) for arg in (entry.get('args') or []) if str(arg).lower().replace('/', '\\').endswith('\\coli')),
        '',
    )
    if not coli:
        return {'success': False, 'error': 'launcher does not name the coli script'}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'external-{entry.get("id")}-import.log'
    argv = [str(entry.get('command') or sys.executable), coli, 'import', str(gguf), '--out', str(model_dir)]
    env = {**os.environ, **{str(k): str(v) for k, v in (entry.get('env') or {}).items()}, 'NO_COLOR': '1'}
    try:
        with log_path.open('ab') as log_file:
            log_file.write(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} import: {" ".join(argv)}\n'.encode('utf-8'))
            log_file.flush()
            result = subprocess.run(
                argv,
                cwd=str(entry.get('cwd') or ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
                env=env,
                check=False,
                **_hidden(),
            )
    except Exception as exc:
        return {'success': False, 'error': f'coli import failed: {exc}', 'log': str(log_path)}
    if result.returncode != 0 or not (model_dir / 'config.json').is_file():
        return {
            'success': False,
            'error': f'coli import exited with {result.returncode}',
            'log': str(log_path),
        }
    return {'success': True, 'imported': True, 'model_dir': str(model_dir), 'log': str(log_path)}


def start(
    launcher_id: str,
    *,
    parallel: Any = None,
    wait: bool = True,
    timeout_s: float = DEFAULT_WAIT_S,
    client_label: str = '',
    gpu_device: Any = None,
) -> dict[str, Any]:
    """Start a launcher (or restart it with another slot count) and report."""
    entry = get_launcher(launcher_id)
    if entry is None:
        return {'success': False, 'error': f'unknown launcher: {launcher_id}'}
    entry = note_launcher_client(launcher_id, client_label, allow_console=True) or entry

    wanted_parallel = _clamp_parallel(parallel if parallel is not None else entry.get('parallel') or MIN_PARALLEL)
    gpu_changed = _apply_gpu_device(entry, gpu_device)
    with _LOCK:
        current = status(entry)
        # A load already in progress must never be restarted: killing it mid-load
        # made every extra load call throw away several minutes of model loading.
        if current.get('starting') and not current.get('running'):
            if not gpu_changed:
                return {
                    'success': True,
                    'starting': True,
                    'already_starting': True,
                    **current,
                    'parallel': wanted_parallel,
                }
            stop(launcher_id)
        if current['running']:
            if not gpu_changed and wanted_parallel == _clamp_parallel(entry.get('parallel') or MIN_PARALLEL):
                if parallel is not None:
                    set_parallel(launcher_id, wanted_parallel)
                return {'success': True, 'already_running': True, **current, 'parallel': wanted_parallel}
            stop(launcher_id)
        set_parallel(launcher_id, wanted_parallel)
        entry = get_launcher(launcher_id) or entry
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f'external-{entry["id"]}.log'
        ownership = _dflash_ownership_check(entry)
        if ownership and ownership.get('success') is False:
            return {
                'success': False,
                'error': str(ownership.get('message') or 'DFlash Console rejected the GPU claim'),
                'error_code': str(ownership.get('error') or 'gpu_ownership_conflict'),
                'gpu_ownership': ownership.get('detail') or ownership,
            }
        if launcher_kind(entry) == 'colibri':
            prepared = prepare_colibri_model(entry)
            if not prepared.get('success'):
                return {**prepared, 'success': False}
        argv = [str(entry.get('command') or sys.executable), *_args_with_parallel(entry, wanted_parallel)]
        extra_env = entry.get('env') if isinstance(entry.get('env'), dict) else {}
        popen_env = {**os.environ, **{str(k): str(v) for k, v in extra_env.items()}} if extra_env else None
        try:
            with log_path.open('ab') as log_file:
                log_file.write(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} starting: {" ".join(argv)}\n'.encode('utf-8'))
                log_file.flush()
                process = subprocess.Popen(
                    argv,
                    cwd=str(entry.get('cwd') or ROOT),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                    env=popen_env,
                    **_detached(),
                )
        except Exception as exc:
            return {'success': False, 'error': f'could not start {entry.get("id")}: {exc}'}
        _STARTING[str(entry.get('id'))] = time.time()
        if not wait:
            return {
                'success': True,
                'starting': True,
                'pid': process.pid,
                'parallel': wanted_parallel,
                'log': str(log_path),
                'message': f'{entry.get("label") or entry.get("id")} is starting',
            }

        timeout_s = _start_timeout(entry, timeout_s)
        deadline = time.time() + max(30.0, float(timeout_s))
        while time.time() < deadline:
            health = _http_json(str(entry.get('health_url') or ''), timeout=2.0)
            if health_loaded(entry, health):
                _STARTING.pop(str(entry.get('id')), None)
                return {
                    'success': True,
                    'starting': False,
                    'pid': process.pid,
                    'parallel': wanted_parallel,
                    'log': str(log_path),
                    **status(entry),
                }
            if process.poll() is not None:
                _STARTING.pop(str(entry.get('id')), None)
                return {
                    'success': False,
                    'error': f'{entry.get("label") or entry.get("id")} exited while starting',
                    'exit_code': process.poll(),
                    'log': str(log_path),
                }
            time.sleep(2.0)
        _STARTING.pop(str(entry.get('id')), None)
        return {
            'success': False,
            'error': f'{entry.get("label") or entry.get("id")} did not answer within {int(timeout_s)} s',
            'log': str(log_path),
            'still_starting': process.poll() is None,
        }


def stop(launcher_id: str) -> dict[str, Any]:
    """Stop a launcher and wait until its port stops answering."""
    entry = get_launcher(launcher_id)
    if entry is None:
        return {'success': False, 'error': f'unknown launcher: {launcher_id}'}
    pids = [int(row.get('ProcessId')) for row in _processes_for(entry) if row.get('ProcessId')]
    killed: list[int] = []
    for pid in pids:
        try:
            result = subprocess.run(
                [_system_exe('taskkill.exe'), '/F', '/T', '/PID', str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                **_hidden(),
            )
            if result.returncode == 0:
                killed.append(pid)
        except Exception:
            continue
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _http_json(str(entry.get('health_url') or ''), timeout=1.0) is None:
            break
        time.sleep(0.5)
    _STARTING.pop(str(entry.get('id')), None)
    # taskkill can refuse a process that already exited (or a duplicate server
    # that lost the port); what matters is that nothing is left answering.
    still_running = _http_json(str(entry.get('health_url') or ''), timeout=1.0) is not None
    remaining = [int(row.get('ProcessId')) for row in _processes_for(entry) if row.get('ProcessId')]
    if not still_running and not remaining:
        return {
            'success': True,
            'stopped': True,
            'was_running': bool(pids),
            'killed_pids': killed,
            'running': False,
        }
    return {
        'success': False,
        'error': 'could not stop the process',
        'pids': remaining or pids,
        'running': still_running,
    }
