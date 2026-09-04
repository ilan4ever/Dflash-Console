"""Detect GPU compute processes from other apps (LM Studio, Whisper, etc.)."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.gpu_devices import format_gpu_display_name
from core.net_listeners import listening_ports_map, loopback_listening_ports, pid_listening_on_port
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
    r'ShellExperienceHost|ShellInfrastructureHost|CrossDeviceResume|TextInputHost|'
    r'ApplicationFrameHost|SystemSettings|msedge|msedgewebview2|Discord|ShareX|'
    r'Telegram|WhatsApp(?:\.Root)?|dwm|PhoneExperienceHost|DCv2|Cursor|Code|'
    r'Code - Insiders|notepad(?:\+\+)?|sublime_text|winword|excel|powerpnt|outlook|'
    r'spotify|vlc|VoiceRecorder|SoundRecorder|WindowsSoundRecorder|'
    r'CHXSmartScreen|SmartScreen|TabTip|Dashboard|HWiNFO(?:64)?|ctfmon|Widgets|'
    r'GameBar|RuntimeBroker|sihost|taskhostw|fontdrvhost|SecurityHealthSystray)\.exe$',
    re.I,
)

_ELECTRON_UI = re.compile(r'electron\.exe$', re.I)

_ML_PROCESS = re.compile(
    r'llama-server|ollama|whisper|piper|kobold|comfyui|text-generation-webui|'
    r'faster-whisper|whisper\.cpp',
    re.I,
)

_ML_CMD = re.compile(
    r'\.gguf|\.bin|\.onnx|whisper|faster.whisper|llama-server|ollama|piper|'
    r'transcribe|speech|cuda|torch|transformers|llama\.cpp|speak_stt|'
    r'\\stt\\|/stt/|onevoice|scraper\.py|transcribe_module|voice_core|speaker_diagnosis',
    re.I,
)

_AI_TOOLS_PATH = re.compile(r'\\ai-tools\\|/ai-tools/|ai-tools\.exe', re.I)

_AI_TOOLS_FUNC_LABELS: dict[str, str] = {
    'speaker_name_transcription': 'Speaker names',
    'transcribe_audio': 'Transcription',
    'force_english_transcription': 'English transcription',
    'generate_srt': 'SRT generation',
    'refine_srt': 'SRT refinement',
    'recognize_faces': 'Face recognition',
    'label_faces': 'Face labeling',
    'ocr': 'OCR',
    'generate_summary_long': 'Long summary',
    'generate_summary_short': 'Short summary',
}

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
    if (
        'speak_stt' in hay
        or 'whisper' in hay
        or 'small.en' in hay
        or 'faster-whisper' in hay
        or 'transcribe_module' in hay
        or 'voice_core' in hay
        or 'speaker_diagnosis' in hay
    ):
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


def _is_ui_only_process(*, process_name: str, command_line: str) -> bool:
    hay = f'{process_name} {command_line}'
    if _APP_SERVER_CMD.search(hay):
        return True
    if _is_lmstudio_chromium_helper(command_line):
        return True
    if _ELECTRON_UI.search(process_name) and not (_ML_PROCESS.search(hay) or _ML_CMD.search(hay)):
        return True
    if re.search(r'Microsoft\.WindowsSoundRecorder|WindowsApps\\[^\\]*SoundRecorder', hay, re.I):
        return True
    return False


def _generic_workload_title(
    *,
    process_name: str,
    command_line: str,
    parent_name: str,
    app_label: str,
) -> str:
    cmd = str(command_line or '')
    module_match = re.search(r'(?:^|\s)-m\s+([\w.]+)', cmd)
    if module_match:
        return module_match.group(1).split('.')[-1]
    script_match = re.search(r'([\w.-]+\.py)\b', cmd, re.I)
    if script_match:
        script = script_match.group(1)
        if script.lower() not in {'server.py', 'app.py', 'web_ui.py', 'stt_manager.py'}:
            return script[:-3] if script.lower().endswith('.py') else script
    hinted_name, _ = _model_hint_from_cmdline(cmd)
    if hinted_name and hinted_name.lower() not in {'python', 'pythonw'}:
        return hinted_name
    parent_clean = str(parent_name or '').strip()
    if parent_clean.lower().endswith('.exe'):
        parent_clean = parent_clean[:-4]
    proc_clean = str(process_name or '').replace('\\', '/').split('/')[-1]
    if proc_clean.lower().endswith('.exe'):
        proc_clean = proc_clean[:-4]
    if proc_clean.lower() in {'python', 'pythonw'} and parent_clean:
        return f'{parent_clean} worker'
    return str(app_label or proc_clean or 'GPU workload').strip()


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
    if _is_ui_only_process(process_name=process_name, command_line=command_line):
        return False
    # nvidia-smi listed this PID under compute apps — show it unless filtered above.
    return True


def _has_ml_signals(*parts: str) -> bool:
    hay = ' '.join(str(part or '') for part in parts).lower()
    return bool(_ML_PROCESS.search(hay) or _ML_CMD.search(hay))


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
    if _is_ui_only_process(process_name=process_name, command_line=command_line):
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
    if not _has_ml_signals(process_name, command_line, model_name, model_path):
        return False
    return bool(str(model_name or '').strip())


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
        engine = 'AI Tools' if _AI_TOOLS_PATH.search(hay) else (
            'faster-whisper' if 'speak_stt' in hay or 'faster-whisper' in hay or 'faster_whisper' in hay else 'Whisper'
        )
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
    return pid_listening_on_port(port, host)


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


def _listening_ports_map() -> dict[int, list[int]]:
    return listening_ports_map()


def _listening_ports_for_pid(pid: int) -> list[int]:
    return list(_listening_ports_map().get(int(pid), []))


def _query_process_details(pids: list[int]) -> dict[int, dict[str, Any]]:
    pid_set = {int(pid) for pid in pids if int(pid) > 0}
    if not pid_set:
        return {}

    now = time.time()
    cache = _PROCESS_DETAILS_CACHE
    cached_map = cache.get('map') if isinstance(cache.get('map'), dict) else {}
    cached_at = float(cache.get('at') or 0.0)
    if cached_map and (now - cached_at) < _PROCESS_DETAILS_TTL_SECONDS:
        if pid_set <= set(cached_map.keys()):
            return {pid: dict(cached_map[pid]) for pid in pid_set if pid in cached_map}

    missing = sorted(pid for pid in pid_set if pid not in cached_map)
    if not missing and cached_map and (now - cached_at) < _PROCESS_DETAILS_TTL_SECONDS:
        return {pid: dict(cached_map[pid]) for pid in pid_set if pid in cached_map}

    fetched = _fetch_process_details(missing or sorted(pid_set))
    merged = dict(cached_map) if (now - cached_at) < _PROCESS_DETAILS_TTL_SECONDS else {}
    merged.update(fetched)
    _PROCESS_DETAILS_CACHE['at'] = now
    _PROCESS_DETAILS_CACHE['map'] = merged
    return {pid: dict(merged.get(pid, {})) for pid in pid_set}


def _fetch_process_details(pids: list[int]) -> dict[int, dict[str, Any]]:
    if not pids:
        return {}
    try:
        import psutil
    except ImportError:
        return _fetch_process_details_powershell(pids)

    details: dict[int, dict[str, Any]] = {}
    parent_pids: set[int] = set()
    for pid in sorted({int(item) for item in pids if int(item) > 0}):
        try:
            proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        try:
            parent_pid = int(proc.ppid() or 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            parent_pid = 0
        if parent_pid > 0:
            parent_pids.add(parent_pid)
        try:
            cmdline = proc.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            cmdline = []
        try:
            executable_path = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            executable_path = ''
        try:
            process_name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            process_name = ''
        details[pid] = {
            'process_name': process_name,
            'command_line': ' '.join(cmdline) if cmdline else '',
            'executable_path': executable_path,
            'parent_process_name': '',
            'parent_pid': parent_pid or None,
        }

    parent_names: dict[int, str] = {}
    for parent_pid in parent_pids:
        try:
            parent_names[parent_pid] = psutil.Process(parent_pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    for row in details.values():
        parent_pid = row.get('parent_pid')
        if parent_pid:
            row['parent_process_name'] = parent_names.get(int(parent_pid), '')
    return details


def _fetch_process_details_powershell(pids: list[int]) -> dict[int, dict[str, Any]]:
    if not pids or sys.platform != 'win32':
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
            ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script],
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
    ('ai-tools', r'\\ai-tools\\|/ai-tools/|ai-tools\.exe', 'AI Tools'),
    ('onevoice', r'onevoice|speak_stt|\\dev\\onevoice', 'OneVoice'),
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
    proc_lower = str(process_name or '').lower()
    if proc_lower.endswith(('python.exe', 'pythonw.exe')) and parent:
        parent_clean = parent
        if parent_clean.lower().endswith('.exe'):
            parent_clean = parent_clean[:-4]
        if parent_clean:
            return 'unknown', parent_clean

    for source, pattern, label in [*_RUNTIME_APP_RULES, *_FALLBACK_APP_RULES]:
        if re.search(pattern, hay, re.I):
            return source, label

    proc_base = str(process_name or '').strip()
    if proc_base.lower().endswith('.exe'):
        proc_base = proc_base[:-4]
    exe = _executable_hint(process_name, command_line)
    exe_base = os.path.basename(exe) if exe else ''
    if exe_base.lower().endswith('.exe'):
        exe_base = exe_base[:-4]
    base = proc_base or exe_base
    parent_clean = parent
    if parent_clean.lower().endswith('.exe'):
        parent_clean = parent_clean[:-4]
    if base.lower() in {'python', 'pythonw'} and parent_clean:
        return 'unknown', parent_clean
    return 'unknown', base or 'Unknown app'


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


def _draft_hint_from_cmdline(command_line: str) -> tuple[str, str]:
    """Return the observable draft model passed to an external llama server."""
    cmd = str(command_line or '').strip()
    if not cmd:
        return '', ''
    pattern = re.compile(
        r'(?:^|\s)(?:-md|--model-draft|--spec-draft-model)(?:=|\s+)'
        r'(?:"([^"]+)"|(\S+))',
        re.I,
    )
    match = pattern.search(cmd)
    if not match:
        return '', ''
    path = str(match.group(1) or match.group(2) or '').strip().strip('"')
    return _display_name_from_path(path), path


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


_STT_MODEL_CACHE: dict[str, tuple[float, str]] = {}

# The STT debug log is append-heavy transcription JSONL and can be multi-GB
# (OneVoiceSpeak's speak_stt.debug.log is ~2.4 GB here). Reading the whole file
# to find the last model event added ~13s to every external-GPU status poll.
# Scan backward from the end instead: the most recent model event is what we
# want, and it typically sits well within a bounded window.
_STT_LOG_SCAN_BYTES = 512 * 1024 * 1024
_STT_LOG_SCAN_CHUNK = 4 * 1024 * 1024


def _speak_stt_log_paths(command_line: str = '') -> list[Path]:
    """Return candidate speak_stt debug logs, preferring the running script's tree."""
    paths: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    cmd = str(command_line or '')
    match = re.search(r'([A-Za-z]:[^"\s]*speak_stt\.py)', cmd, re.IGNORECASE)
    if match:
        # speak_stt.py lives in .../stt/; logs in sibling .../logs/ directory.
        tools_dir = Path(match.group(1)).resolve().parent.parent
        add(tools_dir / 'logs' / 'speak_stt.debug.log')

    add(
        Path(os.path.expanduser('~'))
        / 'AppData'
        / 'Local'
        / 'Programs'
        / 'OneVoiceSpeak'
        / 'resources'
        / 'tools'
        / 'logs'
        / 'speak_stt.debug.log'
    )
    return paths


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
                # chunk; carry it forward and only scan complete lines. When this
                # chunk starts at the beginning of the scan window, lines[0] is
                # a complete line and must be included.
                at_bof = pos == 0
                if at_bof:
                    scan_lines = lines
                    carry = b''
                else:
                    scan_lines = lines[1:]
                    carry = lines[0].encode('utf-8', errors='replace')
                for line in reversed(scan_lines):
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


