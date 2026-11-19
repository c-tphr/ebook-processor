"""An HTML file, taken whole."""

from __future__ import annotations

import re
from typing import Any

from . import clean as C

SPACE = re.compile(r"\s+")


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


def read(html: str) -> tuple[str, dict[str, Any]]:
    return html, facts(html)
