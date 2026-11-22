"""Any HTML, as the small set of markup the app reads.

Every converter hands its HTML to ``clean``. What comes back is a ``div`` whose children are ready to be written
as XHTML: only tags the reader shows, only a handful of attributes, no scripts, no menus, no forms. Nothing else in
the package decides what markup survives.

The tree is lxml's, because its parser mends broken markup the way a browser does.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import lxml.etree as ET
import lxml.html

# Kept as they are. The reader's own list (reading.KEEP) holds all of these.
INLINE = frozenset("em i b strong u s small sub sup code kbd samp var cite q abbr mark del ins time a br img".split())
BLOCK = frozenset("""p h1 h2 h3 h4 h5 h6 blockquote ul ol li dl dt dd pre hr figure figcaption table thead tbody tfoot
tr td th caption div""".split())
KEEP = INLINE | BLOCK
# Containers: the tag goes, a ``div`` stays, so that the text inside is still a block of its own.
BOXES = frozenset("article section main center details summary hgroup address fieldset body html header".split())
RENAME = {"tt": "code", "strike": "s", "listing": "pre", "xmp": "pre", "dir": "ul", "menu": "ul"}
# Gone with everything inside.
DROP = frozenset("""script style noscript template nav footer form button iframe object embed svg canvas video audio
select input textarea dialog head title link meta base map area source track param applet frame frameset
datalist""".split())
ATTRS = {"a": ("href",), "img": ("src", "alt"), "td": ("colspan", "rowspan"), "th": ("colspan", "rowspan"),
         "ol": ("start",)}
NOTE_ROLES = frozenset({"doc-noteref", "doc-footnote", "doc-endnote", "doc-endnotes"})
EPUB_ROLES = {"noteref": "doc-noteref", "footnote": "doc-footnote", "endnote": "doc-endnote", "endnotes": "doc-endnotes",
              "rearnote": "doc-endnote", "rearnotes": "doc-endnotes", "footnotes": "doc-endnotes"}
LAZY = ("data-src", "data-original", "data-lazy-src", "data-url", "data-orig-file")
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# What XML 1.0 does not allow in a document, and lone surrogates.
FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿\ud800-\udfff]")
XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
SPACE = re.compile(r"\s+")
WORD = re.compile(r"\w+", re.UNICODE)


def words(el: Any) -> int:
    return len(WORD.findall(el.text_content()))


def parse(html: str) -> Any:
    """The document's ``body``, mended."""
    html = FORBIDDEN.sub("", XML_DECL.sub("", html))
    if not html.strip():
        html = "<p></p>"
    try:
        root = lxml.html.document_fromstring(html)
    except (ET.ParserError, ET.XMLSyntaxError, ValueError):
        root = lxml.html.document_fromstring("<p></p>")
    body = root.find("body")
    return body if body is not None else root


def _picture_address(el: Any) -> str:
    """Where an image's picture is. Pages that load pictures late keep a placeholder in ``src``."""
    src = (el.get("src") or "").strip()
    placeholder = not src or (src.startswith("data:") and len(src) < 400) or src.startswith("data:image/svg")
    if not placeholder:
        return src
    for name in LAZY:
        if (el.get(name) or "").strip():
            return el.get(name).strip()
    best, size = "", -1.0
    for part in (el.get("srcset") or el.get("data-srcset") or "").split(","):
        bits = part.split()
        if not bits:
            continue
        try:
            width = float(bits[1][:-1]) if len(bits) > 1 else 0.0
        except ValueError:
            width = 0.0
        if width > size:
            best, size = bits[0], width
    return best or src


OPS_TYPE = "{http://www.idpf.org/2007/ops}type"
ET.register_namespace("epub", "http://www.idpf.org/2007/ops")


def is_note(el: Any) -> bool:
    return bool(NOTE_ROLES.intersection((el.get("role") or "").split())) or el.get(OPS_TYPE) == "footnote"


def _mark_note(el: Any) -> None:
    """Say that an element is the body of a footnote, in the way its tag allows: a list item takes no such
    ``role``, so it says it with ``epub:type``."""
    if el.tag == "li":
        el.attrib.pop("role", None)
        el.set(OPS_TYPE, "footnote")
    else:
        el.set("role", "doc-footnote")


