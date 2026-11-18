"""The questions on their own: structured JSON, an Anki deck and a tab-separated file for other apps."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from html import escape
from pathlib import Path
from typing import Any

from . import forms as qforms
from . import __version__
from .content import passage_text
from .pipeline import KEPT as KEPT_STATUSES, BookIndex, kept, now
from .prep.epub import DC_NS, Book, SpineDoc, squash

QUESTIONS_FORMAT = "ebook-processor-questions"
QUESTIONS_VERSION = 1
ANKI_MODEL_ID = 1702331501  # fixed, so that every deck made by this tool shares one note type
TSV_COLUMNS = ("id", "question", "answer", "book", "chapter", "section", "passage", "tags")

LEFT_OUT_REASONS = {
    "failed": "It still failed the check after the last revision.",
    "withdrawn": "The drafter withdrew it after the check failed it.",
    "removed": "You removed it.",
}


def chapter_titles(book: Book) -> dict[str, str]:
    """A name for each spine document: its table-of-contents label, else its <title>, else its first heading."""
    out: dict[str, str] = {}
    for entry in book.toc:
        href = entry.href.split("#")[0]
        if href and href not in out and entry.label.strip():
            out[href] = squash(entry.label)
    for sd in book.spine:
        if sd.href in out:
            continue
        heading = next((b for b in sd.blocks if b.kind == "heading" and b.words), None)
        out[sd.href] = sd.title or (" ".join(heading.words) if heading else sd.href.rsplit("/", 1)[-1])
    return out


def authors(book: Book) -> list[str]:
    return [squash(el.text()) for el in book.opf.root.find_all(DC_NS, "creator") if squash(el.text())]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "book"


def left_out_reason(q: dict[str, Any]) -> str:
    if q["status"] in KEPT_STATUSES and q.get("removed"):
        return LEFT_OUT_REASONS["removed"]
    return LEFT_OUT_REASONS.get(q["status"], q["status"])



CARD_CHOICES = ("all", "missed", "reached")


def card_text(q: dict[str, Any]) -> tuple[str, str, list[str]]:
    """A question of the questions document as a card for a program that knows only a front and a back: the
    front, the back, and the choices that belong on the front of a multiple-choice card."""
    front, back = qforms.as_text(q, q["question"], q["answer"])
    return front, back, list(q["choices"]) if qforms.form_of(q) == "choice" else []


def selected_cards(doc: dict[str, Any], cards: str = "all") -> list[dict[str, Any]]:
    """The questions that go into the deck and the text file: all, those missed on the first try, or those met."""
    if cards == "missed":
        return [q for q in doc["questions"] if q.get("result") == "missed"]
    if cards == "reached":
        return [q for q in doc["questions"] if q.get("reached")]
    return doc["questions"]


def questions_document(book: Book, state: dict[str, Any], results: dict[str, str] | None = None,
                       reached: set[str] | None = None) -> dict[str, Any]:
    """``results`` (question id -> correct or missed) and ``reached`` come from reading the book in the app."""
    results, reached = results or {}, reached or set()
    index = BookIndex(book)
    titles = chapter_titles(book)
    book_tag = slug(state["source"]["title"])
    sections = {}
    section_rows = []
    for order, s in enumerate(state["sections"], start=1):
        sd, first, last = index.span(s)
        blocks = sd.blocks[first : last + 1]
        sections[s["id"]] = (s, sd)
        section_rows.append({
            "id": s["id"], "order": order, "chapter_href": s["chapter_href"], "chapter": titles[sd.href],
            "label": s["label"], "first_paragraph": s["first_paragraph"], "last_paragraph": s["last_paragraph"],
            "word_count": blocks[-1].end - blocks[0].start, "checkpoint": s["checkpoint"],
            "natural_boundary": s["natural_boundary"], "boundary_note": s["boundary_note"],
        })
    questions, left_out = [], []
    for q in state["questions"]:
        s, sd = sections[q["section"]]
        base = {"id": q["id"], "checkpoint": s["checkpoint"], "section": s["id"], "section_label": s["label"],
                "chapter_href": sd.href, "chapter": titles[sd.href], "question": q["prompt"], "answer": q["answer"],
                "form": qforms.form_of(q), **{k: v for k, v in qforms.members(q).items() if k != "form"}}
        if not kept(q):
            left_out.append({**base, "status": "removed" if q.get("removed") and q["status"] in KEPT_STATUSES
                             else q["status"], "reason": left_out_reason(q), "problem": q["problem"],
                             "failed_criteria": [c for c, ok in q["criteria"].items() if not ok],
                             "history": q["history"]})
            continue
        passage = []
        for ref in q["supporting_paragraphs"]:
            doc, at = index.where[ref]
            passage.append({"ref": ref, "text": passage_text(doc, doc.blocks[at])})
        questions.append({
            **base, "order": len(questions) + 1, "supporting_paragraphs": q["supporting_paragraphs"],
            "passage": passage, "status": q["status"],
            "check": {"criteria": q["criteria"], "citations": q["citations"], "evidence": q["evidence"],
                      "minor_note": q["minor_note"], "times_checked": q["checks"],
                      "revisions": len(q["history"])},
            "result": results.get(q["id"]), "reached": q["id"] in reached or q["id"] in results,
            "tags": [book_tag, f"{book_tag}::ch{sd.index:02d}"]
            + ([f"recall::{results[q['id']]}"] if q["id"] in results else ["recall::unseen"] if results else []),
        })
    return {
        "format": QUESTIONS_FORMAT,
        "version": QUESTIONS_VERSION,
        "generated": now(),
        "tool": {"name": "ebook-processor", "version": __version__,
                 "instructions_version": state["instructions_version"]},
        "run_id": state["run_id"],
        "book": {"title": state["source"]["title"], "authors": authors(book), "language": state["source"]["language"],
                 "source_file": state["source"]["file"], "source_sha256": state["source"]["sha256"],
                 "words": state["source"]["words"]},
        "settings": state["settings"],
        "sections": section_rows,
        "questions": questions,
        "left_out": left_out,
        "excluded": [{k: e[k] for k in ("chapter_href", "first_paragraph", "last_paragraph", "reason")}
                     for e in state["excluded"]],
    }


def add_from_highlights(doc: dict[str, Any], book: Book, asked: list[dict[str, Any]], results: dict[str, str] | None = None,
                        reached: set[str] | None = None) -> int:
    """Add the reader's accepted questions from highlights to the questions document, after the book's own.
    Their passage is the paragraphs the highlight lies in. Returns how many were added."""
    results, reached = results or {}, reached or set()
    titles = chapter_titles(book)
    book_tag = slug(doc["book"]["title"])
    sections = {s["id"]: s for s in doc["sections"]}
    added = 0
    for q in asked:
        sd = next((d for d in book.spine if d.index == q["chapter"] and d.doc is not None), None)
        if sd is None:
            continue
        blocks = [b for b in sd.blocks if b.start < q["end"] and q["start"] < b.end]
        section = sections.get(q["section"])
        checked = q.get("checked") or {}
        doc["questions"].append({
            "id": q["id"], "order": len(doc["questions"]) + 1,
            "checkpoint": section["checkpoint"] if section and q["ask"] == "checkpoint" else None,
            "section": section["id"] if section else None, "section_label": section["label"] if section else "",
            "chapter_href": sd.href, "chapter": titles[sd.href], "question": q["question"], "answer": q["answer"],
            "form": qforms.form_of(q), **{k: v for k, v in qforms.members(q).items() if k != "form"},
            "supporting_paragraphs": [b.ref for b in blocks],
            "passage": [{"ref": b.ref, "text": passage_text(sd, b)} for b in blocks],
            "status": "passed" if checked.get("passed") else "unchecked",
            "check": {"criteria": checked.get("criteria") or {}, "citations": [], "evidence": "", "minor_note": "",
                      "times_checked": 1 if checked else 0, "revisions": 0},
            "origin": "highlight", "highlight": q["text"],
            "result": results.get(q["id"]), "reached": q["id"] in reached or q["id"] in results,
            "tags": [book_tag, f"{book_tag}::ch{sd.index:02d}", "from::highlight"]
            + ([f"recall::{results[q['id']]}"] if q["id"] in results else ["recall::unseen"] if results else []),
        })
        added += 1
    return added


def _passage_plain(q: dict[str, Any]) -> str:
    return "\n\n".join(p["text"] for p in q["passage"])


def tsv_text(doc: dict[str, Any], cards: str = "all") -> str:
    """One row per question, tab-separated, with a header row. Fields that hold a line break are quoted."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(TSV_COLUMNS)
    for q in selected_cards(doc, cards):
        front, back, choices = card_text(q)
        front += "".join(f"\n{'ABCD'[n]}. {c}" for n, c in enumerate(choices))
        writer.writerow([q["id"], front, back, doc["book"]["title"], q["chapter"], q["section_label"],
                         _passage_plain(q), " ".join(q["tags"])])
    return out.getvalue()


