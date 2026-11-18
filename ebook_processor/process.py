"""Processing: what was marked in a book, as a list of items to make something of.

An item is a highlight (with the note on it, if it has one) or a note written in the book: on a paragraph, a
section, a chapter or the book. Each item ends in one of three ways:

    processed    a note in the notes folder cites it. This is not stored: it is read from the notes' links, so
                 a citation made anywhere counts, and an item whose citing note is gone waits again
    set aside    the reader does not want to make anything of it. Stored here, and it can be brought back
    waiting      neither

``process.json`` in the book's folder holds what was set aside:

    {"version": 1, "aside": {"h3f9a": "2026-11-18T15:30:00Z"}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import notes
from .pipeline import now
from .reading import ReadingBook

FILE = "process.json"
VERSION = 1


def load(folder: Path) -> dict[str, Any]:
    path = folder / FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == VERSION and isinstance(data.get("aside"), dict):
                return data
        except ValueError:
            pass
    return {"version": VERSION, "aside": {}}


def items(book: ReadingBook, store: dict[str, Any]) -> list[dict[str, Any]]:
    """The book's items in reading order. ``block`` is the item's id in the book's file; ``words`` is what an
    idea note started from the item begins with: the reader's own words, where there are any."""
    data = notes.notebook(book, store, None)
    out: list[dict[str, Any]] = []

    def add(row: dict[str, Any], kind: str, chapter: dict[str, Any], section: dict[str, Any] | None, go: Any) -> None:
        mine = row.get("note") if kind == "highlight" else row["text"]
        out.append({"id": row["id"], "block": row["id"], "kind": kind, "text": row["text"] if kind == "highlight" else "",
                    "note": mine or "", "words": mine or "", "created": row.get("created") or "",
                    "chapter": chapter["index"] if chapter else None, "chapter_title": chapter["title"] if chapter else "",
                    "section": section["id"] if section else "", "section_label": section["label"] if section else "", "go": go})

    def rows(found: list[dict[str, Any]], chapter: dict[str, Any], section: dict[str, Any] | None) -> None:
        for i in found:
            if i["type"] == "highlight":
                add(i, "highlight", chapter, section, {"chapter": i["chapter"], "word": i["start"]})
            elif i["type"] == "note":
                add(i, "note", chapter, section, {"chapter": i["chapter"], "word": i["word"]})

    if data["note"]:
        add(data["note"], "book-note", {}, None, None)
    for c in data["chapters"]:
        if c["note"]:
            add(c["note"], "chapter-note", c, None, {"chapter": c["index"], "word": 0})
        rows(c["loose"], c, None)
        for s in c["sections"]:
            if s["note"]:
                add(s["note"], "section-note", c, s, {"chapter": c["index"], "word": s["start"]})
            rows(s["items"], c, s)
    return out


def set_aside(state: dict[str, Any], ids: list[str], aside: bool) -> None:
    for item in ids:
        if aside:
            state["aside"].setdefault(item, now())
        else:
            state["aside"].pop(item, None)


def tidy(state: dict[str, Any], known: set[str]) -> bool:
    """Forget what was set aside and is no longer in the book. True when something was forgotten."""
    gone = [i for i in state["aside"] if i not in known]
    for i in gone:
        del state["aside"][i]
    return bool(gone)