def _roles(el: Any) -> str:
    """The footnote marks of an element, whichever way the source wrote them."""
    found = [r for r in (el.get("role") or "").split() if r in NOTE_ROLES]
    found += [EPUB_ROLES[t] for t in (el.get("epub:type") or "").split() if t in EPUB_ROLES]
    return " ".join(dict.fromkeys(found))


def _hidden(el: Any) -> bool:
    return (el.get("hidden") is not None or (el.get("aria-hidden") or "").lower() == "true"
            or bool(HIDDEN_STYLE.search(el.get("style") or "")))


def _layout(table: Any) -> bool:
    """A table that only arranges the page, as old pages have: its cells hold the text itself."""
    if table.find(".//th") is not None or table.find(".//caption") is not None:
        return False
    cells = [c for c in table.iter("td") if _owner(c) is table]
    if not cells:
        return True
    rows = [r for r in table.iter("tr") if _owner(r) is table]
    widest = max((len([c for c in r if c.tag == "td"]) for r in rows), default=1)
    if len(rows) < 2 or widest < 2:
        return True
    blocks = ("p", "div", "table", "ul", "ol", "blockquote", "pre") + HEADINGS
    if any(c.find(".//" + b) is not None for c in cells for b in blocks):
        return True
    return sum(words(c) for c in cells) / len(cells) > 40


def _owner(el: Any) -> Any:
    parent = el.getparent()
    while parent is not None and parent.tag != "table":
        parent = parent.getparent()
    return parent


def _walk(el: Any, base: str | None) -> None:
    for child in list(el):
        tag = child.tag
        if not isinstance(tag, str):  # a comment, a processing instruction
            child.drop_tree()
            continue
        tag = tag.lower().rsplit(":", 1)[-1] if ":" in tag else tag.lower()
        roles = _roles(child)
        if tag in DROP or _hidden(child) or "doc-backlink" in (child.get("role") or "").split() \
                or (tag == "aside" and not roles):
            child.drop_tree()
            continue
        if tag == "header" and (child.find(".//nav") is not None or child.find(".//h1") is None):
            child.drop_tree()  # the top of a page, and not the heading of the piece
            continue
        if tag == "math":
            text = child.get("alttext") or SPACE.sub(" ", child.text_content()).strip()
            replace(child, text)
            continue
        if tag == "picture":
            image = child.find(".//img")
            if image is None:
                child.drop_tree()
                continue
            image.tail = None
            for other in list(child):
                child.remove(other)
            child.text = None
            child.append(image)
        if tag == "table" and _layout(child):
            for part in list(child.iter("thead", "tbody", "tfoot", "tr", "td", "colgroup", "col")):
                if _owner(part) is child:
                    part.tag = "div" if part.tag in ("td", "tr") else "span"
            tag = "div"
        _walk(child, base)
        tag = RENAME.get(tag, tag)
        if tag == "aside":  # a footnote
            tag = "div"
        if tag in BOXES:
            tag = "div"
        if tag not in KEEP:
            child.drop_tag()
            continue
        child.tag = tag
        kept: dict[str, str] = {}
        if tag == "img":
            src = _picture_address(child)
            if base and src and not src.startswith("data:"):
                src = urllib.parse.urljoin(base, src)
            kept = {"src": src, "alt": SPACE.sub(" ", child.get("alt") or "").strip()}
        elif tag == "a":
            href = (child.get("href") or "").strip()
            if href and not href.startswith("#") and base:
                href = urllib.parse.urljoin(base, href)
            scheme = urllib.parse.urlsplit(href).scheme.lower() if not href.startswith("#") else "#"
            if href and scheme in ("http", "https", "mailto", "#"):
                kept["href"] = href
        else:
            for name in ATTRS.get(tag, ()):
                value = (child.get(name) or "").strip()
                if value.isdigit():
                    kept[name] = value
        anchor = (child.get("id") or (child.get("name") if tag == "a" else "") or "").strip()
        child.attrib.clear()
        child.attrib.update(kept)
        if anchor:
            child.set("id", anchor)
        if tag == "a" and "doc-noteref" in roles.split():
            child.set("role", "doc-noteref")
        elif tag in ("li", "div", "p") and roles and roles != "doc-noteref":
            _mark_note(child)  # a list of notes says nothing itself: its items say what they are
        if tag == "a" and "href" not in kept and not anchor:
            child.drop_tag()


