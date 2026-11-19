"""Other sources than EPUB, turned into an EPUB at the door.

A Markdown file, a text file or an HTML file goes in; a small EPUB comes out, and from there on the app sees an
ordinary book. Nothing outside this package knows about formats. ``docs/SOURCES-PLAN.md`` has the design.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import lxml.etree as ET

from ..prep.epub import EpubError, load_book_bytes
from . import clean as C
from . import fetch, pack, split
from .chars import declared_in_html, decode

FORMATS = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".text": "text", ".html": "html",
           ".htm": "html", ".xhtml": "html"}
NAMES = {"markdown": "Markdown file", "text": "Text file", "html": "HTML file", "epub": "EPUB"}
ACCEPTED = "EPUB, Markdown (.md), plain text (.txt) and HTML (.html)"
TYPES = ("book", "article")
MIN_WORDS = 150
MAX_BYTES = 50 * 1024 * 1024  # of text or HTML; a whole library in one file is not a piece to read
BOOK_WORDS, BOOK_CHAPTERS = 20000, 3


class SourceError(ValueError):
    """The file cannot be taken in, said in words the reader can act on."""


@dataclass
class Made:
    epub: bytes
    name: str  # the made book's file name, ``Title.epub``
    title: str
    author: str
    type: str  # the guess: "book" or "article"
    words: int
    origin: dict[str, Any]
    found: dict[str, Any] | None  # how chapters were found, when they were
    pictures: dict[str, int] = field(default_factory=lambda: {"kept": 0, "missing": 0})
    warnings: list[str] = field(default_factory=list)
    book: Any = None  # the made EPUB as the app reads it


def format_of(name: str) -> str | None:
    """The kind of source a file name stands for, or None for one this package does not read."""
    return FORMATS.get(Path(name).suffix.lower())


def is_source(name: str) -> bool:
    return format_of(name) is not None


def _stem(name: str) -> str:
    return re.sub(r"[_]+", " ", Path(name.replace("\\", "/")).stem).strip() or "Untitled"


def _file_name(title: str) -> str:
    name = re.sub(r"[^\w .,()'&+-]", " ", title)
    return (re.sub(r"\s+", " ", name).strip(" .")[:80].rstrip(" .") or "Untitled") + ".epub"


def _getter(base: str | None, folder: Path | None, web: bool) -> Callable[[str], bytes | None]:
    """How the bytes of a picture are had: written into the text, beside the file, or on the web."""

    def get(address: str) -> bytes | None:
        if address.startswith("data:"):
            head, _, body = address.partition(",")
            try:
                return base64.b64decode(body, validate=False) if ";base64" in head else urllib.parse.unquote_to_bytes(body)
            except (binascii.Error, ValueError):
                return None
        if base and not urllib.parse.urlsplit(address).scheme:
            address = urllib.parse.urljoin(base, address)
        scheme = urllib.parse.urlsplit(address).scheme.lower()
        if scheme in ("http", "https"):
            if not web:
                return None
            try:
                return fetch.get(address, fetch.PICTURE_LIMIT, fetch.PICTURE_SECONDS, public_only=True,
                                 accept="image/png,image/jpeg,image/gif,image/webp,*/*;q=0.5").data
            except fetch.FetchError:
                return None
        if scheme or folder is None:
            return None
        try:
            path = (folder / urllib.parse.unquote(address.split("#")[0].split("?")[0])).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file() or path.stat().st_size > pack.MAX_PICTURE:
                return None
            return path.read_bytes()
        except (OSError, ValueError):
            return None

    return get


def convert(name: str, data: bytes, *, url: str | None = None, folder: Path | None = None, split_how: str = "auto",
            title: str | None = None, author: str | None = None, web: bool = True,
            pictures: Callable[[Callable[[str], bytes | None]], Callable[[str], bytes | None]] | None = None) -> Made:
    """A file that is not an EPUB, as an EPUB.

    ``url`` is the address the bytes came from, when there is one. ``folder`` is where the file lies on this
    computer, when the app was given its path: pictures beside it are read from there. ``split_how`` is ``auto``
    or ``none`` (one piece, whatever headings it has). ``title`` and ``author`` overrule what the file says.
    ``web`` allows the pictures of the text to be fetched. ``pictures`` wraps the way a picture's bytes are had
    (the app keeps what was fetched, so that converting again asks no site twice).
    """
    kind = format_of(name)
    if kind is None:
        ending = Path(name).suffix.lower()
        if ending == ".pdf":
            raise SourceError("PDF files are not supported yet.")
        if ending in (".doc", ".docx", ".rtf", ".odt"):
            raise SourceError("Word files are not supported yet. Save it as HTML or plain text and add that.")
        raise SourceError(f"This kind of file is not supported. The app takes {ACCEPTED}.")
    if not data.strip():
        raise SourceError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise SourceError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Text of that size is not taken.")
    found = None
    if kind == "markdown":
        from . import from_markdown
        html, facts = from_markdown.read(decode(data))
    elif kind == "text":
        from . import from_text
        html, facts, found = from_text.read(decode(data))
    else:
        from . import from_html
        html, facts = from_html.read(decode(data, declared_in_html(data)))
    address = url or facts.get("url")
    root = C.clean(html, address)
    opening = C.first_heading(root)
    top = root.find(".//h1")
    named = (facts.get("title") or "").strip()
    heading = C.plain(top) if top is not None else C.plain(opening) if opening is not None and not named else ""
    if heading and named and heading != named and heading.lower() in named.lower() and len(heading) >= 8:
        named = heading  # "On Keeping Lamps | The Shore Review": the page's own heading is the title
    said = (named or heading or _stem(name))[:300]
    final = (title or "").strip()[:300] or said
    mine = top if top is not None and C.plain(top) == said else opening if opening is not None and C.plain(opening) == said else None
    if mine is None and top is None:
        head = ET.Element("h1")
        head.text = final
        root.insert(0, head)
    elif mine is not None and final != said:  # the reader renamed the piece: its heading follows
        for child in list(mine):
            mine.remove(child)
        mine.text = final
    by = (author if author is not None else facts.get("author") or "").strip()[:300]
    getter = _getter(address, folder, web)
    files, counts = pack.pictures(root, pictures(getter) if pictures else getter)
    docs = split.split(root, final, "none" if split_how == "none" else "auto")
    if len(docs) > 1 and found is None:
        found = {"rule": "headings", "count": len(docs), "example": docs[-1].title}
    elif len(docs) == 1:
        found = None
    epub = pack.pack(final, [by] if by else [], docs, files, facts.get("language") or "en", facts.get("published"),
                     address, facts.get("site"))
    try:
        book = load_book_bytes(epub, _file_name(final))
    except EpubError as exc:  # the made book must be one the app reads; anything else is a fault here
        raise SourceError(f"This file could not be turned into a book ({exc}).") from exc
    words = sum(len(sd.words) for sd in book.spine)
    if words < MIN_WORDS:
        raise SourceError(f"This has only {words} word{'' if words == 1 else 's'} of text. That is too short to make questions from.")
    chapters = sum(1 for sd in book.spine if sd.blocks and sd.words)
    guess = "book" if chapters >= BOOK_CHAPTERS and words >= BOOK_WORDS else "article"
    warnings = []
    if counts["missing"]:
        n = counts["missing"]
        warnings.append(f"{n} picture{' was' if n == 1 else 's were'} not found and {'is' if n == 1 else 'are'} left out."
                        + ("" if folder is not None or address else
                           " To take pictures that lie beside the file, type the file's path instead of dropping it."))
    origin = {"format": kind, "url": address, "site": facts.get("site") or (urllib.parse.urlsplit(address).hostname if address else None),
              "published": facts.get("published"), "fetched": None, "original": Path(name.replace("\\", "/")).name,
              "sha256": hashlib.sha256(data).hexdigest()}
    return Made(epub, _file_name(final), final, by, guess, words, origin, found, counts, warnings, book)
