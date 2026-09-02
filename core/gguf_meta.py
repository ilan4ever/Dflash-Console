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
    6: 4,  # FLOAT32
    7: 1,  # BOOL
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


def read_gguf_metadata(
    path: str | Path,
    *,
    keys: set[str] | None = None,
) -> dict[str, object]:
    """Read scalar GGUF metadata without loading tensor data.

    This intentionally remains a small dependency-free reader.  DFlash
    compatibility only needs the model identity and structural fields in the
    header; loading the model is left to llama.cpp.
    """
    target = Path(path).expanduser()
    scalar_formats = {
        0: ('<B', 1),
        1: ('<b', 1),
        2: ('<H', 2),
        3: ('<h', 2),
        4: ('<I', 4),
        5: ('<i', 4),
        6: ('<f', 4),
        7: ('<?', 1),
    }
    try:
        with target.open('rb') as handle:
            if handle.read(4) != b'GGUF':
                return {}
            version = handle.read(4)
            tensor_count = handle.read(8)
            kv_count_raw = handle.read(8)
            if len(version) < 4 or len(tensor_count) < 8 or len(kv_count_raw) < 8:
                return {}
            kv_count = struct.unpack('<Q', kv_count_raw)[0]
            found: dict[str, object] = {}
            for _ in range(int(kv_count)):
                key_len_raw = handle.read(8)
                if len(key_len_raw) < 8:
                    break
                key_len = struct.unpack('<Q', key_len_raw)[0]
                if key_len > 1024:
                    return {}
                key_bytes = handle.read(int(key_len))
                if len(key_bytes) < key_len:
                    break
                key = key_bytes.decode('utf-8', errors='replace')
                value_type_raw = handle.read(4)
                if len(value_type_raw) < 4:
                    break
                value_type = struct.unpack('<I', value_type_raw)[0]
                if value_type == 8:
                    value_len_raw = handle.read(8)
                    if len(value_len_raw) < 8:
                        break
                    value_len = struct.unpack('<Q', value_len_raw)[0]
                    if value_len > 16 * 1024 * 1024:
                        return {}
                    value_bytes = handle.read(int(value_len))
                    if len(value_bytes) < value_len:
                        break
                    if keys is None or key in keys:
                        found[key] = value_bytes.decode('utf-8', errors='replace')
                    continue
                scalar = scalar_formats.get(value_type)
                if scalar:
                    fmt, size = scalar
                    value_bytes = handle.read(size)
                    if len(value_bytes) < size:
                        break
                    if keys is None or key in keys:
                        found[key] = struct.unpack(fmt, value_bytes)[0]
                    continue
                if value_type == 9:  # array
                    element_type_raw = handle.read(4)
                    length_raw = handle.read(8)
                    if len(element_type_raw) < 4 or len(length_raw) < 8:
                        break
                    element_type = struct.unpack('<I', element_type_raw)[0]
                    length = struct.unpack('<Q', length_raw)[0]
                    if element_type == 8:
                        # String arrays are variable-length, so they cannot be
                        # skipped arithmetically.  Large tokenizer arrays occur
                        # after all model structure KVs; return what we have
                        # instead of walking hundreds of thousands of tokens.
                        if length > 4096:
                            return found
                        for _ in range(int(length)):
                            item_len_raw = handle.read(8)
                            if len(item_len_raw) < 8:
                                return found
                            item_len = struct.unpack('<Q', item_len_raw)[0]
                            if item_len > 16 * 1024 * 1024:
                                return {}
                            handle.seek(int(item_len), 1)
                    else:
                        item_size = _GGUF_VALUE_SIZES.get(element_type)
                        if item_size is None or length > (1 << 31):
                            return {}
                        handle.seek(int(length * item_size), 1)
                    continue
                return found
            return found
    except (OSError, OverflowError, struct.error):
        return {}
