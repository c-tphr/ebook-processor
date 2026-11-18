"""The notes folder in the browser: the Notes area, the editor, quick notes, and idea notes from the reader."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import App, Reader
from tests.browser.test_notes import SELECT

BOOK = "The Lighthouse Keeper's Almanac"   # the session's book; a test elsewhere may add a second of the same title, filed as "... 2"


@pytest.fixture
def vault(book: App, tmp_path: Path) -> Path:
    """A notes folder of this test's own, not yet made."""
    folder = tmp_path / "notes"
    book.call("PUT", "/settings", json_body={"notes_folder": str(folder)})
    return folder


def make(app: App, **note) -> dict:
    return app.call("POST", "/vault/notes", json_body=note)


def saved(page: Page, inside: str = "") -> None:
    expect(page.locator(f"{inside} .nt-status")).to_have_text("Saved")


def accept(page: Page) -> None:
    """Take the first thing the editor offers. For a moment after the list appears the editor does not take
    Enter for it, so that a line break typed just then is not swallowed; a person is slower than that."""
    page.wait_for_timeout(250)
    page.keyboard.press("Enter")


def write(page: Page, text: str, inside: str = "") -> None:
    """Type into the note's text, as a person would."""
    page.locator(f"{inside} .cm-content").click()
    page.keyboard.type(text)


def test_the_front_page_a_new_note_and_a_link_that_completes(page: Page, book: App, vault: Path):
    page.goto(book.url + "/#/notes")
    expect(page.locator(".nz-cols")).to_contain_text("Nothing waits here")
    expect(page.locator(".nz-cols")).to_contain_text("No notes yet")
    expect(page.locator(".nz-book").first).to_contain_text(BOOK)              # every book has its file
    assert (vault / "Books" / f"{BOOK}.md").is_file() and (vault / "Inbox").is_dir()
    make(book, title="Prices carry information", body="No one holds it all. #pricing\n\n## Why it matters\n\nA block. ^b1\n")
    page.get_by_role("link", name="New note").click()
    page.wait_for_selector(".cm-content")
    assert sorted(p.name for p in vault.glob("*.md")) == ["Prices carry information.md"]   # nothing is kept before something is written
    title = page.get_by_label("Title")
    title.fill("Markets are conversations")
    title.press("Enter")
    page.wait_for_function("() => /^#\\/notes\\/n[0-9a-f]+$/.test(location.hash)")
    page.keyboard.type("Typed straight after the title. See [[pri")              # the keyboard is in the text at once
    options = page.locator(".cm-tooltip-autocomplete li")
    expect(options).to_have_count(1)
    expect(options).to_contain_text("Prices carry information")
    accept(page)
    page.keyboard.type(" and its [[prices#")
    expect(options.first).to_contain_text("Why it matters")
    accept(page)
    page.keyboard.type(" #markets")
    saved(page)
    text = (vault / "Markets are conversations.md").read_text(encoding="utf-8")
    assert text.endswith("---\nTyped straight after the title. See [[Prices carry information]] and its "
                         "[[Prices carry information#Why it matters]] #markets")
    assert "kind: idea" in text and "id: n" in text
    # the other note knows it is linked from here, and leads back
    page.goto(book.url + "/#/notes")
    expect(page.locator("#nz-recent .nz-row")).to_have_count(2)
    expect(page.locator(".nz-chips")).to_contain_text("#markets")
    page.locator("#nz-recent .nz-row").filter(has=page.locator("b", has_text="Prices carry information")).click()
    back = page.locator(".nt-back .back")
    expect(back).to_have_count(1)
    expect(back).to_contain_text("Markets are conversations")
    expect(back).to_contain_text("Typed straight after the title")
    back.click()
    expect(page.get_by_label("Title")).to_have_value("Markets are conversations")