def _html(text: str) -> str:
    return escape(text, quote=False).replace("\n", "<br>")


ANKI_CSS = """\
.card { font-family: Georgia, "Iowan Old Style", "Times New Roman", serif; font-size: 20px; line-height: 1.45;
  text-align: left; color: #1c1b19; background: #fbfaf7; max-width: 34em; margin: 0 auto; padding: 1em; }
.src { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 0.6em; letter-spacing: 0.06em;
  text-transform: uppercase; color: #6f6a60; margin-bottom: 1.2em; }
.q { font-weight: bold; }
.a { margin-top: 0.4em; }
.where { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 0.65em; color: #6f6a60; margin-top: 1.6em; }
.passage { font-size: 0.8em; color: #45413a; border-left: 3px solid #d9d4c7; padding-left: 0.8em; margin-top: 0.6em; }
.nightMode.card, .night_mode .card { color: #e9e6df; background: #1f1e1c; }
.nightMode .src, .nightMode .where, .night_mode .src, .night_mode .where { color: #a39d90; }
.nightMode .passage, .night_mode .passage { color: #cfcabd; border-left-color: #55514a; }
"""
ANKI_FRONT = '<div class="src">{{Book}} · {{Chapter}}</div>\n<div class="q">{{Question}}</div>'
ANKI_BACK = ('{{FrontSide}}\n<hr id="answer">\n<div class="a">{{Answer}}</div>\n'
             '<div class="where">{{Section}}</div>\n'
             '{{#Passage}}<div class="passage">{{Passage}}</div>{{/Passage}}')


