"""Stand-in programs for the tests, runnable on every system.

A test that needs "claude" or "codex" writes a small Python program and starts it by its path. On Linux and macOS
that is a file with an interpreter line. On Windows it is a ``.py`` file beside a ``.cmd`` script that starts
Python with it, which is how npm installs the real tools there.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path


def make_program(folder: Path, name: str, source: str) -> str:
    """Write the Python ``source`` as a program called ``name`` in ``folder`` and return the path to start it by."""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (folder / f"{name}.py").write_text(source, encoding="utf-8")
        script = folder / f"{name}.cmd"
        script.write_text(f'@"{sys.executable}" "%~dp0{name}.py" %*\r\n', encoding="utf-8")
    else:
        script = folder / name
        script.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        script.chmod(0o755)
    return str(script)


def alive(pid: int) -> bool:
    """Whether a process is still running. A process that has ended but was not yet collected does not count."""
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # Linux: ended, waiting to be collected
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except OSError:
        return True


def gone(pid: int, within: float = 5.0) -> bool:
    """Whether a process has ended, giving the system a moment to finish with it."""
    deadline = time.monotonic() + within
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True
