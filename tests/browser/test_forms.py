"""Multiple choice, true or false and fill in the blank: answered at checkpoints and in Practice, graded by the app,
shown in the notebook and on a book's page, and edited where they come from highlights."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from tests.browser.helpers import App, Reader, highlight, next_checkpoint


@pytest.fixture
def formed(shelf: App) -> App:
    return shelf.never_opened()


def test_typed_answers_are_matched_with_some_forgiveness(page: Page, formed: App):
    page.goto(formed.url + "/")
    cases = [("Clockwork", ["clockwork"], True), ("the clock-work!", ["clockwork"], True), ("clockwrok", ["clockwork"], True),
             ("clokwerk", ["clockwork"], True), ("clock", ["clockwork"], False), ("tom", ["tim"], False), ("café", ["cafe"], True),
             ("1890", ["1890"], True), ("1891", ["1890"], False), ("4000", ["4,000"], True), ("3.5 knots", ["3.5 knots"], True),
             ("35 knots", ["3.5 knots"], False), ("Novembre and December", ["November and December"], True),
             ("december", ["November and December", "december and november"], False), ("a weight", ["the weight"], True),
             ("", ["x"], False), ("   ", ["x"], False), ("second", ["first", "second"], True)]
    for typed, accept, want in cases:
        assert page.evaluate("([t, a]) => QForms.matches(t, a)", [typed, accept]) is want, (typed, accept)


def test_each_form_is_answered_and_graded_at_its_checkpoint(page: Page, formed: App):
    reader = Reader(page, formed)
    reader.open("2:0", "page")
    assert next_checkpoint(reader).get("form") is None        # an open question: as before
    reader.answer("Correct")
    reader.answer("Correct")
    # multiple choice: the right one by its key, then the explanation, then on with Enter
    asked = next_checkpoint(reader)
    assert asked["form"] == "choice"
    expect(page.locator(".cp .kind")).to_have_text("Multiple choice")
    expect(page.locator(".cp .qf-choice")).to_have_count(4)
    place = page.evaluate("() => cp.order.indexOf(cp.section.prompts[cp.index].correct)")
    reader.key("abcd"[place])
    expect(page.locator(".cp .qf-verdict.right")).to_contain_text("Right")
    expect(page.locator(".cp .qf-verdict")).to_contain_text(asked["answer"])
    assert reader.state()["grades"][asked["id"]] == "correct"
    reader.key("Enter")
    reader.answer("Correct")                                    # the section's second question, an open one
    # true or false: a wrong answer is one try, with the right answer and the way to reread
    asked = next_checkpoint(reader)
    assert asked["form"] == "truefalse" and asked["truth"] is True
    expect(page.locator(".cp .kind")).to_have_text("True or false?")
    reader.key("f")
    expect(page.locator(".cp .qf-verdict.wrong")).to_contain_text("Not right")
    expect(page.locator(".cp .qf-verdict")).to_contain_text("True")
    expect(page.locator(".cp")).to_contain_text("Reread the section in")
    expect(page.locator("#cp-right")).to_have_count(0)          # only a typed answer can be overruled
    assert reader.state()["grades"][asked["id"]] == "incorrect"
    page.locator("#cp-next").click()
    reader.answer("skip")
    # fill in the blank: typed, capitals forgiven, and the words appear in the statement
    asked = next_checkpoint(reader)
    assert asked["form"] == "cloze"
    expect(page.locator(".cp h1 .qf-gap")).to_have_count(1)
    page.locator(".cp .qf-input").fill(asked["accept"][0].upper())
    page.locator(".cp .qf-input").press("Enter")
    expect(page.locator(".cp .qf-verdict.right")).to_be_visible()
    expect(page.locator(".cp h1 .qf-gap.on")).to_have_text(asked["accept"][0])
    assert reader.state()["mode"] == "page"                     # the letters typed were not keys of the reader
    reader.key("Enter")
    reader.answer("Correct")
    saved = reader.saved()
    kept = {a["id"]: a for a in saved["x_attempts"]}
    assert kept[asked["id"]]["text"] == asked["accept"][0].upper() and kept[asked["id"]]["grade"] == "correct"
    assert [a["text"] for a in saved["x_attempts"] if a["grade"] == "incorrect"] == ["False"]


def test_a_typed_answer_judged_wrong_can_be_overruled(page: Page, formed: App):
    reader = Reader(page, formed)
    reader.open("3:0", "page")
    asked = next_checkpoint(reader)
    assert asked["form"] == "cloze"
    page.locator(".cp .qf-input").fill("something else")
    page.locator(".cp .qf-type button").click()
    expect(page.locator(".cp .qf-verdict.wrong")).to_contain_text("something else")
    assert reader.state()["grades"][asked["id"]] == "incorrect"
    page.locator("#cp-right").click()
    expect(page.locator(".cp .qf-verdict.right")).to_be_visible()
    expect(page.locator(".cp")).to_contain_text("Counted as right on your word.")
    assert reader.state()["grades"][asked["id"]] == "correct"
    reader.key("Enter")
    mine = next(a for a in reader.saved()["x_attempts"] if a["id"] == asked["id"])
    assert mine["grade"] == "correct" and mine["overruled"] is True and mine["text"] == "something else"


def test_practice_asks_in_the_same_form_and_grades_the_card(page: Page, formed: App):
    sections = formed.call("GET", f"/jobs/{formed.id}/book")["sections"]
    choice = next(p for s in sections for p in s["prompts"] if p.get("form") == "choice")
    cloze = next(p for s in sections for p in s["prompts"] if p.get("form") == "cloze")
    stamp = "2026-12-01T10:00:00"
    state = formed.reading()
    state.update(grades={choice["id"]: {"first": "incorrect", "at": stamp}, cloze["id"]: {"first": "incorrect", "at": stamp}},
                 x_attempts=[{"id": choice["id"], "at": stamp, "grade": "incorrect", "text": "x", "where": "read"},
                             {"id": cloze["id"], "at": stamp, "grade": "incorrect", "text": "y", "where": "read"}])
    formed.call("PUT", f"/jobs/{formed.id}/reading", json_body=state)
    page.goto(formed.url + "/#/practice")
    seen = set()
    for _ in range(2):                                          # the two cards, in whichever order they come
        page.locator(".pr .qf-ask").wait_for()
        if page.locator(".pr .qf-choice").count() == 4:
            seen.add("choice")
            page.locator(".pr .qf-choice", has_text=choice["choices"][choice["correct"]]).click()
            expect(page.locator(".pr .qf-verdict.right")).to_be_visible()
        else:
            seen.add("cloze")
            page.locator(".pr .qf-input").fill("not it")
            page.locator(".pr .qf-input").press("Enter")
            expect(page.locator(".pr .qf-verdict.wrong")).to_contain_text(cloze["accept"][0])
            expect(page.locator("#pr-right")).to_have_count(1)
        page.locator("#pr-next").click()
        page.wait_for_timeout(300)
    assert seen == {"choice", "cloze"}
    cards = json.loads((formed.folder / "srs.json").read_text(encoding="utf-8"))["cards"]
    assert cards[choice["id"]]["reviews"][-1]["remembered"] is True and cards[cloze["id"]]["reviews"][-1]["remembered"] is False
    # the notebook and the book's page show the forms too
    page.goto(f"{formed.url}/#/notebook/{formed.id}")
    expect(page.locator(".nb .ans", has_text=cloze["prompt"])).to_contain_text(f"{cloze['accept'][0]}. {cloze['answer']}")
    page.goto(f"{formed.url}/#/run/{formed.id}")
    expect(page.locator(".q", has_text=choice["prompt"]).first.locator(".qf-list li.right")).to_have_text(choice["choices"][choice["correct"]] + " right")
    expect(page.locator(".q .lab", has_text="True or false").first).to_be_visible()


def test_the_kinds_are_ticked_in_settings_and_a_highlights_question_can_be_edited(page: Page, formed: App):
    page.goto(formed.url + "/#/add")
    page.locator("#app form .st-ticks[aria-label='Practice questions']").wait_for()
    open_box = page.locator("#app form .st-ticks[aria-label='Practice questions'] input[value=open]")
    expect(open_box).to_be_checked()
    open_box.click()                                            # the last one ticked stays ticked
    expect(open_box).to_be_checked()
    page.locator("#b-settings").click()
    ticks = page.locator(".stg .st-ticks[aria-label='Practice questions']")
    ticks.locator("label", has_text="Multiple choice").click()
    ticks.locator("label", has_text="Open").click()
    page.wait_for_function("() => AppSettings.values.add_forms === 'choice'")
    page.locator(".stg header button").click()
    expect(page.locator("#app form .st-ticks[aria-label='Practice questions'] input[value=choice]")).to_be_checked()   # the form on the page follows
    expect(open_box).not_to_be_checked()
    assert formed.call("GET", "/settings")["add_forms"] == "choice"
    # a question for a highlight is now multiple choice; its choices can be changed before it is accepted
    reader = Reader(page, formed)
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    page.goto(f"{formed.url}/#/notebook/{formed.id}")
    entry = page.locator(".nb .quote").first.locator("xpath=..")
    entry.get_by_role("button", name="Make a question").click()
    proposal = entry.locator(".ans[data-status=proposed]")
    expect(proposal.locator(".qf-list li")).to_have_count(4, timeout=15000)
    proposal.get_by_role("button", name="Reword").click()
    entry.get_by_label("Choice 2", exact=True).fill("A choice I wrote")
    entry.get_by_label("Choice 2 is the right one").check()
    entry.get_by_role("button", name="Save").click()
    expect(entry.locator(".qf-list li.right")).to_have_text("A choice I wrote right")
    entry.locator(".ans").get_by_role("button", name="Accept").click()
    expect(entry.locator(".ans[data-status=accepted]")).to_have_count(1)
    kept = formed.call("GET", f"/jobs/{formed.id}/questions")["questions"][0]
    assert kept["form"] == "choice" and kept["choices"][kept["correct"]] == "A choice I wrote" and kept["by"] == "you"


def test_the_page_words_a_question_as_plain_text_as_the_files_do(page: Page, book: App):
    """forms.py as_text and forms.js plain are two copies of one rule; the page's had lost a case."""
    from ebook_processor import forms
    page.goto(book.url + "/")
    page.wait_for_function("() => !!window.QForms")
    cases = [{"form": "truefalse", "truth": True, "prompt": "The lamp is lit at dusk.", "answer": "True, because the rule says so."},
             {"form": "truefalse", "truth": False, "prompt": "The lamp is lit at noon.", "answer": "It is lit at dusk."},
             {"form": "choice", "choices": ["Dusk", "Noon"], "correct": 0, "prompt": "When is the lamp lit?", "answer": "The rule names dusk."},
             {"form": "cloze", "accept": ["dusk"], "prompt": "The lamp is lit at ____.", "answer": "The rule names it."},
             {"prompt": "When is the lamp lit?", "answer": "At dusk."}]
    for q in cases:
        shown = page.evaluate("(q) => QForms.plain(q)", q)
        assert (shown["question"], shown["answer"]) == forms.as_text(q), q
