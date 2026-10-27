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


def infer_context_length_from_metadata(meta: dict[str, object]) -> int | None:
    """Best-effort native context length from GGUF header metadata."""
    if not meta:
        return None
    best = 0
    for key, value in meta.items():
        if not str(key).endswith('.context_length'):
            continue
        try:
            length = int(value)
        except (TypeError, ValueError):
            continue
        if length >= 2048:
            best = max(best, length)
    if best:
        return best

    hay = ' '.join(
        str(meta.get(key) or '')
        for key in (
            'general.basename',
            'general.name',
            'general.base_model.0.name',
            'general.base_model.0.repo_url',
        )
    ).lower()
    if 'qwen3.8' in hay or 'qwen3-8' in hay or 'qwen3_8' in hay:
        return 262144
    if 'qwen3' in hay:
        return 131072
    if 'gemma 4' in hay or 'gemma-4' in hay or 'gemma4' in hay:
        return 131072
    if 'gemma 3' in hay or 'gemma-3' in hay or 'gemma3' in hay:
        return 8192
    if 'llama 3' in hay or 'llama-3' in hay or 'llama3' in hay:
        return 131072
    return None


# llama.cpp llama_ftype. Full-precision values are not trusted on their own:
# some mixed quants still write MOSTLY_BF16 in the header.
_GGUF_FILE_TYPE_LABELS = {
    0: 'F32',
    1: 'F16',
    2: 'Q4_0',
    3: 'Q4_1',
    7: 'Q8_0',
    8: 'Q5_0',
    9: 'Q5_1',
    10: 'Q2_K',
    11: 'Q3_K_S',
    12: 'Q3_K_M',
    13: 'Q3_K_L',
    14: 'Q4_K_S',
    15: 'Q4_K_M',
    16: 'Q5_K_S',
    17: 'Q5_K_M',
    18: 'Q6_K',
    19: 'IQ2_XXS',
    20: 'IQ2_XS',
    21: 'Q2_K_S',
    22: 'IQ3_XS',
    23: 'IQ3_XXS',
    24: 'IQ1_S',
    25: 'IQ4_NL',
    26: 'IQ3_S',
    27: 'IQ3_M',
    28: 'IQ2_S',
    29: 'IQ2_M',
    30: 'IQ4_XS',
    31: 'IQ1_M',
    32: 'BF16',
}
_GGUF_FULL_PRECISION_TYPES = {0, 1, 32}
_GGML_TYPE_LABELS = {
    0: 'F32',
    1: 'F16',
    2: 'Q4_0',
    3: 'Q4_1',
    6: 'Q5_0',
    7: 'Q5_1',
    8: 'Q8_0',
    10: 'Q2_K',
    11: 'Q3_K',
    12: 'Q4_K',
    13: 'Q5_K',
    14: 'Q6_K',
    30: 'BF16',
}
_GGML_STORAGE_TYPES = {'F32', 'F16', 'BF16'}
_QUANT_LABEL_CACHE: dict[tuple[str, int, int], str] = {}


def _skip_gguf_value(handle, value_type: int) -> bool:
    if value_type == 8:
        raw = handle.read(8)
        if len(raw) < 8:
            return False
        handle.seek(struct.unpack('<Q', raw)[0], 1)
        return True
    size = _GGUF_VALUE_SIZES.get(value_type)
    if size:
        return len(handle.read(size)) == size
    if value_type != 9:
        return False
    element_raw = handle.read(4)
    length_raw = handle.read(8)
    if len(element_raw) < 4 or len(length_raw) < 8:
        return False
    element_type = struct.unpack('<I', element_raw)[0]
    length = struct.unpack('<Q', length_raw)[0]
    if element_type == 8:
        for _ in range(int(length)):
            item_raw = handle.read(8)
            if len(item_raw) < 8:
                return False
            handle.seek(struct.unpack('<Q', item_raw)[0], 1)
        return True
    item_size = _GGUF_VALUE_SIZES.get(element_type)
    if not item_size or length > (1 << 31):
        return False
    handle.seek(int(length * item_size), 1)
    return True


