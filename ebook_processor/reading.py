"""A prepared EPUB as the web reader needs it.

The reader shows the book in its own typography, so each chapter is sent as clean HTML built from the EPUB's
markup: a fixed set of elements, no styles, no scripts. On top of that:

- every block carries ``data-w`` and ``data-n``, the index of its first word and its word count in the chapter's
  word stream. That stream is the engine's (``prep/words.py``), the one the firmware uses, so a position means the
  same thing in paged view, scroll view and RSVP;
- the invisible section markers become ``<span data-anchor="s0007-end">``;
- links inside the book become ``data-go="<chapter>:<id>"``; footnote marks also get the class ``noteref``;
- images keep their place as ``data-src="<path in the archive>"``, which the page turns into a request for the
  file.

Positions are (chapter, word): the chapter is the 1-based place of its document in the EPUB's spine.
"""

from __future__ import annotations

import json
import posixpath
import re
import urllib.parse
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from . import forms as qforms
from .exports import authors, chapter_titles
from .prep import checkpoints as cp
from .prep import words as W
from .prep.build import CHECKPOINTS_ID
from .prep.epub import Book, EpubError, SpineDoc, is_footnote_container, is_invisible, is_noteref, load_book_bytes
from .prep.xmltree import Element

STATE_FILE = "reading.json"
STATE_VERSION = 1
MODES = ("page", "scroll", "rsvp")

KEEP = frozenset("""p h1 h2 h3 h4 h5 h6 em i b strong u s small sub sup blockquote ul ol li dl dt dd table thead tbody
tfoot tr td th caption colgroup col pre code kbd samp var br hr figure figcaption cite q abbr span div section article
aside header footer main a img del ins mark ruby rt rp time address""".split())
DROP = frozenset("""script style head title link meta iframe object embed video audio canvas form input button select
textarea nav template noscript""".split())
VOID = frozenset({"br", "hr", "img", "col"})
ATTRS = {"td": ("colspan", "rowspan"), "th": ("colspan", "rowspan", "scope"), "ol": ("start", "type"), "img": ("alt",),
         "col": ("span",), "colgroup": ("span",)}
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
EXTERNAL = re.compile(r"^(https?|mailto):", re.I)


class ReadingError(Exception):
    """The file cannot be read as a prepared book."""


@dataclass
class ReadingBook:
    book: Book
    data: dict[str, Any]  # rsvp/checkpoints.json
    job_file: str

    # ---- what the reader asks for

    def manifest(self) -> dict[str, Any]:
        book = self.book
        titles = chapter_titles(book)
        chapters = [{"index": sd.index, "title": titles[sd.href], "words": len(sd.words),
                     "prose": sum(b.word_count for b in sd.blocks)}
                    for sd in book.spine if sd.doc is not None and sd.words]
        by_href = {sd.href: sd for sd in book.spine}
        with_checkpoint = {c["section"]: c for c in self.data["checkpoints"]}
        sections = []
        for s in self.data["sections"]:
            sd = by_href.get(s["chapter_href"])
            if sd is None or sd.stream is None:
                continue
            start, end = sd.stream.anchor(s["start_anchor"]), sd.stream.anchor(s["end_anchor"])
            if start is None or end is None:
                continue
            c = with_checkpoint.get(s["id"])
            sections.append({"id": s["id"], "label": s["label"], "chapter": sd.index, "start": start.index,
                             "end": end.index, "checkpoint": c["id"] if c else None,
                             "prompts": [qforms.from_x(p) for p in c["prompts"]] if c else []})
        toc = []
        where = {b.ref: (sd, b) for sd in book.spine for b in sd.blocks}
        for entry in book.toc:
            if entry.ref in where:
                sd, block = where[entry.ref]
                toc.append({"label": entry.label, "depth": entry.depth, "chapter": sd.index, "word": block.start})
        return {"title": self.data["book"]["title"], "authors": authors(book),
                "sha256": self.data["book"]["source_sha256"], "language": self.data["book"]["language"],
                "words": sum(c["words"] for c in chapters), "chapters": chapters, "sections": sections, "toc": toc}

    def chapter(self, index: int) -> dict[str, Any]:
        sd = self._doc(index)
        assert sd.stream is not None
        return {"index": index, "html": render_chapter(self.book, sd),
                "words": [w.text for w in sd.stream.words], "flags": [w.flags for w in sd.stream.words],
                "markers": [[m.kind, m.start, m.end] for m in sd.stream.markers]}

    def asset(self, path: str) -> tuple[bytes, str]:
        for item in self.book.manifest:
            if item.zip_path == path and item.media_type in IMAGE_TYPES and path in self.book.archive.by_name:
                return self.book.archive.read(path), item.media_type
        raise ReadingError(f"no image at {path}")

    def search(self, query: str, limit: int = 60) -> list[dict[str, Any]]:
        needle = " ".join(query.lower().split())
        out: list[dict[str, Any]] = []
        if len(needle) < 2:
            return out
        for sd in self.book.spine:
            for block in sd.blocks:
                if block.placeholder is not None:
                    continue
                text = " ".join(block.words)
                at = text.lower().find(needle)
                if at < 0:
                    continue
                lo, hi = max(0, at - 60), min(len(text), at + len(needle) + 80)
                out.append({"chapter": sd.index, "word": block.start, "before": ("…" if lo else "") + text[lo:at],
                            "match": text[at : at + len(needle)], "after": text[at + len(needle) : hi] + ("…" if hi < len(text) else "")})
                if len(out) >= limit:
                    return out
        return out

    def _doc(self, index: int) -> SpineDoc:
        for sd in self.book.spine:
            if sd.index == index and sd.doc is not None:
                return sd
        raise ReadingError(f"no chapter {index}")


