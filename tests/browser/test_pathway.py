"""The graduated pathway in the reader, on the worked chapter of ``docs/COMPREHENSION-PLAN.md`` (``tests/messenger.py``):
a messenger takes a forged letter to the council, by the slow road, and two councillors read its delivery differently.

The chapter is prepared both ways the plan offers, through the app's own upload. Prepared for general attention it
has three ephemeral questions (the road, who knows of the forgery, what the gatekeeper was told) and a cue for the
checkpoint's question. Prepared with explicit links it has one, about who knows of the forgery, which supports the
checkpoint's question about the councillors' reactions.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from playwright.sync_api import Page, expect

from tests import messenger
from tests.browser.helpers import READY, App
from tests.webapp import WebApp, start_web_app

ROAD, FORGERY, GATE = "s0001-e1", "s0001-e2", "s0001-e3"   # the ephemeral questions of the book prepared for attention
ONLY = "s0001-e1"                                          # ... and the one of the book prepared with links: who knows
REACTIONS, VOTE = "c0001-p1", "c0002-p1"                   # the practice questions of the two checkpoints
STATE = """() => ({
    check: qc ? { id: qc.check.id, revealed: !!qc.revealed } : null,
    question: cp ? cp.section.prompts[cp.index].id : null, help: !!(cp && cp.help),
    met: S.x_checks, grades: Object.fromEntries(Object.entries(S.grades).map(([k, v]) => [k, v.first])),
    answers: S.x_attempts.map((a) => a.id), helped: Object.keys(S.x_helped),
    word: pos.word, mode, back: !!revisit, playing: !!(view && view.playing), setting: settings.read_checks })"""


@pytest.fixture(scope="module")
def road(tmp_path_factory):
    web = start_web_app(tmp_path_factory.mktemp("road"), model=lambda s: messenger.script().backend(),
                        claude={"ok": True, "version": "0.0.0 (test)"})
    yield web
    web.close()


def prepare(web: WebApp, how: str) -> App:
    """The chapter, added as the Add form adds a book, with ephemeral questions of this purpose."""
    job = web.wait(web.add_fixture(name="The Forged Letter.epub", book=messenger.book(), density="short", ephemeral=how))
    assert job["status"] == "done", job.get("message")
    return App(web, job["id"])


@pytest.fixture(scope="module")
def prepared(road: WebApp) -> Callable[[str], App]:
    """``prepared("attention")`` or ``prepared("linked")``: the chapter prepared that way, as never opened. Each is
    prepared once for the file; a test that changes the book's questions prepares one of its own."""
    made: dict[str, App] = {}

    def get(how: str) -> App:
        if how not in made:
            made[how] = prepare(road, how)
        return made[how].never_opened()
    return get


class Reading:
    """The reader page on the chapter, driven as a person would."""

    def __init__(self, page: Page, app: App, view: str = "page"):
        self.page, self.app = page, app
        page.goto(f"{app.url}/read/{app.id}")
        page.wait_for_function(READY)
        if view != "page":
            page.locator("#modes button", has_text={"scroll": "Scroll", "rsvp": "RSVP"}[view]).click()
            page.wait_for_function("(m) => mode === m && !!view", arg=view)

    def state(self) -> dict[str, Any]:
        return self.page.evaluate(STATE)

    def key(self, key: str) -> None:
        self.page.keyboard.press(key)
        self.page.wait_for_timeout(200)

    def on(self) -> dict[str, Any]:
        """Turn pages until something stops the reader: an ephemeral question or a checkpoint."""
        for _ in range(12):
            state = self.state()
            if state["check"] or state["question"]:
                return state
            self.key("ArrowRight")
        raise AssertionError(f"nothing stopped the reader: {self.state()}")

    def saved(self) -> dict[str, Any]:
        self.page.evaluate("() => save(true)")
        self.page.wait_for_timeout(300)
        return self.app.reading()


