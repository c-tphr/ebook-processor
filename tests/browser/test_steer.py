"""What the reader wants from a book: said in Settings and on the Add a book page, read back by the model,
tried on a chapter, and followed by the questions asked for on highlights."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.browser.conftest import App, Reader
from tests.browser.test_notes import highlight
from tests.test_pipeline import FIXTURE

FOCUS = "textarea[aria-label='What the questions will favour']"


def test_wishes_are_said_read_back_tried_on_a_chapter_and_kept_with_the_book(page: Page, book: App):
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(book.url + "/#/add")
    page.locator("#app form").wait_for()
    # what is true of every book is kept in Settings
    page.locator("#b-settings").click()
    page.locator(".stg nav button", has_text="Your questions").click()
    page.locator("#st-steer_about").fill("I keep a lighthouse and want to run it better. " * 6)   # longer than any other setting
    page.locator("#st-steer_style").fill("Short answers. Multiple choice if you can.")
    page.locator("#st-steer_style").blur()
    page.locator(".stg header button").click()
    expect(page.locator("#use-about")).to_be_checked()   # the form offers it at once
    # the chosen book is looked at: its length gives an estimate that follows the options
    page.locator("#app form input[type=file]").set_input_files(str(FIXTURE))
    estimate = page.locator("#estimate")
    expect(estimate).to_contain_text("from 1,380 words")
    before = estimate.inner_text()
    page.locator("#app form .st-seg label", has_text="short").click()
    page.wait_for_function("(old) => document.querySelector('#estimate').innerText !== old", arg=before)
    expect(estimate).to_contain_text("at most 3 questions at a checkpoint")
    page.locator("#purpose").fill("I want to understand how the lamp is kept burning.")
    expect(page.locator("#app form")).to_contain_text("Nothing else runs until you confirm")
    page.locator("#try select").select_option(label="The Fog Season / 608 words")
    page.get_by_role("button", name="Try on a chapter").click()
    # the model's reading: what cannot be followed is said, and its question can be answered
    page.locator("#confirm-whole").wait_for(timeout=30000)
    expect(page.locator(FOCUS)).to_have_value("The reader wants: I want to understand how the lamp is kept burning.")
    expect(page.locator("#app .msg", has_text="Cannot be followed")).to_contain_text("Multiple choice")
    expect(page.locator("#app .tick", has_text="The Lamp Room")).to_have_count(1)
    page.get_by_label("Do you mean the lamp or the fog?").fill("The lamp.")
    page.get_by_label("What should change").fill("The fog matters too.")
    page.locator("#read-again").click()
    page.wait_for_function("(sel) => (document.querySelector(sel) || {}).value?.includes('Changed as asked: The fog matters too.')", arg=FOCUS, timeout=30000)
    expect(page.locator(FOCUS)).to_have_value("The reader wants: I want to understand how the lamp is kept burning. Changed as asked: The fog matters too.. Answered: The lamp..")
    # corrected by hand and confirmed: the chapter is written from exactly these words
    page.locator(FOCUS).fill("Favour how the lamp is kept burning, and the fog.")
    page.get_by_role("button", name="Confirm and try a chapter").click()
    page.locator("#go-whole").wait_for(timeout=30000)
    expect(page.locator("#tried .section")).to_have_count(3)
    expect(page.locator("#tried")).to_contain_text("text/chapter-2.xhtml")
    expect(page.locator(".stats .stat b").first).to_have_text("6")
    job_id = page.url.rsplit("/", 1)[1]
    assert book.call("GET", f"/jobs/{job_id}")["status"] == "tried"
    # the whole book keeps the chapter, and the book keeps the wish
    page.locator("#go-whole").click()
    page.locator(".files .file").first.wait_for(timeout=60000)
    expect(page.locator(".stats .stat b").first).to_have_text("16")
    expect(page.locator("#purpose")).to_have_value("I want to understand how the lamp is kept burning.")
    page.locator("details", has_text="How your wishes were read").locator("summary").click()
    expect(page.locator("details", has_text="How your wishes were read")).to_contain_text("Favour how the lamp is kept burning, and the fog.")
    job = book.call("GET", f"/jobs/{job_id}")
    assert job["confirmed"]["focus"] == "Favour how the lamp is kept burning, and the fog." and job["confirmed"]["style"] == "Short answers."
    assert any("taking over the chapter you tried: d03" in e["message"] for e in job["events"])
    page.locator("#purpose").fill("Now I care about the fog.")
    page.locator("#save-purpose").click()
    page.wait_for_function("() => document.body.innerText.includes('SAVED') || document.body.innerText.includes('Saved')")
    assert book.call("GET", f"/jobs/{job_id}")["steer"]["purpose"] == "Now I care about the fog."
    book.call("DELETE", f"/jobs/{job_id}")


def test_a_book_without_wishes_starts_at_once_and_one_with_them_can_go_on_without(page: Page, book: App):
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(book.url + "/#/add")
    page.locator("#app form input[type=file]").set_input_files(str(FIXTURE))
    page.locator("#purpose").fill("The lamp.")
    page.get_by_role("button", name="Process book").click()
    page.locator("#confirm-whole").wait_for(timeout=30000)
    expect(page.locator(".title .lab")).to_have_text("Waiting for you")
    page.get_by_role("button", name="Go on without them").click()
    page.locator(".files .file").first.wait_for(timeout=60000)
    job_id = page.url.rsplit("/", 1)[1]
    job = book.call("GET", f"/jobs/{job_id}")
    assert job["status"] == "done" and job["reading_state"] == "without" and job["report"]["counts"]["questions"] > 0
    book.call("DELETE", f"/jobs/{job_id}")


def test_another_question_on_a_highlight_takes_a_wish(reader: Reader, page: Page, book: App):
    book.call("PUT", "/settings", json_body={"hq_review": "wait"})
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    hid = reader.app.notes()["highlights"][0]["id"]
    # in the reader: the first comes at a click, another asks what should be different
    page.evaluate("(id) => questionPanel(id)", hid)
    page.locator(".hq .more").get_by_role("button", name="Make a question").click()
    expect(page.locator(".hq .one[data-q]")).to_have_count(1, timeout=15000)
    page.locator(".hq .more").get_by_role("button", name="Another").click()
    page.locator("#hq-wish").fill("Ask about the cause instead.")
    page.locator("#hq-wish").press("Enter")
    expect(page.locator(".hq .one[data-q]")).to_have_count(2, timeout=15000)
    expect(page.locator(".hq .one[data-q]").nth(1)).to_contain_text("(as asked: Ask about the cause instead.)")
    assert reader.state()["mode"] == "scroll"       # the keys typed into the box did not reach the reader
    # in the notebook: the same, and the box may be left empty
    page.goto(f"{book.url}/#/notebook/{book.id}")
    entry = page.locator(".nb .quote").first.locator("xpath=..")
    entry.get_by_role("button", name="Another question").click()
    page.locator("#nb-wish input").fill("Make it harder.")
    page.locator("#nb-wish").get_by_role("button", name="Write it").click()
    expect(page.locator(".nb .ans", has_text="(as asked: Make it harder.)")).to_have_count(1, timeout=15000)
    page.locator(".nb .ans[data-status=proposed]").first.get_by_role("button", name="Another", exact=True).click()
    page.locator("#nb-wish input").press("Enter")
    expect(page.locator(".nb .ans[data-status=proposed]")).to_have_count(4, timeout=15000)
