from __future__ import annotations

import os

from api.app import _parent_process_alive


def test_parent_process_alive_zero_pid():
    assert _parent_process_alive(0) is True
    assert _parent_process_alive(-1) is True


def test_parent_process_alive_current_process():
    assert _parent_process_alive(os.getpid()) is True


def test_parent_process_alive_dead_pid():
    # PID 4194304 is above the Windows PID range and never valid there; on
    # POSIX, 99999999 is above pid_max on default kernels.
    assert _parent_process_alive(99999999) is False