def _read_speak_stt_active_model(*, command_line: str = '', max_age_seconds: float = 45.0) -> str:
    global _STT_MODEL_CACHE
    now = time.time()
    cache_key = '|'.join(str(path).lower() for path in _speak_stt_log_paths(command_line))
    cached_at, cached_model = _STT_MODEL_CACHE.get(cache_key, (0.0, ''))
    if cached_model and (now - cached_at) < max_age_seconds:
        return cached_model
    model = ''
    for path in _speak_stt_log_paths(command_line):
        model = _read_last_json_log_model(path, events=('model-ready', 'server-start', 'model-loading'))
        if model:
            break
    _STT_MODEL_CACHE[cache_key] = (now, model)
    return model


_STT_WS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STT_WS_CACHE_TTL = 5.0


def _ws_send_text_frame(sock: socket.socket, text: str) -> None:
    payload = text.encode('utf-8')
    mask = secrets.token_bytes(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    header = bytearray([0x81])
    length = len(payload)
    if length <= 125:
        header.append(0x80 | length)
    elif length <= 65535:
        header.append(0x80 | 126)
        header.extend(struct.pack('!H', length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack('!Q', length))
    header.extend(mask)
    header.extend(masked)
    sock.sendall(header)


def _ws_recv_text_frame(sock: socket.socket) -> str:
    header = sock.recv(2)
    if len(header) < 2:
        return ''
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack('!H', sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack('!Q', sock.recv(8))[0]
    mask_key = sock.recv(4) if masked else b''
    payload = bytearray(sock.recv(length))
    if masked and mask_key:
        payload = bytearray(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
    if opcode == 0x8:
        return ''
    if opcode != 0x1:
        return ''
    return payload.decode('utf-8', errors='replace')


def _probe_onevoice_stt_status(host: str = '127.0.0.1', port: int = 2711, *, timeout: float = 2.5) -> dict[str, Any]:
    """Read live model readiness from OneVoice speak_stt's local WebSocket."""
    cache_key = f'{host}:{int(port)}'
    now = time.time()
    cached_at, cached = _STT_WS_CACHE.get(cache_key, (0.0, {}))
    if cached and (now - cached_at) < _STT_WS_CACHE_TTL:
        return dict(cached)

    result: dict[str, Any] = {}
    if not tcp_port_open(host, port):
        _STT_WS_CACHE[cache_key] = (now, result)
        return result

    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        sock.settimeout(timeout)
        ws_key = base64.b64encode(secrets.token_bytes(16)).decode('ascii')
        request = (
            f'GET / HTTP/1.1\r\n'
            f'Host: {host}:{int(port)}\r\n'
            f'Upgrade: websocket\r\n'
            f'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {ws_key}\r\n'
            f'Sec-WebSocket-Version: 13\r\n'
            f'\r\n'
        )
        sock.sendall(request.encode('ascii'))
        response = b''
        while b'\r\n\r\n' not in response and len(response) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b' 101 ' not in response:
            _STT_WS_CACHE[cache_key] = (now, result)
            return result

        _ws_send_text_frame(sock, json.dumps({'config': {}}))
        model = ''
        model_loaded = False
        loading = False
        error = ''
        device = ''
        for _ in range(6):
            text = _ws_recv_text_frame(sock)
            if not text:
                break
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            status = str(payload.get('status') or '').lower()
            if payload.get('model_loaded') is True:
                model_loaded = True
            if payload.get('model'):
                model = str(payload.get('model') or '').strip()
            if payload.get('device'):
                device = str(payload.get('device') or '').strip()
            if status in {'loading-model', 'loading'}:
                loading = True
            if status == 'model-ready':
                model_loaded = True
                loading = False
            if status == 'error' or payload.get('error'):
                error = str(payload.get('error') or payload.get('message') or '').strip()
                model_loaded = False
                loading = False
            if model_loaded and model:
                break

        if model_loaded or loading or error:
            result = {
                'model': model,
                'model_loaded': model_loaded,
                'loading': loading and not model_loaded,
                'device': device,
                'error': error,
            }
    except OSError:
        result = {}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    _STT_WS_CACHE[cache_key] = (time.time(), result)
    return result


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
                'display_name': str(entry.get('display_name') or entry.get('id') or entry.get('key') or model_id),
                'publisher': str(entry.get('publisher') or ''),
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


def _ai_tools_root_from_command(command_line: str) -> Path | None:
    cmd = str(command_line or '')
    for match in re.finditer(r'([A-Za-z]:[\\/][^"\'\s]+?[\\/]AI-Tools)', cmd, re.I):
        candidate = Path(match.group(1))
        if candidate.is_dir():
            return candidate
    return None


def _read_ai_tools_whisper_model_size(ai_tools_root: Path) -> str:
    config_path = ai_tools_root / 'config.json'
    try:
        payload = json.loads(config_path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, json.JSONDecodeError, ValueError):
        return 'medium'
    whisper_cfg = payload.get('whisper_model')
    if isinstance(whisper_cfg, dict):
        size = str(whisper_cfg.get('model_size') or '').strip()
        if size:
            return size
    legacy = payload.get('whisper')
    if isinstance(legacy, dict):
        size = str(legacy.get('model_size') or '').strip()
        if size:
            return size
    return 'medium'


def _resolve_ai_tools_stt_model_path(command_line: str) -> str:
    hay = str(command_line or '').lower()
    if 'transcribe_module' not in hay and 'voice_core' not in hay and 'speaker_diagnosis' not in hay:
        return ''
    root = _ai_tools_root_from_command(command_line)
    if root is None:
        return ''
    model_size = _read_ai_tools_whisper_model_size(root)
    variants: list[str] = []
    for token in (model_size, f'{model_size}.en'):
        token = str(token or '').strip()
        if token and token not in variants:
            variants.append(token)

    local_dirs: list[Path] = [root / '.model_cache']
    sibling_models = root.parent / 'Dflash-Console' / 'models'
    if sibling_models.is_dir():
        local_dirs.append(sibling_models)

    def _pick_from_base(base: Path, slug: str) -> str:
        if not base.is_dir():
            return ''
        direct = base / f'faster-whisper-{slug}'
        if direct.is_dir():
            return str(direct)
        for match in base.glob(f'models--*--faster-whisper-{slug}'):
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

    for base in local_dirs:
        for variant in variants:
            for slug in {variant, variant.replace('.', '-'), variant.replace('-', '.')}:
                picked = _pick_from_base(base, slug)
                if picked:
                    return picked

    for variant in variants:
        for slug in {variant, variant.replace('.', '-'), variant.replace('-', '.')}:
            for base in _stt_hub_search_roots():
                picked = _pick_from_base(base, slug)
                if picked:
                    return picked
    return _resolve_stt_model_path(model_size, command_line)


def _ai_tools_task_from_functions_file(functions_file: str) -> str:
    path = Path(str(functions_file or '').strip().strip('"').strip("'"))
    if not path.is_file():
        return ''
    try:
        payload = json.loads(path.read_text(encoding='utf-8', errors='replace') or '{}')
    except (OSError, json.JSONDecodeError, ValueError):
        return ''
    processing = payload.get('processing_functions') if isinstance(payload, dict) else None
    if not isinstance(processing, dict):
        return ''
    labels: list[str] = []
    for key, enabled in processing.items():
        if not enabled:
            continue
        label = _AI_TOOLS_FUNC_LABELS.get(str(key), '')
        if label and label not in labels:
            labels.append(label)
    return ' + '.join(labels[:2])


def _resolve_ai_tools_model_name(command_line: str) -> str:
    cmd = str(command_line or '')
    hay = cmd.lower()
    if 'transcribe_module' in hay or 'voice_core' in hay or 'speaker_diagnosis' in hay:
        return 'Voice recognition'
    match = re.search(r'--functions-file=(\S+)', cmd, re.I)
    if match:
        task = _ai_tools_task_from_functions_file(match.group(1))
        if task:
            return task
    if 'scraper.py' in hay and '--run-functions' in hay:
        return 'GPU worker'
    if 'transcribe' in hay:
        return 'Transcription'
    return 'GPU worker'


def _resolve_external_model_name(
    *,
    app_source: str,
    app_label: str,
    process_name: str,
    command_line: str,
    parent_name: str = '',
    api_model_id: str = '',
) -> tuple[str, str]:
    hinted_name, model_path = _model_hint_from_cmdline(command_line)
    if api_model_id:
        return api_model_id, model_path

    model_name = hinted_name

    if app_source == 'ai-tools':
        ai_name = _resolve_ai_tools_model_name(command_line)
        if ai_name and not model_path:
            model_path = _resolve_ai_tools_stt_model_path(command_line)
        if ai_name:
            return ai_name, model_path

    if not model_name and app_source in {'onevoice', 'whisper'} and 'speak_stt' in command_line.lower():
        for port in _ONEVOICE_STT_PORTS:
            stt = _probe_onevoice_stt_status('127.0.0.1', port)
            if stt.get('model_loaded') and str(stt.get('model') or '').strip():
                model_name = str(stt.get('model') or '').strip()
                break
            if stt.get('loading'):
                return 'Loading…', model_path
        if not model_name:
            log_model = _read_speak_stt_active_model(command_line=command_line, max_age_seconds=45.0)
            if log_model:
                model_name = log_model

    if not model_name and 'speak_stt.py' in command_line.lower():
        return 'Loading…', model_path

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

    generic = _generic_workload_title(
        process_name=process_name,
        command_line=command_line,
        parent_name=parent_name,
        app_label=app_label,
    )
    if generic and (not model_name or model_name == app_label):
        model_name = generic

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
    if timed_out:
        return {
            'api_url': api_base_url(api_url),
            'model_id': '',
            'unload_via_api': False,
            'loading': True,
        }
    if not entries and not timed_out:
        entries, timed_out = _probe_models_fast(f'http://{host}:{int(port)}')
        if timed_out:
            bare_url = f'http://{host}:{int(port)}'
            return {
                'api_url': api_base_url(bare_url),
                'model_id': '',
                'unload_via_api': False,
                'loading': True,
            }
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
    # Router-mode llama.cpp spawns each loaded model as a child llama-server on
    # a random port. That child belongs to the Console-managed engine (its
    # parent listens on a configured port) — never show it as an external card.
    parent_pid = int(details.get('parent_pid') or 0)
    if parent_pid and parent_pid in managed_pids:
        return None

    process_name = str(details.get('process_name') or entry.get('process_name') or '')
    if process_name.startswith('['):
        return None
    command_line = str(details.get('command_line') or '')
    parent_name = str(details.get('parent_process_name') or '')
    hay = f'{process_name} {command_line} {parent_name}'.lower()

    app_source, app_label = _classify_app(
        process_name=process_name,
        command_line=command_line,
        parent_name=parent_name,
    )
    if app_source == 'dflash':
        # A draft filename often contains "DFlash"; that must not make an
        # external llama-server look like a Console-owned process.
        app_source, app_label = 'llama-server', 'llama-server'

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
    loading = False

    listen_ports = _listening_ports_for_pid(pid)
    if any(port in configured_ports for port in listen_ports):
        return None

    ports_to_check = list(listen_ports)
    if 'speak_stt.py' in command_line.lower():
        for port in _ONEVOICE_STT_PORTS:
            if port not in ports_to_check and port not in configured_ports:
                ports_to_check.append(port)

    for port in ports_to_check:
        if port in configured_ports:
            continue
        if port in _ONEVOICE_STT_PORTS:
            stt = _probe_onevoice_stt_status('127.0.0.1', port)
            listen_port = port
            if stt.get('model_loaded'):
                model_id = str(stt.get('model') or '').strip()
                loading = False
                break
            if stt.get('loading'):
                loading = True
                break
            if stt.get('error'):
                loading = False
                break
            continue
        probe = _probe_loaded_model('127.0.0.1', port)
        if probe.get('loading'):
            api_url = str(probe.get('api_url') or f'http://127.0.0.1:{int(port)}/v1')
            listen_port = port
            loading = True
            break
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
        parent_name=parent_name,
        api_model_id=model_id,
    )
    if not str(model_name or '').strip():
        if loading or (vram_mib is not None and float(vram_mib) >= _MIN_VRAM_MIB):
            model_name = 'Loading…'
            loading = True
        else:
            model_name = _generic_workload_title(
                process_name=process_name,
                command_line=command_line,
                parent_name=parent_name,
                app_label=app_label,
            )
            if not model_name:
                return None
    elif str(model_name).strip().lower().startswith('loading'):
        loading = True
    speak_stt_ready = (
        'speak_stt.py' in command_line.lower()
        and model_name
        and not str(model_name).strip().lower().startswith('loading')
    )
    if (
        not loading
        and not api_url
        and not speak_stt_ready
        and vram_mib is not None
        and float(vram_mib) >= _MIN_VRAM_MIB
        and _should_track_process(
            process_name=process_name,
            command_line=command_line,
            parent_name=parent_name,
            vram_mib=float(vram_mib),
            app_source=app_source,
        )
    ):
        loading = True
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
    draft_name, draft_path = _draft_hint_from_cmdline(command_line)
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
    llama_process = 'llama-server' in hay or 'llama_server' in hay
    if draft_path:
        acceleration = {
            'acceleration_expected': True,
            'acceleration_mode': 'dflash',
            'acceleration_label': 'DFlash active',
            'draft_loaded': True,
            'draft_status': 'active',
            'draft_path': draft_path,
            'draft_filename': draft_name,
        }
    elif llama_process:
        acceleration = {
            'acceleration_expected': True,
            'acceleration_mode': 'unknown',
            'acceleration_label': 'DFlash status unknown',
            'draft_loaded': None,
            'draft_status': 'unknown',
        }
    else:
        acceleration = {}

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
        'card_state': 'loading' if loading else 'ready',
        'ejectable': not loading,
        'title': model_name,
        'subtitle': card_detail,
        'card_detail': card_detail,
        **kind_fields,
        **acceleration,
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
    display_name: str = '',
    publisher: str = '',
    quantization: str = '',
    card_state: str = 'ready',
    lm_state: str = '',
) -> dict[str, Any]:
    if str(lm_state or '').lower() == 'loading':
        card_state = 'loading'
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
        'display_name': display_name,
        'publisher': publisher,
        'quantization': quantization,
        'listen_port': listen_port,
        'unload_method': 'api' if unload_via_api else 'kill',
        'card_state': card_state,
        'ejectable': card_state != 'loading',
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
                display_name=str(model.get('display_name') or ''),
                publisher=str(model.get('publisher') or ''),
                quantization=str(model.get('quantization') or ''),
                lm_state=str(model.get('state') or ''),
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


_API_KEY_RE = re.compile(r'--api-key[=\s]+(\S+)', re.I)
_API_KEY_CACHE: dict[int, tuple[float, str]] = {}
_PROCESS_DETAILS_CACHE: dict[str, Any] = {'at': 0.0, 'map': {}}
_LISTEN_PORTS_CACHE: tuple[float, dict[int, list[int]]] = (0.0, {})
_PROCESS_DETAILS_TTL_SECONDS = 30.0
_LISTEN_PORTS_TTL_SECONDS = 3.0
_GPU_BUSY_THRESHOLD = 5
_GPU_LIVE_CACHE: tuple[float, dict[int, dict[str, Any]]] = (0.0, {})
_GPU_LIVE_TTL_SECONDS = 1.5
_EXTERNAL_SCAN_CACHE: dict[str, Any] = {'at': 0.0, 'cards': []}
_EXTERNAL_SCAN_MIN_INTERVAL = 2.5


def _api_key_from_cmdline(command_line: str) -> str:
    match = _API_KEY_RE.search(str(command_line or ''))
    if not match:
        return ''
    return match.group(1).strip('"\'')


def _api_key_for_process(pid: int, fallback_cmdline: str = '') -> str:
    """API key a llama-server worker was launched with.

    LM Studio workers are started with ``--api-key <token>``; their native
    endpoints (e.g. /slots) return 401 without it. Query the process command
    line once per pid and cache the key for 60s to keep status polls cheap.
    """
    key_pid = int(pid or 0)
    now = time.time()
    cached = _API_KEY_CACHE.get(key_pid)
    if cached and (now - cached[0]) < 60.0:
        return cached[1]
    key = _api_key_from_cmdline(fallback_cmdline)
    if not key and key_pid > 0:
        details = _query_process_details([key_pid]).get(key_pid, {})
        key = _api_key_from_cmdline(str(details.get('command_line') or ''))
    _API_KEY_CACHE[key_pid] = (now, key)
    return key


def _attach_external_inference_stats(card: dict[str, Any]) -> dict[str, Any]:
    """Poll llama-server /slots for token metrics on external GPU model cards.

    Any llama-server-backed kind (LLM, OCR, vision, embedding, translation, …)
    exposes /slots, so attach live IN/OUT/SPEED stats whenever the card has an
    API URL. fetch_inference_stats fails gracefully (returns empty stats) for
    endpoints without /slots, so STT/TTS cards simply keep showing zeros.
    """
    enriched = dict(card)
    api_url = str(card.get('api_url') or '').strip()
    if not api_url:
        return enriched
    pid = int(card.get('pid') or 0)
    server_id = f'external-{pid}' if pid > 0 else str(card.get('id') or '')
    from core.inference_stats import fetch_inference_stats

    # LM Studio workers require their --api-key on /slots; extract it from the
    # worker's command line (cached per pid) and pass it along.
    api_key = ''
    if str(card.get('app_source') or '').lower() == 'lmstudio':
        api_key = _api_key_for_process(pid, fallback_cmdline=str(card.get('command_line') or ''))

    enriched['inference_stats'] = fetch_inference_stats(
        api_url,
        server_id=server_id,
        model_id=str(card.get('model_id') or card.get('model_name') or ''),
        api_key=api_key,
    )
    return enriched


def _gpu_live_map() -> dict[int, dict[str, Any]]:
    global _GPU_LIVE_CACHE
    now = time.time()
    cached_at, cached = _GPU_LIVE_CACHE
    if cached and (now - cached_at) < _GPU_LIVE_TTL_SECONDS:
        return cached

    from core.system_stats import _query_gpus_live

    live = {int(row.get('index', -1)): row for row in _query_gpus_live() if int(row.get('index', -1)) >= 0}
    _GPU_LIVE_CACHE = (now, live)
    return live


def _attach_external_gpu_activity(card: dict[str, Any], *, gpu_live: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Keep per-process VRAM from nvidia-smi; never substitute whole-GPU stats."""
    return dict(card)


def _enrich_external_cards(cards: list[dict[str, Any]], *, attach_stats: bool = True) -> list[dict[str, Any]]:
    gpu_live = _gpu_live_map()
    enriched: list[dict[str, Any]] = []
    for card in cards:
        row = _attach_external_gpu_activity(card, gpu_live=gpu_live)
        if attach_stats:
            row = _attach_external_inference_stats(row)
        enriched.append(row)
    return enriched


def _normalize_model_token(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _external_cards_refer_same_model(api_card: dict[str, Any], process_card: dict[str, Any]) -> bool:
    """True when an API-derived external card is just another view of the same
    model already shown by a GPU-process card (which carries the real file path
    and can therefore offer Copy to Console)."""
    if str(api_card.get('app_source') or '') != str(process_card.get('app_source') or ''):
        return False
    path_norm = _normalize_model_token(str(process_card.get('model_path') or ''))
    if not path_norm:
        return False
    tokens = [
        _normalize_model_token(str(api_card.get('model_id') or '')),
        _normalize_model_token(str(api_card.get('display_name') or '')),
    ]
    if not any(token and token in path_norm for token in tokens):
        return False
    quant = _normalize_model_token(str(api_card.get('quantization') or ''))
    if quant and quant not in path_norm:
        return False
    return True


def _dedupe_external_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop API-derived cards (from LM Studio / Ollama /models endpoints) that
    duplicate a GPU-process card for the same model. The process card carries the
    real file path (Copy to Console) — showing both is confusing."""
    process_cards = [c for c in cards if str(c.get('id') or '').startswith('external-gpu-')]
    if not process_cards:
        return cards
    kept: list[dict[str, Any]] = []
    for card in cards:
        if str(card.get('id') or '').startswith('external-api-'):
            if any(_external_cards_refer_same_model(card, p) for p in process_cards):
                continue
        kept.append(card)
    return kept


def _external_card_path_missing(card: dict[str, Any]) -> bool:
    """True when an external card references a model path that no longer exists
    on disk (file or directory was moved/deleted) — such a card must not show."""
    path = str(card.get('model_path') or card.get('path') or '').strip()
    if not path:
        return False
    try:
        return not Path(path).exists()
    except OSError:
        return False


_ONEVOICE_STT_PORTS = (2711,)


def _retain_alive_external_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep recent external cards when nvidia-smi briefly drops a live process."""
    kept: list[dict[str, Any]] = []
    pids = [int(card['pid']) for card in cards if int(card.get('pid') or 0) > 0]
    details_map = _query_process_details(pids) if pids else {}
    for card in cards:
        pid = int(card.get('pid') or 0)
        if pid > 0 and pid in details_map:
            kept.append(dict(card))
            continue
        listen_port = int(card.get('listen_port') or 0)
        if listen_port > 0 and _pid_listening_on_port(listen_port) == pid:
            kept.append(dict(card))
            continue
        if str(card.get('model_kind') or '').lower() == 'stt':
            for port in _ONEVOICE_STT_PORTS:
                live_pid = _pid_listening_on_port(port)
                if live_pid == pid or (live_pid and live_pid > 0):
                    refreshed = dict(card)
                    if live_pid != pid:
                        refreshed['pid'] = live_pid
                        refreshed['id'] = f'external-gpu-{live_pid}'
                    refreshed['listen_port'] = port
                    kept.append(refreshed)
                    break
    return kept


def _discover_speak_stt_listener_cards(
    *,
    gpus: list[dict[str, Any]],
    managed_pids: set[int],
    configured_ports: set[int],
    dflash_root: str,
    seen_pids: set[int],
) -> list[dict[str, Any]]:
    """Find OneVoice speak_stt even when nvidia-smi omits the worker PID."""
    cards: list[dict[str, Any]] = []
    for port in _ONEVOICE_STT_PORTS:
        if port in configured_ports:
            continue
        pid = _pid_listening_on_port(port)
        if not pid or pid in seen_pids or pid in managed_pids:
            continue
        details_map = _query_process_details([pid])
        details = details_map.get(pid, {})
        hay = ' '.join(
            [
                str(details.get('process_name') or ''),
                str(details.get('command_line') or ''),
                str(details.get('parent_process_name') or ''),
            ]
        ).lower()
        if 'speak_stt' not in hay:
            continue
        entry = {
            'pid': pid,
            'gpu_index': 0,
            'vram_mb': None,
            'vram_gb': None,
            'process_name': str(details.get('process_name') or 'python.exe'),
        }
        card = _build_external_card(
            entry,
            details=details,
            gpus=gpus,
            managed_pids=managed_pids,
            configured_ports=configured_ports,
            dflash_root=dflash_root,
        )
        if card:
            card['listen_port'] = port
            cards.append(card)
            seen_pids.add(pid)
    return cards


def get_external_gpu_loads(
    *,
    servers: list[dict[str, Any]] | None = None,
    gpus: list[dict[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
    fast: bool = False,
) -> list[dict[str, Any]]:
    servers = servers or []
    if gpus is None:
        from core.gpu_devices import query_gpu_devices

        gpus = query_gpu_devices()

    hardware = (cfg or {}).get('hardware_settings') or {}
    if hardware.get('detect_external_gpu_loads') is False:
        return []

    now = time.time()
    cached_cards = _EXTERNAL_SCAN_CACHE.get('cards')
    cached_at = float(_EXTERNAL_SCAN_CACHE.get('at') or 0.0)
    if (
        isinstance(cached_cards, list)
        and cached_cards
        and (now - cached_at) < _EXTERNAL_SCAN_MIN_INTERVAL
    ):
        return _enrich_external_cards([dict(row) for row in cached_cards], attach_stats=not fast)

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

    for card in _discover_speak_stt_listener_cards(
        gpus=gpus,
        managed_pids=managed_pids,
        configured_ports=configured_ports,
        dflash_root=dflash_root,
        seen_pids=seen_pids,
    ):
        card_id = str(card.get('id') or '')
        if card_id in seen_ids:
            continue
        seen_ids.add(card_id)
        cards.append(card)

    cards = _dedupe_external_cards(cards)
    # Never show a card for a model file that no longer exists on disk (e.g.
    # after the file was moved or deleted) — a card for a missing file is
    # misleading. Applies to every external card.
    cards = [card for card in cards if not _external_card_path_missing(card)]
    if not cards:
        prev = _EXTERNAL_SCAN_CACHE.get('cards') or []
        if isinstance(prev, list) and prev:
            cards = _retain_alive_external_cards(prev)
    cards.sort(key=lambda item: (-float(item.get('vram_mb') or 0), str(item.get('title') or '')))
    _EXTERNAL_SCAN_CACHE['at'] = time.time()
    _EXTERNAL_SCAN_CACHE['cards'] = [dict(card) for card in cards]
    return _enrich_external_cards(cards, attach_stats=not fast)


def _forget_external_scan() -> None:
    _EXTERNAL_SCAN_CACHE['at'] = 0.0
    _EXTERNAL_SCAN_CACHE['cards'] = []


def _related_external_compute_pids(
    target_pid: int,
    *,
    cached: dict[str, Any] | None,
    matching: dict[str, Any] | None,
) -> list[int]:
    """Live GPU PIDs for the same external model, not just the (possibly stale) card PID."""
    seed = ' '.join(
        [
            str((matching or {}).get('command_line') or ''),
            str((matching or {}).get('process_name') or ''),
            str((cached or {}).get('command_line') or ''),
            str((cached or {}).get('process_name') or ''),
            str((cached or {}).get('model_name') or ''),
            str((cached or {}).get('model_path') or ''),
        ]
    ).lower()
    kind = str((cached or {}).get('model_kind') or '').lower()
    app_source = str((cached or {}).get('app_source') or '').strip().lower()
    model = str((cached or {}).get('model_name') or '').strip().lower()
    want_stt = (
        kind == 'stt'
        or 'speak_stt' in seed
        or 'faster-whisper' in seed
        or 'faster_whisper' in seed
        or (app_source in {'onevoice', 'whisper'} and 'python' in seed)
    )
    compute = query_compute_apps()
    pids = [int(row['pid']) for row in compute if int(row.get('pid') or 0) > 0]
    details_map = _query_process_details(pids) if pids else {}
    live: set[int] = set()
    for row in compute:
        pid = int(row['pid'])
        details = details_map.get(pid, {})
        process_name = str(details.get('process_name') or row.get('process_name') or '')
        command_line = str(details.get('command_line') or row.get('command_line') or '')
        hay = f'{process_name} {command_line}'.lower()
        source, _ = _classify_app(
            process_name=process_name,
            command_line=command_line,
            parent_name=str(details.get('parent_process_name') or ''),
        )
        if pid == int(target_pid):
            live.add(pid)
            continue
        if want_stt and ('speak_stt' in hay or 'faster-whisper' in hay or 'faster_whisper' in hay):
            live.add(pid)
            continue
        if app_source and source == app_source and model and model in hay:
            live.add(pid)
    return sorted(live)


def _kill_external_pid(pid: int) -> dict[str, Any]:
    try:
        if sys.platform == 'win32':
            proc = subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(int(pid))],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                **_subprocess_no_window_kwargs(),
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or '').strip() or 'taskkill failed'
                return {'success': False, 'error': detail, 'pid': int(pid)}
        else:
            os.kill(int(pid), 9)
    except Exception as exc:
        return {'success': False, 'error': str(exc), 'pid': int(pid)}
    return {'success': True, 'pid': int(pid), 'method': 'kill'}


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
                _forget_external_scan()
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
                _forget_external_scan()
                return {**native, 'pid': target_pid, 'method': 'lmstudio-api'}

        # Generic OpenAI-compatible unload (Ollama, llama-server, LM Studio
        # workers). Try it BEFORE the PID lookup: the stored PID is often the
        # service/app process (e.g. ollama.exe) rather than the current GPU
        # compute worker, so it would not appear in nvidia-smi's compute apps.
        result = unload_model(api_url=api, model_id=model)
        if result.get('success'):
            _forget_external_scan()
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
    cached = next(
        (
            row for row in (_EXTERNAL_SCAN_CACHE.get('cards') or [])
            if int(row.get('pid') or 0) == target_pid
        ),
        None,
    )
    related = _related_external_compute_pids(target_pid, cached=cached, matching=matching)
    if not matching and not cached and not related:
        return {'success': False, 'error': 'process is not a current GPU compute process', 'pid': target_pid}

    killed: list[int] = []
    last_error = ''
    kill_targets = related or ([target_pid] if (matching or cached) else [])
    for kill_pid in kill_targets:
        details = _query_process_details([kill_pid]).get(kill_pid, {})
        process_name = str(
            details.get('process_name')
            or (matching or {}).get('process_name')
            or (cached or {}).get('process_name')
            or ''
        )
        command_line = str(
            details.get('command_line')
            or (matching or {}).get('command_line')
            or (cached or {}).get('command_line')
            or ''
        )
        if _DESKTOP_NOISE.search(process_name) or _is_ui_only_process(
            process_name=process_name,
            command_line=command_line,
        ):
            last_error = 'process is not an approved model process'
            continue
        identity = f'{process_name} {command_line}'
        # A card we already listed as an external model is safe to unload even
        # when nvidia-smi only reports python.exe with no model keywords.
        if not cached and (
            _APP_SERVER_CMD.search(identity)
            or not (_ML_PROCESS.search(identity) or _ML_CMD.search(identity))
        ):
            last_error = 'process is not an approved model process'
            continue
        result = _kill_external_pid(kill_pid)
        if result.get('success'):
            killed.append(kill_pid)
        else:
            last_error = str(result.get('error') or 'taskkill failed')

    _forget_external_scan()
    if not killed:
        return {
            'success': False,
            'error': last_error or 'process is not an approved model process',
            'pid': target_pid,
        }
    return {
        'success': True,
        'pid': target_pid,
        'killed_pids': killed,
        'method': 'kill',
        'message': 'Process terminated',
    }


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
