"""Standalone faster-whisper STT worker for DFlash Console.

A minimal loopback HTTP server (stdlib only) that the Console's
``faster-whisper`` runtime adapter drives. It loads a faster-whisper model
directory (a CTranslate2 snapshot containing ``model.bin`` + ``config.json`` +
``tokenizer.json``) and exposes:

  GET  /health       -> {ok, model_loaded, model, device, compute_type, ...}
  POST /load         JSON {model_dir, device, compute_type, language, task,
                           beam_size, vad_filter, temperature, cpu_threads,
                           num_workers}
  POST /unload       release the loaded model
  POST /transcribe   raw audio bytes -> {text} (headers X-Language, X-Task,
                                         X-Response-Format; format 'text'
                                         returns plain text)

The worker runs under a dedicated venv (``runtimes/faster-whisper/venv``)
that has ``faster-whisper`` installed. The Console adapter handles the
OpenAI ``/v1/audio/transcriptions`` multipart translation and forwards raw
audio here, so this server never has to parse multipart.
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from typing import Any

try:  # pragma: no cover - import guard for a bare env
    from faster_whisper import WhisperModel
    _IMPORT_ERROR = ''
except Exception as exc:  # pragma: no cover
    WhisperModel = None
    _IMPORT_ERROR = str(exc)

_STATE_LOCK = threading.Lock()
_MODEL: Any = None
_MODEL_DIR = ''
_DEVICE = ''
_COMPUTE_TYPE = ''
_OPTIONS: dict[str, Any] = {}
_LOAD_ERROR = ''


def _log(text: str) -> None:
    print(f'[faster-whisper-server] {text}', flush=True)


def _load_model(
    model_dir: str,
    *,
    device: str,
    compute_type: str,
    cpu_threads: int,
    num_workers: int,
    device_index: int | None = None,
) -> tuple[Any, str, str]:
    """Instantiate WhisperModel. Returns (model, effective_device, effective_compute_type)."""
    kwargs: dict[str, Any] = {
        'device': device,
        'compute_type': compute_type,
        'local_files_only': True,
    }
    if cpu_threads and cpu_threads > 0:
        kwargs['cpu_threads'] = int(cpu_threads)
    if num_workers and num_workers > 0:
        kwargs['num_workers'] = int(num_workers)
    if device_index is not None:
        kwargs['device_index'] = int(device_index)
    model = WhisperModel(model_dir, **kwargs)
    return model, device, compute_type


class Handler(BaseHTTPRequestHandler):
    server_version = 'DFlashFasterWhisper/1.0'
    protocol_version = 'HTTP/1.1'

    # -- helpers -----------------------------------------------------------

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, text: str) -> None:
        body = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return b''
        return self.rfile.read(length)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == '/health':
            with _STATE_LOCK:
                loaded = _MODEL is not None
                payload: dict[str, Any] = {
                    'ok': True,
                    'model_loaded': loaded,
                    'model': _MODEL_DIR,
                    'device': _DEVICE,
                    'compute_type': _COMPUTE_TYPE,
                    'options': dict(_OPTIONS),
                    'import_error': _IMPORT_ERROR,
                    'load_error': _LOAD_ERROR,
                }
            self._send_json(200, payload)
            return
        self._send_json(404, {'success': False, 'error': 'not found'})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/load':
            self._handle_load()
        elif path == '/unload':
            self._handle_unload()
        elif path == '/transcribe':
            self._handle_transcribe()
        else:
            self._send_json(404, {'success': False, 'error': 'not found'})

    def _handle_load(self) -> None:
        global _MODEL, _MODEL_DIR, _DEVICE, _COMPUTE_TYPE, _OPTIONS, _LOAD_ERROR
        try:
            body = json.loads(self._read_body() or b'{}')
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {'success': False, 'error': 'invalid JSON body'})
            return
        if WhisperModel is None:
            self._send_json(500, {'success': False, 'error': f'faster-whisper is not importable in the runtime venv: {_IMPORT_ERROR}'})
            return
        model_dir = str(body.get('model_dir') or body.get('path') or '').strip()
        if not model_dir:
            self._send_json(400, {'success': False, 'error': 'model_dir is required'})
            return
        device = str(body.get('device') or 'auto').strip() or 'auto'
        device_index = body.get('device_index')
        if device_index is not None:
            device_index = int(device_index)
        compute_type = str(body.get('compute_type') or 'auto').strip() or 'auto'
        cpu_threads = int(body.get('cpu_threads') or 0)
        num_workers = int(body.get('num_workers') or 0)
        options = {
            'language': str(body.get('language') or '').strip(),
            'task': str(body.get('task') or 'transcribe').strip() or 'transcribe',
            'beam_size': int(body.get('beam_size') or 5),
            'vad_filter': bool(body.get('vad_filter') is True or body.get('vad_filter') is True),
            'temperature': float(body.get('temperature') if body.get('temperature') is not None else 0.0),
        }
        # Release any previously loaded model so VRAM is freed before the new one.
        with _STATE_LOCK:
            _MODEL = None
            _LOAD_ERROR = ''
        gc.collect()
        _log(f'loading model dir={model_dir} device={device} compute_type={compute_type}')
        try:
            model, eff_device, eff_compute = _load_model(
                model_dir,
                device=device,
                device_index=device_index,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
            )
        except Exception as exc:  # noqa: BLE001 - surface any load failure to the adapter
            _LOAD_ERROR = str(exc)
            _log(f'load failed: {exc}')
            self._send_json(500, {'success': False, 'error': f'load failed: {exc}'})
            return
        with _STATE_LOCK:
            _MODEL = model
            _MODEL_DIR = model_dir
            _DEVICE = eff_device
            _COMPUTE_TYPE = eff_compute
            _OPTIONS = options
        _log(f'model loaded device={eff_device} compute_type={eff_compute}')
        self._send_json(200, {
            'success': True,
            'model': model_dir,
            'device': eff_device,
            'compute_type': eff_compute,
            'options': options,
        })

    def _handle_unload(self) -> None:
        global _MODEL, _MODEL_DIR, _DEVICE, _COMPUTE_TYPE, _OPTIONS
        with _STATE_LOCK:
            _MODEL = None
            _MODEL_DIR = ''
            _DEVICE = ''
            _COMPUTE_TYPE = ''
            _OPTIONS = {}
        gc.collect()
        _log('model unloaded')
        self._send_json(200, {'success': True, 'unloaded': True})

    def _handle_transcribe(self) -> None:
        with _STATE_LOCK:
            model = _MODEL
            options = dict(_OPTIONS)
        if model is None:
            self._send_json(409, {'success': False, 'error': 'no model loaded (load a faster-whisper model first)'})
            return
        audio = self._read_body()
        if not audio:
            self._send_json(400, {'success': False, 'error': 'empty audio data'})
            return
        language = (self.headers.get('X-Language') or '').strip() or options.get('language') or None
        task = (self.headers.get('X-Task') or '').strip() or options.get('task') or 'transcribe'
        response_format = (self.headers.get('X-Response-Format') or '').strip() or 'json'
        beam_size = int(options.get('beam_size') or 5)
        vad_filter = bool(options.get('vad_filter'))
        temperature = float(options.get('temperature') or 0.0)
        try:
            segments, _info = model.transcribe(
                io.BytesIO(audio),
                language=language,
                task=task,
                beam_size=beam_size,
                vad_filter=vad_filter,
                temperature=temperature,
            )
            text = ''.join(segment.text for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001
            _log(f'transcribe failed: {exc}')
            self._send_json(500, {'success': False, 'error': f'transcription failed: {exc}'})
            return
        _log(f'transcribe ok audio_bytes={len(audio)} text_chars={len(text)}')
        if response_format == 'text':
            self._send_text(200, text)
            return
        self._send_json(200, {'success': True, 'text': text})


def main() -> int:
    parser = argparse.ArgumentParser(description='DFlash Console faster-whisper STT worker')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    _log(f'listening on http://{args.host}:{server.server_address[1]}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with _STATE_LOCK:
            _MODEL = None
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