def replace(el: Any, text: str) -> None:
    """Put ``text`` where the element stood."""
    tail = text + (el.tail or "")
    previous, parent = el.getprevious(), el.getparent()
    if previous is not None:
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(el)


def _paragraph(group: list[Any]) -> Any:
    """A paragraph that holds texts and elements, in the order they are given."""
    p = ET.Element("p")
    last = None
    for item in group:
        if not isinstance(item, str):
            p.append(item)
            last = item
        elif last is None:
            p.text = (p.text or "") + item
        else:
            last.tail = (last.tail or "") + item
    return p


def _breaks(root: Any) -> None:
    """Two line breaks in a row mean a new paragraph in pages written without ``p``."""

    def is_break(item: Any) -> bool:
        return not isinstance(item, str) and item.tag == "br"

    for el in [root] + list(root.iter("div", "blockquote", "li", "dd", "td", "p")):
        kids = list(el)
        if not any(a.tag == "br" and b.tag == "br" and not (a.tail or "").strip() for a, b in zip(kids, kids[1:])):
            continue
        items: list[Any] = [el.text or ""]
        for k in kids:
            items += [k, k.tail or ""]
            k.tail = None
            el.remove(k)
        el.text = None
        runs: list[list[Any]] = [[]]
        i = 0
        while i < len(items):
            j, count = i, 0
            while j < len(items) and (is_break(items[j]) or (isinstance(items[j], str) and not items[j].strip())):
                count += is_break(items[j])
                j += 1
            if count >= 2:
                runs.append([])
                i = j
            else:
                runs[-1].append(items[i])
                i += 1
        made: list[Any] = []
        for run in runs:
            group: list[Any] = []
            for item in run + [None]:
                if item is None or (not isinstance(item, str) and item.tag in BLOCK):
                    if any(not isinstance(x, str) or x.strip() for x in group):
                        made.append(_paragraph(group))
                    group = []
                    if item is not None:
                        made.append(item)
                else:
                    group.append(item)
        parent = el.getparent()
        if el.tag == "p" and el is not root and parent is not None:
            at, tail = parent.index(el), el.tail
            parent.remove(el)
            for n, item in enumerate(made):
                parent.insert(at + n, item)
            if made:
                made[-1].tail = tail
        else:
            for item in made:
                el.append(item)


def _empty(root: Any) -> None:
    """Take away what holds neither words nor a picture."""
    solid = ("img", "br", "hr", "td", "th", "table")
    for el in reversed(list(root.iter())):
        if el is root or el.tag in solid or el.getparent() is None:
            continue
        if (el.text or "").strip() or len(el) or el.get("id"):
            continue
        el.drop_tree()


def _notes(root: Any) -> None:
    """Footnotes as Markdown and Word converters write them: a raised number that links to an item of a list at
    the end, and a link back. Marked the way the reader knows (``role``), with the way back taken out."""
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    for link in list(root.iter("a")):
        href = link.get("href") or ""
        if not href.startswith("#") or link.getparent() is None:
            continue
        target = by_id.get(urllib.parse.unquote(href[1:]))
        if target is None:
            continue
        raised = link.getparent().tag == "sup" or link.find("sup") is not None
        body = target if target.tag in ("li", "div", "p") else None
        if raised and body is not None and re.match(r"(fn|footnote|endnote|note)", target.get("id"), re.I):
            link.set("role", "doc-noteref")
            if not is_note(body):
                _mark_note(body)
    notes = [el for el in root.iter() if el.tag != "a" and is_note(el)]
    for body in notes:
        for link in list(body.iter("a")):
            href = link.get("href") or ""
            target = by_id.get(urllib.parse.unquote(href[1:])) if href.startswith("#") else None
            if target is None or link.getparent() is None:
                continue
            marks = [target] + list(target.iter("a")) + ([target.getparent()] if target.getparent() is not None else [])
            if any("doc-noteref" in (m.get("role") or "") for m in marks if m is not None) or target.tag == "sup":
                previous = link.getprevious()  # the space before the arrow goes with it
                if previous is None and link.getparent().text:
                    link.getparent().text = link.getparent().text.rstrip("\xa0 ")
                link.drop_tree()


