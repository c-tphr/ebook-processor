"""Questions made from highlights: asked for in the reader and the notebook, reviewed, and then asked."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import App, Reader
from tests.browser.test_notes import SELECT, highlight


def ask_in_reader(reader: Reader, first: int = 2, last: int = 7) -> dict:
    """Select words and press Question, as a reader would."""
    chosen = reader.page.evaluate(SELECT, [first, last])
    reader.page.locator(".tip button", has_text="Question").click()
    reader.page.wait_for_function("() => NB.highlights.length > 0")
    return chosen


def settled(reader: Reader) -> list[dict]:
    reader.page.wait_for_function("() => asking.pending.length === 0 && NB.questions.length > 0", timeout=10000)
    return reader.page.evaluate("() => NB.questions")


def test_a_question_asked_for_while_reading_waits_in_the_notebook_and_is_then_asked_at_the_checkpoint(reader: Reader, page: Page, book: App):
    reader.open("2:0", "page")
    ask_in_reader(reader)
    expect(page.locator(".toast")).to_contain_text("It will wait in the notebook")
    assert reader.state()["drawer"] is False       # reading goes on
    made = settled(reader)
    assert [q["status"] for q in made] == ["proposed"] and len(reader.sections(2)[0]["prompts"]) == 2
    page.goto(f"{book.url}/#/notebook/{book.id}")
    entry = page.locator(".nb .ans[data-status=proposed]")
    expect(entry).to_contain_text("Proposed")
    expect(page.locator(".kv", has_text="Proposals waiting for you")).to_contain_text("1")
    entry.get_by_role("button", name="Accept").click()
    accepted = page.locator(".nb .ans[data-status=accepted]")
    expect(accepted).to_contain_text("Not asked yet")
    expect(accepted).to_contain_text("Asked at the end of its section")
    # in the reader the section's checkpoint now has a third question, the reader's own
    reader.open("2:0", "page")
    assert reader.sections(2)[0]["prompts"][2] == made[0]["id"]
    reader.read_to_checkpoint()
    reader.answer("Correct")
    reader.answer("Correct")
    expect(page.locator(".cp .head")).to_contain_text("Question 3 of 3 / from your highlight")
    reader.answer("Incorrect")
    stored = reader.saved()
    assert stored["grades"][made[0]["id"]]["first"] == "incorrect" and len(stored["grades"]) == 3
    page.goto(f"{book.url}/#/notebook/{book.id}")
    expect(page.locator(".nb .ans[data-status=accepted]")).to_contain_text("Missed")


@pytest.mark.parametrize("mode", ["wait", "toast", "auto"])
def test_the_setting_decides_what_happens_when_the_question_arrives(reader: Reader, page: Page, book: App, mode: str):
    book.call("PUT", "/settings", json_body={"hq_review": mode})
    reader.open("2:0", "scroll")
    ask_in_reader(reader)
    made = settled(reader)
    if mode == "wait":                           # a panel, with the proposal to decide on
        expect(page.locator("#d-title")).to_have_text("Questions")
        one = page.locator(".hq .one").first
        expect(one).to_contain_text("Proposed")
        one.get_by_role("button", name="Accept").click()
        expect(page.locator(".hq .one").first).to_contain_text("Accepted")
    elif mode == "toast":                        # a quiet notice that can be pressed
        notice = page.locator(".toast")
        expect(notice).to_contain_text("A question is ready")
        assert reader.state()["drawer"] is False
        notice.get_by_role("button", name="Review").click()
        expect(page.locator(".hq .one")).to_have_count(1)
        page.locator(".hq .one").get_by_role("button", name="Accept").click()
        expect(page.locator(".hq .one").first).to_contain_text("Accepted")
    else:                                        # accepted without asking, after its check
        assert made[0]["status"] == "accepted" and made[0]["checked"]["passed"] and reader.state()["drawer"] is False
    # either way the section's checkpoint has the question at once, without loading the book again
    page.wait_for_function("() => sectionsIn(2)[0].prompts.length === 3")
    assert reader.sections(2)[0]["prompts"][2] == made[0]["id"]


def test_questions_are_made_reworded_written_and_proposed_for_many_in_the_notebook(reader: Reader, page: Page, book: App):
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    highlight(reader, 12, 16)
    highlight(reader, 22, 26)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{book.url}/#/notebook/{book.id}")
    first = page.locator(".nb .quote").nth(0).locator("xpath=..")
    first.get_by_role("button", name="Make a question").click()
    proposal = first.locator(".ans[data-status=proposed]")
    expect(proposal).to_have_count(1)             # written in the background, shown when it is there
    proposal.get_by_role("button", name="Reword").click()
    first.get_by_label("Question").fill("What is said, in my own words?")
    first.get_by_role("button", name="Save").click()
    expect(first.locator(".ans")).to_contain_text("What is said, in my own words?")
    first.locator(".ans").get_by_role("button", name="Accept").click()
    expect(first.locator(".ans[data-status=accepted]")).to_have_count(1)
    first.get_by_role("button", name="Write my own").click()
    first.get_by_label("Question").fill("A question I wrote?")
    first.get_by_label("Answer").fill("The answer I wrote.")
    first.get_by_role("button", name="Save").click()
    expect(first.locator(".ans[data-status=accepted]")).to_have_count(2)
    # the other two highlights have no question: one action proposes for both
    page.get_by_role("button", name="Propose a question for each of the 2 highlights that has none").click()
    expect(page.locator(".nb .ans[data-status=proposed]")).to_have_count(2, timeout=15000)
    expect(page.get_by_role("button", name="Propose a question for each")).to_have_count(0)
    # removing the first highlight asks about its questions; kept, they stay with the words they were about
    first.get_by_role("button", name="Remove highlight").click()
    expect(page.locator("[id^=nb-kept-]")).to_have_count(2)
    expect(page.locator(".nb .quote")).to_have_count(4)   # two highlights, and the words each kept question was about
    state = book.call("GET", f"/jobs/{book.id}/questions")["questions"]
    assert sorted(q["status"] for q in state) == ["accepted", "accepted", "proposed", "proposed"]
    assert [q["highlight"] for q in state if q["status"] == "accepted"] == [None, None]


def test_a_question_for_a_section_already_read_is_practised(reader: Reader, page: Page, book: App):
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    hid = reader.app.notes()["highlights"][0]["id"]
    reader.page.evaluate("() => jump(3, 0)")       # read on, past the end of the section
    reader.saved()
    book.call("POST", f"/jobs/{book.id}/questions", json_body={"highlight": hid, "question": "Asked later, in practice?", "answer": "Yes."})
    page.goto(book.url + "/#/practice")
    expect(page.locator(".pr h1")).to_have_text("Asked later, in practice?")
    page.get_by_role("button", name="Reveal answer").click()
    page.locator(".pr summary", has_text="From the book").click()
    expect(page.locator(".pr mark").first).to_be_visible()   # the passage it came from, with the highlight
