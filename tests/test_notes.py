"""Highlights, notes, the notebook and its Markdown export."""

from __future__ import annotations

from pathlib import Path

import pytest

from ebook_processor import notes
from tests.paths import PREPARED


def empty() -> dict:
    return notes.load(Path("/nowhere"))


def test_a_highlight_is_a_run_of_words_and_touching_ones_become_one(book):
    store = empty()
    words = book.chapter(2)["words"]
    first = notes.add_highlight(store, book, 2, 10, 20, "one")
    assert first["text"] == " ".join(words[10:20]) and first["note"] == "one" and first["id"].startswith("h")
    notes.add_highlight(store, book, 2, 40, 45)
    joined = notes.add_highlight(store, book, 2, 20, 41, "two")  # touches the first, overlaps the second
    assert len(store["highlights"]) == 1 and (joined["start"], joined["end"]) == (10, 45)
    assert joined["id"] == first["id"] and joined["note"] == "one\n\ntwo" and joined["text"] == " ".join(words[10:45])
    notes.add_highlight(store, book, 3, 10, 12)  # another chapter stays apart
    assert [(h["chapter"], h["start"]) for h in store["highlights"]] == [(2, 10), (3, 10)]
    assert notes.marked(store, 2, 0, 12) == [(10, 12)] and notes.marked(store, 2, 50, 60) == []
    for bad in ((2, 5, 5), (2, -1, 4), (2, 0, 10**6), (99, 0, 3), ("2", 0, 3), (2, 0, 2500)):
        with pytest.raises(notes.NotesError):
            notes.add_highlight(store, book, *bad)


def test_notes_have_one_place_each(book):
    store = empty()
    a = notes.set_note(store, book, {"on": "section", "section": "s0001"}, "  First.  ")
    b = notes.set_note(store, book, {"on": "section", "section": "s0001"}, "Second.")
    assert a["id"] == b["id"] and b["text"] == "Second." and len(store["notes"]) == 1  # written over, not added
    notes.set_note(store, book, {"on": "place", "chapter": 2, "word": 61}, "At a paragraph")
    notes.set_note(store, book, {"on": "place", "chapter": 2, "word": 108}, "At another")
    notes.set_note(store, book, {"on": "chapter", "chapter": 3}, "On a chapter")
    notes.set_note(store, book, {"on": "book"}, "On the book")
    assert sorted(n["on"] for n in store["notes"]) == ["book", "chapter", "place", "place", "section"]
    assert notes.set_note(store, book, {"on": "book"}, "   ") is None and "book" not in [n["on"] for n in store["notes"]]
    for target, text in (({"on": "section", "section": "s9999"}, "x"), ({"on": "margin"}, "x"), ({"on": "book"}, 5),
                         ({"on": "place", "chapter": 2, "word": -3}, "x"), ({"on": "book"}, "x" * 20001)):
        with pytest.raises(notes.NotesError):
            notes.set_note(store, book, target, text)


def test_items_are_edited_and_removed_by_id(book):
    store = empty()
    h = notes.add_highlight(store, book, 2, 3, 9)
    n = notes.set_note(store, book, {"on": "place", "chapter": 2, "word": 3}, "A note")
    assert notes.update_item(store, h["id"], "now with a note")["note"] == "now with a note"
    notes.update_item(store, h["id"], "")  # emptying a highlight's note keeps the highlight
    assert store["highlights"][0]["note"] == "" and len(store["highlights"]) == 1
    notes.update_item(store, n["id"], "")  # emptying a note removes it
    assert store["notes"] == []
    notes.remove_item(store, h["id"])
    assert store["highlights"] == []
    with pytest.raises(notes.NotesError):
        notes.remove_item(store, "h0000")


def test_the_notebook_follows_the_book_and_the_export_leaves_out_what_is_empty(book):
    store = empty()
    notes.add_highlight(store, book, 2, 3, 9, "Why *this* matters.")
    notes.set_note(store, book, {"on": "place", "chapter": 2, "word": 61}, "A margin note")
    notes.set_note(store, book, {"on": "section", "section": "s0001"}, "My summary.\n\n- a point")
    notes.set_note(store, book, {"on": "book"}, "About the book.")
    state = {"grades": {"c0001-p1": {"first": "incorrect", "at": "t"}},
             "x_attempts": [{"id": "c0001-p1", "at": "t", "grade": "incorrect", "text": "what I said"},
                            {"id": "c0001-p1", "at": "t2", "grade": "correct", "text": ""}]}
    data = notes.notebook(book, store, state)
    assert data["counts"] == {"highlights": 1, "notes": 4, "answers": 1, "questions": 0, "proposals": 0}
    chapter = next(c for c in data["chapters"] if c["index"] == 2)
    first = chapter["sections"][0]
    assert first["note"]["text"].startswith("My summary") and [i["type"] for i in first["items"]] == ["highlight", "note"]
    assert first["answers"][0]["first"] == "incorrect" and len(first["answers"][0]["attempts"]) == 2
    assert first["answers"][0]["go"] == {"chapter": 2, "word": first["start"]}  # no passage known: the section's start
    there = notes.notebook(book, store, state, {"c0001-p1": {"chapter": 2, "word": 61}})
    assert next(c for c in there["chapters"] if c["index"] == 2)["sections"][0]["answers"][0]["go"] == {"chapter": 2, "word": 61}
    assert chapter["sections"][1]["items"] == [] and chapter["sections"][1]["answers"] == []
    md = notes.markdown(data)
    assert md.startswith("# The Lighthouse Keeper's Almanac\n\nMara Ellery Quill\n\nAbout the book.\n\n## The Lamp Room\n\n### Arrival at Gannet Rock\n\nMy summary.")
    assert "> " + store["highlights"][0]["text"] + "\n\nWhy *this* matters.\n\nA margin note\n" in md
    assert "(missed)\n  - Answered: what I said\n  - Reference: " in md
    assert first["answers"][0]["previewed"] is False
    # A question shown at the start of its section before it was graded is marked; one first shown afterwards is not.
    early = notes.notebook(book, store, {**state, "x_previewed": {"c0001-p1": "s"}})   # "s" sorts before the grade's "t"
    late = notes.notebook(book, store, {**state, "x_previewed": {"c0001-p1": "u"}})
    answer_of = lambda d: next(c for c in d["chapters"] if c["index"] == 2)["sections"][0]["answers"][0]  # noqa: E731
    assert answer_of(early)["previewed"] is True and answer_of(late)["previewed"] is False
    assert "(missed, previewed)\n" in notes.markdown(early) and "previewed" not in notes.markdown(late)
    assert "Winding the Clockwork" not in md and "The Fog Season" not in md  # nothing marked there
    plain = notes.markdown(data, answers=False)
    assert "Checkpoint" not in plain and "My summary." in plain
    assert notes.markdown(notes.notebook(book, empty(), None)) == "# The Lighthouse Keeper's Almanac\n\nMara Ellery Quill\n"


