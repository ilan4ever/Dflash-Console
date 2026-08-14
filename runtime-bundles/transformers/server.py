"""Standalone Transformers / PyTorch LLM worker for DFlash Console.

Loopback HTTP server driven by ``core.runtimes.transformers_hf``. Loads Hugging
Face model directories (config.json + safetensors weights) and exposes:

  GET  /health              -> {ok, model_loaded, model, device, ...}
  POST /load                JSON {model_dir, device, torch_dtype, trust_remote_code, max_new_tokens}
  POST /unload              release the model
  POST /v1/chat/completions OpenAI-shaped chat (non-streaming for now)

Runs under ``runtimes/transformers/venv`` (installed on demand from Settings).
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_STATE_LOCK = threading.Lock()
_MODEL: Any = None
_TOKENIZER: Any = None
_MODEL_DIR = ''
_DEVICE = ''
_TORCH_DTYPE = ''
_ARCH = 'causal'
_MAX_NEW_TOKENS = 512
_TRUST_REMOTE_CODE = False
_LOAD_ERROR = ''


def _log(text: str) -> None:
    print(f'[transformers-server] {text}', flush=True)


def _resolve_device(requested: str) -> str:
    import torch

    value = str(requested or 'auto').strip().lower()
    if value in ('cuda', 'gpu'):
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    if value == 'cpu':
        return 'cpu'
    return 'cuda' if torch.cuda.is_available() else 'cpu'


def _resolve_dtype(requested: str, device: str) -> Any:
    import torch

    value = str(requested or 'auto').strip().lower()
    if value in ('float16', 'fp16', 'half'):
        return torch.float16
    if value in ('bfloat16', 'bf16'):
        return torch.bfloat16
    if value in ('float32', 'fp32'):
        return torch.float32
    if device == 'cuda':
        return torch.float16
    return torch.float32


def _is_seq2seq(config: Any) -> bool:
    if getattr(config, 'is_encoder_decoder', False):
        return True
    model_type = str(getattr(config, 'model_type', '') or '').lower()
    return model_type in {
        't5',
        'bart',
        'marian',
        'mbart',
        'pegasus',
        'nllb',
        'm2m_100',
        'switch_transformers',
    }


def _is_vision_model(config: Any) -> bool:
    model_type = str(getattr(config, 'model_type', '') or '').lower()
    architectures = [str(x).lower() for x in (getattr(config, 'architectures', None) or [])]
    hay = ' '.join([model_type, *architectures])
    return any(token in hay for token in ('glm_ocr', 'glmocr', 'image-text-to-text', 'vision'))


def _needs_trust_remote_code(config: Any) -> bool:
    model_type = str(getattr(config, 'model_type', '') or '').lower()
    return model_type in {'glm_ocr', 'glmocr'} or _is_vision_model(config)


def _load_model(
    model_dir: str,
    *,
    device: str = 'auto',
    torch_dtype: str = 'auto',
    trust_remote_code: bool = False,
    max_new_tokens: int = 512,
) -> tuple[Any, Any, str, str, str]:
    from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForSeq2SeqLM, AutoTokenizer

    path = Path(model_dir).expanduser().resolve()
    if not path.is_dir() or not (path / 'config.json').is_file():
        raise FileNotFoundError(f'model directory with config.json not found: {model_dir}')

    eff_device = _resolve_device(device)
    dtype = _resolve_dtype(torch_dtype, eff_device)
    config = AutoConfig.from_pretrained(str(path), trust_remote_code=trust_remote_code)
    if _needs_trust_remote_code(config):
        trust_remote_code = True
        config = AutoConfig.from_pretrained(str(path), trust_remote_code=True)

    load_kwargs: dict[str, Any] = {
        'config': config,
        'torch_dtype': dtype,
        'trust_remote_code': trust_remote_code,
        'low_cpu_mem_usage': True,
    }

    if _is_vision_model(config):
        from transformers import AutoModelForImageTextToText, AutoProcessor

        processor = AutoProcessor.from_pretrained(str(path), trust_remote_code=trust_remote_code)
        model = AutoModelForImageTextToText.from_pretrained(str(path), **load_kwargs)
        model.to(eff_device)
        model.eval()
        return model, processor, eff_device, str(dtype).replace('torch.', ''), 'vision'

    arch = 'seq2seq' if _is_seq2seq(config) else 'causal'
    tokenizer = AutoTokenizer.from_pretrained(str(path), trust_remote_code=trust_remote_code)
    if tokenizer.pad_token is None and tokenizer.eos_token is not None:
        tokenizer.pad_token = tokenizer.eos_token

    if arch == 'seq2seq':
        model = AutoModelForSeq2SeqLM.from_pretrained(str(path), **load_kwargs)
    else:
        model = AutoModelForCausalLM.from_pretrained(str(path), **load_kwargs)
    model.to(eff_device)
    model.eval()
    return model, tokenizer, eff_device, str(dtype).replace('torch.', ''), arch


def _messages_to_prompt(messages: list[dict[str, Any]], tokenizer: Any) -> str:
    if hasattr(tokenizer, 'apply_chat_template'):
        try:
            return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        except Exception:
            pass
    parts: list[str] = []
    for row in messages:
        role = str(row.get('role') or 'user').strip().lower()
        content = str(row.get('content') or '').strip()
        if not content:
            continue
        if role == 'system':
            parts.append(f'System: {content}')
        elif role == 'assistant':
            parts.append(f'Assistant: {content}')
        else:
            parts.append(f'User: {content}')
    parts.append('Assistant:')
    return '\n'.join(parts)


def _generate_chat(payload: dict[str, Any]) -> dict[str, Any]:
    import torch

    with _STATE_LOCK:
        model = _MODEL
        tokenizer = _TOKENIZER
        device = _DEVICE
        arch = _ARCH
        default_max = _MAX_NEW_TOKENS

    if model is None or tokenizer is None:
        return {'error': {'message': 'no model loaded', 'type': 'server_error'}}

    messages = payload.get('messages')
    if not isinstance(messages, list) or not messages:
        prompt = str(payload.get('prompt') or '').strip()
        if not prompt:
            return {'error': {'message': 'messages or prompt required', 'type': 'invalid_request_error'}}
        messages = [{'role': 'user', 'content': prompt}]

    max_tokens = int(payload.get('max_tokens') or payload.get('max_new_tokens') or default_max or 512)
    max_tokens = max(1, min(4096, max_tokens))
    temperature = float(payload.get('temperature') if payload.get('temperature') is not None else 0.7)
    top_p = float(payload.get('top_p') if payload.get('top_p') is not None else 0.9)

    if arch == 'seq2seq':
        user_text = ''
        for row in reversed(messages):
            if str(row.get('role') or '').lower() == 'user':
                user_text = str(row.get('content') or '').strip()
                break
        if not user_text:
            user_text = str(messages[-1].get('content') or '').strip()
        inputs = tokenizer(user_text, return_tensors='pt', truncation=True, max_length=2048)
    else:
        prompt = _messages_to_prompt(messages, tokenizer)
        inputs = tokenizer(prompt, return_tensors='pt', truncation=True, max_length=4096)

    inputs = {key: value.to(device) for key, value in inputs.items()}
    gen_kwargs: dict[str, Any] = {
        'max_new_tokens': max_tokens,
        'do_sample': temperature > 0,
        'pad_token_id': tokenizer.pad_token_id or tokenizer.eos_token_id,
    }
    if gen_kwargs['do_sample']:
        gen_kwargs['temperature'] = max(0.05, temperature)
        gen_kwargs['top_p'] = top_p

    started = time.time()
    with torch.no_grad():
        output_ids = model.generate(**inputs, **gen_kwargs)
    elapsed = max(time.time() - started, 1e-6)

    if arch == 'seq2seq':
        text = tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()
        completion_tokens = int(output_ids.shape[-1])
        prompt_tokens = int(inputs['input_ids'].shape[-1])
    else:
        generated = output_ids[0][inputs['input_ids'].shape[-1]:]
        text = tokenizer.decode(generated, skip_special_tokens=True).strip()
        completion_tokens = int(generated.shape[-1])
        prompt_tokens = int(inputs['input_ids'].shape[-1])

    return {
        'id': f'chatcmpl-{int(time.time())}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': str(payload.get('model') or _MODEL_DIR or 'transformers'),
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': text},
            'finish_reason': 'stop',
        }],
        'usage': {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': prompt_tokens + completion_tokens,
        },
        'dflash_timing': {'generation_seconds': round(elapsed, 3)},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = 'DFlashTransformers/1.0'

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        _log(format % args)

    def _read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(length) if length > 0 else b''

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == '/health':
            with _STATE_LOCK:
                payload = {
                    'ok': True,
                    'model_loaded': _MODEL is not None,
                    'model': _MODEL_DIR,
                    'device': _DEVICE,
                    'torch_dtype': _TORCH_DTYPE,
                    'arch': _ARCH,
                    'load_error': _LOAD_ERROR,
                }
            self._send_json(200, payload)
            return
        self._send_json(404, {'success': False, 'error': 'not found'})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == '/load':
            self._handle_load()
        elif path == '/unload':
            self._handle_unload()
        elif path == '/v1/chat/completions':
            self._handle_chat()
        else:
            self._send_json(404, {'success': False, 'error': 'not found'})

    def _handle_load(self) -> None:
        global _MODEL, _TOKENIZER, _MODEL_DIR, _DEVICE, _TORCH_DTYPE, _ARCH, _MAX_NEW_TOKENS, _TRUST_REMOTE_CODE, _LOAD_ERROR
        try:
            body = json.loads(self._read_body() or b'{}')
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {'success': False, 'error': 'invalid JSON body'})
            return
        model_dir = str(body.get('model_dir') or body.get('path') or '').strip()
        if not model_dir:
            self._send_json(400, {'success': False, 'error': 'model_dir is required'})
            return
        with _STATE_LOCK:
            _MODEL = None
            _TOKENIZER = None
            _LOAD_ERROR = ''
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        _log(f'loading model dir={model_dir}')
        try:
            model, tokenizer, eff_device, eff_dtype, arch = _load_model(
                model_dir,
                device=str(body.get('device') or 'auto'),
                torch_dtype=str(body.get('torch_dtype') or body.get('dtype') or 'auto'),
                trust_remote_code=bool(body.get('trust_remote_code')),
                max_new_tokens=int(body.get('max_new_tokens') or 512),
            )
        except Exception as exc:  # noqa: BLE001
            _LOAD_ERROR = str(exc)
            _log(f'load failed: {exc}')
            self._send_json(500, {'success': False, 'error': f'load failed: {exc}'})
            return
        with _STATE_LOCK:
            _MODEL = model
            _TOKENIZER = tokenizer
            _MODEL_DIR = model_dir
            _DEVICE = eff_device
            _TORCH_DTYPE = eff_dtype
            _ARCH = arch
            _MAX_NEW_TOKENS = int(body.get('max_new_tokens') or 512)
            _TRUST_REMOTE_CODE = bool(body.get('trust_remote_code'))
        self._send_json(200, {
            'success': True,
            'model': model_dir,
            'device': eff_device,
            'torch_dtype': eff_dtype,
            'arch': arch,
        })

    def _handle_unload(self) -> None:
        global _MODEL, _TOKENIZER, _MODEL_DIR, _DEVICE, _TORCH_DTYPE, _ARCH
        with _STATE_LOCK:
            _MODEL = None
            _TOKENIZER = None
            _MODEL_DIR = ''
            _DEVICE = ''
            _TORCH_DTYPE = ''
            _ARCH = 'causal'
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        self._send_json(200, {'success': True, 'unloaded': True})

    def _handle_chat(self) -> None:
        try:
            body = json.loads(self._read_body() or b'{}')
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {'error': {'message': 'invalid JSON', 'type': 'invalid_request_error'}})
            return
        if body.get('stream'):
            self._send_json(400, {'error': {'message': 'streaming not supported yet', 'type': 'invalid_request_error'}})
            return
        result = _generate_chat(body)
        if 'error' in result:
            self._send_json(409, result)
            return
        self._send_json(200, result)


def main() -> int:
    parser = argparse.ArgumentParser(description='DFlash Console Transformers worker')
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
            globals()['_MODEL'] = None
            globals()['_TOKENIZER'] = None
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