def open_reading_book(path: Path) -> ReadingBook:
    """Read a prepared EPUB. ``ReadingError`` when it is not one."""
    try:
        book = load_book_bytes(path.read_bytes(), path.name)
    except (EpubError, OSError) as exc:
        raise ReadingError(f"{path.name} cannot be read as an EPUB: {exc}") from exc
    item = next((m for m in book.manifest if m.id == CHECKPOINTS_ID), None)
    if item is None or item.zip_path not in book.archive.by_name:
        raise ReadingError(f"{path.name} has no checkpoints: it is not a prepared book. Process the original EPUB "
                           "first.")
    raw = book.archive.read(item.zip_path)
    issues = cp.check_bytes(raw)
    if issues:
        raise ReadingError(f"{path.name}: its checkpoint file is not valid ({issues[0].message})")
    return ReadingBook(book, json.loads(raw), path.name)


# --------------------------------------------------------------------------
# Chapter HTML


def _target(book: Book, sd: SpineDoc, href: str) -> str | None:
    """``<chapter>:<id>`` for a link inside the book, else ``None``."""
    path, _, fragment = href.partition("#")
    zip_path = sd.zip_path
    if path:
        zip_path = posixpath.normpath(posixpath.join(posixpath.dirname(sd.zip_path), urllib.parse.unquote(path)))
    doc = next((d for d in book.spine if d.zip_path == zip_path and d.doc is not None), None)
    return f"{doc.index}:{urllib.parse.unquote(fragment)}" if doc is not None else None


def _image(sd: SpineDoc, src: str | None, alt: str | None) -> str:
    if not src or EXTERNAL.match(src) or src.startswith("data:"):
        return ""
    path = posixpath.normpath(posixpath.join(posixpath.dirname(sd.zip_path), urllib.parse.unquote(src.split("#")[0])))
    return f'<img data-src="{escape(path)}" alt="{escape(alt or "")}"/>'


