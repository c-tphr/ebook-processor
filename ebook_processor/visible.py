"""The book with visible checkpoints, for any reading app.

After the last paragraph of each section that has a checkpoint, the chapter gets one block per question: the
question alone on a page, then its answer on the next page. Pages are forced with CSS page breaks written as inline
styles, which every mainstream reading system honours and which need no stylesheet added to the book.

With previews switched on, each of those sections also begins with a page that lists its questions, to read with
them in mind.

Like the firmware-format build, this splices bytes into the original documents and re-serialises nothing: outside
the inserted blocks every byte of every document is the original's. The only other change is the title in the
package document, which gets a suffix so that the two editions can be told apart in a library.
"""

from __future__ import annotations

from html import escape
from typing import Any

from . import forms as qforms
from .prep.build import splice
from .prep.draft import PlannedSection
from .prep.epub import DC_NS, Block, Book, EpubError, load_book_bytes
from .prep.xmltree import Element

# Elements that may hold a <div>: the checkpoint goes after the section's last block when its parent is one of
# these, else inside the block when the block is one (a list item in a list), else further out.
FLOW = frozenset({
    "body", "div", "section", "article", "main", "aside", "blockquote", "li", "dd", "dt", "td", "th", "details",
    "nav", "header", "footer", "form", "fieldset", "center", "figcaption",
})

PAGE = "page-break-before: always; break-before: page; margin: 0; padding: 2em 0 0 0; text-align: left; text-indent: 0;"
PAGE_END = " page-break-after: always; break-after: page;"
KICKER = "font-size: 0.8em; letter-spacing: 0.08em; text-transform: uppercase; margin: 0 0 1.5em 0; text-indent: 0;"
QUESTION = "font-size: 1.15em; font-weight: bold; line-height: 1.4; margin: 0 0 1.5em 0; text-indent: 0;"
HINT = "font-size: 0.9em; font-style: italic; margin: 0; text-indent: 0;"
RECAP = "font-size: 0.9em; font-style: italic; margin: 0 0 1.2em 0; text-indent: 0;"
ANSWER = "line-height: 1.5; margin: 0 0 0.8em 0; text-indent: 0;"
PREVIEW = "font-weight: bold; line-height: 1.4; margin: 0 0 1.2em 0; text-indent: 0;"
CHOICE = "line-height: 1.4; margin: 0 0 0.7em 1.2em; text-indent: 0;"
KEY = "font-weight: bold; line-height: 1.4; margin: 0 0 0.8em 0; text-indent: 0;"
LETTERS = "ABCD"


def insertion_point(block: Block, before: bool = False) -> tuple[int, Element, bool] | None:
    """(byte offset, element whose namespace prefix the new tags take, whether the place is further out than the
    block), or ``None`` when no element around the block may hold a ``div``. The place is after the block; with
    ``before`` it is in front of it, for a ``div`` that goes before the first block of a section."""
    el = block.element
    moved = False
    while True:
        parent = el.parent
        if parent is not None and parent.local in FLOW:
            return el.start if before else el.end_end, parent, moved
        if el.local in FLOW and not el.is_empty_tag:
            return el.start_end if before else el.end, el, moved
        if parent is None:
            return None
        el, moved = parent, True


def _paragraphs(text: str, style: str, tag: str, cls: str) -> str:
    return "".join(f'<{tag} class="{cls}" style="{style}">{escape(line, quote=False)}</{tag}>'
                   for line in text.split("\n") if line.strip())


def checkpoint_html(host: Element, checkpoint: dict[str, Any], label: str, number: int, break_after: bool) -> bytes:
    div, p, br = host.qname("div"), host.qname("p"), host.qname("br")
    prompts = [qforms.from_x(q) for q in checkpoint["prompts"]]
    pages = []
    for n, q in enumerate(prompts, start=1):
        form = qforms.form_of(q)
        count = f"Question {n} of {len(prompts)}" if len(prompts) > 1 else "Question"
        kicker = f"Checkpoint {number} · {label}" if label else f"Checkpoint {number}"
        # the question as this form shows it, and what the reader is to do before turning the page
        shown, hint = _paragraphs(q["prompt"], QUESTION, p, "ebp-prompt"), "Recall your answer, then turn the page."
        key = ""
        if form == "choice":
            shown += "".join(f'<{p} class="ebp-choice" style="{CHOICE}">{LETTERS[k]}.  {escape(c, quote=False)}</{p}>'
                             for k, c in enumerate(q["choices"]))
            hint, key = "Choose one, then turn the page.", f"{LETTERS[q['correct']]}.  {q['choices'][q['correct']]}"
        elif form == "truefalse":
            count += " · True or false?"
            hint, key = "Decide whether this is true, then turn the page.", qforms.key(q) + "."
        elif form == "cloze":
            hint, key = "Recall the missing words, then turn the page.", qforms.key(q)
        pages.append(
            f'<{div} class="ebp-question" style="{PAGE}">'
            f'<{p} class="ebp-kicker" style="{KICKER}">{escape(kicker, quote=False)}<{br}/>{count}</{p}>'
            + shown
            + f'<{p} class="ebp-hint" style="{HINT}">{hint}</{p}>'
            f"</{div}>")
        last = n == len(prompts)
        pages.append(
            f'<{div} class="ebp-answer" style="{PAGE}{PAGE_END if last and break_after else ""}">'
            f'<{p} class="ebp-kicker" style="{KICKER}">Answer · {count}</{p}>'
            + _paragraphs(q["prompt"], RECAP, p, "ebp-recap")
            + (_paragraphs(key, KEY, p, "ebp-key") if key else "")
            + _paragraphs(q["answer"], ANSWER, p, "ebp-text")
            + f"</{div}>")
    html = f'<{div} class="ebp-checkpoint" id="ebp-{checkpoint["id"]}">' + "".join(pages) + f"</{div}>"
    return html.encode("utf-8")


