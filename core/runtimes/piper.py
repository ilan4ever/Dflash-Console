"""Piper TTS runtime adapter (CLI mode).

Wraps the Piper native binary (``runtimes/piper/piper.exe``) and its ONNX voice
models (``runtimes/piper/voices/*.onnx`` + sibling ``.onnx.json``). Piper is a
per-utterance CLI (text on stdin -> WAV on stdout), so ``execution_mode`` is
``cli`` and there is no long-lived child process or loopback port.

Process identity: every spawned piper.exe matches the registered
``process_identity_tokens`` so ``managed_process_identity`` / ``server.ps1``
can adopt and clean up any orphaned synthesis processes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.runtimes.base import EXECUTION_MODE_CLI, MODALITY_TEXT_TO_SPEECH, RUNTIME_PIPER

PIPER_BUNDLE = ROOT / 'runtimes' / 'piper'
PIPER_EXE = PIPER_BUNDLE / 'piper.exe'
PIPER_VOICES = PIPER_BUNDLE / 'voices'
PIPER_MANIFEST = PIPER_BUNDLE / 'manifest.json'

LOG_DIR = ROOT / 'logs' / 'runtimes'
PIper_LOG = LOG_DIR / 'piper.log'

_SYNTH_TIMEOUT_SECONDS = 180.0
_STATE_LOCK = threading.Lock()
_PROFILE: dict[str, Any] = {}
_ACTIVE_VOICE: str = ''

# Identity token: a distinctive, path-segment substring that appears in the
# command line of Console-managed piper.exe but NOT in other apps' piper
# processes (e.g. OneVoiceSpeak's bundled piper). Matching against the bare
# process name would let the Console kill a foreign app's piper on shutdown.
PIPER_PROCESS_TOKEN = f'runtimes{os.sep}piper{os.sep}piper.exe'


def _log_line(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PIper_LOG.open('a', encoding='utf-8') as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


class PiperRuntimeAdapter:
    runtime_id = RUNTIME_PIPER
    modalities = (MODALITY_TEXT_TO_SPEECH,)
    execution_mode = EXECUTION_MODE_CLI
    process_identity_tokens = (PIPER_PROCESS_TOKEN,)

    # -- install / health ---------------------------------------------------

    @staticmethod
    def is_installed() -> bool:
        return PIPER_EXE.is_file()

    def health(self) -> dict[str, Any]:
        voices = self.list_voices()
        with _STATE_LOCK:
            active = _ACTIVE_VOICE
        return {
            'ok': True,
            'runtime_id': self.runtime_id,
            'installed': self.is_installed(),
            'execution_mode': self.execution_mode,
            'running': self.is_installed() and bool(active),
            'active_voice': active,
            'voices': len(voices),
            'bundle': str(PIPER_BUNDLE),
        }

    def start(self, profile: dict[str, Any]) -> dict[str, Any]:
        with _STATE_LOCK:
            _PROFILE.clear()
            _PROFILE.update(dict(profile or {}))
        if not self.is_installed():
            return {'success': False, 'error': 'Piper runtime is not installed under runtimes/piper/'}
        self.write_manifest()
        _log_line(f"piper adapter started (profile={profile.get('device_policy') or 'cpu'})")
        return {'success': True, 'started': True, 'runtime_id': self.runtime_id}

    def stop(self) -> dict[str, Any]:
        with _STATE_LOCK:
            _ACTIVE_VOICE = ''
            _PROFILE.clear()
        _log_line('piper adapter stopped; active voice cleared')
        return {'success': True, 'stopped': True, 'runtime_id': self.runtime_id}

    def load(self, model: dict[str, Any]) -> dict[str, Any]:
        voice_path = str((model or {}).get('path') or '').strip()
        voice_id = str((model or {}).get('id') or '').strip()
        if voice_path:
            path_obj = Path(voice_path)
            if not path_obj.is_file():
                return {'success': False, 'error': f'voice not found: {voice_path}'}
            voice_id = voice_id or path_obj.stem
        elif voice_id:
            resolved = self.resolve_voice(voice_id)
            if not resolved:
                return {'success': False, 'error': f'voice not found: {voice_id}'}
            voice_id = resolved.stem
        else:
            default = self.default_voice()
            if not default:
                return {'success': False, 'error': 'no Piper voices installed under runtimes/piper/voices/'}
            voice_id = default.stem
        with _STATE_LOCK:
            global _ACTIVE_VOICE
            _ACTIVE_VOICE = voice_id
        _log_line(f"piper voice loaded: {voice_id}")
        return {'success': True, 'loaded': True, 'runtime_id': self.runtime_id, 'voice': voice_id}

    def unload(self) -> dict[str, Any]:
        with _STATE_LOCK:
            global _ACTIVE_VOICE
            _ACTIVE_VOICE = ''
        _log_line('piper voice unloaded')
        return {'success': True, 'unloaded': True, 'runtime_id': self.runtime_id}

    def openai_routes(self) -> list[str]:
        return ['/v1/audio/speech']

    # -- voices -------------------------------------------------------------

    def list_voices(self) -> list[dict[str, Any]]:
        if not PIPER_VOICES.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for onnx in sorted(PIPER_VOICES.glob('*.onnx')):
                if onnx.name.lower().startswith('mmproj'):
                    continue
                json_path = onnx.with_suffix('.onnx.json')
                if not json_path.is_file():
                    continue
                rows.append({
                    'id': onnx.stem,
                    'label': onnx.stem.replace('_', ' ').replace('-', ' ').title(),
                    'path': str(onnx),
                    'config': str(json_path),
                    'size_bytes': _safe_size(onnx),
                })
        except OSError:
            pass
        return rows

    def resolve_voice(self, voice_id: str) -> Path | None:
        needle = str(voice_id or '').strip()
        if not needle or not PIPER_VOICES.is_dir():
            return None
        direct = (PIPER_VOICES / needle).with_suffix('.onnx')
        if direct.is_file():
            return direct
        try:
            for onnx in PIPER_VOICES.glob('*.onnx'):
                if onnx.stem == needle or needle in onnx.stem:
                    return onnx
        except OSError:
            pass
        return None

    def default_voice(self) -> Path | None:
        voices = self.list_voices()
        if not voices:
            return None
        preferred = next((v for v in voices if 'lessac' in v['id']), voices[0])
        return Path(preferred['path'])

    def write_manifest(self) -> Path:
        try:
            voices = [v['id'] for v in self.list_voices()]
            payload = {
                'version': 1,
                'runtime_id': self.runtime_id,
                'binary': str(PIPER_EXE),
                'voices_dir': str(PIPER_VOICES),
                'voices': voices,
                'execution_mode': self.execution_mode,
                'generated_by': 'core.runtimes.piper',
            }
            temporary = PIPER_MANIFEST.with_suffix('.tmp')
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
                encoding='utf-8',
            )
            temporary.replace(PIPER_MANIFEST)
        except OSError:
            pass
        return PIPER_MANIFEST

    # -- synthesis ----------------------------------------------------------

    def synthesize(self, text: str, *, voice: str = '', speed: float = 1.0) -> dict[str, Any]:
        """Synthesize ``text`` to WAV bytes via the Piper CLI."""
        if not text or not text.strip():
            return {'success': False, 'error': 'input text is required'}
        if not self.is_installed():
            return {'success': False, 'error': 'Piper runtime is not installed under runtimes/piper/'}
        with _STATE_LOCK:
            voice_id = voice or _ACTIVE_VOICE
        voice_path = self.resolve_voice(voice_id) if voice_id else self.default_voice()
        if not voice_path:
            return {'success': False, 'error': 'no Piper voice available'}
        if not voice_path.with_suffix('.onnx.json').is_file():
            return {'success': False, 'error': f'voice config missing for {voice_path.name}'}

        cmd = [
            str(PIPER_EXE),
            '--model', str(voice_path),
            '--output_file', '-',
        ]
        if speed and speed > 0:
            # Piper length_scale: higher = slower. speed > 1 => faster => smaller scale.
            length_scale = round(1.0 / float(speed), 3)
            cmd.extend(['--length_scale', str(length_scale)])

        popen_kwargs: dict[str, Any] = {
            'input': text.encode('utf-8'),
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
        }
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, timeout=_SYNTH_TIMEOUT_SECONDS, **popen_kwargs)
        except subprocess.TimeoutExpired:
            _log_line(f"piper synthesis timed out after {_SYNTH_TIMEOUT_SECONDS}s (voice={voice_path.name})")
            return {'success': False, 'error': f'piper synthesis timed out after {_SYNTH_TIMEOUT_SECONDS:.0f}s'}
        except OSError as exc:
            _log_line(f"piper spawn failed: {exc}")
            return {'success': False, 'error': f'could not run piper: {exc}'}
        elapsed = time.monotonic() - started

        stderr = proc.stderr.decode('utf-8', errors='replace') if proc.stderr else ''
        if stderr.strip():
            _log_line(f"[voice={voice_path.name} rc={proc.returncode}] {stderr.strip()[-400:]}")
        if proc.returncode != 0:
            return {
                'success': False,
                'error': stderr.strip() or f'piper exited with code {proc.returncode}',
            }
        audio = proc.stdout if proc.stdout else b''
        if not audio:
            return {'success': False, 'error': 'piper produced no audio'}
        _log_line(f"piper ok voice={voice_path.name} text_chars={len(text)} wav_bytes={len(audio)} elapsed={elapsed:.2f}s")
        return {'success': True, 'audio': audio, 'media_type': 'audio/wav', 'voice': voice_path.stem}


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


# Module-level singleton used by the registry.
piper_adapter = PiperRuntimeAdapter()
