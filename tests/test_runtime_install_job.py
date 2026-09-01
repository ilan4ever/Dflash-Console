from core.runtime_install_job import parse_install_line


def test_parse_progress_marker():
    pct, message = parse_install_line('DFLASH_PROGRESS 40 Installing vLLM inside WSL distro: Ubuntu')
    assert pct == 40.0
    assert 'WSL' in message


def test_parse_pip_megabytes():
    pct, message = parse_install_line('Downloading vllm-0.8.0.whl (12.4/50.2 MB)')
    assert pct is not None
    assert 30.0 <= pct <= 80.0
    assert '12.4/50.2' in message


def test_parse_successfully_installed():
    pct, message = parse_install_line('Successfully installed vllm-0.8.0')
    assert pct == 94.0
    assert 'Finishing' in message


def test_parse_successfully_installed_dependency_not_final():
    pct, message = parse_install_line('Successfully installed setuptools-84.0.0')
    assert pct == 88.0
    assert 'Installing Python packages' in message


def test_parse_existing_installation():
    pct, message = parse_install_line('Found existing installation: setuptools 84.0.0')
    assert pct == 87.0
    assert 'dependencies' in message.lower()


def test_parse_empty_line():
    assert parse_install_line('   ') == (None, '')
