"""The notes folder: titles, properties, links, the index, saving, renaming, the trash and search."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from ebook_processor import vault as V
from ebook_processor.vault import OPAQUE, Vault, VaultError


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    made = Vault(tmp_path / "notes")
    made.ensure()
    return made


def drop(vault: Vault, relative: str, text: str) -> Path:
    """A file written by another program."""
    path = vault.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    vault.refresh(force=True)
    return path


# ---- titles


@pytest.mark.parametrize("title", ["a/b", "a\\b", "what?", "a:b", 'say "x"', "a<b", "a>b", "a|b", "a*b", "bell\x07here",
                                   ".hidden", "ends.", "CON", "con", "LPT1", "nul.txt", "note#1", "a^b", "[x]", "",
                                   "x" * 121])
def test_a_title_must_be_a_file_name_everywhere(title):
    assert V.title_problem(title)
    with pytest.raises(VaultError):
        V.clean_title(title)


def test_good_titles_and_the_names_the_app_makes():
    assert V.clean_title("  Prices   carry\ninformation ") == "Prices carry information"
    assert V.clean_title("Ça va, déjà-vu (2nd) & more!") == "Ça va, déjà-vu (2nd) & more!"
    assert V.clean_title("Console") == "Console"  # only the reserved names themselves are refused
    assert V.safe_title('What is "price"? A/B: tests.') == "What is price A B tests"
    assert V.safe_title("???") == "Untitled" and V.safe_title("con") == "con -" and V.safe_title("") == "Untitled"
    assert not V.title_problem(V.safe_title("x" * 300))
    when = dt.datetime(2026, 11, 18, 15, 30)
    assert V.inbox_name("\n# A *thought* about [[Prices|pricing]]: why? Because it matters a lot\nmore", when) \
        == "2026-11-18 1530 A thought about pricing why Because"
    assert V.inbox_name("", when) == "2026-11-18 1530" and V.inbox_name("Source: [[Book#^h1]]", when) == "2026-11-18 1530 Book"
    assert V.UNTITLED_RE.match(V.inbox_name("x", when)) and not V.UNTITLED_RE.match("2026 plans")


# ---- properties


FRONT = """---
id: n7f3a2c91
kind: idea            # what it is
created: 2026-11-18T15:30:00Z
tags: [pricing, "two words", markets]
sources:
  - "[[The Lighthouse Keeper's Almanac#^h3f9a]]"
  - [[Unquoted link]]
aliases:
- one
- 'it''s'
rating: 4
ratio: 0.5
done: true
empty:
quoted: "a: b # not a comment \\"x\\""
plain: some words # a comment
# a line of its own
nested:
  a: 1
  b: [2]
long: |
  line one
  line two
cssclasses: {wide: true}
---
The text.
"""


def test_properties_are_read_for_a_small_subset_and_the_rest_is_kept():
    front, body = V.split(FRONT)
    props = V.parse_props(front)
    get = lambda name: V.prop(props, name)
    assert body == "The text.\n"
    assert get("id") == "n7f3a2c91" and get("kind") == "idea" and get("created") == "2026-11-18T15:30:00Z"
    assert get("tags") == ["pricing", "two words", "markets"]
    assert get("sources") == ["[[The Lighthouse Keeper's Almanac#^h3f9a]]", "[[Unquoted link]]"]
    assert get("aliases") == ["one", "it's"]
    assert get("rating") == 4 and get("ratio") == 0.5 and get("done") is True and get("empty") is None
    assert get("quoted") == 'a: b # not a comment "x"' and get("plain") == "some words"
    assert get("nested") is OPAQUE and get("long") is OPAQUE and get("cssclasses") is OPAQUE
    assert V.compose(props, body) == FRONT  # untouched properties are written back exactly as they were


def test_a_changed_property_is_written_so_that_it_reads_back():
    props = V.parse_props(V.split(FRONT)[0])
    values = {"plain": "yes: no", "tags": ["a", "b c", "d,e"], "rating": 5, "done": False, "empty": "true", "quoted": None,
              "new": "- starts like a list", "multi": "two\nlines", "number": "42", "link": "[[A note]]", "hash": "a #b",
              "path": "C:\\dir", "simple": ["x", "y-z", "é/ü"]}
    for name, value in values.items():
        V.set_prop(props, name, value)
    again = V.parse_props(V.split(V.compose(props, "body"))[0])
    for name, value in values.items():
        assert V.prop(again, name) == value and type(V.prop(again, name)) is type(value), name
    text = V.compose(props, "body")
    assert "simple: [x, y-z, é/ü]" in text and 'tags:\n  - "a"\n  - "b c"\n  - "d,e"' in text and "nested:\n  a: 1\n  b: [2]" in text
    assert V.split("no properties here\n---\n") == (None, "no properties here\n---\n")
    assert V.split("---\nnever: closed\n")[0] is None
    assert V.clean_tags("#one, two  #Two three/four bad!tag") == ["one", "two", "three/four", "bad-tag"]


# ---- what is found in a note's text


def test_links_tags_headings_and_blocks_are_found_outside_code():
    body = """# Top heading
