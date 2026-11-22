"""Small tools for the files the app keeps: the time, writes that never leave half a file, and the loading of a
stored JSON file.

Every file the app writes for itself goes through here. A write goes to a temporary file beside the target and is
then put in its place in one step, so a reader of the file, or a stop in the middle, sees the old file or the new
one, never a part of one.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


def now() -> str:
    """The time now, in UTC, as the app stores times."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def put_in_place(tmp: str, path: Path) -> None:
    """Put a finished temporary file in its place. On Windows this fails for a moment while another program (a
    virus scanner, an indexer, a reader of the file) has the target open, so it is tried a few times."""
    for pause in (0.02, 0.02, 0.05, 0.1, None):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if pause is None:
                raise
            time.sleep(pause)


def write_bytes(path: Path, data: bytes, sync: bool = False) -> None:
    """Write a file through a temporary file. With ``sync`` the bytes are on the disk before the file takes its
    place, so that a power cut cannot leave an empty file under the name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if sync:
                f.flush()
                os.fsync(f.fileno())
        put_in_place(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_text(path: Path, text: str, newline: str = "\n") -> None:
    """Write a text file through a temporary file. ``newline`` is what a line feed in ``text`` becomes ("" leaves
    the text as it is)."""
    write_bytes(path, (text if newline in ("", "\n") else text.replace("\n", newline)).encode("utf-8"))


def write_json(path: Path, data: Any, sync: bool = False) -> None:
    """Write a JSON file through a temporary file, so that a stop in the middle never leaves half a file."""
    write_bytes(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), sync)


def load_json(path: Path, version: int, empty: Callable[[], dict[str, Any]],
              valid: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    """A stored JSON object of this version, or ``empty()`` when the file is not there, is not JSON, is of
    another version or does not pass ``valid``."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("version") == version and (valid is None or valid(data)):
            return data
    return empty()
