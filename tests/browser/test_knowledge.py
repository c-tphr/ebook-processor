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


def test_processing_a_highlight_into_a_note_a_source_or_aside(page: Page, book: App, vault: Path):
    first = book.call("POST", f"/jobs/{book.id}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 14, "note": "Supply is not on a timetable."})["made"]
    second = book.call("POST", f"/jobs/{book.id}/notes/highlight", json_body={"chapter": 2, "start": 61, "end": 80})["made"]
    third = book.call("POST", f"/jobs/{book.id}/notes/highlight", json_body={"chapter": 3, "start": 5, "end": 20})["made"]
    have = make(book, title="Supply sets the pace", body="What the supply boat brings decides what can be done.\n")
    quick = make(book, body="compare with pilots' checklists", kind="quick")
    page.goto(book.url + "/#/notes")
    expect(page.locator("#nz-process")).to_be_visible()
    expect(page.locator(".nz-part h2").first).to_have_text("To process4")
    page.locator("#nz-process a").click()
    expect(page.locator(".pz-item")).to_have_count(4)                          # the quick note first, then the book in reading order
    assert page.locator(".pz-item").evaluate_all("(rows) => rows.map((r) => r.id)") == [f"pz-{quick['id']}", f"pz-{first}", f"pz-{second}", f"pz-{third}"]
    # an idea note: it starts with the words written on the highlight, cites it, and the item is processed
    page.locator(f"#pz-{first}").click()
    expect(page.locator(".pz-pane blockquote")).to_contain_text("The supply boat reached Gannet Rock")
    expect(page.locator(".pz-pane .mine")).to_have_text("Supply is not on a timetable.")
    expect(page.locator("#pz-targets .pz-to b")).to_have_text(["Supply sets the pace"])   # a related note is offered to add it to
    page.locator("#pz-idea").click()
    page.wait_for_selector(".pz-pane .cm-focused")
    expect(page.locator(".pz-pane .cm-content")).to_contain_text("Supply is not on a timetable.")
    page.keyboard.type("In my words: ")
    saved(page, ".pz-pane")
    expect(page.locator(".title .kv").first).to_contain_text("3")
    expect(page.locator(f"#pz-{first}")).to_have_count(0)
    related = page.locator(".pz-pane .nt-related")
    expect(related.locator(".rel .back b").first).to_have_text("Supply sets the pace")
    related.get_by_role("button", name="Link").first.click()                   # a link to the related note, where the cursor is
    saved(page, ".pz-pane")
    made = next(p for p in (vault / "Inbox").iterdir() if "In my words" in p.name).read_text(encoding="utf-8")
    assert "In my words: [[Supply sets the pace]]Supply is not on a timetable." in made and made.rstrip().endswith(f"Source: [[{BOOK}#^{first}]]")
    page.locator(".pz-pane").get_by_role("button", name="Close").click()
    # added to a note that exists, as a source
    page.locator(f"#pz-{second}").click()
    page.get_by_label("Find a note to add this to").fill("supply")
    row = page.locator("#pz-targets .pz-to", has_text="Supply sets the pace")
    row.get_by_role("button", name="Add as a source").click()
    expect(page.locator(".nt-toast")).to_contain_text("Added to “Supply sets the pace” as a source")
    expect(page.locator(f"#pz-{second}")).to_have_count(0)
    assert (vault / "Supply sets the pace.md").read_text(encoding="utf-8").endswith(f"\n\nSource: [[{BOOK}#^{second}]]\n")
    # set aside, seen under its tab, and brought back
    page.locator(f"#pz-{third}").get_by_role("button", name="Set aside").click()
    expect(page.locator(".pz-item")).to_have_count(1)
    page.get_by_role("button", name="Set aside 1").click()
    page.locator(f"#pz-{third}").get_by_role("button", name="Bring back").click()
    expect(page.locator(".pz-list")).to_contain_text("Nothing is set aside")
    page.get_by_role("button", name="Processed 2").click()
    expect(page.locator(f"#pz-{first} .meta a")).to_have_text("a note without a title")
    expect(page.locator(f"#pz-{second} .meta a")).to_have_text("Supply sets the pace")
    # oldest first is one list across books; the choice is kept
    page.get_by_role("button", name="Waiting 2").click()
    page.get_by_label("Order").select_option("oldest")
    expect(page.locator(".pz-item")).to_have_count(2)
    expect(page.locator(".pz-list h3")).to_have_count(0)
    assert "order=oldest" in page.evaluate("location.hash") and book.call("GET", "/settings")["process_order"] == "oldest"
    # a quick note: its words go to a note, which opens to write on
    page.locator(f"#pz-{quick['id']}").click()
    page.get_by_label("Find a note to add this to").fill("supply sets")
    page.locator("#pz-targets .pz-to").get_by_role("button", name="Open and write").click()
    page.wait_for_selector(".pz-pane .cm-content")
    expect(page.locator(".pz-pane").get_by_label("Title")).to_have_value("Supply sets the pace")
    expect(page.locator(".pz-pane .cm-content")).to_contain_text("compare with pilots' checklists")
    expect(page.locator(f"#pz-{quick['id']}")).to_have_count(0)
    assert not list((vault / "Inbox").glob("*compare*")) and list((vault / ".trash" / "Inbox").glob("*compare*"))
    # from the book's own page, for that book alone
    page.goto(f"{book.url}/#/run/{book.id}")
    expect(page.locator("#b-process")).to_have_text("Process 1")
    page.locator("#b-process").click()
    expect(page.locator(".pz-item")).to_have_count(1)
    assert page.evaluate("location.hash").startswith("#/notes/process?book=" + book.id)
    have_now = book.call("GET", f"/vault/notes/{have['id']}")
    assert len(have_now["sources"]) == 1 and "compare with pilots' checklists" in have_now["body"]


def test_an_index_note_and_a_project_with_its_outline(page: Page, book: App, vault: Path):
    duty = make(book, title="A duty is kept by rule", body="The keeper lights the lamp whatever the weather.\n")
    once = make(book, title="Decide once", body="A decision made in advance is cheaper.\n")
    page.goto(book.url + "/#/notes")
    page.wait_for_selector("#nz-recent")
    # an index note: an ordinary note of its kind, listed on the front page as a way in
    page.get_by_role("link", name="New index note").click()
    page.wait_for_selector(".cm-focused")
    expect(page.locator(".nt-meta .lab")).to_have_text("Index note")
    title = page.get_by_label("Title")
    title.fill("On rules")
    title.press("Enter")
    page.keyboard.type("- [[Decide once]]: the core idea.")
    saved(page)
    assert "kind: index\n" in (vault / "On rules.md").read_text(encoding="utf-8")
    page.goto(book.url + "/#/notes")
    expect(page.locator("#nz-indexes .nz-row b")).to_have_text(["On rules"])
    expect(page.locator("#nz-recent .nz-row")).to_have_count(2)                # index notes are not among the notes worked on last
    # a project: made by its name, with the two sections its page works on
    page.get_by_label("Name of a new project").fill("Talk on habits")
    page.get_by_role("button", name="New project").click()
    page.wait_for_selector(".nt-project")
    expect(page.locator(".nt-meta .lab")).to_have_text(["Project", "Active"])
    project = vault / "Talk on habits.md"
    assert project.read_text(encoding="utf-8").endswith("---\n\n\n## Gathered\n\n## Outline\n") and "status: active\n" in project.read_text(encoding="utf-8")
    # notes are gathered from their own pages; the notes are not changed
    for note in (duty, once):
        page.goto(f"{book.url}/#/notes/{note['id']}")
        page.wait_for_selector(".cm-content")
        page.get_by_role("button", name="Add to a project").click()
        page.locator(".nt-pick").get_by_role("button", name="Add").click()
        expect(page.locator(".nt-toast")).to_contain_text("Gathered for “Talk on habits”")
        expect(page.locator(".nt-meta")).to_contain_text("In Talk on habits")
    assert (vault / "Decide once.md").read_text(encoding="utf-8").endswith("---\nA decision made in advance is cheaper.\n")
    page.locator(".nt-toast button").click()                                   # Open the project
    gathered = page.locator(".nt-gathered .rel")
    expect(gathered).to_have_count(2)
    # the outline: what was gathered, under a heading of one's own, put in order
    gathered.nth(0).get_by_role("button", name="To the outline").click()
    gathered.nth(1).get_by_role("button", name="To the outline").click()
    line = page.get_by_label("A heading or a line of your own for the outline")
    line.fill("Why rules")
    line.press("Enter")
    rows = page.locator(".nt-outline .out")
    expect(rows).to_have_count(3)
    rows.nth(2).get_by_role("button", name="Move up").click()
    rows.nth(1).get_by_role("button", name="Move up").click()
    expect(rows.nth(0)).to_contain_text("Why rules")
    rows.nth(1).get_by_role("button", name="Move in, under the entry above").click()
    rows.nth(2).get_by_role("button", name="Move in, under the entry above").click()
    rows.nth(2).get_by_role("button", name="Move up").click()                  # Decide once before the duty
    saved(page)
    assert project.read_text(encoding="utf-8").endswith(
        "## Gathered\n\n- [[A duty is kept by rule]]\n- [[Decide once]]\n\n## Outline\n\n- Why rules\n  - [[Decide once]]\n  - [[A duty is kept by rule]]\n")
    expect(page.locator(".nt-gathered")).to_contain_text("in the outline")
    with page.expect_download() as download:
        page.get_by_role("button", name="Export the outline").click()
    assert download.value.suggested_filename == "Talk on habits.md"
    assert Path(download.value.path()).read_text(encoding="utf-8") == (
        "# Talk on habits\n\n## Why rules\n\nA decision made in advance is cheaper.\n\nThe keeper lights the lamp whatever the weather.\n")
    # a note of the project's own
    page.get_by_role("button", name="New project note").click()
    page.wait_for_selector(".cm-focused")
    title = page.get_by_label("Title")
    title.fill("Talk - to do")
    title.press("Enter")
    page.keyboard.type("- [ ] book a room")
    saved(page)
    expect(page.locator(".nt-meta")).to_contain_text("Project: Talk on habits")
    assert 'project: "[[Talk on habits]]"' in (vault / "Talk - to do.md").read_text(encoding="utf-8")
    page.locator(".nt-meta a", has_text="Project: Talk on habits").click()
    expect(page.locator(".nt-pnotes .back b")).to_have_text(["Talk - to do"])
    # finished: put away, readable, and no longer offered
    page.get_by_role("button", name="Mark as finished").click()
    saved(page)
    expect(page.locator(".nt-meta .lab")).to_have_text(["Project", "Finished"])
    assert "status: finished\n" in project.read_text(encoding="utf-8")
    page.goto(book.url + "/#/notes")
    page.wait_for_selector("#nz-recent")
    expect(page.locator("#nz-projects")).to_have_count(0)
    page.get_by_role("button", name="Finished (1)").click()
    expect(page.locator("#nz-projects .nz-row b")).to_have_text(["Talk on habits"])
    page.goto(f"{book.url}/#/notes/{once['id']}")
    page.wait_for_selector(".cm-content")
    page.get_by_role("button", name="Add to a project").click()
    expect(page.locator(".nt-toast")).to_contain_text("You have no active project")


def test_tables_embeds_callouts_footnotes_and_tasks_show_as_what_they_are(page: Page, book: App, vault: Path):
    make(book, title="Decide once", body="A decision made in advance is cheaper.\n")
    note = make(book, title="More of Markdown", body="| Name | Price |\n| :--- | ---: |\n| Lamp oil | 12 |\n\nA footnote[^1] and an embed:\n\n![[Decide once]]\n\n"
                                                     "> [!note] Remember\n> Callouts are boxes.\n\n- [ ] book a room\n- [x] write the outline\n\n[^1]: The footnote's text.\n")
    page.goto(f"{book.url}/#/notes/{note['id']}")
    page.wait_for_selector(".cm-focused")
    page.locator(".nt-status").click()                                          # the cursor starts in the table, which then shows as text
    content = page.locator(".cm-content")
    expect(content.locator(".cm-table")).to_be_visible()
    expect(content.locator(".cm-table th")).to_have_text(["Name", "Price"])
    expect(content.locator(".cm-table td")).to_have_text(["Lamp oil", "12"])
    expect(content.locator(".cm-embed")).to_contain_text("A decision made in advance is cheaper.")
    expect(content.locator(".cm-callout").first).to_have_text("Note: Remember")
    expect(content.locator(".cm-footnote")).to_have_text("1")
    boxes = content.locator(".cm-tick")
    expect(boxes).to_have_count(2)
    assert boxes.evaluate_all("(all) => all.map((b) => b.checked)") == [False, True]
    boxes.first.click()                                                        # ticking writes the x into the text
    saved(page)
    assert "- [x] book a room\n- [x] write the outline\n" in (vault / "More of Markdown.md").read_text(encoding="utf-8")
    content.locator(".cm-footnote").hover()
    expect(page.locator(".cm-peek")).to_contain_text("The footnote's text.")
    content.locator(".cm-table-wrap").click()                                  # a table is edited as its text
    expect(content.locator(".cm-table")).to_have_count(0)
    expect(content).to_contain_text("| Lamp oil | 12 |")
    content.locator(".cm-embed b").click()                                     # the embedded note opens
    expect(page.get_by_label("Title")).to_have_value("Decide once")


def test_the_graph_a_neighbourhood_revisit_and_the_timeline(page: Page, book: App, vault: Path):
    hid = book.call("POST", f"/jobs/{book.id}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 14})["made"]
    a = make(book, title="First idea", body="Leads to [[Second idea]].", source={"job": book.id, "block": hid})
    b = make(book, title="Second idea", body="Leads to [[Third idea]].", tags=["chain"])
    make(book, title="Third idea", body="The end of the chain.")
    make(book, title="Standing alone", body="Nothing links here.")
    page.goto(book.url + "/#/notes")
    expect(page.locator("#nz-revisit .nz-row b")).to_have_text(["Standing alone"])       # the one note joined to no other
    expect(page.locator("#nz-revisit")).to_contain_text("Joined to no other note")
    # the graph: notes and the book as points, joined by links and citations
    page.locator("#nz-views a", has_text="Graph").click()
    page.wait_for_function("() => !!window.graphState && graphState().points.length > 0")
    books = len(book.call("GET", "/vault")["books"])                                     # another test may have added a second book
    expect(page.locator("#gz-info")).to_have_text(f"{4 + books} points / 3 joins", ignore_case=True)
    page.wait_for_timeout(1500)                                                          # the layout settles
    state = page.evaluate("() => graphState()")
    assert {p["title"]: p["joins"] for p in state["points"] if p["kind"] != "book"} == {"First idea": 2, "Second idea": 2, "Third idea": 1, "Standing alone": 0}
    assert sum(p["kind"] == "book" for p in state["points"]) == books and state["joins"] == 3
    box = page.locator("#gz").bounding_box()
    assert all(box["x"] <= p["x"] <= box["x"] + box["width"] and box["y"] <= p["y"] <= box["y"] + box["height"] for p in state["points"])   # all in view
    second = next(p for p in state["points"] if p["title"] == "Second idea")
    page.mouse.move(second["x"], second["y"])
    page.wait_for_function("(id) => graphState().hover === id", arg=b["id"])
    page.get_by_label("Tag").select_option("chain")
    expect(page.locator("#gz-info")).to_have_text("1 point / 0 joins", ignore_case=True)
    page.get_by_label("Tag").select_option("")
    page.get_by_label("Kind").select_option("idea")
    expect(page.locator("#gz-info")).to_have_text("4 points / 2 joins", ignore_case=True)
    page.get_by_label("Kind").select_option("")
    page.locator("details.adv summary").click()                                          # the same notes as links, for the keyboard
    expect(page.locator("#gz-list .nz-row")).to_have_count(4 + books)
    page.wait_for_timeout(1500)
    first = next(p for p in page.evaluate("() => graphState()")["points"] if p["title"] == "First idea")
    page.mouse.click(first["x"], first["y"])                                             # a click opens the note
    expect(page.get_by_label("Title")).to_have_value("First idea")
    assert page.evaluate("() => window.graphState") is None                              # the drawing stopped with its page
    # the neighbourhood: two links out, every point a link
    page.get_by_role("button", name="Neighbourhood").click()
    points = page.locator(".nt-near .pt")
    expect(points).to_have_count(4)                                                      # itself, the second idea, the book, the third idea
    expect(page.locator(".nt-near .pt.s0 text")).to_have_text("First idea")
    expect(page.locator(".nt-near .pt.s2 text")).to_have_text(["Third idea"])
    expect(page.locator(".nt-near .pt.k-book")).to_have_count(1)
    page.locator(".nt-near .pt", has_text="Second idea").click()
    expect(page.get_by_label("Title")).to_have_value("Second idea")
    expect(page.locator(".nt-near")).to_have_count(0)
    # everything to come back to, and what was done by day
    page.goto(book.url + "/#/notes/revisit")
    expect(page.locator(".nz-part").first.locator(".nz-row b")).to_have_text(["Standing alone"])
    expect(page.locator(".nz-part").nth(1)).to_contain_text("Nothing has been left alone that long")
    page.goto(book.url + "/#/notes/timeline")
    today = page.locator(".tl-day").first
    expect(today.locator(".tl-group").first).to_contain_text("Written 4")
    expect(today.locator(".tl-group a.n", has_text="First idea")).to_have_count(1)
    expect(today).to_contain_text("1 highlight, 0 notes in")
    expect(today.locator("q")).to_contain_text("The supply boat reached Gannet Rock")
    assert a["id"] in today.locator(".tl-group a.n", has_text="First idea").get_attribute("href")
