"""Detect GPU compute processes from other apps (LM Studio, Whisper, etc.)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.gpu_devices import format_gpu_display_name
from core.runtime import (
    _loaded_model_ids,
    api_base_url,
    router_unload_available,
    tcp_port_open,
    unload_model,
)

_MIN_VRAM_MIB = 32

# GGUF split-shard naming, e.g. ``Laguna-...-00001-of-00003.gguf``. Only the
# first shard holds the header (it is tiny), so disk size must sum every part.
_SPLIT_SHARD_RE = re.compile(
    r'^(?P<prefix>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})(?P<suffix>\.gguf)$',
    re.I,
)

_DESKTOP_NOISE = re.compile(
    r'(?:^|[\\/])(?:explorer|ShellHost|SearchHost|StartMenuExperienceHost|LockApp|'
    r'ShellExperienceHost|CrossDeviceResume|TextInputHost|ApplicationFrameHost|'
    r'SystemSettings|msedge|msedgewebview2|Discord|ShareX|Telegram|WhatsApp|'
    r'PhoneExperienceHost|DCv2|Cursor)\.exe$',
    re.I,
)

_ML_PROCESS = re.compile(
    r'llama-server|ollama|whisper|piper|kobold|comfyui|text-generation-webui|'
    r'faster-whisper|whisper\.cpp',
    re.I,
)

_ML_CMD = re.compile(
    r'\.gguf|\.bin|\.onnx|whisper|faster.whisper|llama-server|ollama|piper|'
    r'transcribe|speech|cuda|torch|transformers|llama\.cpp|speak_stt|'
    r'\\stt\\|/stt/|onevoice',
    re.I,
)

_APP_SERVER_CMD = re.compile(
    r'(?:\bserver\.py\b|\bapp\.py\b|\bweb_ui\.py\b|\bui_backend\b|'
    r'\buvicorn\b|\bgunicorn\b|\bflask\b|\bdjango\b|'
    r'\bstt_manager\.py\b|\bmuninn\b|\btts_webhook\b|\bwebhook_bridge\b)',
    re.I,
)


def _infer_model_kind(
    *,
    model_name: str = '',
    model_path: str = '',
    command_line: str = '',
    process_name: str = '',
    role: str = '',
) -> tuple[str, str]:
    hay = f'{model_name} {model_path} {command_line} {process_name} {role}'.lower()
    if 'speak_stt' in hay or 'whisper' in hay or 'small.en' in hay or 'faster-whisper' in hay:
        return 'stt', 'Speech-to-text'
    if '--embedding' in hay or 'nomic-embed' in hay or 'embed-text' in hay or '/embed/' in hay.replace('\\', '/'):
        return 'embedding', 'Embedding'
    if 'piper' in hay or ('/tts/' in hay.replace('\\', '/') and 'stt' not in hay):
        return 'tts', 'Text-to-speech'
    if 'ocr' in hay or 'chandra' in hay or 'lightonocr' in hay or 'ovisocr' in hay:
        return 'ocr', 'OCR'
    if 'onevoice ui server' in hay or ('server.py' in hay and 'onevoice' in hay):
        return 'app', 'App server'
    if role in {'draft-dflash', 'draft-dspark'}:
        return 'llm', 'LLM draft'
    if '.gguf' in hay or 'llama-server' in hay or role in {'alias', 'target'}:
        return 'llm', 'LLM'
    if str(process_name or '').lower().endswith(('python.exe', 'pythonw.exe')):
        return 'app', 'App'
    return 'other', 'Model'


def _model_kind_fields(
    *,
    model_name: str = '',
    model_path: str = '',
    command_line: str = '',
    process_name: str = '',
    role: str = '',
) -> dict[str, str]:
    kind, label = _infer_model_kind(
        model_name=model_name,
        model_path=model_path,
        command_line=command_line,
        process_name=process_name,
        role=role,
    )
    return {'model_kind': kind, 'model_kind_label': label}


def _parse_pid(raw: str) -> int | None:
    text = str(raw or '').strip()
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if pid > 0 else None


def _parse_vram_mib(raw: str) -> float | None:
    text = str(raw or '').strip()
    if not text or text.startswith('['):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _should_track_process(
    *,
    process_name: str,
    command_line: str,
    parent_name: str,
    vram_mib: float | None,
    app_source: str,
) -> bool:
    if _DESKTOP_NOISE.search(process_name):
        return False
    if vram_mib is not None and vram_mib >= _MIN_VRAM_MIB:
        return True
    hay = f'{process_name} {command_line} {parent_name}'
    if _ML_PROCESS.search(hay):
        return True
    if _ML_CMD.search(hay):
        return True
    if app_source in {'lmstudio', 'ollama', 'whisper', 'piper', 'comfyui', 'kobold', 'textgen', 'llama-server'}:
        return True
    if app_source == 'onevoice':
        if vram_mib is not None and vram_mib >= _MIN_VRAM_MIB:
            return True
        if _ML_PROCESS.search(hay) or _ML_CMD.search(hay):
            return True
        return False
    return False


def _is_gpu_model_load(
    *,
    process_name: str,
    command_line: str,
    model_name: str,
    model_path: str,
    model_id: str = '',
    api_url: str = '',
    vram_mib: float | None = None,
) -> bool:
    hay = f'{process_name} {command_line} {model_name} {model_path}'.lower()
    if _APP_SERVER_CMD.search(hay):
        return False
    if model_id and api_url:
        return True
    if 'llama-server' in hay or 'llama_server' in hay:
        return bool('.gguf' in hay or model_path or '--embedding' in hay or model_id)
    if 'speak_stt.py' in hay or 'faster-whisper' in hay or 'faster_whisper' in hay:
        return bool(str(model_name or '').strip())
    if '.gguf' in hay or str(model_path or '').lower().endswith('.gguf'):
        return True
    if '--embedding' in hay:
        return True
    if vram_mib is not None and vram_mib >= _MIN_VRAM_MIB:
        return bool(_ML_PROCESS.search(hay) or _ML_CMD.search(hay))
    return False


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


def _run_nvidia_smi(args: list[str], *, timeout: float = 4) -> str:
    try:
        result = subprocess.run(
            ['nvidia-smi', *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def _gpu_uuid_map() -> dict[str, int]:
    text = _run_nvidia_smi(['--query-gpu=index,uuid', '--format=csv,noheader'])
    mapping: dict[str, int] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 2:
            continue
        try:
            mapping[parts[1]] = int(parts[0])
        except (TypeError, ValueError):
            continue
    return mapping


def query_compute_apps() -> list[dict[str, Any]]:
    """Return GPU compute processes with VRAM usage from nvidia-smi."""
    return _query_compute_apps()


def query_compute_vram_map() -> dict[int, float]:
    result: dict[int, float] = {}
    for row in query_compute_apps():
        pid = row.get('pid')
        vram_gb = row.get('vram_gb')
        if pid is None or vram_gb is None:
            continue
        try:
            result[int(pid)] = float(vram_gb)
        except (TypeError, ValueError):
            continue
    return result


def vram_gb_for_port(
    port: int,
    host: str = '127.0.0.1',
    *,
    vram_map: dict[int, float] | None = None,
) -> float | None:
    pid = _pid_listening_on_port(port, host)
    if pid is None:
        return None
    if vram_map is None:
        vram_map = query_compute_vram_map()
    return vram_map.get(int(pid))


def _size_gb_from_path(path: str) -> float | None:
    if not path:
        return None
    try:
        resolved = Path(path)
        if resolved.is_file():
            # Split-shard GGUF: the first shard is mostly header (a few MB), so
            # report the SUM of all shards instead of the first part alone.
            match = _SPLIT_SHARD_RE.match(resolved.name)
            if match:
                total = 0
                for sibling in resolved.parent.glob(
                    f"{match.group('prefix')}-*-of-{match.group('total')}{match.group('suffix')}"
                ):
                    if not sibling.is_file():
                        continue
                    try:
                        total += sibling.stat().st_size
                    except OSError:
                        continue
                if total > 0:
                    return round(total / (1024 ** 3), 2)
            return round(resolved.stat().st_size / (1024 ** 3), 2)
        if resolved.is_dir():
            total = 0
            for item in resolved.rglob('*'):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        continue
            if total > 0:
                return round(total / (1024 ** 3), 2)
    except OSError:
        pass
    return None


def _stt_hub_search_roots() -> list[Path]:
    home = Path.home()
    return [
        home / 'AppData' / 'Local' / 'OneVoiceSpeakData' / 'models' / 'stt' / 'huggingface' / 'hub',
        home / '.cache' / 'huggingface' / 'hub',
    ]


def _resolve_stt_model_path(model_name: str, command_line: str = '') -> str:
    hinted_name, hinted_path = _model_hint_from_cmdline(command_line)
    if hinted_path:
        hinted = Path(hinted_path)
        if hinted.is_file():
            return str(hinted.parent)
        if hinted.is_dir():
            return str(hinted)
    slug = str(model_name or hinted_name or '').strip()
    if not slug:
        return ''
    slug_variants = {slug, slug.replace('.', '-'), slug.replace('-', '.')}
    for root in _stt_hub_search_roots():
        if not root.is_dir():
            continue
        for variant in slug_variants:
            for match in root.glob(f'models--*--faster-whisper-{variant}'):
                snapshots = match / 'snapshots'
                if snapshots.is_dir():
                    candidates = sorted(
                        (p for p in snapshots.iterdir() if p.is_dir()),
                        key=lambda item: item.stat().st_mtime,
                        reverse=True,
                    )
                    if candidates:
                        return str(candidates[0])
                return str(match)
    return ''


def _quant_from_name(name: str) -> str:
    match = re.search(r'(?:^|[._-])(Q\d+(?:_[A-Z0-9]+)*|Q\d+[A-Z0-9_]*|fp16|bf16|f16|f32)(?:[._-]|$)', str(name or ''), re.I)
    return match.group(1).upper() if match else ''


def _external_card_detail(
    *,
    model_kind: str,
    model_name: str,
    model_path: str,
    command_line: str,
    listen_port: int | None = None,
) -> str:
    hay = f'{model_name} {model_path} {command_line}'.lower()
    parts: list[str] = []
    if model_kind == 'stt':
        engine = 'faster-whisper' if 'speak_stt' in hay or 'faster-whisper' in hay or 'faster_whisper' in hay else 'Whisper'
        parts.extend(['Whisper', engine])
        if model_name and model_name.lower() not in {'whisper', 'onevoice'}:
            parts.append(model_name)
    elif model_kind == 'embedding':
        parts.append('Embedding model')
        if 'nomic-embed' in hay:
            parts.append('nomic-embed-text v1.5')
        quant = _quant_from_name(model_name) or _quant_from_name(model_path)
        if quant:
            parts.append(quant)
        if '--embedding' in hay or 'embedding' in hay:
            parts.append('mean pooling')
    elif model_kind == 'llm':
        parts.append('LLM')
        if '.gguf' in hay:
            parts.append('GGUF')
        quant = _quant_from_name(model_name) or _quant_from_name(model_path)
        if quant:
            parts.append(quant)
    elif model_kind == 'tts':
        parts.extend(['Text-to-speech', model_name or 'voice model'])
    elif model_kind == 'ocr':
        parts.extend(['OCR model', model_name or 'vision model'])
    else:
        if model_name:
            parts.append(model_name)
    if listen_port and model_kind in {'embedding', 'llm'}:
        parts.append(f'port {listen_port}')
    return ' · '.join(part for part in parts if part)


def _query_compute_apps() -> list[dict[str, Any]]:
    text = _run_nvidia_smi([
        '--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory',
        '--format=csv,noheader,nounits',
    ])
    if not text:
        return []

    uuid_map = _gpu_uuid_map()
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 4:
            continue
        gpu_uuid, pid_raw, process_name, vram_raw = parts[0], parts[1], parts[2], parts[3]
        pid = _parse_pid(pid_raw)
        if pid is None:
            continue
        vram_mib = _parse_vram_mib(vram_raw)
        gpu_index = uuid_map.get(gpu_uuid)
        if gpu_index is None:
            continue
        rows.append({
            'gpu_index': gpu_index,
            'pid': pid,
            'process_name': process_name,
            'vram_mb': round(vram_mib, 1) if vram_mib is not None else None,
            'vram_gb': round(vram_mib / 1024, 2) if vram_mib and vram_mib > 0 else None,
        })
    return rows


def _pid_listening_on_port(port: int, host: str = '127.0.0.1') -> int | None:
    if port <= 0:
        return None
    try:
        if sys.platform == 'win32':
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'tcp'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **_subprocess_no_window_kwargs(),
            )
            needle = f':{int(port)}'
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) < 5 or 'LISTENING' not in parts[3]:
                    continue
                local_addr = parts[1]
                if not local_addr.endswith(needle):
                    continue
                if local_addr.startswith('127.0.0.1') or local_addr.startswith('0.0.0.0') or local_addr.startswith('[::]'):
                    try:
                        return int(parts[4])
                    except (ValueError, IndexError):
                        return None
        else:
            result = subprocess.run(
                ['lsof', '-ti', f'tcp:{int(port)}'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.stdout.strip():
                return int(result.stdout.strip().split('\n')[0])
    except Exception:
        return None
    return None


def _managed_listener_pids(servers: list[dict[str, Any]]) -> set[int]:
    pids: set[int] = set()
    for server in servers:
        if not isinstance(server, dict):
            continue
        port = int(server.get('port') or 0)
        if port <= 0:
            continue
        host = str(server.get('host') or '127.0.0.1')
        if not tcp_port_open(host, port):
            continue
        pid = _pid_listening_on_port(port, host)
        if pid:
            pids.add(pid)
    return pids


def _listening_ports_for_pid(pid: int) -> list[int]:
    ports: list[int] = []
    try:
        if sys.platform == 'win32':
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'tcp'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **_subprocess_no_window_kwargs(),
            )
            needle = str(int(pid))
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) < 5 or 'LISTENING' not in parts[3]:
                    continue
                if parts[4] != needle:
                    continue
                local_addr = parts[1]
                if ':' not in local_addr:
                    continue
                port_raw = local_addr.rsplit(':', 1)[-1]
                try:
                    port = int(port_raw)
                except ValueError:
                    continue
                if local_addr.startswith('127.0.0.1') or local_addr.startswith('0.0.0.0') or local_addr.startswith('[::]'):
                    ports.append(port)
        else:
            result = subprocess.run(
                ['lsof', '-Pan', f'-p{int(pid)}', '-iTCP', '-sTCP:LISTEN'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in result.stdout.splitlines()[1:]:
                match = re.search(r':(\d+)\s+\(LISTEN\)', line)
                if match:
                    ports.append(int(match.group(1)))
    except Exception:
        return []
    return sorted(set(ports))


def _query_process_details(pids: list[int]) -> dict[int, dict[str, Any]]:
    if not pids:
        return {}
    if sys.platform != 'win32':
        return {}
    pid_list = ','.join(str(int(pid)) for pid in sorted(set(pids)))
    script = (
        f"$ids = @({pid_list});"
        "$rows = Get-CimInstance Win32_Process | Where-Object {{ $ids -contains $_.ProcessId }} | "
        "Select-Object ProcessId, Name, CommandLine, ExecutablePath, ParentProcessId;"
        "$parents = @{};"
        "foreach ($row in $rows) { if ($row.ParentProcessId) { $parents[$row.ParentProcessId] = $true } };"
        "$parentRows = Get-CimInstance Win32_Process | Where-Object {{ $parents.ContainsKey($_.ProcessId) }} | "
        "Select-Object ProcessId, Name;"
        "$payload = @{ processes = @($rows); parents = @($parentRows) };"
        "$payload | ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}

    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}

    parent_names: dict[int, str] = {}
    for entry in payload.get('parents') or []:
        if not isinstance(entry, dict):
            continue
        try:
            parent_names[int(entry.get('ProcessId') or 0)] = str(entry.get('Name') or '')
        except (TypeError, ValueError):
            continue

    details: dict[int, dict[str, Any]] = {}
    for entry in payload.get('processes') or []:
        if not isinstance(entry, dict):
            continue
        try:
            pid = int(entry.get('ProcessId') or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        try:
            parent_pid = int(entry.get('ParentProcessId') or 0)
        except (TypeError, ValueError):
            parent_pid = 0
        details[pid] = {
            'process_name': str(entry.get('Name') or ''),
            'command_line': str(entry.get('CommandLine') or ''),
            'executable_path': str(entry.get('ExecutablePath') or ''),
            'parent_process_name': parent_names.get(parent_pid, ''),
            'parent_pid': parent_pid or None,
        }
    return details


_RUNTIME_APP_RULES: list[tuple[str, str, str]] = [
    ('onevoice', r'onevoice|speak_stt|\\dev\\onevoice|\\dev\\ai-tools', 'OneVoice'),
    ('dflash', r'dflash|start_llama_server', 'DFlash Console'),
    ('lmstudio', r'lm studio|lmstudio\.exe|lm-studio', 'LM Studio'),
    ('ollama', r'\bollama\b', 'Ollama'),
    ('whisper', r'whisper|faster-whisper|whisper\.cpp', 'Whisper'),
    ('piper', r'\bpiper\b', 'Piper TTS'),
    ('comfyui', r'comfyui|\bcomfy\b', 'ComfyUI'),
    ('kobold', r'kobold', 'KoboldCpp'),
    ('textgen', r'text-generation-webui|oobabooga', 'text-generation-webui'),
]

_FALLBACK_APP_RULES: list[tuple[str, str, str]] = [
    ('llama-server', r'llama-server', 'llama-server'),
    ('python', r'python', 'Python'),
]


def _executable_hint(process_name: str, command_line: str) -> str:
    cmd = str(command_line or '').strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 1:
            return cmd[1:end]
    if cmd.startswith("'"):
        end = cmd.find("'", 1)
        if end > 1:
            return cmd[1:end]
    parts = cmd.split()
    if parts:
        return parts[0].strip('"').strip("'")
    return str(process_name or '')


def _classify_app(*, process_name: str, command_line: str, parent_name: str) -> tuple[str, str]:
    exe = _executable_hint(process_name, command_line)
    parent = str(parent_name or '')
    runtime_hay = f'{exe} {parent}'.lower()

    for source, pattern, label in _RUNTIME_APP_RULES:
        if re.search(pattern, runtime_hay, re.I):
            return source, label

    hay = f'{process_name} {command_line} {parent}'.lower()
    for source, pattern, label in [*_RUNTIME_APP_RULES, *_FALLBACK_APP_RULES]:
        if re.search(pattern, hay, re.I):
            return source, label

    clean = process_name or 'Unknown app'
    if clean.lower().endswith('.exe'):
        clean = clean[:-4]
    return 'unknown', clean


def _model_hint_from_cmdline(command_line: str) -> tuple[str, str]:
    cmd = str(command_line or '').strip()
    if not cmd:
        return '', ''
    for pattern in (
        r'(?:^|\s)(?:-m|--model)\s+"([^"]+)"',
        r'(?:^|\s)(?:-m|--model)\s+(\S+)',
        r'(?:^|\s)(?:--model-path)\s+"([^"]+)"',
        r'(?:^|\s)(?:--model-path)\s+(\S+)',
        r'(?:^|\s)(?:--model-size|--model_size)\s+(\S+)',
    ):
        match = re.search(pattern, cmd, re.I)
        if match:
            path = match.group(1).strip().strip('"')
            name = _display_name_from_path(path)
            return name, path
    gguf = re.search(r'([\w\-.]+\.gguf)', cmd, re.I)
    if gguf:
        path = gguf.group(1)
        return os.path.basename(path), path
    hf = re.search(r'models--([^\\/]+)--([^\\/"\s]+)', cmd, re.I)
    if hf:
        path = hf.group(0)
        return f'{hf.group(1)}/{hf.group(2)}', path
    return '', ''


def _display_name_from_path(path: str) -> str:
    clean = str(path or '').strip().strip('"')
    if not clean:
        return ''
    normalized = clean.replace('\\', '/')
    hf = re.search(r'models--([^/]+)--(.+)$', normalized, re.I)
    if hf:
        return f'{hf.group(1)}/{hf.group(2)}'
    base = os.path.basename(normalized)
    if base.lower().endswith('.py') and base.lower() not in {'speak_stt.py', 'server.py'}:
        return base[:-3]
    return base or clean


_STT_MODEL_CACHE: tuple[float, str] = (0.0, '')

# The STT debug log is append-heavy transcription JSONL and can be multi-GB
# (OneVoiceSpeak's speak_stt.debug.log is ~2.4 GB here). Reading the whole file
# to find the last model event added ~13s to every external-GPU status poll.
# Scan backward from the end instead: the most recent model event is what we
# want, and it typically sits well within a bounded window.
_STT_LOG_SCAN_BYTES = 512 * 1024 * 1024
_STT_LOG_SCAN_CHUNK = 4 * 1024 * 1024


def _speak_stt_log_paths() -> list[Path]:
    return [
        Path(os.path.expanduser('~')) / 'AppData' / 'Local' / 'Programs' / 'OneVoiceSpeak' / 'resources' / 'tools' / 'logs' / 'speak_stt.debug.log',
    ]


def _read_last_json_log_model(log_path: Path, *, events: tuple[str, ...]) -> str:
    """Return the model field from the last matching JSON log event.

    Scans backward from the end of the file in bounded chunks so a multi-GB
    debug log costs milliseconds instead of a full sequential read. Only
    complete JSON lines whose ``detail`` carries a ``model``/``model_id`` are
    considered, so a keyword inside transcription preview text never matches.
    """
    if not log_path.is_file():
        return ''
    try:
        size = log_path.stat().st_size
    except OSError:
        return ''
    cap = min(size, _STT_LOG_SCAN_BYTES)
    model = ''
    try:
        with log_path.open('rb') as handle:
            pos = size
            carry = b''
            scanned = 0
            while pos > 0 and scanned < cap:
                take = min(_STT_LOG_SCAN_CHUNK, pos, cap - scanned)
                pos -= take
                scanned += take
                handle.seek(pos)
                data = (handle.read(take) + carry).decode('utf-8', errors='replace')
                lines = data.split('\n')
                # lines[0] may be a partial line whose head lives in the older
                # chunk; carry it forward and only scan complete lines.
                carry = lines[0].encode('utf-8', errors='replace')
                for line in reversed(lines[1:]):
                    if not any(event in line for event in events):
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    detail = row.get('detail') if isinstance(row, dict) else None
                    if not isinstance(detail, dict):
                        continue
                    candidate = str(detail.get('model') or detail.get('model_id') or '').strip()
                    if candidate:
                        model = candidate
                        break
                if model:
                    break
    except OSError:
        return ''
    return model


def _read_speak_stt_active_model(*, max_age_seconds: float = 45.0) -> str:
    global _STT_MODEL_CACHE
    now = time.time()
    cached_at, cached_model = _STT_MODEL_CACHE
    if cached_model and (now - cached_at) < max_age_seconds:
        return cached_model
    model = ''
    for path in _speak_stt_log_paths():
        model = _read_last_json_log_model(path, events=('model-ready', 'server-start', 'model-loading'))
        if model:
            break
    _STT_MODEL_CACHE = (now, model)
    return model


def _is_lmstudio_chromium_helper(command_line: str) -> bool:
    cmd = str(command_line or '').lower()
    return any(
        token in cmd
        for token in ('--type=gpu-process', '--type=utility', '--type=crashpad-handler', 'crashpad-handler')
    )


def _probe_lmstudio_loaded_models(host: str = '127.0.0.1', port: int = 1234) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    base = f'http://{host}:{int(port)}'

    # LM Studio's current API exposes the authoritative loaded instances here.
    # The older v0 endpoint reports the same information as a model state.
    for endpoint in ('/api/v1/models', '/api/v0/models'):
        try:
            with urllib.request.urlopen(f'{base}{endpoint}', timeout=2.5) as resp:
                payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        entries = payload.get('models') if endpoint.endswith('/v1/models') else payload.get('data')
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            instances = entry.get('loaded_instances')
            state = str(entry.get('state') or '').lower()
            if endpoint.endswith('/v1/models'):
                if not isinstance(instances, list) or not instances:
                    continue
                instance = instances[0] if isinstance(instances[0], dict) else {}
                model_id = str(instance.get('id') or entry.get('key') or '').strip()
                state = str(instance.get('state') or 'loaded').lower()
            else:
                if state not in {'loaded', 'loading', 'running'}:
                    continue
                model_id = str(entry.get('id') or '').strip()
            if not model_id:
                continue
            title = str(entry.get('display_name') or entry.get('id') or entry.get('key') or model_id)
            quant_data = entry.get('quantization')
            quant = (
                str(quant_data.get('name') or '').strip()
                if isinstance(quant_data, dict)
                else str(entry.get('quantization') or '').strip()
            )
            if quant:
                title = f'{title} · {quant}'
            loaded.append({
                'model_id': model_id,
                'title': title,
                'quantization': quant,
                'state': state,
                'size_gb': round(float(entry.get('size_bytes') or 0) / (1024 ** 3), 2)
                if entry.get('size_bytes') else None,
                'api_url': f'{base}/v1',
                'listen_port': int(port),
            })
        if loaded:
            break
    return loaded


def _probe_ollama_running_models(host: str = '127.0.0.1', port: int = 11434) -> list[dict[str, Any]]:
    url = f'http://{host}:{int(port)}/api/ps'
    try:
        with urllib.request.urlopen(url, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    loaded: list[dict[str, Any]] = []
    for entry in payload.get('models') or []:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get('name') or entry.get('model') or '').strip()
        if not model_id:
            continue
        size_gb = None
        size_value = entry.get('size')
        if isinstance(size_value, (int, float)) and size_value > 0:
            size_gb = round(float(size_value) / (1024 ** 3), 2)
        title = model_id
        if size_gb is not None:
            title = f'{model_id} · {size_gb} GB'
        loaded.append({
            'model_id': model_id,
            'title': title,
            'api_url': f'http://{host}:{int(port)}/v1',
            'listen_port': int(port),
            'size_gb': size_gb,
        })
    return loaded


def _resolve_external_model_name(
    *,
    app_source: str,
    app_label: str,
    process_name: str,
    command_line: str,
    api_model_id: str = '',
) -> tuple[str, str]:
    hinted_name, model_path = _model_hint_from_cmdline(command_line)
    if api_model_id:
        return api_model_id, model_path

    model_name = hinted_name

    if not model_name and app_source in {'onevoice', 'whisper'} and 'speak_stt' in command_line.lower():
        log_model = _read_speak_stt_active_model()
        if log_model:
            model_name = log_model

    if not model_name and 'speak_stt.py' in command_line.lower():
        return '', model_path

    if 'speak_stt.py' in command_line.lower() and not model_path:
        model_path = _resolve_stt_model_path(model_name, command_line)

    if not model_name:
        clean_process = process_name.replace('\\', '/').split('/')[-1]
        if clean_process.lower().endswith('.exe'):
            clean_process = clean_process[:-4]
        if clean_process.lower() in {'python', 'pythonw'}:
            model_name = app_label
        else:
            model_name = clean_process or app_label

    return model_name, model_path


def _probe_models_fast(api_url: str) -> tuple[list[dict[str, Any]], bool]:
    """Fetch ``/models`` with a short 1s timeout.

    Returns ``(entries, timed_out)``. ``timed_out=True`` means the port accepted
    the TCP connection but never answered HTTP (e.g. an engine busy mid-load) —
    the caller should skip further probing of that port instead of waiting on
    another full timeout.
    """
    url = f"{str(api_url or '').strip().rstrip('/')}/models"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except TimeoutError:
        return [], True
    except (urllib.error.URLError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError):
        return [], False
    models = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return [], False
    return [entry for entry in models if isinstance(entry, dict)], False


def _probe_loaded_model(host: str, port: int) -> dict[str, Any]:
    # Skip dead ports instantly instead of burning HTTP timeouts on them.
    if not tcp_port_open(host, port):
        return {}
    # Try the OpenAI /v1 prefix first; only fall back to the bare prefix when the
    # /v1 probe answered quickly. A port that times out (engine busy) is not an
    # OpenAI endpoint, so don't wait on a second timeout.
    api_url = f'http://{host}:{int(port)}/v1'
    entries, timed_out = _probe_models_fast(api_url)
    if not entries and not timed_out:
        entries, _ = _probe_models_fast(f'http://{host}:{int(port)}')
        if entries:
            api_url = f'http://{host}:{int(port)}'
    if not entries:
        return {}
    router = router_unload_available(api_url)
    loaded = _loaded_model_ids(entries, router=router)
    if loaded:
        return {
            'api_url': api_base_url(api_url),
            'model_id': loaded[0],
            'unload_via_api': router,
        }
    # Router listening but idle — still useful metadata.
    if router:
        return {'api_url': api_base_url(api_url), 'model_id': '', 'unload_via_api': True}
    return {}


def _build_external_card(
    entry: dict[str, Any],
    *,
    details: dict[str, Any],
    gpus: list[dict[str, Any]],
    managed_pids: set[int],
    configured_ports: set[int],
    dflash_root: str,
) -> dict[str, Any] | None:
    pid = int(entry['pid'])
    if pid in managed_pids:
        return None

    process_name = str(details.get('process_name') or entry.get('process_name') or '')
    if process_name.startswith('['):
        return None
    command_line = str(details.get('command_line') or '')
    parent_name = str(details.get('parent_process_name') or '')
    hay = f'{process_name} {command_line} {parent_name}'.lower()
    if dflash_root and dflash_root.lower() in hay and 'lm studio' not in hay:
        return None

    app_source, app_label = _classify_app(
        process_name=process_name,
        command_line=command_line,
        parent_name=parent_name,
    )
    if app_source == 'dflash':
        return None

    if app_source == 'lmstudio' and _is_lmstudio_chromium_helper(command_line):
        return None

    if app_source == 'ollama' and process_name.lower() in {'ollama app.exe', 'ollama.exe'}:
        return None

    vram_mib = entry.get('vram_mb')
    if not _should_track_process(
        process_name=process_name,
        command_line=command_line,
        parent_name=parent_name,
        vram_mib=float(vram_mib) if vram_mib is not None else None,
        app_source=app_source,
    ):
        return None

    model_id = ''
    api_url = ''
    unload_via_api = False
    listen_port: int | None = None
    model_path = ''

    listen_ports = _listening_ports_for_pid(pid)
    if any(port in configured_ports for port in listen_ports):
        return None

    for port in listen_ports:
        if port in configured_ports:
            continue
        probe = _probe_loaded_model('127.0.0.1', port)
        if probe.get('api_url'):
            api_url = str(probe.get('api_url') or '')
            model_id = str(probe.get('model_id') or '')
            unload_via_api = bool(probe.get('unload_via_api'))
            listen_port = port
            break

    model_name, model_path = _resolve_external_model_name(
        app_source=app_source,
        app_label=app_label,
        process_name=process_name,
        command_line=command_line,
        api_model_id=model_id,
    )
    if not str(model_name or '').strip():
        return None
    if model_name == app_label and str(process_name or '').lower().endswith(('python.exe', 'pythonw.exe')):
        return None
    if not _is_gpu_model_load(
        process_name=process_name,
        command_line=command_line,
        model_name=model_name,
        model_path=model_path,
        model_id=model_id,
        api_url=api_url,
        vram_mib=float(vram_mib) if vram_mib is not None else None,
    ):
        return None

    gpu_index = int(entry.get('gpu_index') or 0)
    gpu = next((g for g in gpus if int(g.get('index', -1)) == gpu_index), None)
    gpu_display = str(gpu.get('display_name') or format_gpu_display_name(str(gpu.get('name') or ''), gpu_index))

    vram_gb = entry.get('vram_gb')
    cmd_path = _model_hint_from_cmdline(command_line)[1]
    if not model_path and cmd_path:
        model_path = cmd_path
    size_gb = _size_gb_from_path(model_path) or _size_gb_from_path(cmd_path)
    kind_fields = _model_kind_fields(
        model_name=model_name,
        model_path=model_path,
        command_line=command_line,
        process_name=process_name,
    )
    card_detail = _external_card_detail(
        model_kind=str(kind_fields.get('model_kind') or ''),
        model_name=model_name,
        model_path=model_path,
        command_line=command_line,
        listen_port=listen_port,
    )

    return {
        'id': f'external-gpu-{pid}',
        'external': True,
        'role': 'external-gpu',
        'pid': pid,
        'process_name': process_name,
        'app_source': app_source,
        'app_label': app_label,
        'gpu_index': gpu_index,
        'gpu_display': gpu_display,
        'vram_mb': entry.get('vram_mb'),
        'vram_gb': vram_gb,
        'size_gb': size_gb,
        'model_name': model_name,
        'model_path': model_path,
        'command_line': command_line[:240] if command_line else '',
        'api_url': api_url,
        'model_id': model_id,
        'listen_port': listen_port,
        'unload_method': 'api' if unload_via_api and model_id else ('api_stop' if unload_via_api else 'kill'),
        'card_state': 'ready',
        'ejectable': True,
        'title': model_name,
        'subtitle': card_detail,
        'card_detail': card_detail,
        **kind_fields,
    }


def _make_api_external_card(
    *,
    app_source: str,
    app_label: str,
    pid: int,
    model_name: str,
    model_id: str,
    api_url: str,
    listen_port: int | None,
    gpus: list[dict[str, Any]],
    gpu_index: int = 0,
    vram_gb: float | None = None,
    size_gb: float | None = None,
) -> dict[str, Any]:
    gpu = next((g for g in gpus if int(g.get('index', -1)) == gpu_index), None)
    gpu_display = str(gpu.get('display_name') or format_gpu_display_name(str(gpu.get('name') or ''), gpu_index))
    unload_via_api = bool(api_url and model_id)
    kind_fields = _model_kind_fields(
        model_name=model_name,
        model_path='',
        command_line='',
        process_name=app_label,
    )
    card_detail = _external_card_detail(
        model_kind=str(kind_fields.get('model_kind') or ''),
        model_name=model_name,
        model_path='',
        command_line='',
        listen_port=listen_port,
    )
    return {
        'id': f'external-api-{app_source}-{model_id or pid}',
        'external': True,
        'role': 'external-gpu',
        'pid': pid,
        'process_name': app_label,
        'app_source': app_source,
        'app_label': app_label,
        'gpu_index': gpu_index,
        'gpu_display': gpu_display,
        'vram_mb': round(vram_gb * 1024, 1) if vram_gb else None,
        'vram_gb': vram_gb,
        'size_gb': size_gb,
        'model_name': model_name,
        'model_path': '',
        'command_line': '',
        'api_url': api_url,
        'model_id': model_id,
        'listen_port': listen_port,
        'unload_method': 'api' if unload_via_api else 'kill',
        'card_state': 'ready',
        'ejectable': True,
        'title': model_name,
        'subtitle': card_detail,
        'card_detail': card_detail,
        **kind_fields,
    }


def _cards_from_known_apps(
    *,
    compute_rows: list[dict[str, Any]],
    details_map: dict[int, dict[str, Any]],
    gpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    lm_gpu_index = 0
    vram_by_pid = {int(row['pid']): row.get('vram_gb') for row in compute_rows if row.get('pid') is not None}
    for row in compute_rows:
        details = details_map.get(int(row['pid']), {})
        app_source, _ = _classify_app(
            process_name=str(details.get('process_name') or row.get('process_name') or ''),
            command_line=str(details.get('command_line') or ''),
            parent_name=str(details.get('parent_process_name') or ''),
        )
        if app_source == 'lmstudio':
            lm_gpu_index = int(row.get('gpu_index') or 0)
            break

    service_pid = _pid_listening_on_port(1234)
    if service_pid:
        for model in _probe_lmstudio_loaded_models():
            cards.append(_make_api_external_card(
                app_source='lmstudio',
                app_label='LM Studio',
                pid=service_pid,
                model_name=str(model.get('model_id') or model.get('title') or 'LM Studio model'),
                model_id=str(model.get('model_id') or ''),
                api_url=str(model.get('api_url') or ''),
                listen_port=int(model.get('listen_port') or 1234),
                gpus=gpus,
                gpu_index=lm_gpu_index,
                vram_gb=vram_by_pid.get(int(service_pid)),
                size_gb=model.get('size_gb'),
            ))
    ollama_pid = _pid_listening_on_port(11434)
    if ollama_pid:
        for model in _probe_ollama_running_models():
            cards.append(_make_api_external_card(
                app_source='ollama',
                app_label='Ollama',
                pid=ollama_pid,
                model_name=str(model.get('model_id') or model.get('title') or 'Ollama model'),
                model_id=str(model.get('model_id') or ''),
                api_url=str(model.get('api_url') or ''),
                listen_port=int(model.get('listen_port') or 11434),
                gpus=gpus,
                gpu_index=0,
                vram_gb=vram_by_pid.get(int(ollama_pid)),
                size_gb=model.get('size_gb'),
            ))

    return cards


def _attach_external_inference_stats(card: dict[str, Any]) -> dict[str, Any]:
    """Poll llama-server /slots for token metrics on external GPU model cards."""
    api_url = str(card.get('api_url') or '').strip()
    model_kind = str(card.get('model_kind') or '').lower()
    if not api_url or model_kind not in {'llm', 'embedding'}:
        return card
    pid = int(card.get('pid') or 0)
    server_id = f'external-{pid}' if pid > 0 else str(card.get('id') or '')
    from core.inference_stats import fetch_inference_stats

    enriched = dict(card)
    enriched['inference_stats'] = fetch_inference_stats(
        api_url,
        server_id=server_id,
        model_id=str(card.get('model_id') or card.get('model_name') or ''),
    )
    return enriched


def get_external_gpu_loads(
    *,
    servers: list[dict[str, Any]] | None = None,
    gpus: list[dict[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    servers = servers or []
    if gpus is None:
        from core.gpu_devices import query_gpu_devices

        gpus = query_gpu_devices()

    hardware = (cfg or {}).get('hardware_settings') or {}
    if hardware.get('detect_external_gpu_loads') is False:
        return []

    compute_rows = _query_compute_apps()
    if not compute_rows:
        return []

    pids = [int(row['pid']) for row in compute_rows]
    details_map = _query_process_details(pids)
    managed_pids = _managed_listener_pids(servers)
    configured_ports = {
        int(server.get('port') or 0)
        for server in servers
        if isinstance(server, dict) and int(server.get('port') or 0) > 0
    }
    dflash_root = ''
    try:
        from core.config import get_dflash_root

        dflash_root = str(get_dflash_root(cfg) or '')
    except Exception:
        dflash_root = ''

    cards: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for card in _cards_from_known_apps(compute_rows=compute_rows, details_map=details_map, gpus=gpus):
        seen_ids.add(str(card['id']))
        cards.append(card)

    seen_pids: set[int] = {int(card['pid']) for card in cards if card.get('pid')}
    for row in compute_rows:
        pid = int(row['pid'])
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        card = _build_external_card(
            row,
            details=details_map.get(pid, {}),
            gpus=gpus,
            managed_pids=managed_pids,
            configured_ports=configured_ports,
            dflash_root=dflash_root,
        )
        if card:
            card_id = str(card.get('id') or '')
            if card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            cards.append(card)

    cards.sort(key=lambda item: (-float(item.get('vram_mb') or 0), str(item.get('title') or '')))
    return [_attach_external_inference_stats(card) for card in cards]


def unload_external_gpu_process(
    pid: int,
    *,
    api_url: str = '',
    model_id: str = '',
) -> dict[str, Any]:
    target_pid = int(pid)
    if target_pid <= 0:
        return {'success': False, 'error': 'invalid pid'}
    api = str(api_url or '').strip()
    model = str(model_id or '').strip()
    if api and model:
        parsed_api = urlparse(api)
        # Ollama has its own unload route (/api/generate keep_alive=0); the
        # generic llama-server path does not work for it.
        if parsed_api.port == 11434:
            native = _unload_ollama_model(api_url=api, model_id=model)
            if native.get('success'):
                return {**native, 'pid': target_pid, 'method': 'ollama-api'}

        native_urls = [api]
        if (
            parsed_api.hostname in {'127.0.0.1', 'localhost', '::1'}
            and parsed_api.port != 1234
        ):
            native_urls.append(f'{parsed_api.scheme}://{parsed_api.hostname}:1234/v1')
        for native_url in native_urls:
            native = _unload_lmstudio_model(api_url=native_url, model_id=model)
            if native.get('success'):
                return {**native, 'pid': target_pid, 'method': 'lmstudio-api'}

        # Generic OpenAI-compatible unload (Ollama, llama-server, LM Studio
        # workers). Try it BEFORE the PID lookup: the stored PID is often the
        # service/app process (e.g. ollama.exe) rather than the current GPU
        # compute worker, so it would not appear in nvidia-smi's compute apps.
        result = unload_model(api_url=api, model_id=model)
        if result.get('success'):
            return {**result, 'pid': target_pid, 'method': 'api'}
        # Some LM Studio worker endpoints reject the legacy unload route with
        # 401/403. The worker PID is still safe to terminate below.
        if int(result.get('http_status') or 0) not in (401, 403, 404, 405):
            return result

    matching = next(
        (
            row for row in query_compute_apps()
            if int(row.get('pid') or 0) == target_pid
        ),
        None,
    )
    if not matching:
        return {'success': False, 'error': 'process is not a current GPU compute process', 'pid': target_pid}
    identity = ' '.join(
        str(matching.get(key) or '')
        for key in ('process_name', 'command_line', 'model_name', 'model_path')
    )
    if _APP_SERVER_CMD.search(identity) or not (_ML_PROCESS.search(identity) or _ML_CMD.search(identity)):
        return {'success': False, 'error': 'process is not an approved model process', 'pid': target_pid}

    try:
        if sys.platform == 'win32':
            proc = subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(target_pid)],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                **_subprocess_no_window_kwargs(),
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or '').strip() or 'taskkill failed'
                return {'success': False, 'error': detail, 'pid': target_pid}
        else:
            os.kill(target_pid, 9)
    except Exception as exc:
        return {'success': False, 'error': str(exc), 'pid': target_pid}

    return {'success': True, 'pid': target_pid, 'method': 'kill', 'message': 'Process terminated'}


def _unload_lmstudio_model(*, api_url: str, model_id: str) -> dict[str, Any]:
    """Use LM Studio's native API when the card represents its main server."""
    parsed = urlparse(str(api_url or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return {'success': False, 'error': 'invalid API URL'}

    base = f'{parsed.scheme}://{parsed.netloc}/api/v1'
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    token = (
        os.environ.get('LM_API_TOKEN')
        or os.environ.get('LM_STUDIO_API_TOKEN')
        or os.environ.get('LM_STUDIO_API_KEY')
    )
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'

    try:
        list_request = urllib.request.Request(
            f'{base}/models',
            method='GET',
            headers=headers,
        )
        with urllib.request.urlopen(list_request, timeout=3) as response:
            payload = json.loads(response.read().decode('utf-8', errors='replace') or '{}')
    except urllib.error.HTTPError as exc:
        return {'success': False, 'error': str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        return {'success': False, 'error': str(exc)}

    requested = str(model_id or '').strip()
    instance_id = ''
    for entry in (payload.get('models') if isinstance(payload, dict) else []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get('key') or '').strip()
        requested_path = requested.replace('\\', '/').rsplit('/', 1)[-1].rsplit('.', 1)[0].lower()
        key_name = key.rsplit('/', 1)[-1].lower()
        for instance in entry.get('loaded_instances') or []:
            if not isinstance(instance, dict):
                continue
            candidate = str(instance.get('id') or '').strip()
            path_matches_key = bool(
                requested_path
                and key_name
                and (
                    requested_path == key_name
                    or requested_path.startswith(f'{key_name}-')
                    or key_name.startswith(f'{requested_path}-')
                )
            )
            if candidate and (requested in {candidate, key} or path_matches_key):
                instance_id = candidate
                break
        if instance_id:
            break
    if not instance_id:
        return {
            'success': False,
            'error': 'LM Studio model instance is not loaded',
            'http_status': 404,
        }

    body = json.dumps({'instance_id': instance_id}).encode('utf-8')
    unload_request = urllib.request.Request(
        f'{base}/models/unload',
        data=body,
        method='POST',
        headers=headers,
    )
    try:
        with urllib.request.urlopen(unload_request, timeout=15) as response:
            response_body = response.read().decode('utf-8', errors='replace')
            result = json.loads(response_body or '{}') if response_body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        return {'success': False, 'error': detail or str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        return {'success': False, 'error': str(exc)}

    return {
        'success': True,
        'unloaded': True,
        'model': instance_id,
        'response': result,
    }


def _unload_ollama_model(*, api_url: str, model_id: str) -> dict[str, Any]:
    """Use Ollama's native API to unload a model.

    Ollama does not implement llama-server's ``/models/unload`` route, so the
    generic OpenAI path 404s and falls back to PID termination (which fails
    because the stored PID is the ollama.exe service, not a GPU compute
    process). Sending ``keep_alive: 0`` in a generate request unloads the
    model from VRAM immediately.
    """
    parsed = urlparse(str(api_url or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return {'success': False, 'error': 'invalid API URL'}

    model = str(model_id or '').strip()
    if not model:
        return {'success': False, 'error': 'model_id required'}

    base = f'{parsed.scheme}://{parsed.netloc}'
    body = json.dumps({
        'model': model,
        'keep_alive': 0,
        'prompt': '',
        'stream': False,
    }).encode('utf-8')
    request = urllib.request.Request(
        f'{base}/api/generate',
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = response.read().decode('utf-8', errors='replace')
            result = json.loads(response_body or '{}') if response_body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        # The model may already be unloaded; treat that as success (idempotent).
        if exc.code in (404, 405) or 'not found' in detail.lower() or 'is not running' in detail.lower():
            return {
                'success': True,
                'unloaded': False,
                'already_unloaded': True,
                'model': model,
                'response': detail,
            }
        return {'success': False, 'error': detail or str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError) as exc:
        return {'success': False, 'error': str(exc)}

    return {
        'success': True,
        'unloaded': True,
        'model': model,
        'response': result,
    }
