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


# ---- step 2: a web address, a Word file, a page saved by the browser


from ebook_processor.sources import fetch  # noqa: E402
from tests.sources.makers import make_docx  # noqa: E402
from tests.sources.test_convert import FILL, PNG, the_web  # noqa: E402, F401 - the_web is a fixture


def test_an_article_is_added_by_its_address(page: Page, own: App, the_web: str):  # noqa: F811
    page.goto(own.url + "/#/add")
    page.locator("#app form").wait_for()
    page.locator("#path").fill(the_web + "/essay?utm_source=mail")
    page.locator("#path").press("Enter")
    expect(page.locator("#seen-what")).to_contain_text("Web page / The Shore Review / 2024-03-09 /")
    expect(page.locator("#src-title")).to_have_value("On Keeping Lamps")
    expect(page.locator("#kind")).to_have_value("article")
    expect(page.locator("#seen-page")).to_contain_text("Only the article was taken")
    expect(page.locator("#seen")).to_contain_text("1 picture kept.")
    words = page.locator("#seen-found").inner_text()
    page.locator("#whole-page").check()
    expect(page.locator("#seen-page")).to_contain_text("The whole page is taken")
    assert page.locator("#seen-found").inner_text() != words          # the menu and the comments count now
    page.locator("#whole-page").uncheck()
    expect(page.locator("#seen-found")).to_have_text(words)
    page.get_by_role("button", name="Process article").click()
    job = finish(page)
    expect(page.locator(".title a[title='Open the original']")).to_have_attribute("href", the_web + "/essay")
    expect(page.locator(".title")).to_contain_text("Fetched")
    page.goto(f"{own.url}/read/{job}")
    expect(page.locator("[data-ref] >> nth=0")).to_have_text("On Keeping Lamps")
    expect(page.locator(".text, .sv, body").first).not_to_contain_text("lamp oil")   # the advert is not in the article
    expect(page.locator("img").first).to_be_visible()
    # the same address again: the form says so, and still lets it be added
    page.goto(own.url + "/#/add")
    page.locator("#path").fill(the_web + "/essay")
    page.locator("#path").press("Enter")
    expect(page.locator("#seen-before")).to_contain_text("You added this on")
    # an address that gives nothing to read says why, and what to do
    page.locator("#path").fill(the_web + "/forbidden")
    page.locator("#path").press("Enter")
    expect(page.locator("#app form .msg.err")).to_contain_text("The site refused to give the page")
    expect(page.locator("#app form .msg.err")).to_contain_text("Save the page in your browser")
    expect(page.locator("#seen")).to_be_hidden()


def test_a_word_file_is_added(page: Page, own: App, tmp_path: Path):
    path = tmp_path / "lamps.docx"
    path.write_bytes(make_docx([("Title", "On Keeping Lamps"), ("Author", "Ione Hartwell"), ("Heading1", "The rule"), (None, FILL * 20),
                                ("Quote", "Whatever the weather."), (None, "<picture>"), (None, FILL * 20)], picture=PNG))
    add(page, own, path)
    expect(page.locator("#src-title")).to_have_value("On Keeping Lamps")
    expect(page.locator("#src-author")).to_have_value("Ione Hartwell")
    expect(page.locator("#seen-what")).to_contain_text("Word file /")
    expect(page.locator("#whole-page")).to_have_count(0)              # no page: nothing to choose
    page.get_by_role("button", name="Process article").click()
    job = finish(page)
    page.goto(f"{own.url}/read/{job}")
    expect(page.locator("blockquote").first).to_contain_text("Whatever the weather.")


def test_a_page_saved_by_the_browser_itself_is_read(page: Page, own: App, the_web: str, tmp_path: Path, browser_name: str):  # noqa: F811
    if browser_name != "chromium":
        pytest.skip("only Chromium can be asked to save a page as one file")
    page.goto(the_web + "/essay")
    saved = page.context.new_cdp_session(page).send("Page.captureSnapshot", {"format": "mhtml"})["data"]
    path = tmp_path / "essay.mhtml"
    path.write_text(saved, encoding="utf-8", newline="")
    add(page, own, path)
    expect(page.locator("#src-title")).to_have_value("On Keeping Lamps")
    expect(page.locator("#seen-what")).to_contain_text("Saved page / The Shore Review /")
    expect(page.locator("#seen-page")).to_contain_text("Only the article was taken")
    expect(page.locator("#seen")).to_contain_text("1 picture kept.")   # the picture came inside the file


def test_a_picture_that_stands_alone_is_shown_in_every_view(page: Page, own: App):
    """An article's picture is a block of its own, not inside a figure as a book's mostly are."""
    import base64

    from tests.browser.conftest import Reader

    inline = "data:image/png;base64," + base64.b64encode(PNG).decode()
    text = f"# Lamps\n\n{FILL * 30}\n\n![The light]({inline})\n\n{FILL * 30}"
    own.id = own.call("POST", "/jobs?name=lamps.md", text.encode())["id"]
    for _ in range(600):
        if own.call("GET", f"/jobs/{own.id}")["status"] not in ("queued", "running"):
            break
        page.wait_for_timeout(50)
    chapter = own.call("GET", f"/jobs/{own.id}/book/chapter/1")
    at = next(a for kind, a, b in chapter["markers"] if kind == "IMAGE")
    assert f'data-w="{at}" data-n="0"' in chapter["html"] and 'data-kind="image" data-src="OEBPS/images/p001.png"' in chapter["html"]
    reader = Reader(page, own).open(f"1:{at}", "scroll")
    shown = "(sel) => { const i = document.querySelector(sel); return !!i && i.complete && i.naturalWidth > 0; }"
    page.wait_for_function(shown, arg=".sv img")
    reader.open(f"1:{at - 4}", "rsvp")
    page.keyboard.press("Space")                                   # play: the reader stops at the picture
    expect(page.locator(".rv .ctx .cue")).to_have_text("A picture: look, then press play")
    page.wait_for_function(shown, arg=".rv .word img")             # the picture itself, where the words stand
    expect(page.locator(".rv .word img")).to_be_visible()
    box = page.evaluate("""() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
        return { img: r(".rv .word img"), area: r(".rv .word"), ctl: r(".rv .ctl"), cue: r(".rv .ctx .cue") }; }""")
    assert box["img"]["bottom"] <= box["ctl"]["top"] < box["cue"]["top"]          # the picture above the controls, the cue below
    assert box["img"]["top"] >= box["area"]["top"] - 1 and box["img"]["bottom"] <= box["area"]["bottom"] + 1   # and all of it in view
    page.keyboard.press("Space")                                   # playing on puts the words back where they were
    page.keyboard.press("Space")
    expect(page.locator(".rv .word.stop")).to_have_count(0)
    expect(page.locator(".rv .word img")).to_have_count(0)
    reader.mode("page")
    page.wait_for_function(shown, arg="img[alt='The light']")
