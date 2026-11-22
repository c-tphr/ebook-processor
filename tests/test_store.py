"""The files the app keeps: whole or not there, never half."""

from __future__ import annotations

import json
import sys
import threading

import pytest

from ebook_processor import store


def test_a_file_is_written_whole_and_leaves_nothing_behind(tmp_path):
    path = tmp_path / "deep" / "state.json"
    store.write_json(path, {"a": "é", "n": [1, 2]}, sync=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": "é", "n": [1, 2]} and path.read_bytes().endswith(b"\n")
    store.write_text(tmp_path / "cards.tsv", "a\tb\r\nc\td\r\n", newline="")
    assert (tmp_path / "cards.tsv").read_bytes() == b"a\tb\r\nc\td\r\n"   # as given: the text has its own line ends
    store.write_text(tmp_path / "report.md", "one\ntwo\n")
    assert (tmp_path / "report.md").read_bytes() == b"one\ntwo\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cards.tsv", "deep", "report.md"]   # no temporary file is left


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not replace a file that is open, and this reader never lets go")
def test_a_reader_never_sees_a_part_of_a_file_while_it_is_written(tmp_path):
    path = tmp_path / "book.epub"
    old, new = b"o" * 2_000_000, b"n" * 3_000_000
    store.write_bytes(path, old)
    seen: set[bytes] = set()
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            data = path.read_bytes()
            seen.add(data if data in (old, new) else b"a part of a file")
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        for _ in range(20):
            store.write_bytes(path, new)
            store.write_bytes(path, old)
    finally:  # whatever happens, the reader ends: a thread left running keeps the whole test run from ending
        stop.set()
        reader.join(10)
    assert seen <= {old, new}


def test_a_replace_that_is_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    """On Windows a file cannot take the place of one that another program has open. That passes in a moment."""
    refused = [PermissionError("in use"), PermissionError("in use")]
    real = store.os.replace

    def replace(tmp, path):
        if refused:
            raise refused.pop()
        real(tmp, path)
    monkeypatch.setattr(store.os, "replace", replace)
    monkeypatch.setattr(store.time, "sleep", lambda seconds: None)
    store.write_json(tmp_path / "a.json", {"n": 1})
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {"n": 1} and not refused
    refused.extend(PermissionError("in use") for _ in range(9))    # it does not pass: the error is the caller's to show
    with pytest.raises(PermissionError):
        store.write_json(tmp_path / "a.json", {"n": 2})
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]    # and no temporary file is left behind


def test_a_stored_file_is_loaded_or_an_empty_store_is_given(tmp_path):
    def empty() -> dict:
        return {"version": 2, "items": []}
    path = tmp_path / "items.json"
    assert store.load_json(path, 2, empty) == empty()
    for text in ("not json", "[]", '{"version": 1, "items": []}', '{"version": 2, "items": 7}'):
        path.write_text(text, encoding="utf-8")
        assert store.load_json(path, 2, empty, lambda d: isinstance(d.get("items"), list)) == empty(), text
    path.write_text('{"version": 2, "items": ["x"]}', encoding="utf-8")
    assert store.load_json(path, 2, empty, lambda d: isinstance(d.get("items"), list))["items"] == ["x"]
    assert store.now().endswith("Z") and len(store.now()) == 20