def _tensor_quant_label(handle, tensor_count: int) -> str:
    counts: dict[str, int] = {}
    for _ in range(int(tensor_count)):
        name_len_raw = handle.read(8)
        if len(name_len_raw) < 8:
            break
        name_len = struct.unpack('<Q', name_len_raw)[0]
        if name_len > 1024 or len(handle.read(int(name_len))) < name_len:
            break
        dims_raw = handle.read(4)
        if len(dims_raw) < 4:
            break
        n_dims = struct.unpack('<I', dims_raw)[0]
        if n_dims > 8 or len(handle.read(8 * int(n_dims))) < 8 * int(n_dims):
            break
        type_raw = handle.read(4)
        offset_raw = handle.read(8)
        if len(type_raw) < 4 or len(offset_raw) < 8:
            break
        ggml_type = struct.unpack('<I', type_raw)[0]
        label = _GGML_TYPE_LABELS.get(ggml_type, '')
        if label:
            counts[label] = counts.get(label, 0) + 1
    quantized = {
        label: count
        for label, count in counts.items()
        if label not in _GGML_STORAGE_TYPES and count > 0
    }
    if not quantized:
        return ''
    total = sum(quantized.values())
    kept = [
        (count, label)
        for label, count in quantized.items()
        if count >= max(5, int(total * 0.05))
    ]
    if not kept:
        kept = [(count, label) for label, count in quantized.items()]
    kept.sort(reverse=True)
    return '+'.join(label for _count, label in kept[:3])


def read_gguf_quant_label(path: str | Path) -> str:
    """Quantization label from the GGUF header, including mixed files whose name omits it."""
    target = Path(path).expanduser()
    try:
        stat = target.stat()
    except OSError:
        return ''
    cache_key = (str(target), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _QUANT_LABEL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    label = ''
    try:
        with target.open('rb') as handle:
            if handle.read(4) != b'GGUF':
                _QUANT_LABEL_CACHE[cache_key] = ''
                return ''
            if len(handle.read(4)) < 4:
                _QUANT_LABEL_CACHE[cache_key] = ''
                return ''
            tensor_raw = handle.read(8)
            kv_raw = handle.read(8)
            if len(tensor_raw) < 8 or len(kv_raw) < 8:
                _QUANT_LABEL_CACHE[cache_key] = ''
                return ''
            tensor_count = struct.unpack('<Q', tensor_raw)[0]
            kv_count = struct.unpack('<Q', kv_raw)[0]
            file_type = None
            kv_done = True
            for _ in range(int(kv_count)):
                key_len_raw = handle.read(8)
                if len(key_len_raw) < 8:
                    kv_done = False
                    break
                key_len = struct.unpack('<Q', key_len_raw)[0]
                if key_len > 1024:
                    kv_done = False
                    break
                key = handle.read(int(key_len)).decode('utf-8', errors='replace')
                type_raw = handle.read(4)
                if len(type_raw) < 4:
                    kv_done = False
                    break
                value_type = struct.unpack('<I', type_raw)[0]
                if key == 'general.file_type' and value_type == 4:
                    raw = handle.read(4)
                    if len(raw) < 4:
                        kv_done = False
                        break
                    file_type = struct.unpack('<I', raw)[0]
                    header_label = _GGUF_FILE_TYPE_LABELS.get(int(file_type), '')
                    if int(file_type) not in _GGUF_FULL_PRECISION_TYPES and header_label:
                        label = header_label
                        kv_done = False
                        break
                    continue
                if not _skip_gguf_value(handle, value_type):
                    kv_done = False
                    break
            if not label:
                header_label = _GGUF_FILE_TYPE_LABELS.get(int(file_type), '') if file_type is not None else ''
                if kv_done:
                    label = _tensor_quant_label(handle, tensor_count) or header_label
                else:
                    label = header_label
    except (OSError, OverflowError, struct.error):
        label = ''
    _QUANT_LABEL_CACHE[cache_key] = label
    return label


def read_gguf_context_length(path: str | Path) -> int | None:
    """Read the model's reported maximum context length from a GGUF file."""
    target = Path(path).expanduser()
    if not target.is_file():
        return None
    meta = read_gguf_metadata(target)
    return infer_context_length_from_metadata(meta)
