"""A plain text file: a short piece, or a whole book with chapter headings."""

from __future__ import annotations

import re
from html import escape
from typing import Any

START = re.compile(r"^\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.I | re.M)
END = re.compile(r"^\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.I | re.M)
HEADER = re.compile(r"^(Title|Author|Language):[ \t]*(.+)$", re.M)

NUMBER = (r"(\d{1,4}|[IVXLCDM]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
          r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[- ]\w+)?|thirty(?:[- ]\w+)?|forty(?:[- ]\w+)?|"
          r"fifty(?:[- ]\w+)?|the (?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last))")
NAMED = re.compile(rf"^(chapter|part|book|volume|section|letter|canto|act|scene)\s+{NUMBER}\b[.:—–-]?\s*(.{{0,90}})$", re.I)
ALONE = re.compile(r"^(prologue|epilogue|introduction|preface|foreword|afterword|conclusion|appendix|interlude|"
                   r"postscript|contents)\.?$", re.I)
NUMERAL = re.compile(r"^(?:[IVXLC]{1,7}|\d{1,3})\.?$")
MARK = re.compile(r"^#{1,6}\s+(.*)$")  # the closing marks are stripped in the code: see _marked
UNDERLINE = re.compile(r"^(=+|-+)\s*$")
ENDS = {"the end", "fin", "finis", "end"}


def _marked(line: str) -> str:
    """The text of a line that opens with # marks, without its closing marks; "" for any other line. (A pattern
    that strips the closing marks itself takes time that grows with the square of a run of spaces.)"""
    m = MARK.match(line)
    return m.group(1).rstrip().rstrip("#").rstrip() if m else ""


def _blocks(text: str) -> list[list[str]]:
    """The file as blocks of lines. An empty line parts them; a file without empty lines has a block per line."""
    lines = [line.rstrip() for line in text.split("\n")]
    filled = [line for line in lines if line.strip()]
    if len(filled) > 3 and not any(not line.strip() for line in lines[lines.index(filled[0]):len(lines) - lines[::-1].index(filled[-1])]):
        return [[line] for line in filled]
    out: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            out[-1].append(line)
        elif out[-1]:
            out.append([])
    return [b for b in out if b]


def _capitals(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return (len(letters) >= 3 and len(line) <= 60 and all(c.isupper() for c in letters)
            and line.lower().strip(" .!") not in ENDS and not line.endswith((",", ";")))


def _heading(block: list[str], rule: str) -> str | None:
    """The block's words when it is a heading by this rule."""
    lines = [line.strip() for line in block]
    first = lines[0]
    if rule == "named":
        if len(lines) <= 2 and (NAMED.match(first) or (len(lines) == 1 and ALONE.match(first))) and len(" ".join(lines)) <= 140:
            return " ".join(lines)
    elif rule == "capitals":
        if len(lines) == 1 and _capitals(first):
            return first
    elif rule == "numerals":
        if len(lines) == 1 and NUMERAL.match(first):
            return first
    elif rule == "marks":
        text = _marked(first)
        if len(lines) == 1 and text:
            return text
        if len(lines) == 2 and UNDERLINE.match(lines[1]) and len(first) <= 120:
            return first
    return None


def _counts(blocks: list[list[str]], rule: str) -> int:
    if rule == "named":  # a lone "Preface" does not make a book of chapters; two numbered headings do
        return sum(1 for b in blocks if len(b) <= 2 and NAMED.match(b[0].strip()))
    return sum(1 for b in blocks if _heading(b, rule) is not None)


def read(text: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """The text as HTML; what is known of it; and how its chapter headings were found (None when none were)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    facts: dict[str, Any] = {}
    start = START.search(text)
    if start:
        for m in HEADER.finditer(text[:start.start()]):
            facts.setdefault({"Title": "title", "Author": "author", "Language": "language"}[m.group(1)], m.group(2).strip())
        if facts.get("language", "").lower() == "english":
            facts["language"] = "en"
        elif "language" in facts:
            del facts["language"]
        end = END.search(text, start.end())
        text = text[start.end():end.start() if end else len(text)]
    blocks = _blocks(text)
    rule = next((r for r in ("named", "capitals", "numerals", "marks") if _counts(blocks, r) >= 2), None)
    indented = [all(line.startswith("    ") for line in b) for b in blocks]
    verse = sum(indented) * 2 < len(blocks)  # where everything is indented, the indent is only the page's margin
    out: list[str] = []
    headings: list[str] = []
    taken = -1
    for n, block in enumerate(blocks):
        if n == taken:
            continue
        head = _heading(block, rule) if rule else None
        if head is not None:
            following = blocks[n + 1] if n + 1 < len(blocks) else None
            m = NAMED.match(head) if rule == "named" else None
            if m and not m.group(3) and following and len(following) == 1 and _heading(following, rule) is None:
                line = following[0].strip()
                if len(line) <= 70 and line[-1:] not in '.!?"”\',;:':  # a short line under "Chapter 4" is its title
                    head, taken = f"{head.rstrip('.:')}: {line}", n + 1
            headings.append(head)
            out.append(f"<h2>{escape(head)}</h2>")
            continue
        if n == 0 and "title" not in facts and len(block) == 1 and len(block[0].strip()) <= 100 and len(blocks) > 1:
            facts["title"] = block[0].strip().lstrip("# ").strip()
            out.append(f"<h1>{escape(facts['title'])}</h1>")
            continue
        if verse and indented[n] and len(block) > 1:
            margin = min(len(line) - len(line.lstrip()) for line in block)
            out.append("<pre>" + escape("\n".join(line[margin:] for line in block)) + "</pre>")
            continue
        out.append("<p>" + escape(" ".join(line.strip() for line in block)) + "</p>")
    found = {"rule": rule, "count": len(headings), "example": headings[0]} if rule else None
    return "\n".join(out), facts, found