A link to [[Prices carry information]] and [[Other note|shown words]], a part [[Other note#A heading]],
a block [[The Book#^h3f9a]] and an embed ![[Picture note]]. #pricing and #markets/local, not #123 or a#b or
https://example.com/page#part.

`[[not a link]] #nottag` and

```
[[also not]] #nope
```

## Second heading ##
- item one ^item1
- item two ^item2

A closing thought. ^last
"""
    note = V.read_text("---\ntags: [from-property, Pricing]\n---\n" + body, "A note.md", (1, 1))
    assert [(k.target, k.anchor, k.shown, k.embed) for k in note.links] == [
        ("Prices carry information", "", "", False), ("Other note", "", "shown words", False),
        ("Other note", "A heading", "", False), ("The Book", "^h3f9a", "", False), ("Picture note", "", "", True)]
    assert note.links[0].context.startswith("A link to Prices carry information and shown words")
    assert note.tags == ["from-property", "Pricing", "markets/local"]
    assert note.headings == ["Top heading", "Second heading"]
    assert note.blocks == {"item1": "- item one", "item2": "- item one ^item1\n- item two", "last": "A closing thought."}
    assert note.excerpt.startswith("A link to Prices carry information") and note.kind == "idea" and note.title == "A note"


def test_a_file_without_properties_is_a_quick_note_in_the_inbox_and_an_idea_note_elsewhere(vault):
    drop(vault, "Inbox/dropped thought.md", "Just text\r\nwith Windows line ends")
    drop(vault, "Made in Obsidian.md", "\ufeffSome text")
    drop(vault, ".obsidian/workspace.md", "not a note")
    drop(vault, "Books/Loose.md", "> a quote ^q1\n")
    rows = {r["title"]: r for r in vault.listing()}
    assert set(rows) == {"dropped thought", "Made in Obsidian", "Loose"}
    assert rows["dropped thought"]["kind"] == "quick" and rows["dropped thought"]["inbox"] and not rows["dropped thought"]["untitled"]
    assert rows["Made in Obsidian"]["kind"] == "idea" and rows["Loose"]["kind"] == "book"
    note = vault.get(rows["dropped thought"]["id"])
    assert note.provisional and note.body == "Just text\nwith Windows line ends"
    # the first save gives the file its properties; the id the page had still finds the note
    saved = vault.save(note.id, "Just text, edited", None, note.token)
    assert saved["id"] != note.id and saved["id"].startswith("n") and vault.get(note.id).id == saved["id"]
    text = (vault.root / "Inbox/dropped thought.md").read_text(encoding="utf-8")
    assert text.startswith(f"---\nid: {saved['id']}\nkind: quick\ncreated: ") and text.endswith("---\nJust text, edited")


# ---- writing


def test_create_puts_a_titled_note_at_the_top_and_the_rest_in_the_inbox(vault):
    when = dt.datetime(2026, 11, 18, 9, 5)
    a = vault.create("Prices carry information", "No single person holds it. #pricing", props={"tags": ["markets"]})
    b = vault.create("", "A thought without a title yet", when=when)
    c = vault.create("", "A thought without a title yet", "quick", when=when)
    d = vault.create("Named quick", "x", "quick")
    assert a["path"] == "Prices carry information.md" and not a["inbox"] and a["tags"] == ["markets", "pricing"]
    assert b["path"] == "Inbox/2026-11-18 0905 A thought without a title yet.md" and b["untitled"] and b["kind"] == "idea"
    assert c["path"] == "Inbox/2026-11-18 0905 A thought without a title yet 2.md" and c["kind"] == "quick"
    assert d["path"] == "Inbox/Named quick.md" and not d["untitled"]
    assert len({a["id"], b["id"], c["id"], d["id"]}) == 4
    for title in ("prices CARRY information", "Prices carry information"):  # not told apart on Windows and macOS
        with pytest.raises(VaultError, match="already a note"):
            vault.create(title, "")
    with pytest.raises(VaultError):
        vault.create("What?", "")
    with pytest.raises(VaultError):
        vault.create("A book", "", "book")
    assert not list(vault.root.rglob("*.tmp"))


def test_save_changes_text_and_properties_and_keeps_what_it_does_not_know(vault):
    made = vault.create("A note", "First")
    path = vault.root / "A note.md"
    path.write_text(path.read_text(encoding="utf-8").replace("---\nFirst", "weird: {a: 1}\nrating: 3\n---\nFirst"), encoding="utf-8")
    vault.refresh(force=True)
    note = vault.detail(vault.get(made["id"]))
    assert {"key": "weird", "raw": "weird: {a: 1}"} in note["props"] and {"key": "rating", "value": 3} in note["props"]
    props = [p for p in note["props"] if p["key"] not in ("rating", "kind")] + [
        {"key": "tags", "value": ["#one", "two words"]}, {"key": "id", "value": "hacked"}, {"key": "kind", "value": "index"}]
    saved = vault.save(note["id"], "Second", props, note["token"])
    text = path.read_text(encoding="utf-8")
    assert saved["id"] == made["id"] and saved["kind"] == "index" and saved["tags"] == ["one", "two-words"] and saved["body"] == "Second"
    assert "weird: {a: 1}" in text and "rating" not in text and f"id: {made['id']}" in text and text.endswith("---\nSecond")
    for bad in ([{"key": "kind", "value": "book"}], [{"key": "bad: key", "value": 1}], [{"key": "a", "value": {"x": 1}}],
                [{"key": "a", "value": 1}, {"key": "a", "value": 2}], "nonsense"):
        with pytest.raises(VaultError):
            vault.save(saved["id"], None, bad, saved["token"])
    with pytest.raises(VaultError):
        vault.save(saved["id"], "x" * (V.BODY_MAX + 1), None, saved["token"])
    with pytest.raises(V.Missing):
        vault.save("n00000000", "x", None, "0-0")


def test_a_note_changed_on_disk_is_not_written_over(vault):
    made = vault.create("A note", "Loaded text")
    path = vault.root / "A note.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAdded in Obsidian", encoding="utf-8")
    with pytest.raises(V.Conflict) as caught:
        vault.save(made["id"], "Typed in the app", None, made["token"])
    assert caught.value.current["body"] == "Loaded text\nAdded in Obsidian" and "Typed" not in path.read_text(encoding="utf-8")
    forced = vault.save(made["id"], "Typed in the app", None, made["token"], force=True)
    assert forced["body"] == "Typed in the app" and forced["token"] != made["token"]
    assert vault.save(made["id"], "Typed in the app", None, forced["token"])["token"] == forced["token"]  # nothing to write


def test_rename_moves_the_file_and_rewrites_every_link(vault):
    old = vault.create("Old name", "See [[Old name#Part]] and myself.\n\n## Part\n")
    a = vault.create("First", "A [[Old name]], an [[old NAME|alias]], a part [[Old name#Part]], an embed ![[Old name]],\n"
                              "`[[Old name]]` in code, and [[Older name]] left alone.", props={"sources": ["[[Old name#^b1]]"]})
    vault.create("Older name", "")
    renamed = vault.rename(old["id"], "New: name".replace(":", " -"))
    assert renamed["id"] == old["id"] and renamed["title"] == "New - name" and renamed["relinked"] == 2
    assert not (vault.root / "Old name.md").exists() and (vault.root / "New - name.md").is_file()
    text = (vault.root / "First.md").read_text(encoding="utf-8")
    assert "A [[New - name]], an [[New - name|alias]], a part [[New - name#Part]], an embed ![[New - name]]," in text
    assert "`[[Old name]]` in code" in text and "[[Older name]] left alone" in text and '"[[New - name#^b1]]"' in text
    assert "See [[New - name#Part]]" in renamed["body"]
    assert [b["id"] for b in renamed["backlinks"]] == [a["id"]]
    # letter case only; a title that is taken; a note out of the inbox
    assert vault.rename(old["id"], "NEW - name")["title"] == "NEW - name"
    assert sorted(p.name for p in vault.root.glob("*.md")) == ["First.md", "NEW - name.md", "Older name.md"]
    assert sorted(n["title"] for n in vault.listing()) == ["First", "NEW - name", "Older name"]  # one note, not one per letter case
    assert "[[NEW - name]]" in (vault.root / "First.md").read_text(encoding="utf-8")
    with pytest.raises(VaultError, match="already a note called First"):
        vault.rename(old["id"], "first")
    quick = vault.create("", "a quick thought", "quick")
    linker = vault.create("Linker", f"About [[{quick['title']}]].")
    out = vault.rename(quick["id"], "A thought")
    assert out["path"] == "A thought.md" and not out["inbox"] and out["kind"] == "quick"
    assert "About [[A thought]]." in vault.get(linker["id"]).body
    dropped = drop(vault, "Inbox/from outside.md", "text")
    moved = vault.rename(vault.listing(inbox=True)[0]["id"], "From outside, titled")
    assert not dropped.exists() and moved["path"] == "From outside, titled.md" and moved["id"].startswith("n")


def test_links_resolve_by_name_in_any_letter_case_and_show_what_they_lead_to(vault):
    vault.create("Target", "Words of the target.\n\n## A Heading\n\nA block. ^b1\n")
    drop(vault, "Books/The Book.md", "---\nid: b-1\nkind: book\n---\n## Chapter\n\n> highlighted words ^h1\n")
    drop(vault, "sub/Target.md", "another of the same name, deeper")
    note = vault.create("From", "[[target]] [[TARGET#a heading]] [[Target#^b1]] [[Target#^none]] [[Missing]] [[Books/The Book#^h1]] "
                                "[[the book#^h1]] [[sub/Target]] [[#Local]]\n\n## Local\n")
    to = [k["to"] for k in note["links"]]
    assert [t["found"] for t in to] == [True, True, True, False, False, True, True, True, True]
    assert to[0]["text"].startswith("Words of the target.") and to[2]["text"] == "A block." and to[5]["text"] == "highlighted words"
    assert to[5]["id"] == "b-1" and to[7]["text"].startswith("another of the same name") and to[8]["id"] == note["id"]
    assert [c["link"] for c in vault.complete("tar")] == ["Target", "sub/Target"]
    assert [c["title"] for c in vault.complete("book")] == ["The Book"] and vault.complete("zzz") == []
    assert vault.anchors("target") == {"link": "Target", "headings": ["A Heading"], "blocks": [{"id": "b1", "text": "A block."}]}
    assert vault.anchors("the bo")["link"] == "The Book" and vault.anchors("the bo")["blocks"] == [{"id": "h1", "text": "highlighted words"}]
    assert vault.anchors("nothing") == vault.anchors("") == {"link": "", "headings": [], "blocks": []}


def test_delete_moves_to_the_trash_and_restore_puts_it_back(vault):
    a = vault.create("To go", "text")
    b = vault.create("Stays", "links to [[To go]]")
    quick = vault.create("", "quick thought", "quick")
    gone = vault.delete(a["id"])
    assert gone["trash"] == "To go.md" and (vault.root / ".trash/To go.md").is_file() and not (vault.root / "To go.md").exists()
    assert vault.detail(vault.get(b["id"]))["links"][0]["to"] == {"found": False}  # the link shows as broken
    with pytest.raises(V.Missing):
        vault.get(a["id"])
    again = vault.create("To go", "a second note of that name")
    vault.delete(again["id"])
    vault.delete(quick["id"])
    names = {r["name"] for r in vault.trash()}
    assert names == {"To go.md", "To go 2.md", "Inbox/" + quick["path"].split("/")[1]}
    back = vault.restore("To go.md")
    assert back["id"] == a["id"] and back["path"] == "To go.md" and back["body"] == "text"
    assert vault.restore("Inbox/" + quick["path"].split("/")[1])["inbox"]
    vault.create("To go 2", "taken meanwhile")
    assert vault.restore("To go 2.md")["path"] == "To go 2 (restored).md"
    assert vault.trash() == []
    for bad in ("../Stays.md", "nothing.md", "x", 5, "../../etc/passwd.md"):
        with pytest.raises(VaultError):
            vault.restore(bad)
    assert (vault.root / "Stays.md").is_file()


# ---- the index, books and search


def test_changes_made_outside_the_app_are_seen(vault):
    made = vault.create("Seen", "one")
    path = vault.root / "Seen.md"
    path.write_text(path.read_text(encoding="utf-8").replace("one", "two #fresh"), encoding="utf-8")
    vault.refresh(force=True)
    assert vault.get(made["id"]).body == "two #fresh" and vault.tags() == [{"tag": "fresh", "count": 1}]
    path.rename(vault.root / "Seen again.md")  # renamed in another program: the id in the file still finds it
    vault.refresh(force=True)
    assert vault.get(made["id"]).title == "Seen again"
    copy = drop(vault, "A copy.md", (vault.root / "Seen again.md").read_text(encoding="utf-8"))
    ids = {r["title"]: r["id"] for r in vault.listing()}
    assert ids["A copy"] != ids["Seen again"] and made["id"] in ids.values()  # a copy made by hand gets an id of its own
    copy.unlink()
    (vault.root / "Seen again.md").unlink()
    vault.refresh(force=True)
    assert vault.listing() == []
    assert Vault(vault.root / "not there").listing() == [] and Vault(vault.root / "not there").search("x")["notes"] == []


BOOK = """---
id: b-20261118-101500-almanac
kind: book
title: The Almanac
---
> [!info] This file is kept by ebook-processor. Edits made here are overwritten.

About the whole book. ^n5e10

## The Lamp Room

### Lighting

> The lamp is lit at sunset and put out at sunrise. ^h3f9a

A first paragraph on that.

A second one. ^h3f9a-note

A note on a paragraph about wicks. ^n81c2
"""


def test_a_books_file_is_written_once_and_found_by_its_id(vault):
    note = vault.put_book("b-20261118-101500-almanac", "The Almanac: a keeper's year?", BOOK)
    assert note.path == "Books/The Almanac a keeper's year.md" and note.kind == "book"
    stamp = note.stamp
    assert vault.put_book("b-20261118-101500-almanac", "Another title now", BOOK).stamp == stamp  # same text: not written
    assert vault.put_book("b-20261118-101500-almanac", "x", BOOK + "\nMore. ^n1\n").stamp != stamp
    assert [p.name for p in (vault.root / "Books").iterdir()] == ["The Almanac a keeper's year.md"]
    vault.create("The Other", "")
    assert vault.put_book("b-2", "the other", "---\nid: b-2\nkind: book\n---\nAnother book.\n").path == "Books/the other 2.md"
    assert vault.book("b-2").title == "the other 2" and vault.book("b-none") is None
    with pytest.raises(VaultError):
        vault.rename("b-2", "Mine now")


def test_search_covers_notes_and_the_passages_of_books(vault):
    vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK)
    lamp = vault.create("Lamps need tending", "A lamp left alone goes out. See [[The Almanac#^h3f9a]]. #keeping")
    wick = vault.create("Wicks", "Trim the wick; the LAMP burns clean. " + "filler " * 80 + "the end mentions sunrise.", props={"tags": ["keeping", "craft"]})
    quick = vault.create("", "lamp oil to buy", "quick")
    # two files written within one tick of the clock (Windows) are as new as each other: say which is older
    then = (vault.root / "Wicks.md").stat().st_mtime - 60
    os.utime(vault.root / "Wicks.md", (then, then))
    vault.refresh(force=True)
    found = vault.search("lamp")
    assert [n["id"] for n in found["notes"]] == [lamp["id"], quick["id"], wick["id"]]  # the title first, then the newest
    assert [t["title"] for t in found["titles"]] == ["Lamps need tending"]
    assert [(p["block"], p["heading"], p["quote"]) for p in found["passages"]] == [("h3f9a", "Lighting", True)]
    assert found["passages"][0]["book"] == "The Almanac" and found["passages"][0]["text"].startswith("The lamp is lit")
    assert vault.search("wick sunrise")["notes"][0]["snippet"].startswith("Trim the wick")
    assert "sunrise" in vault.search("sunrise wick")["notes"][0]["snippet"] and vault.search("sunrise wick")["notes"][0]["snippet"].startswith("… ")
    assert [p["block"] for p in vault.search("first paragraph")["passages"]] == ["h3f9a-note"]  # belongs to the note's entry
    assert [p["block"] for p in vault.search("whole book")["passages"]] == ["n5e10"] and vault.search("kept by")["passages"] == []
    assert vault.search("LAMP sunset nothing")["notes"] == [] and vault.search("keeping")["notes"][0]["id"] in (lamp["id"], wick["id"])
    assert {n["id"] for n in vault.search("", tag="Keeping")["notes"]} == {lamp["id"], wick["id"]} and not vault.search("", tag="keeping")["passages"]
    assert [n["id"] for n in vault.search("lamp", kind="quick")["notes"]] == [quick["id"]] and not vault.search("lamp", kind="quick")["passages"]
    only = vault.search("", book="b-20261118-101500-almanac")
    assert [n["id"] for n in only["notes"]] == [lamp["id"]] and len(only["passages"]) == 5
    assert vault.search("lamp", kind="book")["notes"] == [] and len(vault.search("", kind="book")["passages"]) == 5
    assert vault.search("", book="b-none") == {"titles": [], "notes": [], "passages": [], "more": False}
    assert vault.search("")["passages"] == [] and len(vault.search("")["notes"]) == 3
    assert vault.tags() == [{"tag": "keeping", "count": 2}, {"tag": "craft", "count": 1}]


def test_nothing_is_written_outside_the_folder(vault, tmp_path):
    with pytest.raises(VaultError):
        vault._inside("../outside.md")
    with pytest.raises(VaultError):
        vault._inside("Inbox/../../outside.md")
    assert vault._inside("Inbox/in.md").name == "in.md"
    with pytest.raises(VaultError, match="cannot be made"):
        blocked = tmp_path / "a file"
        blocked.write_text("x")
        Vault(blocked / "notes").ensure()


# ---- processing: who cites what, related notes, what the app adds to a note


def test_the_notes_that_cite_an_entry_of_a_book_are_known(vault):
    book = vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK)
    a = vault.create("One", "From [[The Almanac#^h3f9a]].")
    b = vault.create("Two", "About the note on it: [[the almanac#^h3f9a-note]], and [[The Almanac#^n81c2|the wicks]].", props={"sources": ["[[The Almanac#^h3f9a]]"]})
    vault.create("Three", "Only the book: [[The Almanac]], and a part [[The Almanac#The Lamp Room]].")
    cited = vault.citing(vault.book(book.id))
    assert {k: sorted(n.title for n in v) for k, v in cited.items()} == {"h3f9a": ["One", "Two"], "n81c2": ["Two"]}
    vault.delete(a["id"])
    vault.delete(b["id"])
    assert vault.citing(vault.book(book.id)) == {}


def test_related_notes_share_uncommon_words_or_tags_and_say_which(vault):
    vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK)
    lamp = vault.create("Lamps need tending", "A lamp left alone goes out before sunrise. The keeper trims the wick.", props={"tags": ["keeping"]})
    vault.create("Markets", "Prices carry information that no trader holds alone.")
    tagged = vault.create("Routine", "Doing the same thing daily.", props={"tags": ["keeping", "habits"]})
    for n in range(8):
        vault.create(f"Filler {n}", f"Unrelated words about gardening number {n}: soil, compost, seedlings.")
    found = vault.related("The lamp is lit at sunset and put out at sunrise, whatever the wick.", tags=["Keeping"])
    assert [n["id"] for n in found["notes"]] == [lamp["id"], tagged["id"]]
    assert set(found["notes"][0]["shares"]) == {"lamp", "sunrise", "wick"} and found["notes"][0]["link"] == "Lamps need tending"
    assert found["notes"][1]["shares"] == []                                        # a tag in common, no words
    assert [(p["block"], p["quote"]) for p in found["passages"]][0] == ("h3f9a", True) and "lamp" in found["passages"][0]["shares"]
    less = vault.related("The lamp is lit at sunset and put out at sunrise.", but={lamp["id"], "b-20261118-101500-almanac#h3f9a"})
    assert lamp["id"] not in [n["id"] for n in less["notes"]] and "h3f9a" not in [p["block"] for p in less["passages"]]
    assert vault.related("the and for that with") == {"notes": [], "passages": []}
    assert vault.related("compost")["notes"] == [] and len(vault.related("compost seedlings")["notes"]) == 6  # one common word is not enough
    assert Vault(vault.root / "none").related("lamp wick") == {"notes": [], "passages": []}


def test_the_app_adds_a_source_or_words_to_a_note_and_sets_its_own_properties(vault):
    note = vault.create("Kept", "What I think.\n", props={"tags": ["x"]})
    one = vault.add_to(note["id"], source="[[The Almanac#^h3f9a]]")
    assert one["body"] == "What I think.\n\nSource: [[The Almanac#^h3f9a]]\n" and {"key": "sources", "value": ["[[The Almanac#^h3f9a]]"]} in one["props"]
    assert vault.add_to(note["id"], source="[[The Almanac#^h3f9a]]")["body"] == one["body"]            # not twice
    two = vault.add_to(note["id"], "A thought from the inbox.\n", "[[The Almanac#^n81c2]]")
    assert two["body"].endswith("Source: [[The Almanac#^h3f9a]]\n\nA thought from the inbox.\n\nSource: [[The Almanac#^n81c2]]\n")
    assert {"key": "sources", "value": ["[[The Almanac#^h3f9a]]", "[[The Almanac#^n81c2]]"]} in two["props"] and two["tags"] == ["x"]
    empty = vault.create("Empty", "")
    assert vault.add_to(empty["id"], "First words.")["body"] == "First words.\n"
    quick = vault.create("", "a thought", "quick")
    aside = vault.set_props(quick["id"], {"aside": True})
    assert {"key": "aside", "value": True} in aside["props"] and aside["body"] == "a thought" and aside["id"] == quick["id"]
    assert {"key": "aside", "value": True} not in vault.set_props(quick["id"], {"aside": None})["props"]
    book = vault.put_book("b-1", "A Book", "---\nid: b-1\nkind: book\n---\n> words ^h1\n")
    with pytest.raises(VaultError):
        vault.add_to(book.id, "mine")


# ---- projects: sections of a file, gathering, the outline and its export


def test_a_section_of_a_file_is_found_by_its_heading_and_added_to():
    body = "About.\n\n## Gathered\n\n- [[One]]\n\n## Outline\n\n- Opening\n  - [[One]]\n  continued here\n- Close\n\n```\n## Gathered\n```\n\n# Later\n\ntext\n"
    assert V.section(body, "gathered") == (3, 6) and V.section(body, "Outline") == (7, 17) and V.section(body, "Missing") is None
    assert V.add_under(body, "Gathered", "- [[Two]]").split("\n")[3:7] == ["", "- [[One]]", "- [[Two]]", ""]
    assert V.add_under("About.\n", "Gathered", "- [[One]]") == "About.\n\n## Gathered\n\n- [[One]]\n"
    assert V.add_under("", "Gathered", "- [[One]]") == "## Gathered\n\n- [[One]]\n"
    assert V.add_under("## Gathered\n## Outline\n", "Gathered", "- x") == "## Gathered\n\n- x\n\n## Outline\n"
    assert V.add_under("## Gathered\n", "Gathered", "- x") == "## Gathered\n\n- x\n"
    assert V.outline(body) == [{"depth": 0, "text": "Opening"}, {"depth": 1, "text": "[[One]] continued here"}, {"depth": 0, "text": "Close"}]
    assert V.outline("## Outline\n1. a\n    * b\n        - c\n    * d\n2. e\nloose\n") == [
        {"depth": 0, "text": "a"}, {"depth": 1, "text": "b"}, {"depth": 2, "text": "c"}, {"depth": 1, "text": "d"}, {"depth": 0, "text": "e loose"}]
    assert V.outline("no outline") == []


def test_a_project_gathers_notes_keeps_its_own_and_exports_its_outline(vault):
    vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK.replace("title: The Almanac", "title: The Almanac of Lights"))
    rule = vault.create("A duty is kept by rule", "The keeper lights the lamp *whatever the weather*. See [[Decide once|deciding once]] and [[Nowhere]]. ^b1\n\n"
                                                  "```\n[[kept in code]]\n```\n\nSource: [[The Almanac#^h3f9a]]\n")
    once = vault.create("Decide once", "A decision made in advance is cheaper. #habits")
    project = vault.create("Talk on habits", "For the spring meeting.\n", "project", props={"status": "active"})
    done = vault.create("Old talk", "", "project", props={"status": "finished"})
    mine = vault.create("Talk on habits - to do", "- [ ] book a room", "project-note", props={"project": "[[Talk on habits]]"})
    vault.create("Other project note", "x", "project-note", props={"project": "[[Old talk]]"})
    got = vault.gather(project["id"], rule["id"])
    assert got["added"] and got["body"] == "For the spring meeting.\n\n## Gathered\n\n- [[A duty is kept by rule]]\n"
    assert not vault.gather(project["id"], rule["id"])["added"]                       # once is enough
    assert vault.gather(project["id"], once["id"])["body"].endswith("- [[A duty is kept by rule]]\n- [[Decide once]]\n")
    assert vault.get(rule["id"]).body.startswith("The keeper lights")                  # the note itself is not touched
    for bad in ((rule["id"], once["id"]), (project["id"], project["id"]), (project["id"], done["id"]), (project["id"], "b-20261118-101500-almanac")):
        with pytest.raises(VaultError):
            vault.gather(*bad)
    rows = {r["title"]: r for r in vault.projects()}
    assert (rows["Talk on habits"]["status"], rows["Talk on habits"]["gathered"], rows["Talk on habits"]["project_notes"]) == ("active", 2, 1)
    assert rows["Old talk"]["status"] == "finished" and rows["Old talk"]["project_notes"] == 1
    assert [n.id for n in vault.project_notes(vault.get(project["id"]))] == [mine["id"]]
    vault.rename(project["id"], "Talk on rules")                                      # the project note follows its project
    assert [n.id for n in vault.project_notes(vault.get(project["id"]))] == [mine["id"]]
    now = vault.get(project["id"])
    vault.save(project["id"], now.body + "\n## Outline\n\n- Why rules\n  - [[A duty is kept by rule]]\n  - [[The Almanac#^h3f9a]]\n  - A line of my own, citing [[Decide once]].\n"
                                         "- [[Decide once]]\n  - a point under it\n- [[Gone note]]\n", None, now.token)
    assert {r["title"]: r["outline"] for r in vault.projects()} == {"Talk on rules": 7, "Old talk": 0}
    assert vault.export(project["id"]) == (
        "# Talk on rules\n\n## Why rules\n\n"
        "The keeper lights the lamp *whatever the weather*. See deciding once and Nowhere.\n\n```\n[[kept in code]]\n```\n\nSource: The Almanac of Lights\n\n"
        "> The lamp is lit at sunset and put out at sunrise.\n>\n> (The Almanac of Lights)\n\n"
        "A line of my own, citing Decide once.\n\n"
        "## Decide once\n\nA decision made in advance is cheaper. #habits\n\na point under it\n\nGone note\n")
    with pytest.raises(VaultError):
        vault.export(rule["id"])
    assert vault.export(done["id"]) == "# Old talk\n"


def test_an_embed_shows_the_note_a_part_of_it_or_one_block(vault):
    vault.create("Whole", "First paragraph.\n\n## A part\n\nUnder the part.\n\nA block. ^b1\n\n## Another\n\nNot this.\n")
    assert vault.peek("whole", full=True)["body"].startswith("First paragraph.\n\n## A part")
    assert vault.peek("Whole", "a part", full=True)["body"] == "\nUnder the part.\n\nA block. ^b1".strip("\n") and vault.peek("Whole", "A part")["found"]
    assert vault.peek("Whole", "^b1", full=True)["body"] == "A block." and "body" not in vault.peek("Whole", "^b1")
    assert vault.peek("Whole", "No such part", full=True) == {"found": False, "id": vault.resolve("Whole").id, "title": "Whole", "kind": "idea",
                                                              "text": vault.resolve("Whole").excerpt, "heading": "No such part", "body": ""}


# ---- seeing the whole: the graph, a neighbourhood, what to come back to


def test_the_graph_joins_notes_and_books_and_a_neighbourhood_is_two_steps(vault):
    vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK)
    a = vault.create("A", "Links to [[B]] and [[B]] again, and cites [[The Almanac#^h3f9a]].")
    b = vault.create("B", "Links to [[C]] and back to [[A]].")
    c = vault.create("C", "Links to [[D]].")
    d = vault.create("D", "Three steps from A.")
    e = vault.create("E", "Cites the same book: [[The Almanac#^n81c2]].")
    alone = vault.create("Alone", "No links, to [[Nothing there]].")
    graph = vault.graph()
    assert {n["title"]: n["links"] for n in graph["nodes"]} == {"A": 2, "B": 2, "C": 2, "D": 1, "E": 1, "Alone": 0, "The Almanac": 2}
    book = "b-20261118-101500-almanac"
    assert sorted(map(tuple, graph["edges"])) == sorted(tuple(sorted(pair)) for pair in
                                                        [(a["id"], b["id"]), (b["id"], c["id"]), (c["id"], d["id"]), (a["id"], book), (e["id"], book)])
    near = vault.neighbourhood(a["id"])
    assert near["centre"] == a["id"] and {n["title"]: n["step"] for n in near["nodes"]} == {"A": 0, "B": 1, "The Almanac": 1, "C": 2}
    assert near["more"] and len(near["edges"]) == 3                               # D lies further; E is only reached through the book
    assert {n["title"] for n in vault.neighbourhood(book)["nodes"]} == {"The Almanac", "A", "E", "B"}  # from the book itself, its notes and theirs
    assert vault.neighbourhood(alone["id"]) == {"centre": alone["id"], "nodes": [{"id": alone["id"], "title": "Alone", "kind": "idea", "untitled": False, "step": 0}],
                                                "edges": [], "more": False}
    assert len(vault.neighbourhood(a["id"], most=2)["nodes"]) == 2
    with pytest.raises(V.Missing):
        vault.neighbourhood("n00000000")


def test_unlinked_and_old_notes_are_brought_back_a_few_each_day(vault):
    vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK)
    linked = vault.create("Linked", "To [[Other]].")
    other = vault.create("Other", "x")
    cites = vault.create("Cites only", "From [[The Almanac#^h3f9a]].")           # a source is not a link to another idea
    alone = vault.create("Alone", "x")
    vault.create("", "a quick thought", "quick")                                   # not an idea yet: not asked about
    vault.create("A project", "", "project")
    old = (dt.datetime(2026, 9, 1) - dt.datetime(1970, 1, 1)).total_seconds()
    os.utime(vault.root / "Other.md", (old, old))
    vault.refresh(force=True)
    got = vault.revisit(dt.date(2026, 11, 19))
    assert sorted(n["title"] for n in got["unlinked"]) == ["Alone", "Cites only"] and [n["title"] for n in got["old"]] == ["Other"]
    assert sorted(n["title"] for n in got["today"]) == ["Alone", "Cites only", "Other"] and got["old_days"] == 30
    assert {n["title"]: n["why"] for n in got["today"]} == {"Alone": "unlinked", "Cites only": "unlinked", "Other": "old"}
    two = vault.revisit(dt.date(2026, 11, 19), pick=2)["today"]
    assert two == vault.revisit(dt.date(2026, 11, 19), pick=2)["today"]           # the same all day
    picks = {tuple(n["id"] for n in vault.revisit(dt.date(2026, 11, day), pick=1)["today"]) for day in range(1, 29)}
    assert len(picks) == 3                                                         # and other notes on other days
    assert vault.revisit(dt.date(2026, 9, 15))["old"] == [] and linked["id"] not in {n["id"] for n in got["today"]}
    assert Vault(vault.root / "none").revisit() == {"unlinked": [], "old": [], "old_days": 30, "today": []}


def test_no_line_pattern_takes_long_on_a_long_run_of_one_character():
    """A heading line with 2,000 spaces took 11 seconds, and nothing else ran meanwhile. Every pattern that reads
    a line of a note, a text file or a Markdown file is tried on long runs of the characters that have a meaning."""
    import re
    import time

    from ebook_processor import bookfile
    from ebook_processor.sources import from_markdown, from_text
    assert V.head_of("## A title ##  ") == (2, "A title") and V.head_of("   ### Deep") == (3, "Deep")
    assert V.head_of("# ") == (1, "") and V.head_of("#no") is None and V.head_of("    # code") is None
    assert V.head_of("# C# and F#") == (1, "C# and F") and V.head_of("# a  b") == (1, "a  b")
    assert from_text._marked("# One #") == "One" and from_text._marked("One") == "" and from_text._marked("#") == ""
    n = 50000
    hard = ["# a" + " " * n + "b", "# a" + "\t" * n + "b #", " " * n, "[" * n, "[[" * (n // 2), "#" * n, "- " * (n // 2),
            "a " * (n // 2) + "^x", "*" * n, "\\" * n, "`" * n, "![[" * (n // 3), "|" * n]
    patterns = [value for module in (V, bookfile, from_text, from_markdown) for value in vars(module).values()
                if isinstance(value, re.Pattern)]
    assert len(patterns) > 10
    began = time.monotonic()
    for line in hard:
        V.head_of(line)
        from_text._marked(line)
        for pattern in patterns:
            pattern.search(line)
    assert time.monotonic() - began < 4  # about 0.3 s here; the old heading pattern alone would take hours


def test_a_title_is_limited_by_the_bytes_of_its_file_name():
    long = "雪" * 100
    assert "bytes" in V.title_problem(long) and V.title_problem("雪" * 60) is None
    made = V.safe_title(long)
    assert made and V.title_problem(made) is None and len(made.encode("utf-8")) <= V.TITLE_BYTES


def test_the_folder_is_listed_in_the_order_of_the_names_without_hidden_things(tmp_path):
    """The listing was rewritten for speed; what it gives must be what it gave."""
    root = tmp_path / "notes"
    for rel in ("b.md", "A.MD", "sub/z.md", "sub/a.md", "sub/deep/n.md", ".hidden/x.md", ".dot.md", "not a note.txt", "aa/first.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("text\n", encoding="utf-8")
    vault = Vault(root)
    listed = [rel for rel, _ in vault._listed()]
    assert listed == ["A.MD", "b.md", "aa/first.md", "sub/a.md", "sub/z.md", "sub/deep/n.md"]
    assert [p.relative_to(root).as_posix() for p in vault._files()] == listed
    vault.refresh(force=True)
    assert sorted(vault.notes) == sorted(listed) and all(vault.notes[rel].stamp == stamp for rel, stamp in vault._listed())
    assert vault.notes["b.md"].stamp == Vault._stamp(root / "b.md")   # the two ways of asking for a stamp agree


def test_search_makes_a_snippet_for_the_notes_it_shows_only(tmp_path, monkeypatch):
    root = tmp_path / "notes"
    root.mkdir()
    for n in range(80):
        (root / f"Note {n:02d}.md").write_text(f"The lamp is lit at dusk, note {n}.\n" + "More words. " * 50, encoding="utf-8")
    vault = Vault(root)
    made: list[int] = []
    real = V._snippet
    monkeypatch.setattr(V, "_snippet", lambda *a: (made.append(1), real(*a))[1])
    found = vault.search("lamp", limit=10)
    assert len(found["notes"]) == 10 and found["more"] and all("lamp" in n["snippet"] for n in found["notes"]) and len(made) == 10
