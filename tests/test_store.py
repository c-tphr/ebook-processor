"""The files the app keeps: whole or not there, never half."""

from __future__ import annotations

import json
import threading

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
    reader = threading.Thread(target=read)
    reader.start()
    for _ in range(20):
        store.write_bytes(path, new)
        store.write_bytes(path, old)
    stop.set()
    reader.join()
    assert seen <= {old, new}


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
