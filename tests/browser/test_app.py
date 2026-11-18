"""The pages around the reader: library, adding a book, settings, question previews, practice."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.browser.conftest import App, Reader
from tests.test_pipeline import FIXTURE


def test_the_library_lists_the_book_and_the_settings_window_opens_on_the_tab_of_the_page(page: Page, book: App):
    page.goto(book.url + "/")
    expect(page.locator(".run .t").first).to_have_text("The Lighthouse Keeper's Almanac")
    for where, tab in (("#/", "General"), ("#/add", "Add a book"), (f"#/notebook/{book.id}", "Notebook"), ("#/practice", "Practice")):
        page.goto(f"{book.url}/{where}")
        page.locator("#b-settings").click()
        expect(page.locator(".stg nav [aria-selected=true]")).to_have_text(tab)
        page.keyboard.press("Escape")
        expect(page.locator(".stg[open]")).to_have_count(0)


def test_colours_and_text_settings_apply_at_once_and_are_kept(reader: Reader, page: Page, book: App):
    reader.open("2:0", "scroll")
    size = "() => parseFloat(getComputedStyle(document.querySelector('.sv .text p')).fontSize)"
    before = page.evaluate(size)
    page.locator("#b-set").click()
    expect(page.locator(".stg nav [aria-selected=true]")).to_have_text("Reader")
    page.locator(".stg .st-row", has_text="Size").get_by_role("button", name="Larger").click()
    assert page.evaluate(size) == before + 1   # the text behind the window follows
    page.locator(".stg nav button", has_text="General").click()
    page.locator(".stg .st-row", has_text="Colours").get_by_role("button", name="Dark").click()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    page.wait_for_timeout(300)
    assert book.call("GET", "/settings") == {"size": before + 1, "theme": "dark"}
    page.goto(book.url + "/")                  # every page of the app takes the colours
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"


def test_a_book_is_added_through_the_form_and_ends_up_with_its_files(page: Page, book: App):
    page.goto(book.url + "/#/add")
    page.locator("#app form").wait_for()
    page.locator("#app form .st-seg label", has_text="short").click()
    page.locator("#app form input[type=file]").set_input_files(str(FIXTURE))
    page.get_by_role("button", name="Process book").click()
    page.wait_for_url("**/#/run/**")
    page.locator(".files .file").first.wait_for(timeout=60000)
    expect(page.locator(".stats .stat b").first).to_have_text("16")   # two questions for each of eight sections
    assert page.locator(".files .file a.btn").count() == 6


def test_question_previews_show_what_is_unanswered_and_are_noted_with_the_answer(reader: Reader, page: Page, book: App):
    book.call("PUT", "/settings", json_body={"read_preview": True})
    reader.open("2:0", "page")
    first = reader.sections(2)[0]
    box = page.locator(".pv .pre").first
    expect(box.locator("li")).to_have_count(2)
    page.wait_for_function("(ids) => ids.every((id) => S.x_previewed[id])", arg=first["prompts"])   # seen on screen: recorded
    reader.read_to_checkpoint()
    reader.answer("Correct")
    reader.answer("skip")
    reader.dismiss_offer()
    reader.key("ArrowLeft")
    reader.page.evaluate("() => jump(2, 0)")
    expect(page.locator(".pv .pre").first.locator("li")).to_have_count(1)   # the answered one is no longer previewed
    reader.saved()
    # RSVP opened at the section's start shows the questions, and one press of Space plays
    reader.mode("rsvp")
    expect(page.locator(".rv .ctx .pre li")).to_have_count(1)
    reader.key(" ")
    assert reader.state()["playing"]
    reader.key(" ")
    reader.saved()
    page.goto(f"{book.url}/#/notebook/{book.id}")
    expect(page.locator(".nb .ans", has_text="Previewed before reading")).to_have_count(2)   # both were shown beforehand


def test_an_answered_question_comes_back_in_practice(reader: Reader, page: Page, book: App):
    reader.open("2:0", "page")
    reader.read_to_checkpoint()
    reader.answer("Correct")
    reader.answer("Correct")
    reader.saved()
    page.goto(book.url + "/#/practice")
    expect(page.locator(".pr h1")).to_contain_text("What does the passage")
    page.get_by_role("button", name="Reveal answer").click()
    page.get_by_role("button", name="Remembered").click()
    page.wait_for_timeout(300)
    cards = book.call("GET", "/practice")["counts"]
    assert cards["cards"] == 2 and cards["due"] + cards["new"] + cards["again"] >= 0
