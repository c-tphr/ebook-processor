"""What the tests of other formats are made from: Word files and saved pages built inside the tests (nothing
binary is committed), the texts and pages they convert, and a small web site on this computer that serves them."""

from __future__ import annotations

import http.server
import io
import zipfile
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from ebook_processor.sources import fetch
from tests.prep.fixture.generate import lighthouse_png

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STYLES = (("Title", "Title"), ("Heading1", "heading 1"), ("Heading2", "heading 2"), ("Quote", "Quote"), ("Author", "Author"),
          ("Odd", "Something Odd"))


def _paragraph(style: str | None, text: str) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _picture(rel: str) -> str:
    return ('<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            '<wp:extent cx="952500" cy="952500"/><wp:docPr id="1" name="Picture 1" descr="The light"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="1" name="light.png"/>'
            f'<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rel}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></pic:blipFill><pic:spPr/></pic:pic>'
            "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>")


def make_docx(paragraphs: list[tuple[str | None, str]], title: str = "", author: str = "", picture: bytes | None = None) -> bytes:
    """A small Word file. ``paragraphs`` are (style id or None, text); the style ids are those of ``STYLES``. A
    paragraph whose text is ``<picture>`` is the picture."""
    body = "".join(_picture("rId9") if text == "<picture>" else _paragraph(style, text) for style, text in paragraphs)
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>{body}'
                "<w:sectPr/></w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
              + "".join(f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/></w:style>' for sid, name in STYLES)
              + "</w:styles>")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(title)}</dc:title><dc:creator>{escape(author)}</dc:creator>'
            "</cp:coreProperties>")
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>")
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                + ('<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/light.png"/>'
                   if picture else "") + "</Relationships>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        if picture:
            z.writestr("word/media/light.png", picture)
    return out.getvalue()


def make_mhtml(html: str, url: str, pictures: dict[str, bytes] | None = None) -> bytes:
    """A page saved as one file, as Chrome writes it: the page, then what it showed, each under its address."""
    message = MIMEMultipart("related", type="text/html")
    message["From"] = "<Saved by Blink>"
    message["Snapshot-Content-Location"] = url
    message["Subject"] = "A saved page"
    page = MIMEText(html, "html", "utf-8")
    page["Content-Location"] = url
    message.attach(page)
    for address, data in (pictures or {}).items():
        part = MIMEImage(data, "png")
        part["Content-Location"] = address
        message.attach(part)
    return message.as_bytes()


# ---- texts and pages


FILL = "The keeper lit the lamp at dusk and wrote the hour in the log. "
PNG = lighthouse_png()


