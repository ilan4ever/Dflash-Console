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
    if config:
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


def status(entry: dict[str, Any], *, probe: bool = True) -> dict[str, Any]:
    """Running state, model and request slots of a launcher."""
    health = _http_json(str(entry.get('health_url') or ''), timeout=2.0) if probe else None
    pids = [int(row.get('ProcessId')) for row in _processes_for(entry) if row.get('ProcessId')]
    loaded = bool((health or {}).get('loaded'))
    if loaded:
        # The server answers, so a pending start marker is stale.
        _STARTING.pop(str(entry.get('id') or ''), None)
    starting_since = _STARTING.get(str(entry.get('id') or ''))
    return {
        **entry,
        'running': health is not None,
        'loaded': loaded,
        'model': str((health or {}).get('model') or ''),
        'max_context': (health or {}).get('max_context'),
        'images': (health or {}).get('images'),
        'pids': pids,
        'starting': bool(starting_since) and (time.time() - (starting_since or 0) < DEFAULT_WAIT_S),
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


def start(
    launcher_id: str,
    *,
    parallel: Any = None,
    wait: bool = True,
    timeout_s: float = DEFAULT_WAIT_S,
    client_label: str = '',
) -> dict[str, Any]:
    """Start a launcher (or restart it with another slot count) and report."""
    entry = get_launcher(launcher_id)
    if entry is None:
        return {'success': False, 'error': f'unknown launcher: {launcher_id}'}
    entry = note_launcher_client(launcher_id, client_label, allow_console=True) or entry

    wanted_parallel = _clamp_parallel(parallel if parallel is not None else entry.get('parallel') or MIN_PARALLEL)
    with _LOCK:
        current = status(entry)
        # A load already in progress must never be restarted: killing it mid-load
        # made every extra load call throw away several minutes of model loading.
        if current.get('starting') and not current.get('running'):
            return {
                'success': True,
                'starting': True,
                'already_starting': True,
                **current,
                'parallel': wanted_parallel,
            }
        if current['running']:
            if wanted_parallel == _clamp_parallel(entry.get('parallel') or MIN_PARALLEL):
                if parallel is not None:
                    set_parallel(launcher_id, wanted_parallel)
                return {'success': True, 'already_running': True, **current, 'parallel': wanted_parallel}
            stop(launcher_id)
        set_parallel(launcher_id, wanted_parallel)
        entry = get_launcher(launcher_id) or entry
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f'external-{entry["id"]}.log'
        argv = [str(entry.get('command') or sys.executable), *_args_with_parallel(entry, wanted_parallel)]
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

        deadline = time.time() + max(30.0, float(timeout_s))
        while time.time() < deadline:
            health = _http_json(str(entry.get('health_url') or ''), timeout=2.0)
            if health is not None and health.get('loaded'):
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
