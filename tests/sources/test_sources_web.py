"""Markdown, text and HTML files through the app and the command line."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tests.paths import FIXTURE
from tests.sources.makers import CLUTTER, FILL, MARKDOWN, PAGE, PNG, TEXT_BOOK, body, make_docx, make_mhtml


@pytest.fixture
def app(make_web_app):
    call = make_web_app().function("/api")
    call.done, call.folder = call.wait, call.notes_folder
    return call


def test_a_markdown_file_is_looked_at_held_and_processed(app):
    status, seen = app("POST", "/look?name=lamps.md", body=(MARKDOWN + "\n\n" + body(600)).encode())
    assert status == 200 and seen["type"] == "article" and seen["title"] == "On Keeping Lamps" and seen["author"] == "Ione Hartwell"
    assert seen["origin"]["format"] == "markdown" and seen["found"] is None and len(seen["chapters"]) == 1 and seen["seen_before"] is None
    assert seen["words"] == seen["chapters"][0]["words"] > 600 and seen["split"] == "auto"
    status, job = app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}, "plan": {"title": "Keeping Lamps", "author": "I. H."}})
    assert status == 201 and (job["title"], job["file"], job["type"]) == ("Keeping Lamps", "Keeping Lamps.epub", "article")
    assert job["origin"]["url"] == "https://example.org/essays/lamps" and job["origin"]["original"] == "lamps.md"
    job = app.done(job["id"])
    assert job["status"] == "done" and job["can_read"] and job["type"] == "article"
    folder = app.data / job["id"]
    assert (folder / "input" / "Keeping Lamps.epub").is_file() and (folder / "Keeping Lamps.prepared.epub").is_file()
    assert (folder / "input" / "original" / "lamps.md").read_text(encoding="utf-8").startswith("---\ntitle: On Keeping Lamps")
    assert app("GET", f"/jobs/{job['id']}/book")[1]["authors"] == ["I. H."]
    assert app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}})[0] == 410  # a held file is used once
    again = app("POST", "/look?name=other-name.md", body=(MARKDOWN + "\n\n" + body(600)).encode())[1]
    assert again["seen_before"] == job["created"]  # the same file, whatever it is called


def test_a_text_book_shows_its_chapters_and_can_be_one_piece(app):
    seen = app("POST", "/look?name=lamp_book.txt", body=TEXT_BOOK.encode())[1]
    assert seen["found"] == {"rule": "named", "count": 3, "example": "CHAPTER I"} and [c["title"] for c in seen["chapters"]] == ["CHAPTER I", "CHAPTER II", "CHAPTER III"]
    whole = app("POST", "/look/held", json_body={"held": seen["held"], "split": "none"})[1]
    assert whole["found"] is None and len(whole["chapters"]) == 1 and whole["split"] == "none" and whole["held"] == seen["held"]
    assert app("POST", "/look/held", json_body={"held": "nothing", "split": "none"})[0] == 410
    job = app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}, "plan": {"split": "none", "type": "book"}})[1]
    assert job["type"] == "book" and len(app.jobs.jobs[job["id"]]["chapters"]) == 1
    assert app.done(job["id"])["status"] == "done"


def test_files_given_whole_and_by_path(app, tmp_path):
    job = app("POST", "/jobs?name=page.html", body=PAGE.encode())[1]
    assert (job["title"], job["type"], job["origin"]["site"]) == ("On Keeping Lamps", "article", "The Shore Review")
    (tmp_path / "essay.md").write_text(MARKDOWN + "\n\n" + body(300), encoding="utf-8")
    seen = app("POST", "/look/local", json_body={"path": str(tmp_path / "essay.md")})[1]
    assert seen["title"] == "On Keeping Lamps" and seen["held"]
    job = app("POST", "/jobs/local", json_body={"path": str(tmp_path / "essay.md"), "settings": {}, "plan": {"split": "none"}})[1]
    assert job["type"] == "article" and app.done(job["id"])["status"] == "done"
    assert app("POST", "/look?name=book.epub", body=FIXTURE.read_bytes())[1]["type"] == "book"
    assert "held" not in app("POST", "/look?name=book.epub", body=FIXTURE.read_bytes())[1]


def test_what_cannot_be_taken_is_said(app):
    assert app("POST", "/look?name=a.txt", body=b"too short")[1]["error"].startswith("This has only 3 words")
    assert app("POST", "/look?name=a.pdf", body=b"%PDF-1.4")[1]["error"] == "PDF files are not supported yet."
    assert app("POST", "/jobs?name=a.xyz", body=b"what")[0] == 400
    assert app("POST", "/look?name=a.epub", body=b"not a zip")[1]["error"].startswith("This file cannot be read as an EPUB")


def test_only_a_few_files_are_held(app):
    tokens = [app("POST", f"/look?name=n{n}.md", body=(f"# Piece {n}\n\n" + body(300)).encode())[1]["held"] for n in range(6)]
    assert len(app.jobs.held) == 4 and tokens[0] not in app.jobs.held and tokens[-1] in app.jobs.held


def test_an_article_has_its_file_in_the_notes_folder_and_can_become_a_book(app):
    job = app("POST", "/jobs?name=lamps.md", body=(MARKDOWN + "\n\n" + body(600)).encode())[1]
    app.done(job["id"])
    made = app("POST", f"/jobs/{job['id']}/notes/highlight", json_body={"chapter": 1, "start": 3, "end": 9, "note": "Why it matters."})[1]["made"]
    home = app("GET", "/vault")[1]
    assert [(b["title"], b["kind"], b["job"]) for b in home["books"]] == [("On Keeping Lamps", "article", job["id"])]
    file = app.folder / "Articles" / "On Keeping Lamps.md"
    text = file.read_text(encoding="utf-8")
    assert "kind: article\n" in text and "url: https://example.org/essays/lamps\n" in text and "published: 2024-03-09\n" in text
    assert f"^{made}\n" in text and not (app.folder / "Books" / "On Keeping Lamps.md").exists()
    # a note cites the article as it would a book
    note = app("POST", "/vault/notes", json_body={"title": "Lamps are promises", "body": f"See [[On Keeping Lamps#^{made}]]."})[1]
    assert note["links"][0]["to"]["kind"] == "article" and note["sources"][0]["job"] == job["id"]
    found = app("GET", "/vault/search?" + urllib.parse.urlencode({"kind": "article"}))[1]
    assert {p["book"] for p in found["passages"]} == {"On Keeping Lamps"} and app("GET", "/vault/search?kind=book")[1]["passages"] == []
    assert app("POST", f"/jobs/{job['id']}/type", json_body={"type": "pamphlet"})[0] == 400
    assert app("POST", f"/jobs/{job['id']}/type", json_body={"type": "book"})[1]["type"] == "book"
    moved = app.folder / "Books" / "On Keeping Lamps.md"
    assert moved.is_file() and not file.exists() and "kind: book\n" in moved.read_text(encoding="utf-8")
    assert app("GET", f"/vault/notes/{note['id']}")[1]["links"][0]["to"]["kind"] == "book"  # the citation holds
    assert [j["type"] for j in app("GET", "/jobs")[1]["jobs"]] == ["book"]
    # taken out of the library as an article, its file stays among the articles (it used to be moved to the books)
    assert app("POST", f"/jobs/{job['id']}/type", json_body={"type": "article"})[1]["type"] == "article"
    assert app("DELETE", f"/jobs/{job['id']}")[0] == 200
    assert file.is_file() and not moved.exists() and "kind: article\n" in file.read_text(encoding="utf-8")


def test_an_epub_can_be_called_an_article_and_old_runs_are_books(app):
    plan = urllib.parse.quote(json.dumps({"type": "article"}))
    job = app("POST", f"/jobs?name=fixture.epub&plan={plan}", body=FIXTURE.read_bytes())[1]
    assert job["type"] == "article" and job["origin"] is None
    app.done(job["id"])
    del app.jobs.jobs[job["id"]]["type"]  # a run made before articles were known
    assert app("GET", f"/jobs/{job['id']}")[1]["type"] == "book" and app("GET", "/jobs")[1]["jobs"][0]["type"] == "book"


# ---- step 2: web addresses, saved pages, Word files


def test_an_address_is_fetched_once_held_and_processed(app, the_web, monkeypatch):
    from ebook_processor.sources import fetch
    asked = []
    real = fetch.get
    monkeypatch.setattr(fetch, "get", lambda url, *a, **k: asked.append(url) or real(url, *a, **k))
    status, seen = app("POST", "/look/url", json_body={"url": the_web + "/moved"})
    assert status == 200 and seen["origin"]["format"] == "web" and seen["origin"]["url"] == the_web + "/essay" and seen["type"] == "article"
    assert seen["page"] == {"page": True, "whole": False, "lost": False} and seen["pictures"] == {"kept": 1, "missing": 0}
    assert (seen["title"], seen["author"]) == ("On Keeping Lamps", "Ione Hartwell") and seen["origin"]["fetched"]
    whole = app("POST", "/look/held", json_body={"held": seen["held"], "whole": True})[1]
    assert whole["page"]["whole"] and whole["words"] > seen["words"]
    job = app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}, "plan": {"whole": False}})[1]
    assert asked == [the_web + "/moved", the_web + "/light.png"]  # the page and its picture, each asked for once
    assert job["type"] == "article" and job["origin"]["site"] == "The Shore Review" and job["file"] == "On Keeping Lamps.epub"
    job = app.done(job["id"])
    assert job["status"] == "done" and (app.data / job["id"] / "input" / "original" / "page.html").read_bytes() == CLUTTER.encode()
    assert job["words"] == seen["words"]
    made = app("POST", f"/jobs/{job['id']}/notes/highlight", json_body={"chapter": 1, "start": 3, "end": 9, "note": ""})[1]["made"]
    app("GET", "/vault")
    text = (app.folder / "Articles" / "On Keeping Lamps.md").read_text(encoding="utf-8")
    assert f"url: {the_web}/essay\n" in text and "site: The Shore Review\n" in text and "fetched: " in text and f"^{made}" in text
    again = app("POST", "/look/url", json_body={"url": the_web + "/essay?utm_source=x#top"})[1]
    assert again["seen_before"] == job["created"]  # the same address, however it was written
    second = app("POST", "/jobs/url", json_body={"url": the_web + "/essay", "settings": {}, "plan": {}})
    assert second[0] == 201 and second[1]["id"] != job["id"]  # a second entry, as the reader asked


def test_an_address_that_cannot_be_taken_says_why(app, the_web):
    for path, said in (("/forbidden", "refused to give the page"), ("/paper.pdf", "PDF files are not supported yet"), ("/shell", "JavaScript")):
        status, answer = app("POST", "/look/url", json_body={"url": the_web + path})
        assert status == 400 and said in answer["error"]
    assert app("POST", "/look/url", json_body={"url": ""})[0] == 400 and app("POST", "/look/url", json_body={"url": "file:///etc/passwd"})[0] == 400


def test_a_word_file_and_a_saved_page_through_the_app(app):
    data = make_docx([("Title", "On Keeping Lamps"), ("Heading1", "The rule"), (None, FILL * 20), (None, "<picture>")], picture=PNG)
    seen = app("POST", "/look?name=lamps.docx", body=data)[1]
    assert seen["origin"]["format"] == "docx" and seen["title"] == "On Keeping Lamps" and seen["pictures"]["kept"] == 1 and seen["page"] is None
    assert app.done(app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}})[1]["id"])["status"] == "done"
    page = make_mhtml(CLUTTER.replace('src="/light.png"', 'src="https://example.org/img/light.png"'), "https://example.org/essays/lamps",
                      {"https://example.org/img/light.png": PNG})
    seen = app("POST", "/look?name=lamps.mhtml", body=page)[1]
    assert seen["origin"]["format"] == "saved" and seen["origin"]["url"] == "https://example.org/essays/lamps" and seen["pictures"]["kept"] == 1
    assert seen["page"] == {"page": True, "whole": False, "lost": False}