def render_chapter(book: Book, sd: SpineDoc) -> str:
    assert sd.doc is not None
    blocks = {id(b.element): b for b in sd.blocks + sd.notes}
    out: list[str] = []

    def walk(el: Element, in_sup: bool, in_link: bool) -> None:
        name = el.local.lower()
        if name in DROP or is_invisible(el):
            return
        if name == "svg":  # a cover or figure wrapped in SVG: keep its picture
            for image in (e for e in el.iter() if e.local == "image"):
                out.append(_image(sd, image.get(XLINK_HREF) or image.get("href"), el.get("aria-label")))
            return
        if name == "math":
            out.append(f'<span class="math">{escape(el.get("alttext") or " ".join(el.text().split()))}</span>')
            return
        if name == "img":
            out.append(_image(sd, el.get("src"), el.get("alt")))
            return
        anchor = el.get("id") or ""
        if anchor.startswith("rsvp-") and name == "span":
            out.append(f'<span data-anchor="{escape(anchor[5:])}"></span>')
            return
        keep = name in KEEP
        if keep:
            attrs = []
            block = blocks.get(id(el))
            if block is not None:
                attrs.append(f'data-w="{block.start}" data-n="{block.end - block.start}" data-ref="{block.ref}"'
                             f' data-kind="{block.kind}"')
            if anchor:
                attrs.append(f'data-id="{escape(anchor)}"')
            if is_footnote_container(el):
                attrs.append('class="note-body"')
            for attr in ATTRS.get(name, ()):
                if el.get(attr):
                    attrs.append(f'{attr}="{escape(el.get(attr) or "")}"')
            if name == "a":
                href = el.get("href") or ""
                if EXTERNAL.match(href):
                    attrs.append(f'href="{escape(href)}" target="_blank" rel="noopener noreferrer"')
                elif href:
                    target = _target(book, sd, href)
                    if target:
                        attrs.append(f'data-go="{escape(target)}"')
                        if is_noteref(el) or in_sup or any(c.local.lower() == "sup" for c in el.iter()):
                            attrs.append('class="noteref"')
            out.append(f"<{name}{' ' if attrs else ''}{' '.join(attrs)}>")
            if name in VOID:
                return
        sup = in_sup or name == "sup"
        link = in_link or (name == "a" and W.is_internal_link(el))
        for child in el.children:
            if isinstance(child, str):
                out.append(escape(child, quote=False))
            else:
                walk(child, sup, link)
        if keep:
            out.append(f"</{name}>")

    body = next((c for c in sd.doc.root.elements() if c.local == "body"), None)
    if body is not None:
        for child in body.children:
            if isinstance(child, str):
                out.append(escape(child, quote=False))
            else:
                walk(child, False, False)
    return "".join(out)


# --------------------------------------------------------------------------
# Saved state: the firmware's shape (docs/PLAN.md section 7 of eink-learner-aid), with members of our own


def new_state(sha256: str) -> dict[str, Any]:
    return {"version": STATE_VERSION, "book_sha256": sha256, "overrides": {"wpm": 240, "words_per_update": 1},
            "position": None, "grades": {}, "x_attempts": [], "x_cleared": {}, "x_previewed": {}, "x_furthest": None,
            "x_progress": 0.0,
            "x_stats": {"seconds": {m: 0 for m in MODES}, "words": {m: 0 for m in MODES}, "last_read": None}}


def _position(value: Any, need_mode: bool = True) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("chapter"), int) or not isinstance(value.get("word"), int):
        return None
    if value["chapter"] < 1 or value["word"] < 0 or (need_mode and value.get("mode") not in MODES):
        return None
    out = {"chapter": value["chapter"], "word": value["word"]}
    if value.get("mode") in MODES:
        out["mode"] = value["mode"]
    return out


def clean_state(raw: Any, sha256: str, known: set[str]) -> dict[str, Any]:
    """The state as it is stored: what the page sent, with anything malformed dropped.

    ``known`` are the book's question ids. A first grade is never replaced: that is settled in ``merge_state``.
    """
    state = new_state(sha256)
    if not isinstance(raw, dict):
        return state
    o = raw.get("overrides") if isinstance(raw.get("overrides"), dict) else {}
    if isinstance(o.get("wpm"), int) and 60 <= o["wpm"] <= 1200:
        state["overrides"]["wpm"] = o["wpm"]
    if o.get("words_per_update") in (1, 2, 3, 4):
        state["overrides"]["words_per_update"] = o["words_per_update"]
    state["position"] = _position(raw.get("position"))
    state["x_furthest"] = _position(raw.get("x_furthest"), need_mode=False)
    if isinstance(raw.get("x_progress"), (int, float)) and 0 <= raw["x_progress"] <= 1:
        state["x_progress"] = round(float(raw["x_progress"]), 4)
    for pid, g in (raw.get("grades") if isinstance(raw.get("grades"), dict) else {}).items():
        if pid in known and isinstance(g, dict) and g.get("first") in ("correct", "incorrect") and isinstance(g.get("at"), str):
            state["grades"][pid] = {"first": g["first"], "at": g["at"][:32]}
    for a in (raw.get("x_attempts") if isinstance(raw.get("x_attempts"), list) else [])[-5000:]:
        if isinstance(a, dict) and a.get("id") in known and a.get("grade") in ("correct", "incorrect", "skipped") \
                and isinstance(a.get("at"), str):
            state["x_attempts"].append({"id": a["id"], "at": a["at"][:32], "grade": a["grade"],
                                        "text": str(a.get("text") or "")[:4000],
                                        "where": a.get("where") if a.get("where") in ("read", "review") else "read",
                                        # a typed answer the app judged wrong and the reader counted as right
                                        **({"overruled": True} if a.get("overruled") is True else {})})
    for pid, at in (raw.get("x_cleared") if isinstance(raw.get("x_cleared"), dict) else {}).items():
        if pid in known and isinstance(at, str):
            state["x_cleared"][pid] = at[:32]
    # when each question was first shown ahead of its section (the reader's question previews)
    for pid, at in (raw.get("x_previewed") if isinstance(raw.get("x_previewed"), dict) else {}).items():
        if pid in known and isinstance(at, str):
            state["x_previewed"][pid] = at[:32]
    stats = raw.get("x_stats") if isinstance(raw.get("x_stats"), dict) else {}
    for key in ("seconds", "words"):
        values = stats.get(key) if isinstance(stats.get(key), dict) else {}
        for mode in MODES:
            if isinstance(values.get(mode), (int, float)) and values[mode] >= 0:
                state["x_stats"][key][mode] = round(float(values[mode]), 1)
    if isinstance(stats.get("last_read"), str):
        state["x_stats"]["last_read"] = stats["last_read"][:32]
    active = raw.get("active_checkpoint")
    if isinstance(active, dict) and isinstance(active.get("id"), str) and isinstance(active.get("prompt_index"), int):
        resume = _position(active.get("resume"))
        if resume is not None:
            state["active_checkpoint"] = {"id": active["id"][:64], "prompt_index": max(0, active["prompt_index"]),
                                          "resume": resume}
            if active.get("x_all") is True:  # "go through it again": every question is asked, also the answered ones
                state["active_checkpoint"]["x_all"] = True
            reread = _position(active.get("reread"))
            if reread is not None:
                state["active_checkpoint"]["reread"] = reread
    return state


