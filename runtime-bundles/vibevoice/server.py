"""Standalone VibeVoice TTS worker for DFlash Console.

A minimal loopback HTTP server (stdlib only) that the Console's ``vibevoice``
runtime adapter drives. It loads a Microsoft VibeVoice realtime TTS model (a
Transformers safetensors directory with config.json + model.safetensors +
preprocessor_config.json) and exposes:

  GET  /health       -> {ok, model_loaded, model, device, voice, ...}
  POST /load         JSON {model_dir, device, voice, cfg_scale, ddpm_steps}
  POST /unload       release the model
  POST /synthesize   JSON {text, voice, cfg_scale} -> WAV bytes (audio/wav)

The worker runs under a dedicated venv (``runtimes/vibevoice/venv``) that has
torch (CUDA) + transformers + the ``vibevoice`` package (from Microsoft's
VibeVoice repo, cloned to ``runtimes/vibevoice/repo``). Voice presets (cached
KV prompts) live under ``runtimes/vibevoice/voices/*.pt``.
"""

from __future__ import annotations

import argparse
import copy
import gc
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import torch  # noqa: F401 - used by voice preset loading (weights_only=False)

BASE = Path(__file__).resolve().parent
REPO = BASE / 'repo'
VOICES_DIR = BASE / 'voices'

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_SAMPLE_RATE = 24000

_STATE_LOCK = threading.Lock()
_MODEL: Any = None
_PROCESSOR: Any = None
_PRESET: Any = None
_MODEL_DIR = ''
_DEVICE = ''
_VOICE = ''
_CFG_SCALE = 1.5
_DDPM_STEPS = 5
_LOAD_ERROR = ''
_IMPORT_ERROR = ''


def _log(text: str) -> None:
    print(f'[vibevoice-server] {text}', flush=True)


def _list_voices() -> list[str]:
    if not VOICES_DIR.is_dir():
        return []
    return sorted(p.stem for p in VOICES_DIR.glob('*.pt'))


def _resolve_voice(name: str) -> Path | None:
    needle = str(name or '').strip()
    if not needle or not VOICES_DIR.is_dir():
        return None
    direct = VOICES_DIR / f'{needle}.pt'
    if direct.is_file():
        return direct
    lowered = needle.lower()
    for path in VOICES_DIR.glob('*.pt'):
        if lowered in path.stem.lower():
            return path
    return None


def _load_voice_preset(name: str, device: str) -> Any:
    """Load a cached voice prompt (.pt) from the bundled voices folder."""
    path = _resolve_voice(name)
    if path is None:
        raise ValueError(f'voice preset not found: {name} (available: {", ".join(_list_voices()[:6])}…)')
    # These voice presets ship with the runtime (Microsoft's official
    # VibeVoice repo) and are only ever loaded from the bundled voices/ folder
    # — a trusted source. torch>=2.6's weights_only=True cannot unpickle the
    # ModelOutput SETITEMS used by these cached KV prompts, so weights_only=False
    # is required (same trust model as the official demo).
    return torch.load(path, map_location=device, weights_only=False)


def _load_model(
    model_dir: str,
    *,
    device: str,
    voice: str,
    cfg_scale: float,
    ddpm_steps: int,
) -> tuple[Any, Any, Any, str, str]:
    """Load processor + model + voice preset. Returns (model, processor, preset, device, voice)."""
    import torch

    global _IMPORT_ERROR
    try:
        from vibevoice.modular.modeling_vibevoice_streaming_inference import (
            VibeVoiceStreamingForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor
    except Exception as exc:  # noqa: BLE001
        _IMPORT_ERROR = str(exc)
        raise RuntimeError(f'vibevoice package is not importable: {exc}')

    if device in ('gpu', 'auto'):
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    elif device.startswith('cuda:'):
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is not available')
        try:
            index = int(device.split(':', 1)[1])
        except (TypeError, ValueError):
            raise RuntimeError(f'invalid CUDA device: {device}') from None
        if index < 0 or index >= torch.cuda.device_count():
            raise RuntimeError(f'CUDA device {index} is not available')

    load_dtype = torch.bfloat16 if device.startswith('cuda') else torch.float32
    attn_impl = 'sdpa'  # sdpa is broadly compatible; flash_attention_2 needs extra kernels

    processor = VibeVoiceStreamingProcessor.from_pretrained(model_dir)
    model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
        model_dir,
        torch_dtype=load_dtype,
        device_map=device if device.startswith('cuda') else 'cpu',
        attn_implementation=attn_impl,
        trust_remote_code=True,
    )
    model.eval()
    model.set_ddpm_inference_steps(num_steps=max(1, int(ddpm_steps)))
    target = device if device.startswith('cuda') else 'cpu'
    if device.startswith('cuda') and next(model.parameters()).device.type != 'cuda':
        model.to(device)

    preset = _load_voice_preset(voice, target)
    return model, processor, preset, device, voice


