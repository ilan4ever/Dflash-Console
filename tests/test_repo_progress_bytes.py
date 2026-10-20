"""Repo download progress must never exceed the real payload size."""

from __future__ import annotations

from core.huggingface import _directory_download_bytes


def test_orphaned_partial_blobs_are_counted_once(tmp_path):
    folder = tmp_path / 'org' / 'repo'
    cache = folder / '.cache' / 'huggingface' / 'download'
    cache.mkdir(parents=True)
    (folder / 'model-00001-of-00002.safetensors').write_bytes(b'\0' * 1000)
    # Interrupted attempts for the same file leave one partial blob each.
    (cache / 'abc=.etag1.uuid1.incomplete').write_bytes(b'\0' * 300)
    (cache / 'abc=.etag1.uuid2.incomplete').write_bytes(b'\0' * 700)
    (cache / 'abc=.etag1.uuid3.incomplete').write_bytes(b'\0' * 100)

    # 1000 finished + 700 (largest partial), not 1000 + 1100.
    assert _directory_download_bytes(folder) == 1700


def test_finished_files_are_summed_across_components(tmp_path):
    folder = tmp_path / 'org' / 'repo'
    folder.mkdir(parents=True)
    (folder / 'model-00001-of-00002.safetensors').write_bytes(b'\0' * 500)
    (folder / 'text_encoder.pth').write_bytes(b'\0' * 250)
    (folder / 'README.md').write_bytes(b'\0' * 9999)

    assert _directory_download_bytes(folder) == 750
