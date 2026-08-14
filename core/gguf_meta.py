"""Minimal GGUF metadata readers (no gguf-py dependency)."""

from __future__ import annotations

import struct
from pathlib import Path

_GGUF_VALUE_SIZES = {
    0: 1,  # UINT8
    1: 1,  # INT8
    2: 2,  # UINT16
    3: 2,  # INT16
    4: 4,  # UINT32
    5: 4,  # INT32
    6: 8,  # FLOAT32
    7: 8,  # BOOL
}


def read_gguf_string_fields(path: str | Path, *, keys: set[str] | None = None) -> dict[str, str]:
    """Read selected string KV fields from a GGUF file header."""
    target = Path(path).expanduser()
    try:
        with target.open('rb') as handle:
            if handle.read(4) != b'GGUF':
                return {}
            handle.read(4)  # version
            handle.read(8)  # tensor count
            raw_kv = handle.read(8)
            if len(raw_kv) < 8:
                return {}
            kv_count = struct.unpack('<Q', raw_kv)[0]
            found: dict[str, str] = {}
            for _ in range(int(kv_count)):
                key_len_raw = handle.read(8)
                if len(key_len_raw) < 8:
                    break
                key_len = struct.unpack('<Q', key_len_raw)[0]
                key_bytes = handle.read(int(key_len))
                if len(key_bytes) < key_len:
                    break
                key = key_bytes.decode('utf-8', errors='replace')
                vtype_raw = handle.read(4)
                if len(vtype_raw) < 4:
                    break
                value_type = struct.unpack('<I', vtype_raw)[0]
                if value_type == 8:  # string
                    vlen_raw = handle.read(8)
                    if len(vlen_raw) < 8:
                        break
                    value_len = struct.unpack('<Q', vlen_raw)[0]
                    value_bytes = handle.read(int(value_len))
                    if len(value_bytes) < value_len:
                        break
                    if keys is None or key in keys:
                        found[key] = value_bytes.decode('utf-8', errors='replace')
                elif value_type in _GGUF_VALUE_SIZES:
                    size = _GGUF_VALUE_SIZES[value_type]
                    if not handle.read(size):
                        break
                elif value_type == 9:  # array — stop early; we only need top-level strings
                    break
                else:
                    break
                if keys and keys.issubset(found.keys()):
                    break
            return found
    except OSError:
        return {}


def read_gguf_architecture(path: str | Path) -> str:
    fields = read_gguf_string_fields(path, keys={'general.architecture'})
    return str(fields.get('general.architecture') or '').strip().lower()
