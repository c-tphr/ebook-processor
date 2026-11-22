"""What the text of a note looks like: a link, a tag, a heading, a block id, a code fence.

    [[Title]], [[Title|shown]], [[Title#Heading]], [[Title#^block]]     a link; with ! before it, an embed
    #tag                                                                 a tag
    ## A heading
    A paragraph with a block id at its end. ^h3f9a

These are the patterns, and the small functions that read with them. The notes folder (``vault.py``) reads its
notes with them. The Markdown converter and a book's file use them too, and need no notes folder for it. The two
link patterns in the pages (``notes.js`` and the editor) follow ``LINK_RE``.
"""

from __future__ import annotations

import re

LINK_RE = re.compile(r"(!?)\[\[([^\[\]|#\n]*)(?:#([^\[\]|\n]*))?(?:\|([^\[\]\n]*))?\]\]")
TAG_RE = re.compile(r"(?<![\w/#&])#((?:[\w/-]*[^\W\d][\w/-]*))")
_HEAD_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
BLOCK_ID = r"\^([A-Za-z0-9-]+)"  # a block id as it is written, for the patterns that look for one
BLOCK_RE = re.compile(rf"(?:^|\s){BLOCK_ID}\s*$")
NOTE_END = "-note"  # the block id of the note on a highlight: the highlight's id, and this
FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
ESCAPE_RE = re.compile(r"\\[\\`*_\[\]<>#~=^|!]")  # a backslash before a mark of Markdown


def head_of(line: str) -> tuple[int, str] | None:
    """A heading line as (level, text), its closing marks and spaces taken off; None for any other line.

    The closing marks are stripped here and not in the pattern: a pattern that tries every place of a long run of
    spaces takes time that grows with the cube of the run."""
    m = _HEAD_RE.match(line)
    return (len(m.group(1)), m.group(2).rstrip(" \t").rstrip("#").rstrip(" \t")) if m else None


def entry_of(block: str) -> str:
    """The entry of a book's file that a block id belongs to: the note on a highlight belongs to its highlight."""
    return block[:-len(NOTE_END)] if block.endswith(NOTE_END) else block


def mask_code(text: str) -> str:
    """The text with code blanked out, letter for letter, so that what is found in it is not inside code."""
    out, fence = [], ""
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if fence:
            if m and m.group(1) == fence:
                fence = ""
            out.append(" " * len(line))
        elif m:
            fence = m.group(1)
            out.append(" " * len(line))
        else:
            line = ESCAPE_RE.sub("  ", line)  # an escaped mark is not a mark
            out.append(re.sub(r"(`+)(.+?)\1", lambda c: " " * len(c.group(0)), line))
    return "\n".join(out)


def unescape(text: str) -> str:
    return ESCAPE_RE.sub(lambda m: m.group(0)[1], text)
