"""A stuck NVIDIA query must not freeze the caller."""

from __future__ import annotations

import subprocess

from core import bounded_proc


def test_run_bounded_gives_up_when_the_child_will_not_exit(monkeypatch):
    waits: list[float | None] = []

    class Stuck:
        def communicate(self, timeout=None):
            waits.append(timeout)
            raise subprocess.TimeoutExpired(cmd='nvidia-smi', timeout=timeout or 0)

        def kill(self):
            return None

    monkeypatch.setattr(bounded_proc.subprocess, 'Popen', lambda *args, **kwargs: Stuck())
    code, text = bounded_proc.run_bounded(['nvidia-smi'], timeout=1.5)
    assert code is None
    assert text == ''
    assert waits == [1.5, 0.4]


def test_run_nvidia_smi_keeps_each_query_separate(monkeypatch):
    bounded_proc._NVIDIA_LAST_OK.clear()
    bounded_proc._NVIDIA_COOLDOWN_UNTIL = 0.0
    answers = {
        ('--query-gpu=index',): '0, RTX',
        ('--query-compute-apps=pid',): '1234, 100',
    }

    def fake_run(argv, *, timeout=3.0):
        return 0, answers[tuple(argv[1:])]

    monkeypatch.setattr(bounded_proc, 'run_bounded', fake_run)
    assert bounded_proc.run_nvidia_smi(['--query-gpu=index']) == '0, RTX'
    assert bounded_proc.run_nvidia_smi(['--query-compute-apps=pid']) == '1234, 100'
