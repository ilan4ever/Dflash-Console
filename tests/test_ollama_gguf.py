
def test_is_gguf_file_detects_magic(tmp_path):
    from core.local_models import _is_gguf_file

    gguf = tmp_path / 'model.bin'
    gguf.write_bytes(b'GGUF' + b'\x00' * 8)
    other = tmp_path / 'config.json'
    other.write_bytes(b'{"model": true}')

    assert _is_gguf_file(gguf) is True
    assert _is_gguf_file(other) is False
