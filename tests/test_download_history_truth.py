"""Last-downloads rows must not claim success for an unfinished repository."""

from __future__ import annotations

from core.huggingface import _drop_superseded_file_rows


def test_done_file_row_hidden_while_repo_job_runs():
    jobs = [
        {
            'id': 'repo-1',
            'status': 'downloading',
            'kind': 'repo',
            'path': r'C:\models\Wan-AI\Wan2.2-TI2V-5B',
        },
        {
            'id': 'file-1',
            'status': 'done',
            'kind': '',
            'path': r'C:\models\Wan-AI\Wan2.2-TI2V-5B\diffusion_pytorch_model-00001-of-00003.safetensors',
        },
        {
            'id': 'other',
            'status': 'done',
            'kind': '',
            'path': r'C:\models\other\model.gguf',
        },
    ]

    kept = [row['id'] for row in _drop_superseded_file_rows(jobs)]

    assert kept == ['repo-1', 'other']


def test_done_rows_kept_when_no_repo_job_is_active():
    jobs = [
        {'id': 'file-1', 'status': 'done', 'kind': '', 'path': r'C:\models\org\repo\model.gguf'},
    ]
    assert [row['id'] for row in _drop_superseded_file_rows(jobs)] == ['file-1']


def test_incomplete_repo_job_also_hides_its_file_rows():
    jobs = [
        {
            'id': 'incomplete::org--repo',
            'status': 'incomplete',
            'kind': 'repo',
            'path': 'C:/models/org/repo',
        },
        {'id': 'file-1', 'status': 'done', 'kind': '', 'path': 'C:/models/org/repo/shard-1.gguf'},
    ]
    assert [row['id'] for row in _drop_superseded_file_rows(jobs)] == ['incomplete::org--repo']