def test_the_text_is_formatted_as_it_is_typed_and_links_open(page: Page, book: App, vault: Path):
    make(book, title="Target", body="What the target says.")
    note = make(book, title="Formatted", body="# A heading\n\nSome *emphasis*, **strong**, `code` and [[Target]], [[Target|other words]] and [[Nowhere yet]]. #a-tag\n\n"
                                               "> quoted\n\n- one\n- two\n\n```\n[[not a link]]\n```\n")
    page.goto(f"{book.url}/#/notes/{note['id']}")
    page.wait_for_selector(".cm-content .cm-h1")
    page.locator(".nt-status").click()                                          # the keyboard is not in the text: every mark is hidden
    content = page.locator(".cm-content")
    expect(content.locator(".cm-h1")).to_have_text("A heading")
    expect(content.locator(".cm-em")).to_have_text("emphasis")
    expect(content.locator(".cm-strong")).to_have_text("strong")
    expect(content.locator(".cm-code")).to_have_text("code")
    expect(content.locator(".cm-tag")).to_have_text("#a-tag")
    expect(content.locator(".cm-quote")).to_have_text("quoted")
    expect(content.locator(".cm-bullet")).to_have_count(2)
    links = content.locator(".cm-wikilink[data-target]")
    expect(links).to_have_text(["Target", "other words", "Nowhere yet"])
    expect(links.nth(2)).to_have_class("cm-wikilink cm-live cm-broken")         # no such note: shown as broken
    expect(links.nth(0)).to_have_class("cm-wikilink cm-live")
    expect(content.locator(".cm-codeblock").nth(1)).to_have_text("[[not a link]]")
    # with the cursor in a construct, its marks are there to edit
    page.evaluate("() => noteView.focus()")
    page.keyboard.press("ControlOrMeta+Home")
    expect(content.locator(".cm-h1")).to_have_text("# A heading")
    page.locator(".nt-status").click()
    links.nth(1).hover()
    expect(page.locator(".cm-peek")).to_contain_text("What the target says.")
    links.nth(0).click()
    expect(page.get_by_label("Title")).to_have_value("Target")
    expect(page.locator(".nt-back .back")).to_contain_text("Formatted")
    # a link to a note that is not there offers to make it
    page.go_back()
    page.wait_for_selector(".cm-wikilink.cm-broken")
    page.once("dialog", lambda d: d.accept())
    page.locator(".cm-wikilink.cm-broken").click()
    expect(page.get_by_label("Title")).to_have_value("Nowhere yet")
    assert (vault / "Nowhere yet.md").is_file()


def test_a_quick_note_from_any_page_becomes_an_idea_note_in_place(page: Page, book: App, vault: Path):
    page.goto(book.url + "/#/add")
    page.locator("textarea").first.click()                                     # also while typing somewhere else
    page.keyboard.press("Control+.")
    box = page.get_by_role("textbox", name="Quick note")
    box.fill("check what the book says about fog signals")
    page.keyboard.press("Escape")                                              # closed by mistake: the words are kept
    page.locator("#b-capture").click()
    expect(box).to_have_value("check what the book says about fog signals")
    box.press("ControlOrMeta+Enter")
    expect(page.locator(".nt-toast")).to_contain_text("Quick note saved")
    files = list((vault / "Inbox").iterdir())
    assert len(files) == 1 and files[0].name.endswith(" check what the book says about.md")
    assert "kind: quick" in files[0].read_text(encoding="utf-8")
    page.locator(".nt-toast button").click()
    page.wait_for_selector(".cm-content")
    expect(page.locator(".nt-meta")).to_contain_text("Quick note")
    expect(page.locator(".nt-meta")).to_contain_text("In the inbox")
    make_idea = page.get_by_role("button", name="Make this an idea note")
    make_idea.click()                                                          # not without a title
    expect(page.locator(".nt-toast")).to_contain_text("Give the note a title first")
    assert files[0].is_file()
    page.get_by_label("Title").fill("Fog signals")
    make_idea.click()
    expect(page.locator(".nt-meta")).not_to_contain_text("In the inbox")
    expect(page.locator(".nt-meta .lab")).to_have_text("Idea note")
    expect(make_idea).to_have_count(0)
    text = (vault / "Fog signals.md").read_text(encoding="utf-8")
    assert not files[0].exists() and "kind: idea" in text and text.endswith("check what the book says about fog signals\n")
    page.goto(book.url + "/#/notes")
    expect(page.locator(".nz-cols")).to_contain_text("Nothing waits here")
    expect(page.locator("#nz-recent .nz-row")).to_contain_text("Fog signals")


