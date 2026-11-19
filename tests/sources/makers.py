"""Word files and saved pages built inside tests (nothing binary is committed)."""

from __future__ import annotations

import io
import zipfile
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

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