class Handler(BaseHTTPRequestHandler):
    server_version = 'DFlashVibeVoice/1.0'
    protocol_version = 'HTTP/1.1'

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _send_audio(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return b''
        return self.rfile.read(length)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if urlparse(self.path).path == '/health':
            with _STATE_LOCK:
                self._send_json(200, {
                    'ok': True,
                    'model_loaded': _MODEL is not None,
                    'model': _MODEL_DIR,
                    'device': _DEVICE,
                    'voice': _VOICE,
                    'cfg_scale': _CFG_SCALE,
                    'ddpm_steps': _DDPM_STEPS,
                    'voices': _list_voices(),
                    'import_error': _IMPORT_ERROR,
                    'load_error': _LOAD_ERROR,
                    'repo': str(REPO),
                    'venv_python': sys.executable,
                })
            return
        self._send_json(404, {'success': False, 'error': 'not found'})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/load':
            self._handle_load()
        elif path == '/unload':
            self._handle_unload()
        elif path == '/synthesize':
            self._handle_synthesize()
        else:
            self._send_json(404, {'success': False, 'error': 'not found'})

    def _handle_load(self) -> None:
        global _MODEL, _PROCESSOR, _PRESET, _MODEL_DIR, _DEVICE, _VOICE, _CFG_SCALE, _DDPM_STEPS, _LOAD_ERROR
        try:
            body = json.loads(self._read_body() or b'{}')
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {'success': False, 'error': 'invalid JSON body'})
            return
        model_dir = str(body.get('model_dir') or body.get('path') or '').strip()
        if not model_dir:
            self._send_json(400, {'success': False, 'error': 'model_dir is required'})
            return
        device = str(body.get('device') or 'auto').strip() or 'auto'
        voice = str(body.get('voice') or 'en-Carter_man').strip() or 'en-Carter_man'
        cfg_scale = float(body.get('cfg_scale') if body.get('cfg_scale') is not None else 1.5)
        ddpm_steps = int(body.get('ddpm_steps') or 5)

        with _STATE_LOCK:
            _MODEL = None
            _PROCESSOR = None
            _LOAD_ERROR = ''
        gc.collect()
        _log(f'loading model dir={model_dir} device={device} voice={voice}')
        try:
            model, processor, preset, eff_device, eff_voice = _load_model(
                model_dir, device=device, voice=voice, cfg_scale=cfg_scale, ddpm_steps=ddpm_steps,
            )
        except Exception as exc:  # noqa: BLE001
            _LOAD_ERROR = str(exc)
            _log(f'load failed: {exc}')
            self._send_json(500, {'success': False, 'error': f'load failed: {exc}'})
            return
        with _STATE_LOCK:
            _MODEL = model
            _PROCESSOR = processor
            _PRESET = preset
            _MODEL_DIR = model_dir
            _DEVICE = eff_device
            _VOICE = eff_voice
            _CFG_SCALE = cfg_scale
            _DDPM_STEPS = ddpm_steps
        _log(f'model loaded device={eff_device} voice={eff_voice}')
        self._send_json(200, {
            'success': True,
            'model': model_dir,
            'device': eff_device,
            'voice': eff_voice,
            'cfg_scale': cfg_scale,
            'ddpm_steps': ddpm_steps,
        })

    def _handle_unload(self) -> None:
        global _MODEL, _PROCESSOR, _PRESET, _MODEL_DIR, _DEVICE, _VOICE
        with _STATE_LOCK:
            _MODEL = None
            _PROCESSOR = None
            _PRESET = None
            _MODEL_DIR = ''
            _DEVICE = ''
            _VOICE = ''
        gc.collect()
        _log('model unloaded')
        self._send_json(200, {'success': True, 'unloaded': True})

    def _handle_synthesize(self) -> None:
        import io

        import numpy as np
        import torch
        import soundfile as sf

        with _STATE_LOCK:
            model = _MODEL
            processor = _PROCESSOR
            preset = _PRESET
            device = _DEVICE
            cfg_scale = _CFG_SCALE
        if model is None or processor is None:
            self._send_json(409, {'success': False, 'error': 'no model loaded (load a VibeVoice model first)'})
            return
        try:
            body = json.loads(self._read_body() or b'{}')
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {'success': False, 'error': 'invalid JSON body'})
            return
        text = str(body.get('text') or '').strip()
        if not text:
            self._send_json(400, {'success': False, 'error': 'text is required'})
            return
        voice = str(body.get('voice') or _VOICE or 'en-Carter_man').strip()
        scale = float(body.get('cfg_scale') if body.get('cfg_scale') is not None else cfg_scale)

        try:
            preset_for_text = _load_voice_preset(voice, device) if voice else preset
            inputs = processor.process_input_with_cached_prompt(
                text=text,
                cached_prompt=preset_for_text,
                padding=True,
                return_tensors='pt',
                return_attention_mask=True,
            )
            target = device if device.startswith('cuda') else 'cpu'
            for key, value in inputs.items():
                if torch.is_tensor(value):
                    inputs[key] = value.to(target)
            _log(f'synthesizing text_chars={len(text)} voice={voice}')
            outputs = model.generate(
                **inputs,
                max_new_tokens=None,
                cfg_scale=scale,
                tokenizer=processor.tokenizer,
                generation_config={'do_sample': False},
                all_prefilled_outputs=copy.deepcopy(preset_for_text),
                verbose=False,
            )
            audio = outputs.speech_outputs[0]
            if audio is None:
                self._send_json(500, {'success': False, 'error': 'model produced no audio output'})
                return
            if torch.is_tensor(audio):
                audio = audio.detach().cpu().float()
            if getattr(audio, 'ndim', 0) > 1:
                audio = audio.squeeze(0)
            buf = io.BytesIO()
            sf.write(buf, np.asarray(audio), _SAMPLE_RATE, format='WAV', subtype='PCM_16')
            wav = buf.getvalue()
        except Exception as exc:  # noqa: BLE001
            _log(f'synthesize failed: {exc}')
            self._send_json(500, {'success': False, 'error': f'synthesis failed: {exc}'})
            return
        _log(f'synthesize ok text_chars={len(text)} wav_bytes={len(wav)}')
        self._send_audio(wav)


def main() -> int:
    parser = argparse.ArgumentParser(description='DFlash Console VibeVoice TTS worker')
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
            _PROCESSOR = None
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
