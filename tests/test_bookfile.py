"""A book's file in the notes folder, made from its highlights and notes."""

from __future__ import annotations

from pathlib import Path


from ebook_processor import bookfile, notes
from ebook_processor import vault as V

JOB = "20261118-101500-fixture"


def marked(book) -> dict:
    store = notes.load(Path("/nowhere"))
    notes.add_highlight(store, book, 2, 3, 9, "Why *this* matters.\n\nA second paragraph.")
    notes.add_highlight(store, book, 2, 70, 74)
    notes.set_note(store, book, {"on": "place", "chapter": 2, "word": 61}, "A margin note\n\n- with a list\n- of two")
    notes.set_note(store, book, {"on": "section", "section": "s0001"}, "# My summary\n\nIn my words.")
    notes.set_note(store, book, {"on": "chapter", "chapter": 3}, "```\ncode at the end\n```")
    notes.set_note(store, book, {"on": "book"}, "About the book.")
    return store


def test_the_file_holds_what_was_marked_and_written_in_reading_order(book):
    store = marked(book)
    first, second = store["highlights"]
    ids = {n["on"]: n["id"] for n in store["notes"]}
    text = bookfile.text(JOB, book, store)
    assert text.startswith(f"---\nid: b-{JOB}\nkind: book\ntitle: The Lighthouse Keeper's Almanac\nauthors: [Mara Ellery Quill]\n---\n"
                           f"{bookfile.KEPT}\n\nAbout the book. ^{ids['book']}\n\n## The Lamp Room\n\n### Arrival at Gannet Rock\n\n"
                           f"**My summary**\n\nIn my words. ^{ids['section']}\n\n> {first['text']} ^{first['id']}\n\n"
                           f"Why *this* matters.\n\nA second paragraph. ^{first['id']}-note\n\n")
    assert f"A margin note\n\n- with a list\n- of two\n\n^{ids['place']}\n\n> {second['text']} ^{second['id']}\n" in text
    assert text.endswith(f"## The Fog Season\n\n```\ncode at the end\n```\n\n^{ids['chapter']}\n")
    assert "Winding the Clockwork" not in text and "Checkpoint" not in text and "/read/" not in text
    empty = bookfile.text(JOB, book, notes.load(Path("/nowhere")))
    assert empty.endswith(f"---\n{bookfile.KEPT}\n")


def test_the_file_reads_back_as_blocks_that_links_can_point_to(book):
    store = marked(book)
    first = store["highlights"][0]
    ids = {n["on"]: n["id"] for n in store["notes"]}
    note = V.read_text(bookfile.text(JOB, book, store), "Books/The Almanac.md", (1, 1))
    assert note.kind == "book" and V.prop(note.props, "id") == bookfile.book_id(JOB) and note.links == [] and note.tags == []
    assert note.headings == ["The Lamp Room", "Arrival at Gannet Rock", "The Fog Season"]
    assert note.blocks[first["id"]] == first["text"]
    assert note.blocks[first["id"] + "-note"] == "Why *this* matters.\n\nA second paragraph."
    assert note.blocks[ids["place"]] == "A margin note\n\n- with a list\n- of two"
    assert note.blocks[ids["chapter"]] == "```\ncode at the end\n```" and note.blocks[ids["section"]].endswith("In my words.")
    assert set(note.blocks) == {first["id"], first["id"] + "-note", store["highlights"][1]["id"], *ids.values()}
    assert [(p.block, p.heading, p.quote) for p in note.passages][:4] == [
        (ids["book"], "", False), (ids["section"], "Arrival at Gannet Rock", False),
        (ids["section"], "Arrival at Gannet Rock", False), (first["id"], "Arrival at Gannet Rock", True)]
    assert bookfile.place(store, first["id"]) == bookfile.place(store, first["id"] + "-note") == {"chapter": 2, "word": 3}
    assert bookfile.place(store, ids["place"]) == {"chapter": 2, "word": 61}
    assert bookfile.place(store, ids["book"]) is None and bookfile.place(store, "h0000") is None and bookfile.place(store, ids["section"]) is None
    sections = book.manifest()["sections"]
    assert bookfile.place(store, ids["section"], sections) == {"chapter": 2, "word": sections[0]["start"]}
    assert bookfile.place(store, ids["chapter"], sections) == {"chapter": 3, "word": 0}
    assert bookfile.job_of("b-" + JOB) == JOB and bookfile.job_of("n1234") is None


def test_words_of_the_book_are_not_read_as_markdown():
    assert bookfile._escape("a [[link]], a #tag, *stars*, `code`, <b>, back\\slash,\n C# and # alone") \
        == "a \\[\\[link]], a \\#tag, \\*stars\\*, \\`code\\`, \\<b>, back\\\\slash, C# and # alone"
    text = "---\nid: b-1\nkind: book\n---\n" + bookfile.KEPT + "\n\n> " + bookfile._escape("See [[Other]] and #tag *now*") + " ^h1\n"
    note = V.read_text(text, "Books/B.md", (1, 1))
    assert note.links == [] and note.tags == [] and note.blocks == {"h1": "See [[Other]] and #tag *now*"}


def test_a_removed_book_keeps_its_file_and_says_so():
    kept = "---\nid: b-1\nkind: book\n---\n" + bookfile.KEPT + "\n\n> words ^h1\n"
    gone = bookfile.mark_removed(kept)
    assert bookfile.REMOVED in gone and bookfile.KEPT not in gone and gone.endswith("\n\n> words ^h1\n")
    assert bookfile.mark_removed(gone) == gone
    assert bookfile.mark_removed("---\nid: b-1\n---\nedited by hand\n") == "---\nid: b-1\n---\n" + bookfile.REMOVED + "\n\nedited by hand\n"
    note = V.read_text(gone, "Books/B.md", (1, 1))
    assert bookfile.REMOVED in note.body and note.blocks == {"h1": "words"}
