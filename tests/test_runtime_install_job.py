import threading

from core.runtime_install_job import apply_job_progress, make_install_line_parser, parse_install_line

# What install-vllm-runtime.sh printed on Linux while pip upgraded itself (2026-11-24, OMAN).
_PIP_SELF_UPGRADE = [
    'DFLASH_PROGRESS 18 Upgrading pip',
    'Requirement already satisfied: pip in ./runtimes/vllm/venv/lib/python3.12/site-packages (24.0)',
    'Collecting pip',
    'Downloading pip-25.3-py3-none-any.whl (1.8 MB)',
    '1.8/1.8 MB 12.0 MB/s eta 0:00:00',
    'Installing collected packages: pip',
    'Found existing installation: pip 24.0',
    'Uninstalling pip-24.0:',
    'Successfully uninstalled pip-24.0',
    'Successfully installed pip-25.3',
]


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


def test_requirement_already_satisfied_is_not_progress():
    pct, message = parse_install_line('Requirement already satisfied: numpy in ./venv/lib/python3.12/site-packages (2.3.0)')
    assert pct is None
    assert 'dependencies' in message.lower()


def test_pip_self_upgrade_does_not_move_the_bar():
    parse = make_install_line_parser()
    values = [parse(line)[0] for line in _PIP_SELF_UPGRADE]
    assert values[0] == 18.0
    assert values[1:] == [None] * (len(_PIP_SELF_UPGRADE) - 1)
    # the message still follows pip
    assert parse('Installing collected packages: pip')[1] == 'Installing packages'


def test_job_progress_stays_in_setup_range_during_pip_self_upgrade():
    lock, job = threading.Lock(), {}
    parse = make_install_line_parser()
    for line in _PIP_SELF_UPGRADE:
        pct, message = parse(line)
        apply_job_progress(lock, job, progress=pct, message=message, log_line=line)
    assert job['progress'] == 18.0  # it used to reach 87-88 here


def test_main_stage_lines_still_move_the_bar():
    parse = make_install_line_parser()
    for line in _PIP_SELF_UPGRADE:
        parse(line)
    assert parse('DFLASH_PROGRESS 30 Downloading vLLM (several GB, this takes a while)')[0] == 30.0
    assert 30.0 < parse('Downloading torch-2.14.0-cp312-manylinux_2_28_x86_64.whl (450.0/900.0 MB)')[0] <= 80.0
    assert parse('Installing collected packages: torch, vllm')[0] == 86.0
    assert parse('Successfully installed torch-2.14.0 vllm-0.34.0')[0] == 94.0
    assert parse('DFLASH_PROGRESS 100 vLLM runtime installed')[0] == 99.0


def test_parser_without_markers_keeps_the_heuristics():
    parse = make_install_line_parser()
    assert parse('Found existing installation: setuptools 84.0.0')[0] == 87.0
    assert parse('Successfully installed vllm-0.8.0')[0] == 94.0