def test_a_question_is_asked_after_the_passage_it_is_about_and_completing_it_gives_no_grade(page: Page, prepared):
    reading = Reading(page, prepared("attention"))
    section = page.evaluate("() => book.sections[0]")
    assert [c["id"] for c in section["checks"]] == [ROAD, FORGERY, GATE] and page.evaluate("() => book.ephemeral") == "attention"
    # each one stands at the end of the paragraph that holds what it asks for, inside the section
    for check, words in zip(section["checks"], ("At the fork he stopped", "Only those two knew", "into the wax herself")):
        paragraph = page.evaluate("(at) => [...view_root.querySelectorAll('[data-w]')].find((b) => +b.dataset.w + +b.dataset.n === at).textContent", check["at"])
        assert words in paragraph and section["start"] <= check["from"] < check["at"] < section["end"]
        assert not check["supports"] and check["purpose"] == "attention"   # it needs no link to the checkpoint's question
    assert reading.state()["check"] is None                    # nothing is asked before the page is read
    stop = reading.on()
    assert stop["check"] == {"id": ROAD, "revealed": False} and stop["question"] is None
    card = page.locator(".cp.qc")
    expect(card.locator("h1")).to_have_text("Which road does Tomas take to the council, and why?")
    expect(card.locator(".head")).to_contain_text("Not graded, not kept")
    assert page.locator(".cp.qc").bounding_box()["height"] >= page.locator("#stage").bounding_box()["height"] - 2   # the text is hidden
    assert "river road" not in card.text_content()
    reading.key("Enter")                                       # silent recall: reveal
    expect(card.locator(".ref")).to_contain_text("The slow river road")
    assert card.get_by_role("button", name="Correct").count() == 0   # there is nothing to grade
    reading.key("Enter")                                       # ... and continue
    state = reading.state()
    assert state["met"][ROAD]["outcome"] == "done" and "right" not in state["met"][ROAD]
    assert state["grades"] == {} and state["answers"] == []
    assert state["check"] == {"id": FORGERY, "revealed": False}   # reading goes on: the next thing on this page
    stored = reading.saved()
    assert stored["x_checks"][ROAD]["outcome"] == "done" and stored["grades"] == {} and stored["x_attempts"] == []


def test_the_reader_chooses_how_many_questions_are_asked_while_reading(page: Page, prepared):
    app = prepared("attention")
    reading = Reading(page, app)
    assert reading.on()["check"]["id"] == ROAD
    page.locator(".cp.qc [data-freq=fewer]").click()           # on the question itself
    expect(page.locator(".cp.qc [data-freq=fewer]")).to_have_attribute("aria-pressed", "true")
    reading.key("Enter"), reading.key("Enter")
    assert reading.on()["check"]["id"] == GATE                 # every second one: the question between is left out
    page.locator(".cp.qc [data-freq=off]").click()
    page.locator("#qc-skip").click()
    stop = reading.on()
    assert stop["check"] is None and stop["question"] == REACTIONS
    assert FORGERY not in stop["met"] and stop["met"][GATE]["outcome"] == "skipped" and stop["setting"] == "off"
    # the choice is kept with the app's settings, and the Reader tab of the settings window offers it too
    again = Reading(page, app)
    assert again.state()["setting"] == "off"
    page.evaluate("() => AppSettings.open('reader')")
    page.locator(".stg").get_by_role("button", name="All", exact=True).click()
    assert again.state()["setting"] == "all"


def test_a_reader_who_lost_the_thread_goes_back_to_the_passage_and_tries_again(page: Page, prepared):
    reading = Reading(page, prepared("attention"))
    reading.on()
    reading.key("Enter")
    page.locator("#qc-back").click()                           # "I lost the thread: show me the passage"
    page.wait_for_function("() => !!revisit && !qc")
    state = reading.state()
    start = page.evaluate("() => book.sections[0].checks[0].from")
    assert state["back"] and state["check"] is None and state["word"] == start and state["met"] == {}
    expect(page.locator("#stage .aim")).to_contain_text("At the fork he stopped")   # the passage is shown, and lit
    expect(page.locator(".banner")).to_contain_text("Back at the passage")
    page.locator("#qc-retry").click()                          # with the text hidden, and the answer hidden again
    assert reading.state()["check"] == {"id": ROAD, "revealed": False}
    expect(page.locator(".cp.qc .ref")).to_have_count(0)
    reading.key("Enter"), reading.key("Enter")
    state = reading.state()
    assert state["met"][ROAD] == {**state["met"][ROAD], "outcome": "done", "revisits": 1} and not state["back"]
    assert state["grades"] == {} and state["answers"] == []
    # the other way out of the passage: read on, and the question is done with
    assert reading.on()["check"]["id"] == FORGERY
    reading.key("Enter")
    page.locator("#qc-back").click()
    page.wait_for_function("() => !!revisit && !qc")
    page.locator("#qc-on").click()
    page.wait_for_function("() => !revisit")
    assert reading.state()["met"][FORGERY]["outcome"] == "done"


