from core.config import normalize_remote_node
from core.remote_gpu import match_remote_model, parse_remote_gpu_id, shared_gpu_label


def test_shared_gpu_label_puts_machine_in_brackets():
    assert shared_gpu_label('TITAN', 'production') == 'TITAN (production)'
    assert shared_gpu_label('TITAN (production)', 'production') == 'TITAN (production)'


def test_parse_remote_gpu_id():
    assert parse_remote_gpu_id('remote:production:0') == ('production', 0)
    assert parse_remote_gpu_id('REMOTE:Production:1') == ('production', 1)
    assert parse_remote_gpu_id('auto') is None
    assert parse_remote_gpu_id('0') is None


def test_normalize_remote_node_keeps_ssh_gpu_share():
    node = normalize_remote_node({
        'id': 'production',
        'label': 'Production',
        'base_url': 'http://127.0.0.1:8901',
        'ssh_host': 'production',
        'share_gpu': True,
    })
    assert node['ssh_host'] == 'production'
    assert node['ssh_local_port'] == 8901
    assert node['ssh_remote_port'] == 8900
    assert node['share_gpu'] is True


def test_match_remote_model_by_filename():
    models = [
        {'filename': 'other.gguf', 'path': r'C:\models\other.gguf'},
        {'filename': 'gemma.gguf', 'path': r'D:\models\gemma.gguf', 'hf_repo': 'google/gemma'},
    ]
    found = match_remote_model(
        models,
        filename='gemma.gguf',
        path=r'C:\dev\Dflash-Console\models\gemma.gguf',
    )
    assert found['path'].endswith('gemma.gguf')


def test_match_remote_model_by_repo_when_name_differs_only_in_folder():
    models = [
        {'filename': 'model.gguf', 'path': r'D:\hf\org\name\model.gguf', 'repo_id': 'org/name'},
    ]
    found = match_remote_model(
        models,
        filename='model.gguf',
        repo_id='org/name',
        path=r'C:\local\model.gguf',
    )
    assert found is not None