def body(words: int = 300) -> str:
    return "\n\n".join(FILL * 6 for _ in range(max(1, words // 78)))


MARKDOWN = f"""---
title: On Keeping Lamps
author: Ione Hartwell
date: 2024-03-09
url: https://example.org/essays/lamps
---
# On Keeping Lamps

A lamp is a *promise* made to strangers. See [the almanac](https://example.org/almanac) and [[The log|the log]]. ^h3f9a

## The rule

1. Light at sunset.
2. Put out at sunrise.

> Whatever the weather.

| Hour | Task |
| --- | --- |
| 18 | Trim the wick |

```
oil = [[12]]
```

A footnote follows.[^1] <script>alert(1)</script>

![[Another note]]

[^1]: The keeper's own words.

{body(200)}
"""


PAGE = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>On Keeping Lamps | The Shore Review</title>
<meta name="author" content="Ione Hartwell"><meta property="article:published_time" content="2024-03-09T08:00:00Z">
<meta property="og:site_name" content="The Shore Review"><link rel="canonical" href="https://example.org/essays/lamps">
<script>var x = 1;</script><style>p {{ color: red }}</style></head>
<body><nav><a href="/">Home</a></nav><article><h1>On Keeping Lamps</h1><p>{FILL * 40}</p>
<figure><img src="light.png" alt="The light"><figcaption>Gannet Rock.</figcaption></figure>
<h2>The rule</h2><p>{FILL * 40}</p></article><footer>© The Shore Review</footer></body></html>"""


CLUTTER = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>On Keeping Lamps | The Shore Review</title>
<meta property="og:title" content="On Keeping Lamps"><meta name="author" content="Ione Hartwell">
<meta property="article:published_time" content="2024-03-09T08:00:00Z"><meta property="og:site_name" content="The Shore Review">
<script>var track = 1;</script></head>
<body><header><nav><a href="/">Home</a> <a href="/subscribe">Subscribe now</a></nav></header>
<div class="ad">Buy lamp oil today! Fifty percent off for new readers.</div>
<main><article><h1>On Keeping Lamps</h1><p class="byline">By Ione Hartwell</p>
<p>A lamp is a <em>promise</em> made to strangers. {FILL * 12}</p>
<figure><img src="/light.png" alt="The light"><figcaption>Gannet Rock, 1911.</figcaption></figure>
<h2>The rule</h2><p>{FILL * 14}</p><blockquote><p>Whatever the weather. {FILL * 3}</p></blockquote>
<h2>What the log shows</h2><p>{FILL * 14}</p></article></main>
<aside class="related"><h3>Related</h3><a href="/a">Ten lighthouses to visit</a></aside>
<div id="comments"><h3>Comments (2)</h3><p>Great piece! Posted by reader42.</p></div>
<footer><p>Copyright The Shore Review. <a href="/privacy">Privacy</a></p></footer></body></html>"""


# ---- a web site on this computer


LD = '<script type="application/ld+json">{"@type": "NewsArticle", "isAccessibleForFree": false, "wordCount": 4000}</script>'
PAGES = {
    "/essay": ("text/html; charset=utf-8", CLUTTER.encode()),
    "/latin": ("text/html; charset=iso-8859-1", CLUTTER.replace('<meta charset="utf-8">', "").replace("promise", "promesse naïve").encode("latin-1")),
    "/paywalled": ("text/html", CLUTTER.replace("</head>", LD + "</head>").encode()),
    "/shell": ("text/html", b"<html><head><title>App</title><script src='a.js'></script></head><body><noscript>Please enable JavaScript to "
                            b"read this.</noscript><div id='root'></div></body></html>"),
    "/thin": ("text/html", b"<html><body><nav>x</nav><article><h1>Sign in</h1><p>Sign in to read this article.</p></article></body></html>"),
    "/paper.pdf": ("application/pdf", b"%PDF-1.4"),
    "/notes.md": ("text/markdown", MARKDOWN.encode()),
    "/plain.txt": ("text/plain; charset=utf-8", ("A plain piece\n\n" + body(400)).encode()),
    "/data.zip": ("application/zip", b"PK"),
}


class Site(http.server.BaseHTTPRequestHandler):
    """Serves a picture: as it is, moved, and too large."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/light.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(PNG)
        elif self.path == "/moved.png":
            self.send_response(302)
            self.send_header("Location", "/light.png")
            self.end_headers()
        elif self.path == "/big.png":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(PNG + b"\0" * (fetch.PICTURE_LIMIT + 10))
        else:
            self.send_error(404)

    def log_message(self, *args: object) -> None:
        pass


class Web(Site):
    """Serves ``PAGES``, and the addresses that move, refuse, are slow or unpack to too much."""

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in PAGES:
            kind, data = PAGES[path]
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.end_headers()
            self.wfile.write(data)
        elif path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/essay?utm_campaign=x")
            self.end_headers()
        elif path in ("/forbidden", "/limited", "/gone"):
            self.send_error({"/forbidden": 403, "/limited": 429, "/gone": 404}[path])
        elif path == "/slow":
            import time
            time.sleep(1.5)
            self.send_error(500)
        elif path in ("/packed", "/bomb"):  # an answer sent packed: an ordinary one, and one that unpacks to 60 MB
            import gzip
            data = gzip.compress(PAGES["/essay"][1] if path == "/packed" else b" " * (60 * 1024 * 1024))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(data)
        else:
            super().do_GET()
