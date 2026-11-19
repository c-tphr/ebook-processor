"""Articles and documents that are no EPUB: the Add form, the library, the reader and the notes folder."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ebook_processor.web.server import NOTES_FOLDER, make_server
from tests.browser.conftest import App
from tests.scripted import Script
from tests.sources.test_convert import MARKDOWN, body
from tests.test_pipeline import FIXTURE

TEXT_BOOK = "The Lamp Book\n\n" + "\n\n".join(f"CHAPTER {r}\n\n{body(2500)}" for r in ("I", "II", "III"))


@pytest.fixture
def own(tmp_path: Path) -> App:
    """An app of this test's own, with an empty library, so that what is added here is in nobody's way."""
    server = make_server(tmp_path / "runs", "127.0.0.1", 0, backend_factory=lambda settings: Script().backend(),
                         claude={"ok": True, "version": "0.0.0 (test)"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield App(f"http://127.0.0.1:{server.server_address[1]}", tmp_path / "runs")
    server.shutdown()
    server.server_close()


def add(page: Page, app: App, path: Path) -> None:
    page.goto(app.url + "/#/add")
    page.locator("#app form").wait_for()
    page.locator("#app form input[type=file]").set_input_files(str(path))
    page.locator("#seen").wait_for()
    expect(page.locator("#estimate")).to_be_visible()


def finish(page: Page) -> str:
    page.wait_for_url("**/#/run/**")
    page.locator(".files .file").first.wait_for(timeout=60000)
    return page.url.rsplit("/", 1)[-1]


def test_an_essay_is_added_read_and_cited_and_the_library_is_narrowed(page: Page, own: App, tmp_path: Path):
    essay = tmp_path / "lamps.md"
    essay.write_text(MARKDOWN + "\n\n## What the log shows\n\n" + body(900), encoding="utf-8")
    add(page, own, essay)
    expect(page.locator("#src-title")).to_have_value("On Keeping Lamps")
    expect(page.locator("#src-author")).to_have_value("Ione Hartwell")
    expect(page.locator("#kind")).to_have_value("article")
    expect(page.locator("#seen-what")).to_contain_text("Markdown file / example.org /")
    expect(page.locator("#seen-found")).to_contain_text("One piece of")
    expect(page.locator("#one-piece")).to_have_count(0)               # nothing to cut: nothing to choose
    expect(page.locator("#try")).to_be_hidden()                       # and no chapter to try it on
    expect(page.locator("#want-label")).to_have_text("What do you want from this article?")
    expect(page.locator("#estimate")).to_contain_text("at 1 checkpoints")
    page.locator("#src-title").fill("Keeping Lamps")
    page.get_by_role("button", name="Process article").click()
    job = finish(page)
    expect(page.locator(".title h1")).to_have_text("Keeping Lamps")
    expect(page.locator(".title")).to_contain_text("lamps.md (read as Keeping Lamps.epub)")
    expect(page.locator(".title a[title='Open the original']")).to_have_attribute("href", "https://example.org/essays/lamps")
    expect(page.locator(".band h2").filter(has_text="The article")).to_have_count(1)
    # the reader shows it as it shows a book
    page.goto(f"{own.url}/read/{job}")
    expect(page.locator("[data-ref] >> nth=0")).to_have_text("Keeping Lamps")
    expect(page.locator("table").first).to_contain_text("Trim the wick")
    # its file is with the articles, and a note cites it
    own.id = job
    made = own.call("POST", f"/jobs/{job}/notes/highlight", json_body={"chapter": 1, "start": 3, "end": 9, "note": ""})["made"]
    page.goto(own.url + "/#/notes")
    expect(page.locator(".nz-book").first).to_contain_text("Keeping Lamps")
    folder = own.data.parent / NOTES_FOLDER
    assert f"^{made}" in (folder / "Articles" / "Keeping Lamps.md").read_text(encoding="utf-8")
    note = own.call("POST", "/vault/notes", json_body={"title": "Lamps are promises", "body": f"See [[Keeping Lamps#^{made}]]."})
    page.goto(f"{own.url}/#/notes/{note['id']}")
    expect(page.locator(".nt-aside")).to_contain_text("Keeping Lamps")
    # a book beside it: every entry says what it is, and the list can be narrowed
    page.goto(own.url + "/#/")
    expect(page.locator(".run .sub").first).to_contain_text("Article / example.org /")
    expect(page.locator(".kinds")).to_have_count(0)
    page.goto(own.url + "/#/add")
    page.locator("#app form input[type=file]").set_input_files(str(FIXTURE))
    expect(page.locator("#kind")).to_have_value("book")
    expect(page.locator("#src-title")).to_have_count(0)
    page.get_by_role("button", name="Process book").click()
    finish(page)
    page.goto(own.url + "/#/")
    expect(page.locator(".run")).to_have_count(2)
    expect(page.locator(".run .sub").first).to_contain_text("Book /")
    page.locator(".kinds button", has_text="Articles").click()
    expect(page.locator(".run .t")).to_have_text(["Keeping Lamps"])
    page.reload()                                                     # the choice is kept
    expect(page.locator(".run .t")).to_have_text(["Keeping Lamps"])
    page.locator(".kinds button", has_text="All").click()
    expect(page.locator(".run")).to_have_count(2)
    # a wrong guess is put right on the entry's page, and its file moves
    page.goto(f"{own.url}/#/run/{job}")
    page.locator(".title select[aria-label=Kind]").select_option("book")
    expect(page.locator(".band h2").filter(has_text="The book")).to_have_count(1)
    assert (folder / "Books" / "Keeping Lamps.md").is_file() and not (folder / "Articles" / "Keeping Lamps.md").exists()


def test_a_text_book_shows_its_chapters_and_can_be_taken_as_one_piece(page: Page, own: App, tmp_path: Path):
    book = tmp_path / "lamp_book.txt"
    book.write_text(TEXT_BOOK, encoding="utf-8")
    add(page, own, book)
    expect(page.locator("#src-title")).to_have_value("The Lamp Book")
    expect(page.locator("#seen-found")).to_have_text("3 chapters, found by lines like “CHAPTER I”.")
    expect(page.locator("#seen ol.chapters li")).to_have_count(3)
    expect(page.locator("#try")).to_be_visible()
    page.locator("#one-piece").check()
    expect(page.locator("#seen-found")).to_contain_text("One piece of")
    expect(page.locator("#try")).to_be_hidden()
    page.locator("#one-piece").uncheck()
    expect(page.locator("#seen-found")).to_contain_text("3 chapters")
    page.locator("#kind").select_option("book")
    page.get_by_role("button", name="Process book").click()
    job = finish(page)
    assert own.call("GET", f"/jobs/{job}")["type"] == "book" and len(own.call("GET", f"/jobs/{job}")["chapters"]) == 3


def test_a_file_that_cannot_be_taken_says_why(page: Page, own: App, tmp_path: Path):
    for name, text, said in (("tiny.txt", "too short", "too short to make questions from"), ("paper.pdf", "%PDF-1.4", "PDF files are not supported yet")):
        (tmp_path / name).write_text(text)
        page.goto(own.url + "/#/add")
        page.locator("#app form input[type=file]").set_input_files(str(tmp_path / name))
        expect(page.locator("#app form .msg.err")).to_contain_text(said)
        expect(page.locator("#seen")).to_be_hidden()
