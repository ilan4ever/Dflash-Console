"""uvicorn custom loop factory: use the Selector loop on Windows.

The default Proactor loop can lose its listening socket (WinError 64 / 'Accept failed on a
socket' on Python 3.13/3.14) and the API then stays dead. No asyncio subprocesses are used.
"""
from __future__ import annotations

import asyncio


def loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()