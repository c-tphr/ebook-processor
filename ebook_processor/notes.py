"""Highlights and notes on a book, and the notebook made from them.

One file per book, ``notes.json`` beside ``reading.json``:

    {"version": 1,
     "highlights": [{"id": "h3f9a", "chapter": 3, "start": 120, "end": 141, "text": "the words", "note": "...",
                     "created": "...", "updated": "..."}],
     "notes": [{"id": "n81c2", "on": "place", "chapter": 3, "word": 200, "text": "...", ...},
               {"id": "n77aa", "on": "section", "section": "s0007", "text": "...", ...},
               {"id": "n01bd", "on": "chapter", "chapter": 3, "text": "...", ...},
               {"id": "n5e10", "on": "book", "text": "...", ...}]}

A highlight is a run of words of one chapter, ``[start, end)`` in the chapter's word stream: the positions the
reader's three views share. They do not move when a book is processed again, because processing never changes the
text. Highlights that touch or overlap become one. Notes are Markdown. There is at most one note on a section, on
a chapter and on the book; a note at a place belongs to the paragraph that starts at its word.

The **notebook** is the book's outline (chapters and sections) with, under each section, the reader's note on it,
the highlights and notes in reading order, and the answers given at its checkpoint.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from . import forms as qforms
from .store import load_json, now
from .reading import ReadingBook

FILE = "notes.json"
VERSION = 1
MAX_NOTE = 20000  # characters
MAX_HIGHLIGHT_WORDS = 2000
TARGETS = ("place", "section", "chapter", "book")


class NotesError(ValueError):
    """A request about highlights or notes cannot be carried out; the message says why."""


def load(folder: Path) -> dict[str, Any]:
    return load_json(folder / FILE, VERSION, lambda: {"version": VERSION, "highlights": [], "notes": []},
                     lambda d: isinstance(d.get("highlights"), list) and isinstance(d.get("notes"), list))


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise NotesError("a note is text")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_NOTE:
        raise NotesError(f"a note can hold {MAX_NOTE:,} characters; this one has {len(text):,}")
    return text


def _words(book: ReadingBook, chapter: Any) -> list[str]:
    if not isinstance(chapter, int) or isinstance(chapter, bool):
        raise NotesError("chapter must be a number")
    doc = next((sd for sd in book.book.spine if sd.index == chapter and sd.doc is not None), None)
    if doc is None:
        raise NotesError(f"the book has no chapter {chapter}")
    return doc.words


def add_highlight(store: dict[str, Any], book: ReadingBook, chapter: Any, start: Any, end: Any,
                  note: Any = None) -> dict[str, Any]:
    """Highlight words ``[start, end)`` of a chapter, joining any highlight it touches. Returns the highlight."""
    words = _words(book, chapter)
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (start, end)) or not 0 <= start < end <= len(words):
        raise NotesError("a highlight needs a start and an end inside the chapter")
    if end - start > MAX_HIGHLIGHT_WORDS:
        raise NotesError(f"a highlight can cover {MAX_HIGHLIGHT_WORDS:,} words at most")
    text = _text(note) if note is not None else ""
    stamp = now()
    touching = [h for h in store["highlights"] if h["chapter"] == chapter and h["start"] <= end and start <= h["end"]]
    created = min([h["created"] for h in touching], default=stamp)
    notes = [h["note"] for h in sorted(touching, key=lambda h: h["start"]) if h["note"]] + ([text] if text else [])
    start, end = min([start, *(h["start"] for h in touching)]), max([end, *(h["end"] for h in touching)])
    merged = {"id": touching[0]["id"] if touching else "h" + secrets.token_hex(4), "chapter": chapter, "start": start,
              "end": end, "text": " ".join(words[start:end]), "note": "\n\n".join(notes), "created": created,
              "updated": stamp}
    store["highlights"] = [h for h in store["highlights"] if h not in touching] + [merged]
    store["highlights"].sort(key=lambda h: (h["chapter"], h["start"]))
    if len(touching) > 1 and store.get("questions"):  # highlights that became one: their questions go with it
        from . import highlight_questions
        highlight_questions.highlights_joined(store, [h["id"] for h in touching], merged["id"])
    return merged


def set_note(store: dict[str, Any], book: ReadingBook, target: dict[str, Any], text: Any) -> dict[str, Any] | None:
    """Write (or, with empty text, remove) the note on a place, a section, a chapter or the book."""
    on = target.get("on")
    if on not in TARGETS:
        raise NotesError("a note goes on a place, a section, a chapter or the book")
    key: dict[str, Any] = {"on": on}
    if on == "place":
        words = _words(book, target.get("chapter"))
        word = target.get("word")
        if not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= len(words):
            raise NotesError("a note at a place needs a word inside the chapter")
        key.update(chapter=target["chapter"], word=word)
    elif on == "chapter":
        _words(book, target.get("chapter"))
        key.update(chapter=target["chapter"])
    elif on == "section":
        if target.get("section") not in {s["id"] for s in book.data["sections"]}:
            raise NotesError("the book has no such section")
        key.update(section=target["section"])
    body = _text(text)
    existing = next((n for n in store["notes"] if all(n.get(k) == v for k, v in key.items())), None)
    if not body:
        if existing is not None:
            store["notes"].remove(existing)
        return None
    stamp = now()
    if existing is not None:
        existing.update(text=body, updated=stamp)
        return existing
    note = {"id": "n" + secrets.token_hex(4), **key, "text": body, "created": stamp, "updated": stamp}
    store["notes"].append(note)
    return note


def update_item(store: dict[str, Any], item_id: str, text: Any) -> dict[str, Any]:
    """Change the note of a highlight, or the text of a note. An emptied note is removed; a highlight stays."""
    body = _text(text)
    for h in store["highlights"]:
        if h["id"] == item_id:
            h.update(note=body, updated=now())
            return h
    for n in store["notes"]:
        if n["id"] == item_id:
            if not body:
                store["notes"].remove(n)
            else:
                n.update(text=body, updated=now())
            return n
    raise NotesError("no such highlight or note")


def remove_item(store: dict[str, Any], item_id: str) -> None:
    before = len(store["highlights"]) + len(store["notes"])
    store["highlights"] = [h for h in store["highlights"] if h["id"] != item_id]
    store["notes"] = [n for n in store["notes"] if n["id"] != item_id]
    if len(store["highlights"]) + len(store["notes"]) == before:
        raise NotesError("no such highlight or note")


def marked(store: dict[str, Any], chapter: int, start: int, end: int) -> list[tuple[int, int]]:
    """The highlighted stretches inside words ``[start, end)`` of a chapter."""
    return [(max(start, h["start"]), min(end, h["end"])) for h in store["highlights"]
            if h["chapter"] == chapter and h["start"] < end and start < h["end"]]


# --------------------------------------------------------------------------
# The notebook


def notebook(book: ReadingBook, store: dict[str, Any], reading_state: dict[str, Any] | None,
             sources: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """``sources`` gives, for a question id, the place of the passage its answer comes from. A question without
    one points at the start of its section."""
    manifest = book.manifest()
    sources = sources or {}
    grades = (reading_state or {}).get("grades") or {}
    shown = (reading_state or {}).get("x_previewed") or {}

    def previewed(pid: str) -> bool:
        """The question was shown ahead of its section before it was first graded (or has no grade yet)."""
        return pid in shown and (pid not in grades or shown[pid] <= grades[pid].get("at", ""))

    attempts: dict[str, list[dict[str, Any]]] = {}
    for a in (reading_state or {}).get("x_attempts") or []:
        attempts.setdefault(a["id"], []).append({"at": a["at"], "grade": a["grade"], "text": a.get("text") or ""})

    def asked(q: dict[str, Any]) -> dict[str, Any]:
        """A question made from a highlight, with what the reader has answered to it so far."""
        return {**{k: q[k] for k in ("id", "question", "answer", "status", "by", "problem", "ask", "text", "chapter")},
                **qforms.members(q),
                "word": q["start"], "first": grades.get(q["id"], {}).get("first"), "attempts": attempts.get(q["id"], [])}

    made = store.get("questions") or []
    of_highlight: dict[str, list[dict[str, Any]]] = {}
    for q in made:
        if q["highlight"]:
            of_highlight.setdefault(q["highlight"], []).append(asked(q))
    on = {t: {} for t in ("section", "chapter")}
    for n in store["notes"]:
        if n["on"] in on:
            on[n["on"]][n[n["on"]]] = n
    chapters = []
    for c in manifest["chapters"]:
        sections = [s for s in manifest["sections"] if s["chapter"] == c["index"]]
        items = sorted(
            [{"type": "highlight", "word": h["start"], **h, "questions": of_highlight.get(h["id"], [])}
             for h in store["highlights"] if h["chapter"] == c["index"]]
            + [{"type": "note", **n} for n in store["notes"] if n["on"] == "place" and n["chapter"] == c["index"]]
            # a question kept after its highlight was removed stands where the highlight was, with its words
            + [{"type": "question", **asked(q)} for q in made if not q["highlight"] and q["chapter"] == c["index"]],
            key=lambda i: (i["word"], i["type"]))
        rows, used = [], set()
        for s in sections:
            mine = [i for i in items if s["start"] <= i["word"] < s["end"]]
            used.update(i["id"] for i in mine)
            answers = [{"id": p["id"], **qforms.as_question(p),
                        "first": grades.get(p["id"], {}).get("first"), "attempts": attempts.get(p["id"], []),
                        "previewed": previewed(p["id"]),
                        "go": sources.get(p["id"]) or {"chapter": c["index"], "word": s["start"]}}
                       for p in s["prompts"] if not p.get("hq") and (p["id"] in grades or p["id"] in attempts)]
            rows.append({"id": s["id"], "label": s["label"], "start": s["start"], "end": s["end"],
                         "note": on["section"].get(s["id"]), "items": mine, "answers": answers})
        chapters.append({"index": c["index"], "title": c["title"], "note": on["chapter"].get(c["index"]),
                         "sections": rows, "loose": [i for i in items if i["id"] not in used]})
    counts = {"highlights": len(store["highlights"]), "notes": len(store["notes"]) + sum(bool(h["note"]) for h in store["highlights"]),
              "answers": sum(len(s["answers"]) for c in chapters for s in c["sections"]),
              "questions": sum(q["status"] == "accepted" for q in made), "proposals": sum(q["status"] != "accepted" for q in made)}
    return {"title": manifest["title"], "authors": manifest["authors"], "counts": counts,
            "note": next((n for n in store["notes"] if n["on"] == "book"), None), "chapters": chapters}


def _quote(text: str) -> list[str]:
    return ["> " + line if line else ">" for line in text.split("\n")]


def markdown(data: dict[str, Any], answers: bool = True) -> str:
    """The notebook as one Markdown file. Sections and chapters with nothing written or marked are left out."""
    lines = [f"# {data['title']}", ""]
    if data["authors"]:
        lines += [", ".join(data["authors"]), ""]
    if data["note"]:
        lines += [data["note"]["text"], ""]

    def asked(rows: list[dict[str, Any]]) -> list[str]:
        """The accepted questions made from a highlight. Proposals are not part of the notebook yet."""
        out: list[str] = []
        for q in rows:
            if q["status"] != "accepted":
                continue
            state = {"correct": "recalled", "incorrect": "missed"}.get(q["first"] or "", "not asked yet")
            asked, reference = qforms.as_text(q, q["question"], q["answer"])
            out += [f"- **{asked}** ({state})", "  - Reference: " + reference.replace("\n", " ")]
        return out + [""] if out else out

    def items(rows: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for i in rows:
            if i["type"] == "highlight":
                out += _quote(i["text"]) + [""]
                if i["note"]:
                    out += [i["note"], ""]
                out += asked(i.get("questions") or [])
            elif i["type"] == "question":
                if i["status"] == "accepted":
                    out += _quote(i["text"]) + [""] + asked([i])
            else:
                out += [i["text"], ""]
        return out

    for c in data["chapters"]:
        body: list[str] = []
        if c["note"]:
            body += [c["note"]["text"], ""]
        body += items(c["loose"])
        for s in c["sections"]:
            part: list[str] = []
            if s["note"]:
                part += [s["note"]["text"], ""]
            part += items(s["items"])
            if answers and s["answers"]:
                part += ["**Checkpoint**", ""]
                for a in s["answers"]:
                    grade = {"correct": "recalled", "incorrect": "missed"}.get(a["first"] or "", "not graded")
                    asked, reference = qforms.as_text(a, a["question"], a["answer"])
                    part += [f"- **{asked}** ({grade}{', previewed' if a.get('previewed') else ''})"]
                    typed = next((t["text"] for t in a["attempts"] if t["text"]), "")
                    if typed:
                        part += ["  - Answered: " + typed.replace("\n", " ")]
                    part += ["  - Reference: " + reference.replace("\n", " ")]
                part += [""]
            if part:
                single = len(c["sections"]) == 1 and s["label"] == c["title"]
                body += ([] if single else [f"### {s['label']}", ""]) + part
        if body:
            lines += [f"## {c['title']}", ""] + body
    return "\n".join(lines).rstrip() + "\n"