def merge_state(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """``new`` with the first grades ``old`` already holds put back: a first grade is written once."""
    if old:
        for pid, grade in old.get("grades", {}).items():
            new["grades"][pid] = grade
    return new


def place(state: dict[str, Any], manifest: dict[str, Any], chapter: int, word: int) -> dict[str, Any]:
    """Move the reader's place in ``state`` to a word of a chapter, as "continue here" in the side reader does.

    The reading mode stays as it was. The furthest point only moves forward. A question left open in the reader
    is closed: its place no longer applies. Raises ``ValueError`` for a place the book does not have.
    """
    order = [c["index"] for c in manifest["chapters"]]
    if chapter not in order:
        raise ValueError("the book has no such chapter")
    words = {c["index"]: c["words"] for c in manifest["chapters"]}
    if not 0 <= word <= words[chapter]:
        raise ValueError("the chapter has no such word")
    mode = (state.get("position") or {}).get("mode")
    state["position"] = {"chapter": chapter, "word": word, "mode": mode if mode in MODES else "page"}
    far = state.get("x_furthest")
    if not far or far.get("chapter") not in order \
            or (order.index(chapter), word) > (order.index(far["chapter"]), far.get("word", 0)):
        state["x_furthest"] = {"chapter": chapter, "word": word}
    before = sum(words[c] for c in order[:order.index(chapter)]) + word
    state["x_progress"] = round(min(1.0, before / manifest["words"]), 4) if manifest["words"] else 0.0
    state.pop("active_checkpoint", None)
    return state


def load_state(folder: Path) -> dict[str, Any] | None:
    path = folder / STATE_FILE
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def summary(state: dict[str, Any] | None, question_ids: set[str] | None = None) -> dict[str, Any] | None:
    """What the library shows about a book's reading: progress, first-try results, time and pace."""
    if not state or not state.get("position"):
        return None
    grades = {pid: g for pid, g in state.get("grades", {}).items() if question_ids is None or pid in question_ids}
    stats = state.get("x_stats") or {}
    seconds = stats.get("seconds") or {}
    words = stats.get("words") or {}
    pace = {m: round(60 * words[m] / seconds[m]) for m in MODES if seconds.get(m, 0) >= 60 and words.get(m)}
    return {"progress": state.get("x_progress", 0.0), "mode": state["position"].get("mode"),
            "answered": len(grades), "correct": sum(g["first"] == "correct" for g in grades.values()),
            "seconds": round(sum(seconds.get(m, 0) for m in MODES)), "pace": pace,
            "last_read": stats.get("last_read"), "in_checkpoint": "active_checkpoint" in state}


def results(state: dict[str, Any] | None) -> dict[str, str]:
    """question id -> 'correct' or 'missed', from first grades."""
    return {pid: ("correct" if g.get("first") == "correct" else "missed")
            for pid, g in ((state or {}).get("grades") or {}).items()}