def preview_html(host: Element, checkpoint: dict[str, Any], label: str) -> bytes:
    """One page before a section: the questions its checkpoint will ask, without their answers."""
    div, p, br = host.qname("div"), host.qname("p"), host.qname("br")
    prompts = checkpoint["prompts"]
    count = f"{len(prompts)} questions to read for" if len(prompts) > 1 else "A question to read for"
    kicker = f"Coming up · {label}" if label else "Coming up"
    html = (f'<{div} class="ebp-preview" id="ebp-preview-{checkpoint["id"]}" style="{PAGE}{PAGE_END}">'
            f'<{p} class="ebp-kicker" style="{KICKER}">{escape(kicker, quote=False)}<{br}/>{count}</{p}>'
            + "".join(_paragraphs(q["prompt"], PREVIEW, p, "ebp-prompt") for q in prompts)  # as plain questions
            + f'<{p} class="ebp-hint" style="{HINT}">Keep these in mind as you read. They are asked at the end of '
              f"the section.</{p}></{div}>")
    return html.encode("utf-8")


def retitled_opf(book: Book, suffix: str) -> bytes | None:
    """The package document with ``suffix`` added to its first title, or ``None`` when there is nothing to change."""
    if not suffix:
        return None
    title = book.opf.root.first(DC_NS, "title")
    if title is None or title.is_empty_tag:
        return None
    return splice(book.opf.data, [(title.end, 0, escape(suffix, quote=False).encode("utf-8"))])


def build_visible(book: Book, draft: dict[str, Any], plan: list[PlannedSection],
                  title_suffix: str = " (checkpoints)", preview: bool = False) -> tuple[bytes, list[str]]:
    """The EPUB with visible checkpoints, and notes about checkpoints that could not be placed exactly.

    With ``preview``, each section that has a checkpoint begins with a page listing its questions."""
    sections = {s.id: s for s in plan}
    notes: list[str] = []
    by_doc: dict[str, list[tuple[int, int, bytes]]] = {}
    placed: list[str] = []
    previews: list[str] = []
    for number, checkpoint in enumerate(draft["checkpoints"], start=1):
        section = sections[checkpoint["section"]]
        last = section.blocks[-1]
        point = insertion_point(last)
        if point is None:
            notes.append(f"Checkpoint {checkpoint['id']} is missing from the book with visible checkpoints: "
                         f"nothing around {last.ref} can hold it.")
            continue
        offset, host, moved = point
        doc = section.doc
        assert doc.stream is not None
        if moved and doc.stream.range_of(host)[1] > section.end:
            notes.append(f"Checkpoint {checkpoint['id']} appears a little after the end of its section "
                         f"({last.ref}), because the section ends inside a <{host.local}>.")
        more = section.end < len(doc.words)  # text follows in this document: it must start on a new page
        # Where two insertions meet, they go in reading order: checkpoint 1, preview 2, checkpoint 2, ...
        by_doc.setdefault(doc.zip_path, []).append(
            (offset, 2 * number, checkpoint_html(host, checkpoint, section.label, number, more)))
        placed.append(checkpoint["id"])
        if preview:
            before = insertion_point(section.blocks[0], before=True)
            if before is None:
                notes.append(f"The preview of checkpoint {checkpoint['id']} is missing from the book with visible "
                             f"checkpoints: nothing around {section.blocks[0].ref} can hold it.")
            else:
                by_doc[doc.zip_path].append((before[0], 2 * number - 1, preview_html(before[1], checkpoint, section.label)))
                previews.append(checkpoint["id"])
    replace = {}
    for sd in book.spine:
        if sd.zip_path in by_doc:
            assert sd.doc is not None
            replace[sd.zip_path] = splice(sd.doc.data, by_doc[sd.zip_path])
    opf = retitled_opf(book, title_suffix)
    if opf is not None:
        replace[book.opf_path] = opf
    data = book.archive.rebuild(replace, []) if replace else book.archive.data
    try:
        result = load_book_bytes(data, "visible")
    except EpubError as exc:  # pragma: no cover - a fault of this tool
        raise AssertionError(f"the book with visible checkpoints does not open: {exc}") from exc
    ids = {i for sd in result.spine if sd.doc is not None for i in sd.doc.ids}
    missing = [c for c in placed if f"ebp-{c}" not in ids] + [f"preview of {c}" for c in previews if f"ebp-preview-{c}" not in ids]
    assert not missing, f"checkpoints {missing} are not in the built book"
    return data, notes
