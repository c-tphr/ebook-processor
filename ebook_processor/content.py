"""The book's text as Claude sees it: numbered blocks, with tables, code, formulas and figures by content mode.

``prose``    non-prose blocks are one-line placeholders (``[Table: ...]``).
``text``     the placeholder is followed by the block's own text: table rows, code lines, a formula's text.
``figures``  as ``text``, and each image is also written to the work folder so that Claude can look at it.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from .prep.epub import Block, Book, SpineDoc, squash
from .prep.extract import display_text

MAX_NONPROSE_CHARS = 6000  # of one table, listing or formula shown to Claude
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def _clip(text: str) -> str:
    if len(text) <= MAX_NONPROSE_CHARS:
        return text
    return text[:MAX_NONPROSE_CHARS].rstrip() + "\n[... cut here; the rest is not shown]"


def _table_text(block: Block) -> str:
    rows = []
    for row in (r for r in block.element.iter() if r.local == "tr"):
        cells = [squash(c.text()) for c in row.elements() if c.local in ("td", "th")]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def nonprose_text(block: Block) -> str | None:
    """The text inside a table, code block or formula, or ``None`` when it has none worth showing."""
    el = block.element
    kinds = {e.local for e in el.iter()}
    if "table" in kinds:
        text = _table_text(block)
    elif "pre" in kinds:
        pre = next(e for e in el.iter() if e.local == "pre")
        text = pre.text().strip("\n")
    elif "math" in kinds:
        math = next(e for e in el.iter() if e.local == "math")
        text = math.get("alttext") or squash(math.text())
    else:
        return None
    return _clip(text) if text.strip() else None


def image_sources(book: Book, sd: SpineDoc, block: Block) -> list[tuple[str, str]]:
    """(zip path, file extension) of each raster image inside ``block`` that the archive holds."""
    by_path = {m.zip_path: m.media_type for m in book.manifest}
    out = []
    for el in block.element.iter():
        src = el.get("src") if el.local == "img" else (el.get(XLINK_HREF) or el.get("href")) if el.local == "image" else None
        if not src or ":" in src.split("/")[0]:
            continue
        path = posixpath.normpath(posixpath.join(posixpath.dirname(sd.zip_path), src.split("#")[0]))
        ext = IMAGE_TYPES.get(by_path.get(path, ""))
        if ext and path in book.archive.by_name:
            out.append((path, ext))
    return out


def export_images(book: Book, folder: Path) -> dict[str, list[str]]:
    """Write every figure's images to ``folder``; return block reference -> file names."""
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[str]] = {}
    for sd in book.spine:
        for block in sd.blocks:
            if block.kind != "image":
                continue
            for n, (path, ext) in enumerate(image_sources(book, sd, block), start=1):
                name = f"{block.ref}{'' if n == 1 else f'-{n}'}{ext}"
                (folder / name).write_bytes(book.archive.read(path))
                out.setdefault(block.ref, []).append(name)
    return out


def block_header(block: Block) -> str:
    kind = block.kind + (f" {block.element.local}" if block.kind == "heading" else "")
    return f"[{block.ref}] {kind}, {block.word_count} word{'' if block.word_count == 1 else 's'}"


def block_body(sd: SpineDoc, block: Block, mode: str, images: dict[str, list[str]] | None = None) -> str:
    text = display_text(sd, block)
    if block.placeholder is None or mode == "prose":
        return text
    inner = nonprose_text(block)
    if inner:
        text += "\n" + inner
    if mode == "figures" and images and block.ref in images:
        text += "\n" + "\n".join(f"[image file: {name}]" for name in images[block.ref])
    return text


def render_blocks(sd: SpineDoc, blocks: list[Block], mode: str, images: dict[str, list[str]] | None = None) -> str:
    return "\n\n".join(f"{block_header(b)}\n{block_body(sd, b, mode, images)}" for b in blocks)


def passage_text(block: Block) -> str:
    """A block as quoted on a flashcard: its words, or for a non-prose block its text or placeholder."""
    if block.placeholder is None:
        return " ".join(block.words)
    return nonprose_text(block) or block.placeholder


def clipped(block: Block, words: int = 60) -> str:
    """One line of context: the block's reference and its first words."""
    if block.placeholder is not None:
        return f"[{block.ref}] {block.placeholder}"
    shown = block.words[:words]
    return f"[{block.ref}] {' '.join(shown)}{' ...' if len(block.words) > words else ''}"
