"""A Word file (``.docx``)."""

from __future__ import annotations

import html
import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

# Word's built-in styles that mammoth does not know by itself.
STYLES = """
p[style-name='Title'] => h1.docx-title:fresh
p[style-name='Subtitle'] => p:fresh
p[style-name='Quote'] => blockquote > p:fresh
p[style-name='Intense Quote'] => blockquote > p:fresh
p[style-name='Block Text'] => blockquote > p:fresh
p[style-name='Source Code'] => pre:separator('\\n')
p[style-name='HTML Preformatted'] => pre:separator('\\n')
p[style-name='Plain Text'] => pre:separator('\\n')
p[style-name='Author'] => p.docx-author:fresh
p[style-name='Date'] => p.docx-date:fresh
r[style-name='Verbatim Char'] => code
r[style-name='HTML Code'] => code
"""
DC = "{http://purl.org/dc/elements/1.1/}"
BYLINE = re.compile(r'<p class="docx-(author|date)">(.*?)</p>', re.S)
TAG = re.compile(r"<[^>]+>")
TITLE = '<h1 class="docx-title">'
OLD_WORD = b"\xd0\xcf\x11\xe0"  # how a .doc of before 2007 begins


MAX_TEXT_BYTES = 32 * 1024 * 1024       # word/document.xml, unpacked
MAX_UNPACKED_BYTES = 300 * 1024 * 1024  # the whole file, unpacked: pictures are most of a large one


class NotWord(ValueError):
    pass


def facts(data: bytes) -> dict[str, Any]:
    """What the file's own properties say of it."""
    out: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            core = ElementTree.fromstring(z.read("docProps/core.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, OSError):
        return out
    # The day a file was made is not the day a piece was published, so no date is taken from here.
    for key, tag in (("title", DC + "title"), ("author", DC + "creator"), ("language", DC + "language")):
        value = re.sub(r"\s+", " ", core.findtext(tag) or "").strip()
        if value:
            out[key] = value
    return out


def read(data: bytes) -> tuple[str, dict[str, Any], int]:
    """The file as HTML (its pictures written into it); what is known of it; how many kinds of formatting were
    not recognised."""
    if data.startswith(OLD_WORD):
        raise NotWord("This is a Word file of the old kind (.doc). Open it in Word and save it as .docx.")
    try:  # what the file says it unpacks to, before the library unpacks it: the text of a long book is a few megabytes
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            sizes = {i.filename: i.file_size for i in z.infolist()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise NotWord("This file cannot be read as a Word file.") from exc
    if sizes.get("word/document.xml", 0) > MAX_TEXT_BYTES or sum(sizes.values()) > MAX_UNPACKED_BYTES:
        raise NotWord("This Word file unpacks to more than the app reads: "
                      f"{MAX_TEXT_BYTES // (1024 * 1024)} MB of text, {MAX_UNPACKED_BYTES // (1024 * 1024)} MB in all.")
    import mammoth

    try:
        result = mammoth.convert_to_html(io.BytesIO(data), style_map=STYLES)
    except Exception as exc:  # noqa: BLE001 - whatever the library raises on a file that is no Word file
        raise NotWord("This file cannot be read as a Word file.") from exc
    unknown = sum(1 for m in result.messages if "nrecognised" in m.message)
    known = facts(data)
    # A title page's author and date lines say who and when; they are no paragraphs of the text.
    # What the pages themselves say comes before the file's properties, which are often left over from a template.
    for m in BYLINE.finditer(result.value):
        words = html.unescape(TAG.sub("", m.group(2))).strip()
        if m.group(1) == "author" and words:
            known["author"] = words
    text = BYLINE.sub("", result.value)
    if TITLE in text:
        import lxml.html

        root = lxml.html.fragment_fromstring(text, create_parent="div")
        titles = [el for el in root.iter("h1") if el.get("class") == "docx-title"]
        known.pop("title", None)
        # Word's "Title" stands above "Heading 1": the headings move down one step to make room for it.
        for level in (5, 4, 3, 2, 1):
            for el in list(root.iter(f"h{level}")):
                if el not in titles:
                    el.tag = f"h{level + 1}"
        for extra in titles[1:]:
            extra.tag = "p"
        text = (root.text or "") + "".join(lxml.html.tostring(child, encoding="unicode") for child in root)
    return text, known, unknown
