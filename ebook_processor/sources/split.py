"""One long document as chapters, cut at its headings."""

from __future__ import annotations

from typing import Any

from . import clean as C
from .pack import Doc

LONG = 5000  # words; a shorter piece stays whole


def level(root: Any) -> str | None:
    """The highest level of heading that the text has twice or more."""
    for name in C.HEADINGS:
        if len(list(root.iter(name))) >= 2:
            return name
    return None


def _raise(root: Any, name: str) -> bool:
    """Bring every heading of this level up to the top of the body, by opening the boxes around it. False when
    one stands where it cannot be a chapter's heading (in a list, a quotation, a table)."""
    for _ in range(200):
        deep = [h for h in root.iter(name) if h.getparent() is not root]
        if not deep:
            return True
        parent = deep[0].getparent()
        if parent.tag != "div" or C._is_note(parent):
            return False
        parent.drop_tag()
    return False


def split(root: Any, title: str, how: str = "auto") -> list[Doc]:
    """The body as the documents of a book. ``how`` is ``auto`` (chapters when the piece is long and has them) or
    ``none`` (one document)."""
    name = level(root) if how == "auto" and C.words(root) >= LONG else None
    if name is None or not _raise(root, name):
        return [Doc(title, list(root))]
    C._loose(root)
    docs: list[Doc] = []
    opening: list[Any] = []
    for el in list(root):
        if el.tag == name:
            docs.append(Doc(C.plain(el) or title, [el]))
        elif docs:
            docs[-1].elements.append(el)
        else:
            opening.append(el)
    if sum(C.words(e) for e in opening if e.tag not in C.HEADINGS):
        docs.insert(0, Doc(title, opening))  # what stands before the first chapter, when it says something
    return docs