@pytest.fixture
def app(make_web_app):
    """The answers with their headers, and what is no JSON as text."""
    return make_web_app(model=None).function(answer=lambda r: (r.status, r.data if r.is_json else r.text, r.headers))


def test_the_routes_keep_highlights_and_notes_and_serve_the_notebook(app):
    jid = app("POST", "/api/books/import?name=fixture.prepared.epub", PREPARED.read_bytes())[1]["id"]
    assert app("GET", f"/api/jobs/{jid}/notes")[1] == {"version": 1, "highlights": [], "notes": []}
    made = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9})[1]
    hid = made["made"]
    assert made["highlights"][0]["id"] == hid and made["highlights"][0]["text"].startswith("The supply boat")
    assert app("PUT", f"/api/jobs/{jid}/notes/item/{hid}", json_body={"text": "noted"})[1]["highlights"][0]["note"] == "noted"
    assert app("PUT", f"/api/jobs/{jid}/notes/target", json_body={"target": {"on": "book"}, "text": "On the book"})[1]["notes"][0]["on"] == "book"
    assert app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 9, "end": 3})[0] == 400
    assert app("PUT", f"/api/jobs/{jid}/notes/target", json_body={"target": {"on": "nowhere"}, "text": "x"})[0] == 400
    assert app("DELETE", f"/api/jobs/{jid}/notes/item/h0000")[0] == 400
    listed = app("GET", "/api/jobs")[1]["jobs"][0]
    assert listed["notes"] == {"highlights": 1, "notes": 2}
    book = app("GET", f"/api/jobs/{jid}/notebook")[1]
    assert book["counts"]["highlights"] == 1 and book["note"]["text"] == "On the book"
    status, text, headers = app("GET", f"/api/jobs/{jid}/notebook.md")
    assert status == 200 and text.startswith("# The Lighthouse Keeper's Almanac") and "> The supply boat" in text
    assert headers["Content-Disposition"] == 'attachment; filename="fixture.notes.md"'
    assert app("DELETE", f"/api/jobs/{jid}/notes/item/{hid}")[1]["highlights"] == []


def test_a_practice_card_shows_what_was_highlighted_in_its_passage(tmp_path):
    from ebook_processor.web import server as web
    from tests.scripted import Script
    from tests.paths import FIXTURE
    from tests.webapp import wait_for_run
    jobs = web.Jobs(tmp_path / "data", backend_factory=lambda settings: Script().backend())
    job = jobs.create("fixture.epub", FIXTURE.read_bytes(), web.from_options(density="short"))
    assert wait_for_run(jobs, job["id"])["status"] == "done"
    jid = job["id"]
    sources = jobs.sources(jid)  # where the notebook's "Open the passage" goes: the first paragraph behind the answer
    block = next(b for sd in jobs.book(jid).book.spine for b in sd.blocks if b.start == sources["c0001-p1"]["word"] and sd.index == sources["c0001-p1"]["chapter"])
    assert set(sources) == {p["id"] for s in jobs.book(jid).manifest()["sections"] for p in s["prompts"]} and block.words
    plain = jobs.passage(jid, "c0001-p1")
    assert len(plain) == 1 and plain[0] == [{"t": plain[0][0]["t"], "h": False}]  # one paragraph, nothing marked
    block = next(b for sd in jobs.book(jid).book.spine for b in sd.blocks if " ".join(b.words) == plain[0][0]["t"])
    jobs.notes_do(jid, lambda st, bk: notes.add_highlight(st, bk, 2, block.start + 2, block.start + 5))
    runs = jobs.passage(jid, "c0001-p1")[0]
    assert [r["h"] for r in runs] == [False, True, False] and runs[1]["t"] == " ".join(block.words[2:5])
    assert " ".join(r["t"] for r in runs) == plain[0][0]["t"]