def test_search_finds_notes_and_passages_and_the_filters_narrow_it(page: Page, book: App, vault: Path):
    made = book.call("POST", f"/jobs/{book.id}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 14, "note": "Supply is not on a timetable."})
    lamp = make(book, title="Supply follows the weather", body="Not the calendar. #supply")
    make(book, title="Another thought", body="Nothing about boats. Well, the supply of oil. #oil")
    make(book, body="supply list for the week", kind="quick")
    page.goto(book.url + "/#/notes")
    page.wait_for_selector("#nz-recent")
    find = page.locator("#nz-q")
    find.fill("supply")
    expect(page.locator("#nz-go a")).to_have_text(["Supply follows the weather"])        # a title to jump to
    expect(page.locator("#nz-notes .nz-row")).to_have_count(3)
    expect(page.locator("#nz-notes .nz-row").first).to_contain_text("Supply follows the weather")
    expect(page.locator("#nz-notes mark").first).to_have_text("supply", ignore_case=True)
    hits = page.locator("#nz-passages .nz-hit")
    expect(hits).to_have_count(2)                                                        # the highlight, and the note on it
    expect(hits.first.locator("blockquote")).to_contain_text("The supply boat reached Gannet Rock")
    expect(hits.first).to_contain_text(BOOK)
    assert "q=supply" in page.evaluate("location.hash")
    page.get_by_label("Kind").select_option("quick")
    expect(page.locator("#nz-notes .nz-row")).to_have_count(1)
    expect(page.locator("#nz-passages")).to_have_count(0)
    page.get_by_role("button", name="Clear").click()
    page.locator(".nz-chips a", has_text="#oil").click()
    expect(page.locator("#nz-notes .nz-row")).to_have_count(1)
    expect(page.locator("#nz-notes .nz-row")).to_contain_text("Another thought")
    page.get_by_label("Tag").select_option("")
    page.locator(".nz-book").first.click()                                               # a book: its passages, and the notes that cite it
    expect(page.locator("#nz-passages .nz-hit")).to_have_count(2)
    expect(page.locator("#nz-notes")).to_have_count(0)
    expect(page.locator("#nz-book")).to_contain_text(BOOK)
    assert page.locator("#nz-book a", has_text="Notebook").get_attribute("href") == "#/notebook/" + book.id
    # an idea note from a passage that was found; its source is shown beside the text
    page.locator("#nz-passages a", has_text="Idea note from this").first.click()
    page.wait_for_selector(".cm-content")
    expect(page.locator(".nt-sources blockquote")).to_contain_text("The supply boat reached Gannet Rock")
    page.wait_for_selector(".cm-focused")                                                # the cursor waits on the first line, above the source
    page.keyboard.type("The boat comes when the sea allows.")
    saved(page)
    inbox = list((vault / "Inbox").glob("*The boat comes*.md"))
    assert len(inbox) == 1 and f'sources:\n  - "[[{BOOK}#^{made["made"]}]]"' in inbox[0].read_text(encoding="utf-8")
    assert inbox[0].read_text(encoding="utf-8").endswith(f"The boat comes when the sea allows.\n\nSource: [[{BOOK}#^{made['made']}]]\n")
    page.goto(f"{book.url}/#/notes?book=b-{book.id}")
    expect(page.locator("#nz-notes .nz-row")).to_have_count(1)                           # the note that cites the book
    assert lamp["id"] not in page.locator("#nz-notes").inner_html()


def test_a_note_changed_in_another_program_is_not_written_over(page: Page, book: App, vault: Path):
    note = make(book, title="Shared with Obsidian", body="As first written.")
    path = vault / "Shared with Obsidian.md"
    page.goto(f"{book.url}/#/notes/{note['id']}")
    page.wait_for_selector(".cm-content")
    saved(page)
    # changed outside while nothing is unsaved here: the page takes the new text
    path.write_text(path.read_text(encoding="utf-8") + "\nA line from Obsidian.", encoding="utf-8")
    page.evaluate("() => noteView.reload()")
    expect(page.locator(".cm-content")).to_contain_text("A line from Obsidian.")
    # changed outside while something is unsaved here: the page asks
    write(page, " Typed here.")
    path.write_text(path.read_text(encoding="utf-8") + "\nA second line from Obsidian.", encoding="utf-8")
    page.keyboard.press("ControlOrMeta+s")
    question = page.locator(".nt-note[role=alert]")
    expect(question).to_contain_text("changed outside the app")
    assert "Typed here" not in path.read_text(encoding="utf-8")
    question.get_by_role("button", name="Take the other version").click()
    expect(page.locator(".cm-content")).to_contain_text("A second line from Obsidian.")
    expect(page.locator(".cm-content")).not_to_contain_text("Typed here.")
    write(page, " Typed again.")
    path.write_text(path.read_text(encoding="utf-8") + "\nA third line.", encoding="utf-8")
    page.keyboard.press("ControlOrMeta+s")
    question.get_by_role("button", name="Keep what I have here").click()
    saved(page)
    text = path.read_text(encoding="utf-8")
    assert "Typed again." in text and "A third line." not in text and "A second line from Obsidian." in text


def test_properties_rename_trash_and_the_folder_in_settings(page: Page, book: App, vault: Path, tmp_path: Path):
    old = make(book, title="Old name", body="The note itself.")
    make(book, title="A linker", body="It points at [[Old name]] and [[Old name|the same]].")
    page.goto(f"{book.url}/#/notes/{old['id']}")
    page.wait_for_selector(".cm-content")
    page.get_by_role("button", name="Properties").click()
    page.get_by_label("Tags").fill("pricing, #markets")
    page.get_by_label("Tags").press("Tab")
    page.get_by_label("Name of a new property").fill("status")
    page.get_by_label("Value of the new property").fill("to review")
    page.get_by_role("button", name="Add a property").click()
    saved(page)
    expect(page.locator(".nt-meta")).to_contain_text("#pricing")
    text = (vault / "Old name.md").read_text(encoding="utf-8")
    assert "tags: [pricing, markets]\n" in text and "status: to review\n" in text
    title = page.get_by_label("Title")
    title.fill("New name")
    title.press("Enter")
    expect(page.locator(".nt-toast")).to_contain_text("1 note that links here was updated")
    assert (vault / "New name.md").is_file() and not (vault / "Old name.md").exists()
    assert "It points at [[New name]] and [[New name|the same]]." in (vault / "A linker.md").read_text(encoding="utf-8")
    title.fill("A linker")                                                    # taken: said, and nothing is renamed
    title.press("Enter")
    expect(page.locator(".nt-err")).to_contain_text("already a note called A linker")
    assert (vault / "New name.md").is_file()
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Delete").click()
    page.wait_for_selector("#nz-recent")
    expect(page.locator("#nz-recent .nz-row")).to_have_count(1)
    assert (vault / ".trash" / "New name.md").is_file()
    page.get_by_role("link", name="Trash (1)").click()
    page.get_by_role("button", name="Put back").click()
    expect(page.get_by_label("Title")).to_have_value("New name")
    assert (vault / "New name.md").is_file()
    # the folder: a path the app cannot use is refused; another folder shows what is in it, and nothing is moved
    page.locator("#b-settings").click()
    field = page.locator("#st-notes-folder")
    expect(field).to_have_value(str(vault))
    field.fill("not/a/full/path")
    page.get_by_role("button", name="Use this folder").click()
    expect(page.locator(".stg .err")).to_contain_text("full path")
    other = tmp_path / "other notes"
    field.fill(str(other))
    page.get_by_role("button", name="Use this folder").click()
    expect(page.locator(".stg")).to_contain_text("does not exist yet")
    page.locator(".stg header button", has_text="Close").click()
    page.goto(book.url + "/#/notes")
    expect(page.locator(".nz-cols")).to_contain_text("No notes yet")
    assert (other / "Books" / f"{BOOK}.md").is_file() and (vault / "New name.md").is_file()


def test_an_idea_note_starts_from_a_highlight_beside_the_text(reader: Reader, vault: Path):
    page = reader.page
    reader.open("2:0", "scroll")
    chosen = page.evaluate(SELECT, [2, 9])
    page.locator(".tip button", has_text="Idea note").click()
    page.wait_for_selector(".np .cm-content")
    expect(page.locator("body")).to_have_class("noting")
    hid = reader.app.notes()["highlights"][0]["id"]
    expect(page.locator(".np .nt-sources blockquote")).to_have_text(" ".join(chosen["text"].split()))
    assert not list((vault / "Inbox").iterdir())                               # only the words are highlighted so far
    wide = page.evaluate("() => document.querySelector('.shell').getBoundingClientRect().right")
    assert wide < 1400 - 300                                                   # the text has made room
    page.keyboard.type("hinq 123 Keepers are judged by the lamp.")               # keys of the reader are letters here
    saved(page, ".np")
    state = reader.state()
    assert state["mode"] == "scroll" and not state["drawer"] and len(reader.app.notes()["highlights"]) == 1
    title = page.locator(".np").get_by_label("Title")
    title.fill("The lamp is the job")
    title.press("Enter")
    expect(page.locator(".np .nt-meta")).not_to_contain_text("inbox", ignore_case=True)
    text = (vault / "The lamp is the job.md").read_text(encoding="utf-8")
    assert f'sources:\n  - "[[{BOOK}#^{hid}]]"' in text
    assert text.endswith(f"---\nhinq 123 Keepers are judged by the lamp.\n\nSource: [[{BOOK}#^{hid}]]\n")
    assert f"^{hid}\n" in (vault / "Books" / f"{BOOK}.md").read_text(encoding="utf-8")
    page.locator(".np").get_by_role("button", name="Close").click()
    expect(page.locator("body")).not_to_have_class("noting")
    assert page.evaluate("() => document.querySelector('.shell').getBoundingClientRect().right") > wide
    # from the highlight's own panel, and with the key
    page.evaluate("() => editItem(NB.highlights[0])")
    page.locator("#d-body").get_by_role("button", name="Idea note").click()
    page.wait_for_selector(".np .cm-content")
    assert not reader.state()["drawer"]
    page.locator(".np").get_by_role("button", name="Close").click()
    page.evaluate(SELECT, [20, 24])
    page.wait_for_selector(".tip")
    reader.key("i")
    page.wait_for_selector(".np .cm-content")
    assert len(reader.app.notes()["highlights"]) == 2


def test_a_quick_note_in_the_reader_remembers_its_place_and_a_section_note_becomes_an_idea(reader: Reader, vault: Path):
    page = reader.page
    reader.open("2:61", "scroll")
    place = reader.state()
    page.keyboard.press("Control+.")
    page.get_by_role("textbox", name="Quick note").fill("why sunset and not dusk?")
    page.get_by_role("button", name="Save to the inbox").click()
    expect(page.locator(".nt-toast")).to_contain_text("Quick note saved")
    quick = next((vault / "Inbox").iterdir()).read_text(encoding="utf-8")
    assert f'book: "[[{BOOK}]]"' in quick and f"place: {place['chapter']}:{place['word']}\n" in quick
    page.locator(".nt-toast button").click()                                   # opened here, beside the text
    expect(page.locator(".np .nt-written")).to_contain_text(BOOK)
    expect(page.locator(".np .nt-note")).to_contain_text("A quick note waits in the inbox")
    page.locator(".np").get_by_role("button", name="Close").click()
    # a note written on a section can start an idea note: its words, and a link to it as the source
    section = reader.sections(2)[0]["id"]
    reader.app.call("PUT", f"/jobs/{reader.app.id}/notes/target", json_body={"target": {"on": "section", "section": section}, "text": "The keeper depends on the boat."})
    nid = reader.app.notes()["notes"][0]["id"]
    page.evaluate("async () => { NB = await api(`/jobs/${ID}/notes`); NB.questions = NB.questions || []; }")
    page.evaluate("(id) => offerNote(book.sections.find((s) => s.id === id))", section)
    page.locator(".offer").get_by_role("button", name="Make it an idea note").click()
    page.wait_for_selector(".np .cm-content")
    expect(page.locator(".np .cm-content")).to_contain_text("The keeper depends on the boat.")
    expect(page.locator(".np .nt-sources blockquote")).to_have_text("The keeper depends on the boat.")
    write(page, "x", ".np")
    saved(page, ".np")
    made = [p for p in (vault / "Inbox").iterdir() if "The keeper depends" in p.name]
    assert len(made) == 1 and made[0].read_text(encoding="utf-8").rstrip().endswith(f"Source: [[{BOOK}#^{nid}]]")


def test_a_quick_note_over_practice_leaves_the_card_alone(page: Page, book: App, vault: Path):
    """The keys of the box are not keys of the page behind it."""
    reader = Reader(page, book).open("2:0", "page")
    reader.read_to_checkpoint()
    reader.answer("Incorrect")                                                 # a card to practise
    page.goto(book.url + "/#/practice")
    expect(page.locator(".pr h1")).to_be_visible()
    page.keyboard.press("Control+.")
    box = page.get_by_role("textbox", name="Quick note")
    box.fill("a thought during practice")
    box.press("ControlOrMeta+Enter")
    expect(page.locator(".nt-toast")).to_contain_text("Quick note saved")
    expect(page.get_by_role("button", name="Reveal answer")).to_be_visible()   # the card was not turned over
    assert len(list((vault / "Inbox").iterdir())) == 1
