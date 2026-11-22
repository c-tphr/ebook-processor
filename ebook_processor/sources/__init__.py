"""Other sources than EPUB, turned into an EPUB at the door.

A Markdown, text, HTML or Word file, a saved page or the page at a web address goes in; a small EPUB comes out,
and from there on the app sees an ordinary book. Nothing outside this package knows about formats. ``docs/SOURCES-PLAN.md`` has the design.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import lxml.etree as ET

from ..prep.epub import EpubError, load_book_bytes
from ..store import now
from . import clean as C
from . import fetch, pack, split
from .chars import declared_in_html, decode

FORMATS = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".text": "text", ".html": "html",
           ".htm": "html", ".xhtml": "html", ".docx": "docx", ".mhtml": "saved", ".mht": "saved"}
ACCEPTED = "EPUB, Markdown (.md), plain text (.txt), HTML (.html), Word (.docx) and pages saved as one file (.mhtml)"
SAVE_IT = "Save the page in your browser (in Chrome or Edge: Save as, \"Webpage, Single File\") and add the file."
TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|igshid|ref_src)$", re.I)
TYPES = ("book", "article")
MIN_WORDS = 150
MAX_BYTES = 50 * 1024 * 1024  # of text or HTML; a whole library in one file is not a piece to read
BOOK_WORDS, BOOK_CHAPTERS = 20000, 3


class SourceError(ValueError):
    """The file cannot be taken in, said in words the reader can act on."""


@dataclass
class Page:
    """What a web address gave."""
    name: str  # a file name that says what it is: page.html, page.md, page.txt, page.docx
    data: bytes
    url: str
    charset: str | None
    fetched: str  # when, as an ISO time


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
    page: dict[str, bool] | None = None  # for HTML: whether it looks like a page of a site, and whether all of it was taken


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


def tidy(url: str) -> str:
    """An address without what only follows the reader about: the place in the page, and the tags of campaigns."""
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def fetch_page(url: str) -> Page:
    """Ask a web address for its page. What comes back is converted like a file (``convert`` with ``url``)."""

    url = url.strip()
    if url and "://" not in url and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
        url = "https://" + url
    try:
        got = fetch.get(url)
    except fetch.FetchError as exc:
        if exc.status in (401, 403, 429, 451):
            raise SourceError(f"The site refused to give the page (it answered {exc.status}). It may want a login, or it may "
                              f"not answer programs. {SAVE_IT}") from exc
        if exc.status in (404, 410):
            raise SourceError("There is no page at this address (the site answered " + str(exc.status) + ").") from exc
        if exc.reason == "scheme":
            raise SourceError("Give a web address that begins with http:// or https://.") from exc
        text = str(exc)
        raise SourceError(text[:1].upper() + text[1:] + ("" if exc.reason in ("timeout", "unreachable") else f". {SAVE_IT}")) from exc
    path = urllib.parse.urlsplit(got.url).path.lower()
    if got.kind in ("text/html", "application/xhtml+xml"):
        name = "page.html"
    elif got.kind == "application/pdf" or path.endswith(".pdf"):
        raise SourceError("This address gives a PDF file. PDF files are not supported yet.")
    elif got.kind == "text/markdown" or path.endswith((".md", ".markdown")):
        name = "page.md"
    elif got.kind == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or path.endswith(".docx"):
        name = "page.docx"
    elif got.kind == "text/plain":
        name = "page.txt"
    else:
        raise SourceError(f"This address gives a file that is not a web page ({got.kind}).")
    return Page(name, got.data, tidy(got.url), got.charset, now())


def _getter(base: str | None, folder: Path | None, web: bool, parts: dict[str, bytes] | None = None) -> Callable[[str], bytes | None]:
    """How the bytes of a picture are had: written into the text, kept in a saved page, beside the file, or on
    the web."""

    def get(address: str) -> bytes | None:
        if parts and address in parts:
            return parts[address]
        if address.startswith("data:"):
            head, _, body = address.partition(",")
            try:
                return base64.b64decode(body, validate=False) if ";base64" in head else urllib.parse.unquote_to_bytes(body)
            except (binascii.Error, ValueError):
                return None
        if base and not urllib.parse.urlsplit(address).scheme:
            address = urllib.parse.urljoin(base, address)
        if parts and address in parts:
            return parts[address]
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


def _not_too_deep(fn: Callable[..., Any]) -> Callable[..., Any]:
    """A file nested too deeply to be read is refused with a sentence, not a fault."""
    @functools.wraps(fn)
    def inner(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except RecursionError:
            raise SourceError("This file is nested too deeply to be read.") from None
    return inner


@_not_too_deep
def convert(name: str, data: bytes, *, url: str | None = None, folder: Path | None = None, split_how: str = "auto",
            whole: bool | None = None, title: str | None = None, author: str | None = None, web: bool = True,
            pictures: Callable[[Callable[[str], bytes | None]], Callable[[str], bytes | None]] | None = None,
            fetched: str | None = None, charset: str | None = None) -> Made:
    """A file that is not an EPUB, as an EPUB.

    ``url`` is the address the bytes came from, when there is one. ``folder`` is where the file lies on this
    computer, when the app was given its path: pictures beside it are read from there. ``split_how`` is ``auto``
    or ``none`` (one piece, whatever headings it has). ``whole``, for HTML: take all of it (True), only the
    article in it (False), or decide by whether it looks like a page of a web site (None). ``title`` and
    ``author`` overrule what the file says. ``web`` allows the pictures of the text to be fetched. ``pictures``
    wraps the way a picture's bytes are had (the app keeps what was fetched, so that converting again asks no
    site twice). ``fetched`` (when) and ``charset`` (what the server said) come with a page from a web address.
    """
    kind = format_of(name)
    if kind is None:
        ending = Path(name).suffix.lower()
        if ending == ".pdf":
            raise SourceError("PDF files are not supported yet.")
        if ending in (".doc", ".rtf", ".odt", ".pages"):
            raise SourceError("This kind of document is not supported. Save it as a Word file (.docx) and add that.")
        if ending == ".webarchive":
            raise SourceError("Safari's web archives are not supported yet. Save the page as HTML, or from Chrome or Edge "
                              "as \"Webpage, Single File\".")
        raise SourceError(f"This kind of file is not supported. The app takes {ACCEPTED}.")
    if not data.strip():
        raise SourceError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise SourceError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Text of that size is not taken.")
    found = None
    how: dict[str, bool] | None = None
    parts: dict[str, bytes] | None = None
    warnings: list[str] = []
    signs: dict[str, Any] = {}
    if kind == "markdown":
        from . import from_markdown
        html, facts = from_markdown.read(decode(data, charset))
    elif kind == "text":
        from . import from_text
        html, facts, found = from_text.read(decode(data, charset))
    elif kind == "docx":
        from . import from_docx
        try:
            html, facts, unknown = from_docx.read(data)
        except from_docx.NotWord as exc:
            raise SourceError(str(exc)) from exc
        if unknown:
            warnings.append("Some formatting of the Word file was not recognised and was kept as plain paragraphs.")
    else:
        from . import from_html
        if kind == "saved":
            from . import saved
            try:
                data_html, charset, address_saved, parts = saved.read(data)
            except saved.NotSaved as exc:
                raise SourceError(str(exc)) from exc
            url = url or address_saved
            text = decode(data_html, charset or declared_in_html(data_html))
        else:
            text = decode(data, charset or declared_in_html(data))
        html, facts, how, written = from_html.read(text, whole, url)
        if written:
            parts = {**(parts or {}), **written}
        signs = from_html.signs(text)
        if fetched is not None:
            how["page"] = True  # what an address gave is a page, however plain: the reader may choose how much of it
        if how["lost"]:
            warnings.append("The article could not be told from the rest of the page, so the whole page was taken.")
    said_url = facts.get("url") if isinstance(facts.get("url"), str) and facts["url"].startswith(("http://", "https://")) else None
    address = url or said_url  # what relative links and pictures are joined to
    listed = tidy(said_url or url) if (said_url or url) else None  # the address the entry goes by
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
    for extra in root.findall(".//h1")[1:2]:  # a title page and the first heading that say the same: once is enough
        if top is not None and C.plain(extra) == C.plain(top) and not (extra.tail or "").strip():
            extra.getparent().remove(extra)
    getter = _getter(address, folder, web, parts)
    files, counts = pack.pictures(root, pictures(getter) if pictures else getter)
    docs = split.split(root, final, "none" if split_how == "none" else "auto")
    if len(docs) > 1 and found is None:
        found = {"rule": "headings", "count": len(docs), "example": docs[-1].title}
    elif len(docs) == 1:
        found = None
    epub = pack.pack(final, [by] if by else [], docs, files, facts.get("language") or "en", facts.get("published"),
                     listed, facts.get("site"))
    try:
        book = load_book_bytes(epub, _file_name(final))
    except EpubError as exc:  # the made book must be one the app reads; anything else is a fault here
        raise SourceError(f"This file could not be turned into a book ({exc}).") from exc
    words = sum(len(sd.words) for sd in book.spine)
    from_web = fetched is not None
    if words < MIN_WORDS:
        few = f"only {words} word{'' if words == 1 else 's'} of text"
        if from_web and signs.get("script"):
            raise SourceError(f"The page builds its text with JavaScript, and a program is given {few}. {SAVE_IT}")
        if from_web:
            raise SourceError(f"{few[:1].upper() + few[1:]} {'was' if words == 1 else 'were'} found at this address. The page may want a "
                              f"login, or build its text with JavaScript. {SAVE_IT}")
        raise SourceError(f"This has {few}. That is too short to make questions from.")
    if how is not None and not how["whole"]:
        if signs.get("free") is False:
            warnings.append("The page says it is not free to read. What was taken may be only its opening."
                            + (f" {SAVE_IT}" if from_web else ""))
        elif signs.get("words") and words < signs["words"] * 0.6:
            warnings.append(f"The page says the piece has about {signs['words']:,} words, and {words:,} were found. The rest may "
                            "be behind a login." + (f" {SAVE_IT}" if from_web else ""))
    chapters = sum(1 for sd in book.spine if sd.blocks and sd.words)
    a_page = from_web or kind == "saved" or (how is not None and how["page"])
    guess = "book" if chapters >= BOOK_CHAPTERS and words >= BOOK_WORDS and not a_page else "article"
    if counts["missing"]:
        n = counts["missing"]
        warnings.append(f"{n} picture{' was' if n == 1 else 's were'} not found and {'is' if n == 1 else 'are'} left out."
                        + ("" if folder is not None or address or kind in ("docx", "saved") else
                           " To take pictures that lie beside the file, type the file's path instead of dropping it."))
    site = facts.get("site") or (urllib.parse.urlsplit(listed).hostname if listed else None)
    origin = {"format": "web" if from_web else kind, "url": listed, "site": site, "published": facts.get("published"),
              "fetched": fetched, "original": Path(name.replace("\\", "/")).name, "sha256": hashlib.sha256(data).hexdigest()}
    return Made(epub, _file_name(final), final, by, guess, words, origin, found, counts, warnings, book, how)