def write_anki(doc: dict[str, Any], path: Path, cards: str = "all") -> int:
    """Write the deck; return the number of notes. Needs the ``genanki`` package."""
    import genanki

    model = genanki.Model(
        ANKI_MODEL_ID, "Book checkpoint (ebook-processor)",
        fields=[{"name": n} for n in ("Question", "Answer", "Book", "Chapter", "Section", "Passage", "Id")],
        templates=[{"name": "Recall", "qfmt": ANKI_FRONT, "afmt": ANKI_BACK}], css=ANKI_CSS)
    source = doc["book"]["source_sha256"]
    deck_id = (int(hashlib.sha256(source.encode("ascii")).hexdigest()[:8], 16) % (1 << 30)) + (1 << 30)
    deck = genanki.Deck(deck_id, "Books::" + doc["book"]["title"].replace("::", ": "),
                        description=f"Retrieval-practice questions for {doc['book']['title']}, made by "
                                    "ebook-processor.")
    for q in selected_cards(doc, cards):
        passage = "<br><br>".join(_html(p["text"]) for p in q["passage"])
        front, back, choices = card_text(q)
        front = _html(front) + ("<ol type=\"A\">" + "".join(f"<li>{_html(c)}</li>" for c in choices) + "</ol>" if choices else "")
        deck.add_note(genanki.Note(
            model=model,
            fields=[front, _html(back), _html(doc["book"]["title"]), _html(q["chapter"]),
                    _html(q["section_label"]), passage, q["id"]],
            # The same question of the same run keeps its identity, so importing a rebuilt deck updates cards
            # and keeps their review history. A new run of the book makes new cards.
            guid=genanki.guid_for(source, doc["run_id"], q["id"]),
            tags=q["tags"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(path))
    return len(deck.notes)
