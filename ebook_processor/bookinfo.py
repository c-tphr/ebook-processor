"""Facts about a book that the reader, the review page and the exports all need: who wrote it, and what its
chapters are called. Also the lines that name a book at the top of a task for a model."""

from __future__ import annotations

from .prep.epub import DC_NS, Book, squash


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


def book_line(book: Book) -> str:
    """The head of a task message: which book it is about. Every task that names its book opens with these
    lines, to the letter."""
    return f"BOOK\nTitle: {book.title or '(no title)'}\nLanguage: {book.language or 'unknown'}"
