"""A book's outline is kept in a small file, and its EPUB is read only when its text is wanted."""

from __future__ import annotations

import json
import time

import pytest

from ebook_processor import reading
from ebook_processor.web import server as web
from tests.scripted import Script
from tests.test_pipeline import FIXTURE

PREPARED = FIXTURE.with_name("fixture.prepared.epub")


@pytest.fixture
def opened(monkeypatch):
    """Counts the times a whole EPUB is read."""
    count: list[str] = []
    real = reading.open_reading_book

    def counted(path):
        count.append(path.name)
        return real(path)
    monkeypatch.setattr(reading, "open_reading_book", counted)
    return count


def processed(tmp_path) -> tuple[web.Jobs, str]:
    jobs = web.Jobs(tmp_path / "data", backend_factory=lambda settings: Script().backend())
    job = jobs.create("fixture.epub", FIXTURE.read_bytes(), web.from_options(density="short"))
    for _ in range(400):
        if jobs.get(job["id"])["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert jobs.get(job["id"])["status"] == "done"
    return jobs, job["id"]


def answer(jobs: web.Jobs, jid: str, pid: str) -> None:
    state = jobs.reading_state(jid)
    state["x_attempts"] = [{"id": pid, "at": "2026-12-01T10:00:00", "grade": "correct", "text": "", "where": "read"}]
    state["grades"] = {pid: {"first": "correct", "at": "2026-12-01T10:00:00"}}
    jobs.save_reading(jid, state)


def test_practice_the_library_and_the_notebook_read_no_book(tmp_path, opened, monkeypatch):
    """They need each book's outline only. They used to read every EPUB of the library, and keep all of them."""
    jobs, jid = processed(tmp_path)
    first = jobs.manifest(jid)["sections"][0]
    answer(jobs, jid, first["prompts"][0]["id"])
    outline = json.loads((tmp_path / "data" / jid / reading.OUTLINE_FILE).read_text(encoding="utf-8"))
    assert outline["format"] == reading.OUTLINE_FORMAT and outline["manifest"]["sections"] and outline["blocks"]
    # the app is started again: nothing is in memory
    again = web.Jobs(tmp_path / "data", backend_factory=lambda settings: Script().backend())
    monkeypatch.setattr(web.Jobs, "today", staticmethod(lambda: web._dt.date(2026, 12, 1)))
    opened.clear()
    card = again.practice()["card"]
    assert card["id"] == first["prompts"][0]["id"] and card["passage"] and card["passage"][0][0]["h"] is False
    assert again.listing()[0]["practice"]["cards"] == 1 and again.notebook(jid)["editable"] and again.detail(jid)["status"] == "done"
    assert again.reading_state(jid)["grades"] and again.manifest(jid)["sections"][0]["prompts"]
    assert opened == [] and again.parsed == {}
    # a highlight inside the card's passage: now the words are needed, to show it marked
    again.notes_do(jid, lambda store, book: web.notes.add_highlight(store, book, first["chapter"], first["start"] + 1, first["start"] + 4))
    runs = [run for paragraph in again.practice()["card"]["passage"] for run in paragraph]
    assert any(run["h"] for run in runs) and len(opened) == 1
    assert again.book(jid).chapter(first["chapter"])["words"] and len(opened) == 1   # the read book is kept


def test_the_outline_follows_the_book_and_only_a_few_books_are_kept_whole(tmp_path, opened):
    jobs = web.Jobs(tmp_path / "data", backend_factory=lambda settings: Script().backend())
    ids = [jobs.import_prepared(f"book {n}.prepared.epub", PREPARED.read_bytes())["id"] for n in range(6)]
    opened.clear()
    for jid in ids:
        assert jobs.book(jid).chapter(2)["words"]
    assert len(opened) == 6 and list(jobs.parsed) == ids[-web.Jobs.PARSED_KEPT:]
    assert jobs.book(ids[-1]).search("fog") and len(opened) == 6            # kept: not read again
    assert jobs.book(ids[0]).search("fog") and len(opened) == 7 and len(jobs.parsed) == web.Jobs.PARSED_KEPT
    # an outline made from another state of the book is not used
    folder = tmp_path / "data" / ids[1]
    kept = json.loads((folder / reading.OUTLINE_FILE).read_text(encoding="utf-8"))
    kept["manifest"]["title"] = "An older title"
    (folder / reading.OUTLINE_FILE).write_text(json.dumps({**kept, "of": [1, 2]}), encoding="utf-8")
    fresh = web.Jobs(tmp_path / "data", backend_factory=lambda settings: Script().backend())
    assert fresh.book(ids[1]).manifest()["title"] == "The Lighthouse Keeper's Almanac"
    assert json.loads((folder / reading.OUTLINE_FILE).read_text(encoding="utf-8"))["of"] != [1, 2]
    for text in ("not json", "[]", json.dumps({**kept, "format": 99})):
        (folder / reading.OUTLINE_FILE).write_text(text, encoding="utf-8")
        assert reading.load_outline(folder, kept["of"]) is None


def test_a_books_place_in_the_library_is_worked_out_again_only_when_its_files_changed(tmp_path, monkeypatch):
    jobs, jid = processed(tmp_path)
    first = jobs.manifest(jid)["sections"][0]
    loads: list[int] = []
    real = web.srs.load
    monkeypatch.setattr(web.srs, "load", lambda folder: (loads.append(1), real(folder))[1])
    shown = jobs.listing()[0]
    seen = len(loads)
    assert jobs.listing()[0] == shown and jobs.listing()[0] == shown and len(loads) == seen   # nothing is read again
    answer(jobs, jid, first["prompts"][0]["id"])                                              # the reader answered
    assert jobs.listing()[0]["practice"]["cards"] == 1 and len(loads) > seen
    jobs.notes_do(jid, lambda store, book: web.notes.add_highlight(store, book, first["chapter"], first["start"] + 1, first["start"] + 4))
    assert jobs.listing()[0]["notes"]["highlights"] == 1
    monkeypatch.setattr(web.Jobs, "today", staticmethod(lambda: web._dt.date(2031, 1, 1)))    # and so does another day
    assert jobs.listing()[0]["practice"]["due"] == 1
