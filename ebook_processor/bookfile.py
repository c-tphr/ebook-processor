"""A book's file in the notes folder: what the reader marked and wrote in the book, as Markdown.

    Books/The Lighthouse Keeper's Almanac.md

    ---
    id: b-<book id>
    kind: book
    title: The Lighthouse Keeper's Almanac
    authors: [Mara Voss]
    ---
    > [!info] This file is kept by ebook-processor. Edits made here are overwritten.

    A note on the book. ^n5e10

    ## The Lamp Room

    ### Lighting

    > The lamp is lit at sunset and put out at sunrise. ^h3f9a

    A note on that highlight. ^h3f9a-note

    A note on a paragraph. ^n81c2

The file is made from the book's ``notes.json`` each time that changes; it is never read back. It holds only
highlights and the reader's own notes, in reading order under the book's chapters and sections. Every entry ends
with a block id, which is its id in ``notes.json``: an idea note cites one highlight as
``[[The Lighthouse Keeper's Almanac#^h3f9a]]``, in the app and in Obsidian.
"""

from __future__ import annotations

import re
from typing import Any

from . import notes, vault
from .frontmatter import Prop, compose, parse_props, split
from .reading import ReadingBook

KEPT = "> [!info] This file is kept by ebook-processor. Edits made here are overwritten."
REMOVED = ("> [!warning] This book is no longer in the library of ebook-processor. "
           "Its highlights and notes stay here as they were.")
STRUCTURE_RE = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|>|```|~~~|\|)")


def book_id(job_id: str) -> str:
    return "b-" + job_id


def job_of(note_id: str) -> str | None:
    return note_id[2:] if note_id.startswith("b-") else None


def _escape(text: str) -> str:
    """A book's own words, so that Markdown shows them as they are: no link, tag or emphasis is made of them."""
    return re.sub(r"#(?=\S)", r"\\#", re.sub(r"([\\`*\[<])", r"\\\1", " ".join(text.split())))


def _one_line(text: str) -> str:
    return " ".join(text.split()) or "Untitled"


def _entry(text: str, block: str) -> list[str]:
    """Something the reader wrote, ending with its block id."""
    lines = []
    for line in text.strip().split("\n"):
        head = vault.head_of(line)
        lines.append(f"**{head[1]}**" if head and head[1] else line)  # the file's headings are the book's
    last = lines[-1] if lines else ""
    if not last.strip():
        return []
    fenced = sum(1 for line in lines if vault.FENCE_RE.match(line)) % 2 == 1 or bool(vault.FENCE_RE.match(last))
    if fenced or STRUCTURE_RE.match(last) or vault.BLOCK_RE.search(last):
        return lines + ["", f"^{block}", ""]  # after a list, a quote or code, the id stands on a line of its own
    return lines[:-1] + [f"{last.rstrip()} ^{block}", ""]


def text(job_id: str, book: ReadingBook, store: dict[str, Any], kind: str = "book",
         origin: dict[str, Any] | None = None) -> str:
    """The file of one book or article. ``origin`` says where an article came from; what is known of it goes into
    the file's properties."""
    data = notes.notebook(book, store, None)
    props = [Prop("id", book_id(job_id)), Prop("kind", kind), Prop("title", _one_line(data["title"]))]
    if data["authors"]:
        props.append(Prop("authors", [_one_line(a) for a in data["authors"]]))
    for key in ("url", "site", "published", "fetched"):
        value = (origin or {}).get(key)
        if isinstance(value, str) and value.strip():
            props.append(Prop(key, _one_line(value)))
    lines = [KEPT, ""]
    if data["note"]:
        lines += _entry(data["note"]["text"], data["note"]["id"])

    def items(rows: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for i in rows:
            if i["type"] == "highlight":
                out += [f"> {_escape(i['text'])} ^{i['id']}", ""]
                if i["note"]:
                    out += _entry(i["note"], i["id"] + vault.NOTE_END)
            elif i["type"] == "note":
                out += _entry(i["text"], i["id"])
        return out

    for c in data["chapters"]:
        body: list[str] = []
        if c["note"]:
            body += _entry(c["note"]["text"], c["note"]["id"])
        body += items(c["loose"])
        for s in c["sections"]:
            part = (_entry(s["note"]["text"], s["note"]["id"]) if s["note"] else []) + items(s["items"])
            if part:
                body += ([] if notes.single_section(c, s) else [f"### {_escape(_one_line(s['label']))}", ""]) + part
        if body:
            lines += [f"## {_escape(_one_line(c['title']))}", ""] + body
    return compose(props, "\n".join(lines).rstrip() + "\n")


def mark_removed(current: str) -> str:
    """The file of a book that left the library: as it was, and saying so at the top."""
    if REMOVED in current:
        return current
    if KEPT in current:
        return current.replace(KEPT, REMOVED, 1)
    front, body = split(current)
    return compose(parse_props(front) if front is not None else [], REMOVED + "\n\n" + body.lstrip("\n"))


def place(store: dict[str, Any], block: str, sections: list[dict[str, Any]] | None = None) -> dict[str, int] | None:
    """Where in the book the entry with this block id is, to open the reader there. ``sections`` are those of
    the book's manifest: with them, a note on a section or a chapter leads to where that begins."""
    wanted = vault.entry_of(block)
    for h in store["highlights"]:
        if h["id"] == wanted:
            return {"chapter": h["chapter"], "word": h["start"]}
    for n in store["notes"]:
        if n["id"] != wanted:
            continue
        if n["on"] == "place":
            return {"chapter": n["chapter"], "word": n["word"]}
        if n["on"] == "chapter":
            return {"chapter": n["chapter"], "word": 0}
        section = next((s for s in sections or [] if s["id"] == n.get("section")), None)
        if n["on"] == "section" and section is not None:
            return {"chapter": section["chapter"], "word": section["start"]}
    return None