def test_with_links_the_check_hides_the_question_it_builds_toward_and_comes_back_as_help(page: Page, prepared):
    app = prepared("linked")
    reading = Reading(page, app)
    section = page.evaluate("() => book.sections[0]")
    assert [(c["id"], c["purpose"], c["supports"]) for c in section["checks"]] == [(ONLY, "linked", [REACTIONS])]
    assert section["prompts"][0]["help"] == [ONLY] and "cue" not in section["prompts"][0]
    stop = reading.on()                                        # after the reader learns who knows of the forgery
    assert stop["check"] == {"id": ONLY, "revealed": False}
    card = page.locator(".cp.qc")
    expect(card.locator("h1")).to_have_text("Who knows that the letter Tomas carries was forged?")
    assert "Varga" not in card.text_content() and "Oduya" not in card.text_content()   # the upcoming prompt stays hidden
    page.locator("#qc-skip").click()
    stop = reading.on()
    assert stop["question"] == REACTIONS and stop["met"][ONLY]["outcome"] == "skipped"   # the practice question comes first
    expect(page.locator(".cp h1")).to_contain_text("Why do Councillors Varga and Oduya react so differently")
    expect(page.locator(".cp .head")).to_contain_text("Question 1 of 1")
    page.locator(".cp textarea").fill("Varga trusts the seal.")
    page.locator("#cp-help").click()                           # "Help me recall"
    helper = page.locator(".cp.qc")
    expect(helper.locator(".head")).to_contain_text("Help me recall")
    expect(helper.locator("h1")).to_have_text("Who knows that the letter Tomas carries was forged?")   # the check that was skipped
    reading.key("Enter")
    expect(helper.locator(".ref")).to_contain_text("Only the steward Maren")
    reading.key("Enter")                                       # back to the same question, as it was left
    state = reading.state()
    assert state["question"] == REACTIONS and not state["help"] and state["helped"] == [REACTIONS] and state["grades"] == {}
    assert page.locator(".cp textarea").input_value() == "Varga trusts the seal."
    page.get_by_role("button", name="Reveal answer").click()
    box = page.locator(".cp .two")
    expect(box).to_contain_text("A well-supported answer accounts for")   # more than one answer is defensible
    expect(box.locator(".qf-evidence li")).to_have_count(3)
    expect(box).to_contain_text("One such answer")
    expect(page.locator(".cp .qf-several")).to_contain_text("More than one answer can be right")
    page.get_by_role("button", name="Correct").first.click()
    stored = reading.saved()
    assert stored["grades"][REACTIONS]["first"] == "correct" and list(stored["x_helped"]) == [REACTIONS]
    assert [a["id"] for a in stored["x_attempts"]] == [REACTIONS]


def test_help_is_a_cue_where_no_check_is_linked_and_is_there_without_any_check_being_met(page: Page, prepared):
    app = prepared("attention")
    reading = Reading(page, app)
    page.evaluate("() => AppSettings.set('read_checks', 'off')")   # the reader meets none of the questions inside the section
    stop = reading.on()
    assert stop["question"] == REACTIONS and stop["met"] == {}
    page.locator("#cp-help").click()
    expect(page.locator(".cp.qc h1")).to_have_text("Who knows that the steward's letter was forged?")
    page.locator("#cp-back").click()
    assert reading.state()["question"] == REACTIONS and not reading.state()["help"]


