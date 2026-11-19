"""Markdown, text and HTML files turned into EPUBs: ``ebook_processor.sources``."""

from __future__ import annotations

import base64
import http.server
import threading
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from ebook_processor import reading, sources
from ebook_processor.prep.epub import load_book_bytes
from ebook_processor.sources import clean as C
from ebook_processor.sources import fetch, from_text, pack
from ebook_processor.sources.chars import decode
from tests.prep.fixture.generate import lighthouse_png

FILL = "The keeper lit the lamp at dusk and wrote the hour in the log. "
PNG = lighthouse_png()


def body(words: int = 300) -> str:
    return "\n\n".join(FILL * 6 for _ in range(max(1, words // 78)))


def kinds(made: sources.Made, doc: int = 0) -> list[str]:
    return [b.kind for b in made.book.spine[doc].blocks]


def shown(made: sources.Made, doc: int = 0) -> str:
    return reading.render_chapter(made.book, made.book.spine[doc])


# ---- cleaning


def cleaned(html: str, base: str | None = None) -> str:
    return C.xhtml(list(C.clean(html, base))).replace("\n", "")


def test_broken_markup_is_mended_and_scripts_go() -> None:
    out = cleaned("<p>One <b>two<p>Three &nbsp;&amp; <i>four</p><script>x()</script><div onclick='x()' class='c'>five</div>")
    assert out == "<p>One <b>two</b></p><p>Three \xa0&amp; <i>four</i></p><div>five</div>"


def test_menus_forms_hidden_things_and_comments_go() -> None:
    out = cleaned('<header><nav>Menu</nav></header><header><h1>Title</h1></header><aside>Related</aside><form>f</form>'
                  '<p hidden>no</p><p style="display: none">no</p><p aria-hidden="true">no</p><p>yes</p><footer>foot</footer><!-- c -->')
    assert out == "<div><h1>Title</h1></div><p>yes</p>"


def test_links_keep_only_the_web_and_the_document_itself() -> None:
    out = cleaned('<p><a href="javascript:alert(1)">bad</a> <a href="/x">rel</a> <a href="https://e.org/a" onclick="x">ok</a> '
                  '<a href="#nowhere">gone</a> <a href="#here">in</a></p><h2 id="here">Here</h2><h2 id="unused">U</h2>',
                  "https://e.org/essays/one")
    assert 'bad <a href="https://e.org/x">rel</a> <a href="https://e.org/a">ok</a> gone' in out
    assert "javascript" not in out and "onclick" not in out and "unused" not in out
    assert '<a href="#a2">in</a>' in out and '<h2 id="a2">Here</h2>' in out


def test_two_line_breaks_part_paragraphs() -> None:
    assert cleaned("<div><font>First.<br><br>Second <i>one</i>.<br>same<br><br><br>Third.</font></div>") == \
        "<p>First.</p><p>Second <i>one</i>.<br/>same</p><p>Third.</p>"


def test_a_table_that_arranges_the_page_is_opened_and_a_real_one_is_kept() -> None:
    out = cleaned("<table><tr><td><table><tr><td>Hello there.<br><br>Second.</td></tr></table></td></tr></table>"
                  "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert out == "<div><p>Hello there.</p><p>Second.</p></div><table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"


def test_loose_words_go_into_paragraphs() -> None:
    assert cleaned("Loose <b>bold</b><p>para</p> tail") == "<p>Loose <b>bold</b></p><p>para</p><p>tail</p>"


def test_a_picture_loaded_late_is_found() -> None:
    out = cleaned('<picture><source srcset="a.webp"><img src="data:image/gif;base64,R0lGOD" data-src="/real.jpg" alt=" The  light ">'
                  '</picture><img srcset="s.jpg 300w, big.jpg 900w">', "https://e.org/essays/one")
    assert '<img src="https://e.org/real.jpg" alt="The light"/>' in out and '<img src="https://e.org/essays/big.jpg" alt=""/>' in out


def test_characters_xml_refuses_are_dropped() -> None:
    assert cleaned("<p>a\x00b\x0bc￾</p>") == "<p>abc</p>"


def test_a_large_file_is_cleaned_in_good_time() -> None:
    import time
    html = "<div>" + "".join(f"<p class='x'>Paragraph {n} <span><b>with</b> words</span> in it.</p>" for n in range(60000)) + "</div>"
    start = time.monotonic()
    root = C.clean(html)
    assert len(root.findall(".//p")) == 60000 and time.monotonic() - start < 20


# ---- Markdown


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


def test_markdown() -> None:
    made = sources.convert("lamps.md", MARKDOWN.encode(), web=False)
    assert (made.title, made.author, made.type, made.name) == ("On Keeping Lamps", "Ione Hartwell", "article", "On Keeping Lamps.epub")
    assert made.origin["format"] == "markdown" and made.origin["url"] == "https://example.org/essays/lamps"
    assert made.origin["published"] == "2024-03-09" and made.origin["original"] == "lamps.md"
    assert kinds(made)[:9] == ["heading", "paragraph", "heading", "list-item", "list-item", "blockquote", "table", "code", "paragraph"]
    html = shown(made)
    assert "the log." in html and "[[" not in html.split("<pre")[0] and "^h3f9a" not in html and "alert" not in html
    assert "oil = [[12]]" in html  # code is left as it is
    assert 'class="noteref"' in html and [b.kind for b in made.book.spine[0].notes] == ["footnote"]
    assert "Another note" not in html and "↩" not in html
    assert len(made.book.spine) == 1 and made.found is None


def test_the_same_file_gives_the_same_book() -> None:
    assert sources.convert("lamps.md", MARKDOWN.encode(), web=False).epub == sources.convert("lamps.md", MARKDOWN.encode(), web=False).epub


def test_the_made_book_is_a_sound_archive() -> None:
    made = sources.convert("lamps.md", MARKDOWN.encode(), web=False)
    with zipfile.ZipFile(BytesIO(made.epub)) as z:
        assert z.namelist()[0] == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED
        opf = z.read("OEBPS/content.opf").decode()
    assert "<dc:creator>Ione Hartwell</dc:creator>" in opf and "<dc:date>2024-03-09</dc:date>" in opf
    assert "<dc:source>https://example.org/essays/lamps</dc:source>" in opf
    assert load_book_bytes(made.epub, "x.epub").title == "On Keeping Lamps"


def test_title_and_author_can_be_overruled() -> None:
    made = sources.convert("lamps.md", MARKDOWN.encode(), web=False, title="Lamps", author="")
    assert (made.title, made.author, made.name) == ("Lamps", "", "Lamps.epub")
    assert shown(made).count("Lamps</h1>") == 1 and "On Keeping Lamps" not in shown(made)


def test_a_title_is_put_in_front_when_the_text_has_none() -> None:
    made = sources.convert("my_essay.md", body(300).encode())
    assert made.title == "my essay" and kinds(made)[0] == "heading" and ">my essay</h1>" in shown(made)


def test_a_long_file_is_cut_at_its_headings_and_a_short_one_is_not() -> None:
    chapters = "\n\n".join(f"## Part {n}\n\n{body(2200)}" for n in range(1, 4))
    made = sources.convert("long.md", f"# The Book\n\nA few words before.\n\n{chapters}".encode())
    assert [sd.title for sd in made.book.spine] == ["The Book", "Part 1", "Part 2", "Part 3"]
    assert made.found == {"rule": "headings", "count": 4, "example": "Part 3"} and made.words > 5000
    whole = sources.convert("long.md", f"# The Book\n\n{chapters}".encode(), split_how="none")
    assert len(whole.book.spine) == 1 and whole.found is None
    short = sources.convert("short.md", f"# Essay\n\n## One\n\n{body(300)}\n\n## Two\n\n{body(300)}".encode())
    assert len(short.book.spine) == 1


def test_notes_of_a_text_cut_into_chapters_are_still_reached() -> None:
    chapters = "\n\n".join(f"## Part {n}\n\n{body(2200)}\n\nA note.[^n{n}]\n\n[^n{n}]: Note {n}." for n in range(1, 4))
    made = sources.convert("long.md", f"# The Book\n\n{chapters}".encode())
    assert len(made.book.spine) == 3 and [len(sd.notes) for sd in made.book.spine] == [0, 0, 3]
    with zipfile.ZipFile(BytesIO(made.epub)) as z:
        first, last = z.read("OEBPS/d001.xhtml").decode(), z.read("OEBPS/d003.xhtml").decode()
    assert 'href="d003.xhtml#a1" role="doc-noteref"' in first and 'href="#a3" role="doc-noteref"' in last
    assert 'epub:type="footnote" id="a1"' in last.replace('id="a1" epub:type="footnote"', 'epub:type="footnote" id="a1"')
    assert "doc-footnote" not in last and "doc-endnote" not in last  # a list item takes neither role
    assert 'class="noteref"' in shown(made, 0) and 'data-go="3:a1"' in shown(made, 0)


def test_a_long_book_with_chapters_is_guessed_a_book() -> None:
    chapters = "\n\n".join(f"## Chapter {n}\n\n{body(7000)}" for n in range(1, 4))
    assert sources.convert("book.md", f"# The Book\n\n{chapters}".encode()).type == "book"


# ---- plain text


def test_text_with_named_chapters() -> None:
    chapters = "\n\n".join(f"CHAPTER {r}\n\nThe {r} watch\n\n{body(1800)}" for r in ("I", "II", "III"))
    made = sources.convert("lamp_book.txt", f"The Lamp Book\n\n{chapters}\n".encode())
    assert made.title == "The Lamp Book"
    assert [sd.title for sd in made.book.spine] == ["CHAPTER I: The I watch", "CHAPTER II: The II watch", "CHAPTER III: The III watch"]
    assert made.found == {"rule": "named", "count": 3, "example": "CHAPTER I: The I watch"}
    assert sources.convert("lamp_book.txt", f"The Lamp Book\n\n{chapters}\n".encode(), split_how="none").found is None


@pytest.mark.parametrize("heads, rule", [
    (["THE FIRST NIGHT", "THE SECOND NIGHT"], "capitals"), (["I", "II"], "numerals"), (["1.", "2."], "numerals"),
    (["# One", "# Two"], "marks"), (["One\n===", "Two\n==="], "marks"), (["Chapter One", "Chapter Two"], "named"),
    (["Part 1: The sea", "Part 2: The shore"], "named"),
])
def test_text_heading_rules(heads: list[str], rule: str) -> None:
    html, _, found = from_text.read("\n\n".join(f"{h}\n\n{body(300)}" for h in heads))
    assert found is not None and found["rule"] == rule and found["count"] == 2 and html.count("<h2>") == 2


def test_text_without_headings_is_one_piece() -> None:
    made = sources.convert("note.txt", f"A short title\n\n{body(400)}\n\nTHE END\n".encode())
    assert made.title == "A short title" and made.found is None and len(made.book.spine) == 1


def test_text_without_empty_lines_has_a_paragraph_per_line() -> None:
    made = sources.convert("lines.txt", "\n".join(FILL * 4 for _ in range(8)).encode())
    assert kinds(made).count("paragraph") == 8


def test_text_lines_are_joined_and_verse_is_kept() -> None:
    text = f"First line of a paragraph\nand its second line.\r\n\r\n    Indented verse\n      line two\n\n{body(300)}"
    html = shown(sources.convert("t.txt", text.encode()))
    assert "First line of a paragraph and its second line." in html and "Indented verse\n  line two" in html


def test_text_encodings() -> None:
    text = f"Café de la mer\n\n{body(300)}\n\nnaïve façade"
    for raw in (text.encode("utf-8"), text.encode("utf-8-sig"), text.encode("utf-16"), text.encode("latin-1")):
        made = sources.convert("t.txt", raw)
        assert made.title == "Café de la mer" and "naïve façade" in shown(made)
    assert decode(b"\xff\xfeh\x00i\x00") == "hi"


def test_gutenberg_wrapper() -> None:
    text = (f"The Project Gutenberg eBook of The Lamp\n\nTitle: The Lamp\n\nAuthor: I. Hartwell\n\nLanguage: English\n\n"
            f"*** START OF THE PROJECT GUTENBERG EBOOK THE LAMP ***\n\n{body(400)}\n\n"
            f"*** END OF THE PROJECT GUTENBERG EBOOK THE LAMP ***\n\nLicence words that are no part of the book.")
    made = sources.convert("pg1.txt", text.encode())
    assert (made.title, made.author) == ("The Lamp", "I. Hartwell") and "Licence" not in shown(made) and "eBook" not in shown(made)


# ---- HTML files


PAGE = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>On Keeping Lamps | The Shore Review</title>
<meta name="author" content="Ione Hartwell"><meta property="article:published_time" content="2024-03-09T08:00:00Z">
<meta property="og:site_name" content="The Shore Review"><link rel="canonical" href="https://example.org/essays/lamps">
<script>var x = 1;</script><style>p {{ color: red }}</style></head>
<body><nav><a href="/">Home</a></nav><article><h1>On Keeping Lamps</h1><p>{FILL * 40}</p>
<figure><img src="light.png" alt="The light"><figcaption>Gannet Rock.</figcaption></figure>
<h2>The rule</h2><p>{FILL * 40}</p></article><footer>© The Shore Review</footer></body></html>"""


def test_html_file() -> None:
    made = sources.convert("lamps.html", PAGE.encode(), web=False)
    assert (made.title, made.author) == ("On Keeping Lamps", "Ione Hartwell")
    assert made.origin["url"] == "https://example.org/essays/lamps" and made.origin["site"] == "The Shore Review"
    assert made.origin["published"] == "2024-03-09" and made.book.language == "fr"
    html = shown(made)
    assert "Home" not in html and "Shore Review" not in html and "var x" not in html and html.count("</h1>") == 1
    assert "Gannet Rock." in html and "[The light]" in html and made.pictures == {"kept": 0, "missing": 1}


def test_html_declared_encoding() -> None:
    raw = f"<html><head><meta charset='iso-8859-1'><title>Café</title></head><body><p>{FILL * 40} naïve</p></body></html>".encode("latin-1")
    made = sources.convert("c.htm", raw)
    assert made.title == "Café" and "naïve" in shown(made)


# ---- pictures


def test_pictures_written_into_the_text_and_beside_the_file(tmp_path: Path) -> None:
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "light.png").write_bytes(PNG)
    (tmp_path.parent / "outside.png").write_bytes(PNG)
    inline = "data:image/png;base64," + base64.b64encode(PNG).decode()
    text = (f"# Lamps\n\n![a](img/light.png)\n\n![b]({inline})\n\n![c](../outside.png)\n\n![d](missing.png)\n\n"
            f"![e](img/light.png)\n\n{body(300)}")
    made = sources.convert("l.md", text.encode(), folder=tmp_path, web=False)
    assert made.pictures == {"kept": 3, "missing": 2}
    with zipfile.ZipFile(BytesIO(made.epub)) as z:
        assert [n for n in z.namelist() if n.startswith("OEBPS/images/")] == ["OEBPS/images/p001.png"]  # the same picture once
        assert z.read("OEBPS/images/p001.png") == PNG
    assert 'data-src="OEBPS/images/p001.png"' in shown(made) and "[c]" in shown(made) and "[d]" in shown(made)
    assert "type the file's path" not in made.warnings[0]
    dropped = sources.convert("l.md", text.encode(), web=False)
    assert dropped.pictures == {"kept": 1, "missing": 4} and "type the file's path" in dropped.warnings[0]


def test_what_is_no_picture_is_not_kept() -> None:
    fake = "data:image/png;base64," + base64.b64encode(b"<svg onload='x()'/>" * 20).decode()
    dot = "data:image/gif;base64," + base64.b64encode(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;").decode()
    made = sources.convert("l.md", f"# Lamps\n\n![fake]({fake})\n\n![dot]({dot})\n\n{body(300)}".encode(), web=False)
    assert made.pictures == {"kept": 0, "missing": 1} and "[dot]" not in shown(made)
    assert pack.picture_type(PNG) == "png" and pack.picture_type(b"\xff\xd8\xff\xe0") == "jpg" and pack.picture_type(b"RIFF0000WEBPVP8 ") == "webp"


class _Site(http.server.BaseHTTPRequestHandler):
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


@pytest.fixture
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_pictures_from_the_web(site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    text = f"# Lamps\n\n![a]({site}/light.png)\n\n![b]({site}/moved.png)\n\n![c]({site}/none.png)\n\n![d]({site}/big.png)\n\n{body(300)}"
    refused = sources.convert("l.md", text.encode())
    assert refused.pictures == {"kept": 0, "missing": 4}  # an address on this computer is not fetched for a picture
    monkeypatch.setattr(fetch, "is_private", lambda host: False)
    made = sources.convert("l.md", text.encode())
    assert made.pictures == {"kept": 2, "missing": 2}
    assert sources.convert("l.md", text.encode(), web=False).pictures == {"kept": 0, "missing": 4}


def test_fetch_refuses_what_is_not_the_web(site: str) -> None:
    for bad in ("file:///etc/passwd", "ftp://example.org/x", "notanaddress"):
        with pytest.raises(fetch.FetchError):
            fetch.get(bad)
    with pytest.raises(fetch.FetchError) as caught:
        fetch.get(site + "/none")
    assert caught.value.status == 404
    with pytest.raises(fetch.FetchError) as caught:
        fetch.get(site + "/light.png", public_only=True)
    assert caught.value.reason == "private"
    assert fetch.get(site + "/moved.png").url.endswith("/light.png")


# ---- what is refused


@pytest.mark.parametrize("name, data, said", [
    ("a.txt", b"too short", "only 3 words"), ("a.txt", b"   \n", "empty"), ("a.pdf", b"%PDF", "PDF files are not supported yet"),
    ("a.docx", b"PK", "Word files are not supported yet"), ("a.xyz", b"x", "not supported"),
])
def test_refused(name: str, data: bytes, said: str) -> None:
    with pytest.raises(sources.SourceError, match=said):
        sources.convert(name, data)


def test_a_file_too_large_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "MAX_BYTES", 1000)
    with pytest.raises(sources.SourceError, match="larger than"):
        sources.convert("a.txt", body(600).encode())


def test_formats() -> None:
    assert sources.format_of("A.MD") == "markdown" and sources.format_of("b.txt") == "text" and sources.format_of("c.HTM") == "html"
    assert sources.format_of("d.epub") is None and not sources.is_source("d.epub") and sources.is_source("x/y.markdown")
