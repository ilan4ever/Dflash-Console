from __future__ import annotations

from core.huggingface import (
    _enrich_summaries_sizes,
    _entry_size_bytes,
    _preferred_gguf_size,
    _preferred_size_folders,
    _resolve_repo_tree,
    _row_needs_size_enrich,
    _siblings_have_file_sizes,
    _siblings_with_sizes,
    _summaries_from_models,
)


def test_entry_size_prefers_lfs_blob_over_pointer():
    assert _entry_size_bytes({'size': 134, 'lfs': {'size': 2_500_000_000}}) == 2_500_000_000
    assert _entry_size_bytes({'filename': 'a.gguf', 'size_bytes': 8_000_000}) == 8_000_000
    assert _entry_size_bytes({'rfilename': 'a.gguf'}) is None


def test_siblings_have_file_sizes_reads_filename_and_lfs():
    assert _siblings_have_file_sizes([{'filename': 'model.gguf', 'size_bytes': 4_000_000_000}]) is True
    assert _siblings_have_file_sizes([{'rfilename': 'model.gguf', 'lfs': {'size': 4_000_000_000}}]) is True
    assert _siblings_have_file_sizes([{'rfilename': 'model.gguf'}]) is False


def test_siblings_with_sizes_merges_filename_rows():
    merged = _siblings_with_sizes(
        [{'filename': 'Q4_K_M/model.gguf'}],
        [{'path': 'Q4_K_M/model.gguf', 'size': 5_000_000_000}],
    )
    assert merged[0]['rfilename'] == 'Q4_K_M/model.gguf'
    assert merged[0]['size'] == 5_000_000_000


def test_preferred_size_folders_ranks_quant_dirs():
    folders = _preferred_size_folders(
        [
            {'path': 'BF16', 'type': 'directory'},
            {'path': 'Q4_K_M', 'type': 'directory'},
            {'path': 'Q8_0', 'type': 'directory'},
        ],
        [{'rfilename': 'Q5_K_M/model.gguf'}],
    )
    assert folders[0] == 'Q4_K_M'
    assert 'Q5_K_M' in folders


def test_resolve_repo_tree_prefers_blobs_before_tree(monkeypatch):
    calls: list[str] = []

    def fake_blobs(repo):
        calls.append('blobs')
        return [{'rfilename': 'model.gguf', 'lfs': {'size': 4_000_000_000}}]

    def fake_tree(repo, *, recursive=False, path=''):
        calls.append('tree')
        return []

    monkeypatch.setattr('core.huggingface._fetch_repo_siblings_with_blobs', fake_blobs)
    monkeypatch.setattr('core.huggingface._fetch_repo_tree', fake_tree)

    tree = _resolve_repo_tree('org/repo', [{'rfilename': 'model.gguf'}])
    assert calls == ['blobs']
    assert any(row.get('path') == 'model.gguf' for row in tree)


def test_resolve_repo_tree_follows_quant_folder(monkeypatch):
    calls: list[tuple[str, str, bool]] = []

    def fake_tree(repo, *, recursive=False, path=''):
        calls.append((repo, path, recursive))
        if not path:
            return [
                {'path': 'README.md', 'size': 1200, 'type': 'file'},
                {'path': 'Q4_K_M', 'type': 'directory'},
                {'path': 'Q8_0', 'type': 'directory'},
            ]
        if path == 'Q4_K_M':
            return [{'path': 'Q4_K_M/model-Q4_K_M.gguf', 'size': 16_000_000_000, 'type': 'file'}]
        return []

    monkeypatch.setattr('core.huggingface._fetch_repo_tree', fake_tree)
    monkeypatch.setattr('core.huggingface._fetch_repo_siblings_with_blobs', lambda repo: [])

    tree = _resolve_repo_tree('unsloth/Qwen3.8-27B-GGUF', [{'rfilename': 'Q4_K_M/model-Q4_K_M.gguf'}])
    assert any(row.get('path') == 'Q4_K_M/model-Q4_K_M.gguf' for row in tree)
    assert ('unsloth/Qwen3.8-27B-GGUF', 'Q4_K_M', False) in calls
    assert not any(recursive for _repo, _path, recursive in calls if not _path)


def test_resolve_repo_tree_does_not_give_up_on_many_folders(monkeypatch):
    def fake_tree(repo, *, recursive=False, path=''):
        if not path:
            return [{'path': f'Q{index}', 'type': 'directory'} for index in range(60)]
        if path == 'Q4_K_M' or path.startswith('Q'):
            return [{'path': f'{path}/model.gguf', 'size': 3_000_000_000, 'type': 'file'}]
        return []

    monkeypatch.setattr('core.huggingface._fetch_repo_tree', fake_tree)
    monkeypatch.setattr('core.huggingface._fetch_repo_siblings_with_blobs', lambda repo: [])

    tree = _resolve_repo_tree('org/many-folders', [{'rfilename': 'model.gguf'}])
    assert _siblings_have_file_sizes(_siblings_with_sizes([{'rfilename': 'model.gguf'}], tree))


def test_enrich_summaries_sizes_merges_without_wiping_local_ready(monkeypatch):
    monkeypatch.setattr(
        'core.huggingface._resolve_repo_tree',
        lambda repo, siblings=None: [{'path': 'model-Q4_K_M.gguf', 'size': int(2.5 * 1024 ** 3)}],
    )
    rows = [{
        'id': 'org/qwen-gguf',
        'size_label': '—',
        'size_gb': None,
        'has_gguf': True,
        'tags': ['gguf'],
        'local_ready': True,
        'catalog_ready_to_load': True,
        'gguf_files': [{'filename': 'model-Q4_K_M.gguf'}],
    }]
    _enrich_summaries_sizes(rows)
    assert rows[0]['local_ready'] is True
    assert rows[0]['catalog_ready_to_load'] is True
    assert rows[0]['size_gb'] and rows[0]['size_gb'] > 2
    assert rows[0]['size_label'] != '—'
    assert _row_needs_size_enrich(rows[0]) is False


def test_row_needs_size_enrich_for_tiny_gguf_header_shard():
    assert _row_needs_size_enrich({'has_gguf': True, 'size_gb': 0.01, 'size_label': '0.01 GB'}) is True
    assert _row_needs_size_enrich({'has_gguf': True, 'size_gb': 2.33, 'size_label': '2.33 GB'}) is False


def test_preferred_gguf_size_sums_split_shards():
    siblings = [
        {'rfilename': 'UD-IQ1_S/model-00001-of-00003.gguf', 'size': 10_943_264},
        {'rfilename': 'UD-IQ1_S/model-00002-of-00003.gguf', 'size': 20_000_000_000},
        {'rfilename': 'UD-IQ1_S/model-00003-of-00003.gguf', 'size': 19_000_000_000},
    ]
    size_gb, label = _preferred_gguf_size(siblings)
    assert size_gb is not None
    assert size_gb > 30
    assert 'GB' in label


def test_summaries_from_models_enriches_every_missing_row(monkeypatch):
    fetched: list[str] = []

    def fake_tree(repo, siblings=None):
        fetched.append(repo)
        return [{'path': f'{repo.split("/")[-1]}.gguf', 'size': 1_200_000_000}]

    monkeypatch.setattr('core.huggingface._resolve_repo_tree', fake_tree)
    raw = [
        {'id': f'org/model-{index}', 'siblings': [{'rfilename': f'model-{index}.gguf'}], 'tags': ['gguf']}
        for index in range(12)
    ]
    rows = _summaries_from_models(raw, enrich_sizes=True)
    assert len(fetched) == 12
    assert all(row.get('size_gb') for row in rows)
