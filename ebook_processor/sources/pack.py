"""Clean markup, its pictures and what is known of the piece, as an EPUB 3.

The same input gives the same bytes: the dates inside the archive are fixed and the identifier is made from the
content. A run that was stopped is carried on from the same file made again.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
import time
import uuid
import zipfile
from dataclasses import dataclass
from html import escape
from typing import Any, Callable

from . import clean as C

MAX_PICTURES, MAX_PICTURE, MAX_PICTURES_TOTAL = 60, 5 * 1024 * 1024, 40 * 1024 * 1024
STAMP = (2000, 1, 1, 0, 0, 0)
DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


@dataclass
class Doc:
    title: str
    elements: list[Any]


def picture_type(data: bytes) -> str | None:
    """A picture's kind by its first bytes: png, jpg, gif or webp."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _is_dot(data: bytes, kind: str) -> bool:
    """A picture of a pixel or two, put on a page to count readers or to push text about."""
    try:
        if kind == "png":
            width, height = struct.unpack(">II", data[16:24])
        elif kind == "gif":
            width, height = struct.unpack("<HH", data[6:10])
        else:
            return len(data) < 120
    except struct.error:
        return True
    return width <= 2 or height <= 2


MAX_TRIES = 150      # addresses asked for one text, whatever came of them
MAX_SECONDS = 30.0   # for all the pictures of one text together


def pictures(root: Any, get: Callable[[str], bytes | None]) -> tuple[dict[str, bytes], dict[str, int]]:
    """Take the pictures of the text into the book. ``get`` gives the bytes at an address, or None. Each image
    then points to its file (``images/p001.png``); one that could not be had leaves its description."""
    files: dict[str, bytes] = {}
    by_hash: dict[str, str] = {}
    by_address: dict[str, str | None] = {}
    counts = {"kept": 0, "missing": 0}
    total = tries = 0
    began = time.monotonic()
    for image in list(root.iter("img")):
        if image.getparent() is None:
            continue
        address = image.get("src") or ""
        if address not in by_address:
            name = None
            # A page can name hundreds of pictures that are not there, or a host that answers slowly: asking stops
            # after so many addresses or so many seconds, and the rest count as missing.
            asked = bool(address) and len(files) < MAX_PICTURES and tries < MAX_TRIES and time.monotonic() - began < MAX_SECONDS
            tries += asked
            data = get(address) if asked else None
            kind = picture_type(data) if data else None
            if data and kind and len(data) <= MAX_PICTURE and total + len(data) <= MAX_PICTURES_TOTAL:
                if _is_dot(data, kind):
                    by_address[address] = ""
                    image.drop_tree()
                    continue
                digest = hashlib.sha256(data).hexdigest()
                name = by_hash.get(digest)
                if name is None:
                    name = by_hash[digest] = f"images/p{len(files) + 1:03d}.{kind}"
                    files[name] = data
                    total += len(data)
            by_address[address] = name
        name = by_address[address]
        if name == "":
            image.drop_tree()
        elif name:
            image.set("src", name)
            counts["kept"] += 1
        else:
            counts["missing"] += 1
            alt = image.get("alt") or ""
            C._replace(image, f"[{alt}]" if alt else "")
    return files, counts


def _page(title: str, body: str, language: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'xml:lang="{escape(language)}" lang="{escape(language)}">\n'
            f"<head>\n<title>{escape(title)}</title>\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def pack(title: str, authors: list[str], docs: list[Doc], files: dict[str, bytes] | None = None, language: str = "en",
         published: str | None = None, url: str | None = None, site: str | None = None) -> bytes:
    files = files or {}
    language = language if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", language or "") else "en"
    names = [f"d{n:03d}.xhtml" for n in range(1, len(docs) + 1)]
    # A link to another place of the text, when the text was cut into chapters, names the chapter's file.
    home = {el.get("id"): name for name, d in zip(names, docs) for top in d.elements for el in top.iter() if el.get("id")}
    for name, d in zip(names, docs):
        for top in d.elements:
            for link in top.iter("a"):
                href = link.get("href") or ""
                if href.startswith("#") and home.get(href[1:], name) != name:
                    link.set("href", home[href[1:]] + href)
    pages = [(name, d.title, _page(d.title, C.xhtml(d.elements), language)) for name, d in zip(names, docs)]
    digest = hashlib.sha256()
    for part in [title, *authors, language, published or "", url or "", site or ""]:
        digest.update(part.encode("utf-8") + b"\0")
    for _, _, text in pages:
        digest.update(text.encode("utf-8"))
    for name in sorted(files):
        digest.update(name.encode("utf-8") + files[name])
    identifier = uuid.UUID(hex=digest.hexdigest()[:32])
    facts = [f'<dc:identifier id="uid">urn:uuid:{identifier}</dc:identifier>', f"<dc:title>{escape(title)}</dc:title>"]
    facts += [f"<dc:creator>{escape(a)}</dc:creator>" for a in authors]
    facts.append(f"<dc:language>{escape(language)}</dc:language>")
    if published and DATE.match(published):
        facts.append(f"<dc:date>{published}</dc:date>")
    if url:
        facts.append(f"<dc:source>{escape(url)}</dc:source>")
    if site:
        facts.append(f"<dc:publisher>{escape(site)}</dc:publisher>")
    facts.append('<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>')
    items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    items += [f'<item id="d{n:03d}" href="{href}" media-type="application/xhtml+xml"/>' for n, (href, _, _) in enumerate(pages, 1)]
    items += [f'<item id="p{n:03d}" href="{name}" media-type="{TYPES[name.rsplit(".", 1)[-1]]}"/>'
              for n, name in enumerate(sorted(files), 1)]
    spine = [f'<itemref idref="d{n:03d}"/>' for n in range(1, len(pages) + 1)]
    opf = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="{escape(language)}">',
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">', *facts, "</metadata>",
        "<manifest>", *items, "</manifest>", "<spine>", *spine, "</spine>", "</package>", ""])
    toc = "\n".join(f'<li><a href="{href}">{escape(name)}</a></li>' for href, name, _ in pages)
    nav = _page(title, f'<nav epub:type="toc" id="toc">\n<h1>{escape(title)}</h1>\n<ol>\n{toc}\n</ol>\n</nav>', language)
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
                 "</container>\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        def put(name: str, data: bytes, squeeze: bool = True) -> None:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED if squeeze else zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)

        put("mimetype", b"application/epub+zip", squeeze=False)
        put("META-INF/container.xml", container.encode("utf-8"))
        put("OEBPS/content.opf", opf.encode("utf-8"))
        put("OEBPS/nav.xhtml", nav.encode("utf-8"))
        for href, _, text in pages:
            put("OEBPS/" + href, text.encode("utf-8"))
        for name in sorted(files):
            put("OEBPS/" + name, files[name], squeeze=False)
    return out.getvalue()