def _anchors(root: Any) -> None:
    """Keep an ``id`` only where a link of the document points to it, under a plain name; a link to nowhere
    keeps its words and loses the link."""
    wanted: dict[str, str] = {}
    for link in root.iter("a"):
        href = link.get("href") or ""
        if href.startswith("#") and len(href) > 1:
            wanted.setdefault(urllib.parse.unquote(href[1:]), f"a{len(wanted) + 1}")
    seen: set[str] = set()
    for el in list(root.iter()):
        anchor = el.get("id")
        if anchor is None:
            continue
        if anchor in wanted and anchor not in seen:
            seen.add(anchor)
            el.set("id", wanted[anchor])
        else:
            del el.attrib["id"]
    for link in list(root.iter("a")):
        href = link.get("href") or ""
        if not href.startswith("#"):
            continue
        name = urllib.parse.unquote(href[1:])
        if name in seen:
            link.set("href", "#" + wanted[name])
        else:
            del link.attrib["href"]
            if not link.get("id"):
                link.drop_tag()
    for el in list(root.iter("a")):  # an empty anchor that nothing points to any more
        if not el.get("href") and not el.get("id") and el.getparent() is not None:
            el.drop_tag()


def _tidy(root: Any) -> None:
    """Cells that lost their table, and boxes that hold nothing but one other box."""
    for el in list(root.iter("td", "th", "tr", "thead", "tbody", "tfoot", "caption")):
        if _owner(el) is None:
            el.tag = "div"
    for el in list(root.iter("li", "dt", "dd")):
        parent = el.getparent()
        if parent is None or parent.tag not in (("ul", "ol") if el.tag == "li" else ("dl",)):
            el.tag = "div"
    for el in reversed(list(root.iter("div"))):
        parent = el.getparent()
        if el is root or parent is None or el.get("id") or is_note(el):
            continue
        only = len(parent) == 1 and not (parent.text or "").strip() and not (el.tail or "").strip()
        if only and parent.tag == "div":
            el.drop_tag()


def loose(root: Any) -> None:
    """Words and inline markup that stand loose between the blocks of the body go into paragraphs."""
    items: list[Any] = [root.text or ""]
    for k in list(root):
        items += [k, k.tail or ""]
        k.tail = None
        root.remove(k)
    root.text = None
    group: list[Any] = []
    for item in items + [None]:
        if item is None or (not isinstance(item, str) and item.tag in BLOCK):
            if any(not isinstance(x, str) or x.strip() for x in group):
                p = _paragraph(group)
                root.append(p)
                p.text = (p.text or "").lstrip() or None
            group = []
            if item is not None:
                root.append(item)
        else:
            group.append(item)


def clean(html: str, base: str | None = None) -> Any:
    """``html`` as a ``div`` of clean markup: its children are blocks. ``base`` is the address the HTML came
    from: links and pictures written relative to it are made whole."""
    body = parse(html)
    body.tag = "div"
    for name in list(body.attrib):
        del body.attrib[name]
    _walk(body, base)
    _tidy(body)
    _breaks(body)
    _notes(body)
    _anchors(body)
    _empty(body)
    loose(body)
    return body


def first_heading(root: Any) -> Any:
    """The heading the text opens with, when it opens with one."""
    for el in root.iter():
        if el is root:
            if (el.text or "").strip():
                return None
            continue
        if el.tag in HEADINGS:
            return el
        if el.tag == "img" or (el.text or "").strip() or (el.tail or "").strip():
            return None
    return None


def plain(el: Any) -> str:
    return SPACE.sub(" ", el.text_content()).strip()


def xhtml(elements: list[Any]) -> str:
    """Elements as XHTML text (what stands inside ``body``)."""
    out = []
    for el in elements:
        tail, el.tail = el.tail, None
        out.append(ET.tostring(el, encoding="unicode", method="xml"))
        el.tail = tail
    return "\n".join(out)