def test_practice_has_the_checkpoints_question_and_an_ephemeral_one_only_when_it_is_promoted(page: Page, road: WebApp):
    app = prepare(road, "attention").never_opened()            # its questions change: a book of this test's own
    reading = Reading(page, app)
    reading.on()
    reading.key("Enter"), reading.key("Enter")                  # the road: answered, not promoted
    assert reading.state()["check"]["id"] == FORGERY
    reading.key("Enter")
    page.locator("#qc-keep").click()                           # who knows of the forgery: kept for Practice, as written
    window = page.locator(".qf-dlg[open]")
    expect(window.locator("header b")).to_have_text("Keep for Practice")
    window.locator("#qf-save").click()
    expect(page.locator(".toast")).to_contain_text("Kept for Practice")
    expect(page.locator("#qc-kept")).to_be_visible()
    reading.key("Enter")
    assert reading.state()["check"]["id"] == GATE
    page.locator("#qc-skip").click()                           # what the gatekeeper was told: skipped, not promoted
    stop = reading.on()
    assert stop["question"] == REACTIONS
    expect(page.locator(".cp .head")).to_contain_text("Question 1 of 1")   # the promoted question is not asked here
    page.get_by_role("button", name="Reveal answer").click()
    page.get_by_role("button", name="Correct").first.click()
    stored = reading.saved()
    assert set(stored["x_checks"]) == {ROAD, FORGERY, GATE} and [a["id"] for a in stored["x_attempts"]] == [REACTIONS]
    # Practice: the checkpoint's question under the ordinary rules, the promoted question as a new card, nothing else
    cards = app.web.jobs.cards(app.id)["cards"]
    assert set(cards) == {REACTIONS, "c0001-p2"}
    assert cards[REACTIONS]["state"] == "learned" and cards["c0001-p2"]["state"] == "new"
    page.goto(f"{app.url}/#/practice/{app.id}")
    seen = []
    for _ in range(3):
        if not page.locator(".pr h1").count():
            break
        seen.append(page.locator(".pr h1").text_content())
        page.get_by_role("button", name="Reveal answer").click()
        if "Varga" in seen[-1]:
            expect(page.locator(".pr .qf-evidence li")).to_have_count(3)
        page.get_by_role("button", name="Remembered").click()
        page.wait_for_timeout(300)
    assert any("Why do Councillors Varga and Oduya" in text for text in seen)
    assert any("Who knows that the letter Tomas carries was forged?" in text for text in seen)
    assert not any("Which road" in text or "gatekeeper" in text for text in seen)


def test_an_ephemeral_question_is_promoted_with_an_edit_or_a_rewrite_from_the_books_page(page: Page, road: WebApp):
    app = prepare(road, "attention").never_opened()
    page.goto(f"{app.url}/#/run/{app.id}")
    inside = page.locator("#inside-s0001")
    expect(inside).to_contain_text("Asked while reading / 3 ephemeral questions")
    page.get_by_role("button", name=f"Keep {ROAD} for Practice").click()
    window = page.locator(".qf-dlg[open]")
    window.locator("#qf-wish").fill("the name of the bridge")   # steered: the model rewrites it for what I want to remember
    window.locator("#qf-rewrite").click()
    expect(window.locator(".qf-note")).to_contain_text("The model's rewrite is in the form")
    assert "(as asked: the name of the bridge)" in window.get_by_label("Answer", exact=True).input_value()
    window.get_by_label("Question", exact=True).fill("Which bridge was down when Tomas chose his road?")   # ... and by hand
    window.locator("#qf-save").click()
    expect(page.locator(f"[data-check='{ROAD}']")).to_contain_text("Kept for Practice as c0001-p2")
    kept = app.call("GET", f"/jobs/{app.id}/book-questions/c0001-p2")
    assert kept["question"] == "Which bridge was down when Tomas chose his road?" and kept["promoted_from"] == ROAD
    assert "(as asked: the name of the bridge)" in kept["answer"]
    assert app.web.jobs.cards(app.id)["cards"]["c0001-p2"]["state"] == "new"
    # the ephemeral question itself stays one, and the reader still does not meet the promoted one at the checkpoint
    reading = Reading(page, app)
    assert [p["id"] for p in page.evaluate("() => book.sections[0].prompts")] == [REACTIONS]
    assert reading.on()["check"]["id"] == ROAD
    expect(page.locator("#qc-kept")).to_have_count(0)           # shown once the answer is
    reading.key("Enter")
    expect(page.locator("#qc-kept")).to_be_visible()


