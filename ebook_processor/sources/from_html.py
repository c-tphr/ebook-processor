"""An HTML file or a web page: taken whole, or only the article in it."""

from __future__ import annotations

import json
import re
from typing import Any

import lxml.html

from . import clean as C

SPACE = re.compile(r"\s+")
NEEDS_SCRIPT = re.compile(r"(enable|requires?|turn on|activate)\s+javascript|javascript (is )?(required|disabled|needed)", re.I)


def facts(html: str) -> dict[str, Any]:
    """What the file's head says of it."""
    root = C.parse(html).getroottree().getroot()

    def meta(*names: str) -> str | None:
        for name in names:
            for el in root.iter("meta"):
                if (el.get("property") or el.get("name") or "").lower() == name:
                    value = SPACE.sub(" ", el.get("content") or "").strip()
                    if value:
                        return value
        return None

    title_el = root.find(".//title")
    title = meta("og:title", "twitter:title") or (SPACE.sub(" ", title_el.text_content()).strip() if title_el is not None else None)
    canonical = next((el.get("href") for el in root.iter("link") if "canonical" in (el.get("rel") or "").lower().split()
                      and el.get("href")), None)
    url = canonical or meta("og:url")
    author = meta("author", "article:author", "dc.creator", "citation_author")
    published = meta("article:published_time", "date", "dc.date", "citation_publication_date")
    return {"title": title or None, "author": None if author and author.startswith("http") else author,
            "published": published[:10] if published else None,
            "url": url if url and url.startswith(("http://", "https://")) else None, "site": meta("og:site_name"),
            "language": (root.get("lang") or "").strip() or None}


def is_page(html: str) -> bool:
    """Whether the HTML is a page of a web site (a menu, scripts, the marks sites put in their heads), and not a
    document that someone wrote or exported as HTML."""
    root = C.parse(html).getroottree().getroot()
    marks = any((el.get("property") or "").lower().startswith(("og:", "article:")) for el in root.iter("meta"))
    canonical = any("canonical" in (el.get("rel") or "").lower().split() for el in root.iter("link"))
    scripts = len(list(root.iter("script")))
    return root.find(".//nav") is not None or marks or canonical or scripts >= 3


def signs(html: str) -> dict[str, Any]:
    """What a page says of how much of it a program is given: whether it is free to read, how long the piece is
    said to be, and whether it asks for JavaScript."""
    root = C.parse(html).getroottree().getroot()
    out: dict[str, Any] = {"free": None, "words": None, "script": False}

    def look(data: Any) -> None:
        if isinstance(data, list):
            for item in data:
                look(item)
        elif isinstance(data, dict):
            free = data.get("isAccessibleForFree")
            if free in (False, "False", "false") and out["free"] is None:
                out["free"] = False
            count = data.get("wordCount")
            if isinstance(count, (int, str)) and str(count).isdigit() and out["words"] is None:
                out["words"] = int(count)
            for value in data.values():
                if isinstance(value, (dict, list)):
                    look(value)

    for el in root.iter("script"):
        if "ld+json" in (el.get("type") or "").lower():
            try:
                look(json.loads(el.text_content()))
            except ValueError:
                pass
    for el in root.iter("noscript"):
        if NEEDS_SCRIPT.search(el.text_content() or ""):
            out["script"] = True
    return out


def lift(html: str) -> tuple[str, dict[str, bytes]]:
    """Pictures written into a page (``data:`` addresses, as a page saved as one HTML file has them) are given
    addresses of their own, because the article finder keeps only pictures that have one. The bytes come back
    under those addresses."""
    if "data:image/" not in html:
        return html, {}
    import base64
    import binascii
    import urllib.parse

    tree = C.parse(html).getroottree().getroot()
    held: dict[str, bytes] = {}
    for image in tree.iter("img"):
        src = (image.get("src") or "").strip()
        if not src.startswith("data:image/"):
            continue
        head, _, body = src.partition(",")
        try:
            data = base64.b64decode(body, validate=False) if ";base64" in head else urllib.parse.unquote_to_bytes(body)
        except (binascii.Error, ValueError):
            continue
        kind = head[len("data:image/"):].split(";")[0].split("+")[0].lower() or "png"
        address = f"https://written-into-the-page.invalid/{len(held) + 1}.{ {'jpeg': 'jpg'}.get(kind, kind) }"
        held[address] = data
        image.set("src", address)
        for name in ("srcset", "data-src", "data-srcset"):
            image.attrib.pop(name, None)
    return (lxml.html.tostring(tree, encoding="unicode"), held) if held else (html, {})


def article(html: str, url: str | None) -> tuple[str | None, dict[str, Any]]:
    """The article of a page, as HTML, with what the page says of it; None when no article was found."""
    import warnings

    with warnings.catch_warnings():  # a library it uses grumbles about an extra this app has no use for
        warnings.simplefilter("ignore")
        import trafilatura

    known: dict[str, Any] = {}
    if "<figcaption" in html.lower():  # the library drops the line under a picture: hand it over as a paragraph in italics
        tree = C.parse(html).getroottree().getroot()
        for caption in tree.iter("figcaption"):
            em = lxml.html.Element("em")
            em.text, caption.text = caption.text, None
            for child in list(caption):
                em.append(child)
            caption.append(em)
            caption.tag = "p"
        html = lxml.html.tostring(tree, encoding="unicode")
    try:
        found = trafilatura.extract(html, output_format="html", include_images=True, include_tables=True, include_links=True,
                                    include_formatting=True, include_comments=False, url=url)
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:  # noqa: BLE001 - a page the library cannot read is a page without an article
        return None, known
    if meta is not None:
        for key, name in (("title", "title"), ("author", "author"), ("published", "date"), ("site", "sitename"), ("url", "url")):
            value = getattr(meta, name, None)
            if isinstance(value, str) and value.strip():
                known[key] = value.strip()
    if not found:
        return None, known
    body = lxml.html.document_fromstring(found).find("body")
    if body is None:
        return None, known
    inner = (body.text or "") + "".join(lxml.html.tostring(child, encoding="unicode") for child in body)
    return inner, known


def read(html: str, whole: bool | None = None, url: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, bytes]]:
    """The HTML to clean; what is known of the piece; how it was taken: ``page`` (it looks like a page of a
    site), ``whole`` (all of it was taken) and ``lost`` (an article was looked for and not found); and the
    pictures that were written into the page, by the addresses they now have."""
    known = facts(html)
    page = is_page(html)
    how = {"page": page, "whole": True, "lost": False}
    if whole is True or (whole is None and not page):
        return html, known, how, {}
    lifted, held = lift(html)
    found, said = article(lifted, url or known.get("url"))
    if found is None or C.words(lxml.html.fragment_fromstring(found, create_parent="div")) < 40:
        how["lost"] = True
        return html, known, how, {}
    how["whole"] = False
    # the page's own head is the better word on the address; the library is the better word on the rest
    for key, value in said.items():
        if key == "url" and known.get("url"):
            continue
        if key == "author" and (";" in value or len(value) > 120) and known.get("author"):
            continue
        known[key] = value
    return found, known, how, held
