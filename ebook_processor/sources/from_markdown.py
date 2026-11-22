"""A Markdown file."""

from __future__ import annotations

import re
import xml.etree.ElementTree as etree
from typing import Any

import markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor

from .. import frontmatter, vault

# from the start of a run of spaces only: a long run is then read once
BLOCK_ID = re.compile(rf"(?<![ \t])[ \t]+{vault.BLOCK_ID}[ \t]*$", re.M)
COMMENT = re.compile(r"%%.*?%%", re.S)


class _Wiki(InlineProcessor):
    """``[[Note]]`` and ``[[Note|words]]`` are the words; ``![[Note]]`` (another note shown in place) is nothing."""

    def handleMatch(self, m: re.Match[str], data: str) -> tuple[Any, int, int]:  # noqa: N802 - the library's name
        el = etree.Element("span")
        if not m.group(1):
            el.text = (m.group(4) or m.group(2) or m.group(3) or "").strip()
        return el, m.start(0), m.end(0)


class _Notes(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802
        md.inlinePatterns.register(_Wiki(vault.LINK_RE.pattern, md), "wiki", 175)  # a link as the notes folder reads it


def _one(value: Any) -> str | None:
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if isinstance(v, (str, int, float)))
    if isinstance(value, bool) or value is None or value is frontmatter.OPAQUE:
        return None
    return str(value).strip() or None


def read(text: str) -> tuple[str, dict[str, Any]]:
    """The file's text as HTML, and what its properties say of it."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    front, body = frontmatter.split(text)
    props = frontmatter.parse_props(front) if front is not None else []

    def named(*names: str) -> str | None:
        for name in names:
            found = _one(next((p.value for p in props if p.key and p.key.lower() == name), None))
            if found:
                return found
        return None

    facts = {"title": named("title"), "author": named("author", "authors", "by"), "published": named("date", "published"),
             "url": named("url", "source", "link"), "site": named("site", "publisher"), "language": named("lang", "language")}
    body = BLOCK_ID.sub("", COMMENT.sub("", body))
    md = markdown.Markdown(extensions=["extra", "sane_lists", "smarty", _Notes()], output_format="xhtml")
    return md.convert(body), facts