def test_the_scroll_view_stops_the_text_at_a_question_until_it_is_answered(page: Page, prepared):
    reading = Reading(page, prepared("attention"), "scroll")
    gate = page.locator(f".gate.quick[data-check='{ROAD}']")
    expect(gate).to_be_visible()
    expect(gate).to_contain_text("A question about what you just read")
    hidden = page.evaluate("""() => { const text = document.querySelector('.sv .text'), after = [...text.querySelectorAll('[data-w]')].find((b) => b.textContent.includes('Tomas did not know what he carried'));
        return after.getBoundingClientRect().top >= text.getBoundingClientRect().bottom - 1; }""")
    assert hidden                                              # what follows the question is not shown yet
    gate.get_by_role("button", name="Answer").click()
    assert reading.state()["check"] == {"id": ROAD, "revealed": False}
    reading.key("Enter"), reading.key("Enter")
    expect(gate).to_be_hidden()
    expect(page.locator(f".gate.quick[data-check='{FORGERY}']")).to_be_visible()
    page.locator(f".gate.quick[data-check='{FORGERY}']").get_by_role("button", name="Skip").click()
    state = reading.state()
    assert state["met"][ROAD]["outcome"] == "done" and state["met"][FORGERY]["outcome"] == "skipped"


def test_rsvp_pauses_for_a_question_and_plays_on_when_it_is_done(page: Page, prepared):
    reading = Reading(page, prepared("attention"), "rsvp")
    at = page.evaluate("() => book.sections[0].checks[0].at")
    page.evaluate("(w) => { S.overrides.wpm = 900; return jump(1, w); }", at - 8)
    reading.key(" ")
    page.wait_for_function("() => !!qc", timeout=10000)
    state = reading.state()
    assert state["check"] == {"id": ROAD, "revealed": False} and not state["playing"] and state["word"] == at
    reading.key("Enter")
    page.keyboard.press("Enter")
    page.wait_for_function("() => !qc && view.playing", timeout=5000)   # the words go on
    assert reading.state()["met"][ROAD]["outcome"] == "done"
    page.keyboard.press(" ")


def test_the_add_form_offers_both_purposes_and_a_book_keeps_the_one_chosen(page: Page, road: WebApp, tmp_path):
    source = tmp_path / "The Forged Letter.epub"
    source.write_bytes(messenger.book())
    page.goto(road.url + "/#/add")
    page.locator("#app form").wait_for()
    choice = page.locator("#app form #st-ephemeral")
    expect(choice.locator("label")).to_have_text(["None", "To sustain my attention", "To build toward practice"])
    expect(choice.locator("input:checked")).to_have_value("none")   # as before, unless the reader asks
    choice.locator("label", has_text="To sustain my attention").click()
    expect(choice.locator("small")).to_contain_text("I want ephemeral questions to help me sustain my attention generally.")
    choice.locator("label", has_text="To build toward practice").click()
    expect(choice.locator("small")).to_contain_text("I want ephemeral questions to build toward practice questions explicitly.")
    page.locator("#app form .st-seg label", has_text="short").click()
    page.locator("#app form input[type=file]").set_input_files(str(source))
    page.get_by_role("button", name="Process book").click()
    page.wait_for_url("**/#/run/**")
    page.locator(".files .file").first.wait_for(timeout=60000)
    job_id = page.url.rsplit("/", 1)[1]
    job = road.call("GET", f"/api/jobs/{job_id}").data
    assert job["settings"]["ephemeral"] == "linked"
    expect(page.locator("#inside-s0001")).to_contain_text("Builds toward c0001-p1")
    road.call("PUT", "/api/settings", json={"add_ephemeral": None})   # the form remembers the choice: put it back
