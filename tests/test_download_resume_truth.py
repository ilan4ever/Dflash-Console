"""Download truth: diffusers shard discovery, resume persistence, install state."""

from __future__ import annotations

from core import huggingface as hf
from core.hf_local_match import repo_install_incomplete
from core.huggingface import _discover_incomplete_repo_jobs, _load_pending_downloads


def _diffusers_folder(root):
    folder = root / 'Wan-AI' / 'Wan2.2-TI2V-5B'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_discover_incomplete_diffusers_shards(tmp_path, monkeypatch):
    folder = _diffusers_folder(tmp_path)
    (folder / 'diffusion_pytorch_model-00001-of-00003.safetensors').write_bytes(b'x' * 4096)
    (folder / 'diffusion_pytorch_model-00002-of-00003.safetensors').write_bytes(b'x' * 4096)
    monkeypatch.setattr(
        'core.model_paths.disk_scan_roots',
        lambda cfg=None: [(tmp_path, 'dflash', '', 'Console')],
    )
    monkeypatch.setattr(hf, '_discover_roots_override', [tmp_path])
    monkeypatch.setattr(hf, '_repo_expected_bytes', lambda *_args, **_kwargs: 34_203_123_497)

    found = _discover_incomplete_repo_jobs({})

    assert len(found) == 1
    row = found[0]
    assert row['shard_present'] == 2
    assert row['shard_total'] == 3
    assert row['disk_bytes'] == 8192
    assert row['bytes_total'] == 34_203_123_497
    assert row['status'] == 'incomplete'
    assert row['resumable'] is True


def test_repo_install_incomplete_detects_missing_shards(tmp_path):
    folder = _diffusers_folder(tmp_path)
    shard = folder / 'diffusion_pytorch_model-00001-of-00003.safetensors'
    shard.write_bytes(b'x' * 16)
    assert repo_install_incomplete(shard) is True
    assert repo_install_incomplete(folder) is True

    for index in (2, 3):
        (folder / f'diffusion_pytorch_model-0000{index}-of-00003.safetensors').write_bytes(b'x' * 16)
    assert repo_install_incomplete(shard) is False


def test_repo_download_registers_pending_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(hf, '_is_under_allowed_model_root', lambda path, cfg: True)
    monkeypatch.setattr(hf, '_repo_download_worker', lambda *args: None)
    monkeypatch.setattr(hf, '_repo_expected_bytes', lambda *_args, **_kwargs: 1000)

    dest = _diffusers_folder(tmp_path)
    result = hf.start_repo_download('Wan-AI/Wan2.2-TI2V-5B', dest_path=str(dest), cfg={})

    assert result['success'] is True
    pending = _load_pending_downloads()
    assert [row['id'] for row in pending] == [result['job_id']]
    assert pending[0]['kind'] == 'repo'
    assert pending[0]['path'] == str(dest.resolve())


def test_resume_orphaned_repo_downloads_uses_hf_cache_partials(tmp_path, monkeypatch):
    folder = _diffusers_folder(tmp_path)
    (folder / 'diffusion_pytorch_model-00001-of-00003.safetensors').write_bytes(b'x' * 4096)
    cache = folder / '.cache' / 'huggingface' / 'download'
    cache.mkdir(parents=True)
    (cache / 'Wan2.2_VAE.pth.incomplete').write_bytes(b'x' * 16)

    monkeypatch.setattr(hf, '_discover_roots_override', [tmp_path])
    monkeypatch.setattr(hf, '_is_under_allowed_model_root', lambda path, cfg: True)
    monkeypatch.setattr(hf, '_repo_expected_bytes', lambda *_args, **_kwargs: 100)
    started: list[tuple[str, dict]] = []

    def fake_start(repo, **kwargs):
        started.append((repo, kwargs))
        return {'success': True, 'job_id': 'job-1'}

    monkeypatch.setattr(hf, 'start_repo_download', fake_start)

    resumed = hf._resume_orphaned_repo_downloads(cfg={})

    assert resumed == ['job-1']
    assert started[0][0].lower() == 'wan-ai/wan2.2-ti2v-5b'
    assert started[0][1].get('allow_incomplete_resume') is True


def test_full_shard_folder_is_not_auto_resumed(tmp_path, monkeypatch):
    folder = _diffusers_folder(tmp_path)
    for index in (1, 2, 3):
        (folder / f'diffusion_pytorch_model-0000{index}-of-00003.safetensors').write_bytes(b'x' * 16)
    cache = folder / '.cache' / 'huggingface' / 'download'
    cache.mkdir(parents=True)
    (cache / 'leftover.pth.incomplete').write_bytes(b'x' * 16)

    monkeypatch.setattr(hf, '_discover_roots_override', [tmp_path])
    monkeypatch.setattr(hf, '_repo_expected_bytes', lambda *_args, **_kwargs: 100)
    started: list[str] = []
    monkeypatch.setattr(
        hf,
        'start_repo_download',
        lambda repo, **kwargs: (started.append(repo) or {'success': True}),
    )

    assert hf._resume_orphaned_repo_downloads(cfg={}) == []
    assert started == []
