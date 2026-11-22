"""Questions made from highlights: asked for in the reader and the notebook, reviewed, and then asked."""

from __future__ import annotations

import threading
import time

import pytest
from playwright.sync_api import Page, Route, expect

from ebook_processor.web import server
from tests.browser.helpers import SELECT, App, Reader, highlight


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


def test_a_question_written_while_the_notebook_loads_is_still_shown(reader: Reader, page: Page, book: App, monkeypatch):
    """The notebook page reads the entries and what is still being written. A question can be finished between
    the two, and here it always is: the entries are read before it is written, and anything read apart from them
    is read after. The page must still come to show it, and not rest on "no question, nothing being written"."""
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    page.goto(f"{book.url}/#/notebook/{book.id}")
    entry = page.locator(".nb .quote").first.locator("xpath=..")
    make = entry.get_by_role("button", name="Make a question")
    make.wait_for()
    entries_read = threading.Event()
    read_entries, write_question = server.Jobs.notebook, server.hq.propose

    def notebook(jobs: server.Jobs, job_id: str) -> dict:
        try:
            return read_entries(jobs, job_id)
        finally:
            entries_read.set()

    def propose(*args, **kwargs) -> dict:
        entries_read.wait(5)                     # the model answers only when the page has read the entries
        return write_question(*args, **kwargs)

    def after_the_question(route: Route) -> None:
        for _ in range(250):                     # what is being written is asked about only when nothing is
            if route.request.method != "GET" or not book.call("GET", f"/jobs/{book.id}/questions")["pending"]:
                break
            time.sleep(0.02)
        route.continue_()

    monkeypatch.setattr(server.Jobs, "notebook", notebook)
    monkeypatch.setattr(server.hq, "propose", propose)
    page.route("**/api/jobs/*/questions", after_the_question)
    make.click()
    expect(entry.locator(".ans[data-status=proposed]")).to_have_count(1, timeout=10000)


def test_a_wish_being_typed_is_not_lost_when_another_question_arrives(reader: Reader, page: Page, book: App, monkeypatch):
    """While a question is being written the notebook looks again every second and a half, and draws itself anew.
    That must wait for a wish that is being typed, as it waits for a note that is being typed."""
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    highlight(reader, 12, 16)
    page.goto(f"{book.url}/#/notebook/{book.id}")
    first = page.locator(".nb .quote").nth(0).locator("xpath=..")
    second = page.locator(".nb .quote").nth(1).locator("xpath=..")
    first.get_by_role("button", name="Make a question").click()
    expect(first.locator(".ans[data-status=proposed]")).to_have_count(1, timeout=15000)
    answered = threading.Event()
    write_question = server.hq.propose

    def propose(*args, **kwargs) -> dict:
        answered.wait(20)                        # the model takes its time over the second highlight
        return write_question(*args, **kwargs)

    monkeypatch.setattr(server.hq, "propose", propose)
    try:
        second.get_by_role("button", name="Make a question").click()
        expect(second).to_contain_text("Writing a question")
        first.get_by_role("button", name="Another question").click()
        page.locator("#nb-wish input").fill("Make it harder.")
        page.wait_for_timeout(2000)              # longer than the wait between two looks
        expect(page.locator("#nb-wish input")).to_have_value("Make it harder.")
    finally:
        answered.set()
    page.locator("#nb-wish").get_by_role("button", name="Write it").click()
    expect(first.locator(".ans", has_text="(as asked: Make it harder.)")).to_have_count(1, timeout=15000)
    expect(second.locator(".ans[data-status=proposed]")).to_have_count(1, timeout=15000)


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


def test_a_question_finished_while_the_reader_loads_is_shown(reader: Reader, page: Page, book: App):
    """The reader asks for the highlights, and then for what is being written. A question finished between the two
    is in the second answer only, and the page dropped it there."""
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    hid = book.notes()["highlights"][0]["id"]

    def notes_first_then_the_question(route: Route) -> None:
        if route.request.method != "GET":
            return route.continue_()
        answer = route.fetch()                   # the highlights as they are now, without a question
        book.call("POST", f"/jobs/{book.id}/questions/propose", json_body={"highlight": hid})
        for _ in range(250):
            if not book.call("GET", f"/jobs/{book.id}/questions")["pending"]:
                break
            time.sleep(0.02)
        route.fulfill(response=answer)

    page.route(f"**/api/jobs/{book.id}/notes", notes_first_then_the_question)
    reader.open("2:0", "scroll")
    page.wait_for_function("() => NB.questions.length === 1", timeout=10000)
