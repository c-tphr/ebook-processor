"""Editing a book's questions where they are met: on the book's page, at a checkpoint, in Practice and in the
notebook; adding one by hand; and the model's help in the editor."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from tests.browser.helpers import ASKED, App, Reader, highlight, next_checkpoint


@pytest.fixture
def edited(shelf: App) -> App:
    """The book of every kind (the app of ``shelf``), as the run wrote it and never opened."""
    shelf.never_opened()
    review = shelf.call("GET", f"/jobs/{shelf.id}/review")
    for q in [q for s in review["sections"] for q in s["questions"]]:
        if q["added"]:
            shelf.call("DELETE", f"/jobs/{shelf.id}/book-questions/{q['id']}")
        elif q["edited"]:
            shelf.call("POST", f"/jobs/{shelf.id}/book-questions/{q['id']}/restore", json_body={})
    return shelf


def question(app: App, qid: str) -> dict:
    return app.call("GET", f"/jobs/{app.id}/book-questions/{qid}")


def test_a_question_is_edited_rewritten_and_checked_on_the_books_page(page: Page, edited: App):
    page.goto(f"{edited.url}/#/run/{edited.id}")
    page.get_by_role("button", name="Edit question c0002-p1").click()
    window = page.locator(".qf-dlg[open]")
    expect(window.locator("header .m")).to_have_text("Section at d02-p005")
    expect(window.get_by_label("Choice 4", exact=True)).to_be_visible()
    # by hand: a choice and which one is right
    window.get_by_label("Choice 1", exact=True).fill("A choice of my own")
    window.get_by_label("Choice 1 is the right one").check()
    # the model's rewrite to a wish fills the form and keeps nothing
    window.locator("#qf-wish").fill("Make it harder.")
    window.locator("#qf-wish").press("Enter")
    expect(window.get_by_label("Explanation")).to_have_value("It says what d02-p005 says. (as asked: Make it harder.)", timeout=15000)
    expect(window.locator(".qf-note")).to_contain_text("Save keeps it")
    assert question(edited, "c0002-p1")["status"] == "passed"
    window.get_by_label("Explanation").fill("My own explanation of it.")
    # saved and checked: the verdict is shown, the window stays, the page behind follows
    window.locator("#qf-check").click()
    expect(window.locator(".qf-verdict")).to_contain_text("The check passed it", timeout=15000)
    expect(window.locator("#qf-state")).to_have_text("Edited by you.")
    saved = question(edited, "c0002-p1")
    assert saved["answer"] == "My own explanation of it." and saved["verdict"]["passed"] is True and saved["has_original"]
    window.locator("header button").click()
    row = page.locator(".q", has_text="c0002-p1")
    expect(row.locator(".lab", has_text="Edited by you")).to_be_visible()
    expect(row).to_contain_text("You had it checked: it passed.")
    expect(row).to_contain_text("My own explanation of it.")
    # the original comes back
    page.get_by_role("button", name="Edit question c0002-p1").click()
    page.locator("#qf-restore").click()
    expect(page.locator(".qf-note")).to_contain_text("The run's own wording is back")
    assert question(edited, "c0002-p1")["status"] == "passed"
    page.locator(".qf-dlg header button").click()
    expect(page.locator(".q", has_text="c0002-p1").locator(".lab", has_text="Edited by you")).to_have_count(0)


def test_a_question_of_any_kind_is_added_to_a_section_with_its_passage(page: Page, edited: App):
    page.goto(f"{edited.url}/#/run/{edited.id}")
    # the whole section by default; a wrong form is said in the window and keeps what was typed
    page.get_by_role("button", name="Add a question to Section at d02-p005").click()
    window = page.locator(".qf-dlg[open]")
    window.locator(".qf-kinds button", has_text="Fill in the blank").click()
    window.get_by_label("Statement").fill("The lamp room was round and bright.")
    window.get_by_label("Explanation").fill("It had glass on every side.")
    window.get_by_label("Missing words").fill("round")
    window.locator("#qf-save").click()
    expect(window.locator(".qf-note.bad")).to_contain_text("four underscores")
    window.get_by_label("Statement").fill("The lamp room was ____ and bright.")
    expect(window.locator(".qf-passage summary")).to_have_text("Passage: 3 of the section's 3 paragraphs")
    window.locator("#qf-save").click()
    row = page.locator(".q", has_text="The lamp room was ____ and bright.")
    expect(row.locator(".lab", has_text="Written by you")).to_be_visible(timeout=20000)   # every file is built again: seconds on a slow machine
    expect(row.locator(".lab", has_text="Fill in the blank")).to_be_visible()
    expect(page.locator(".stats .stat b").first).to_have_text("17")
    added = question(edited, "c0002-p3")
    assert added["accept"] == ["round"] and len(added["supporting_paragraphs"]) == 3
    # the model picks the passage and shows its choice first
    edited.call("PUT", "/settings", json_body={"own_passage": "model_review"})
    page.reload()
    page.get_by_role("button", name="Add a question to Section at d02-p005").click()
    window.get_by_label("Question", exact=True).fill("How many steps led to the lamp room?")
    window.get_by_label("Answer", exact=True).fill("One hundred and twelve.")
    expect(window.locator(".qf-passage")).to_be_hidden()
    window.locator("#qf-save").click()
    expect(window.locator(".qf-hint", has_text="The model chose these paragraphs")).to_be_visible(timeout=15000)
    expect(window.locator(".qf-passage summary")).to_have_text("Passage: 1 of the section's 3 paragraphs")
    window.locator(".qf-passage label").first.locator("input").check()
    window.locator("#qf-save").click()
    expect(page.locator(".q", has_text="How many steps led to the lamp room?")).to_be_visible()
    assert question(edited, "c0002-p4")["supporting_paragraphs"] == ["d02-p005", "d02-p006"]
    # one's own question can be deleted from its editor
    page.on("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Edit question c0002-p4").click()
    page.locator("#qf-delete").click()
    expect(page.locator(".q", has_text="How many steps led to the lamp room?")).to_have_count(0)


def test_a_question_is_edited_at_its_checkpoint_and_started_over(page: Page, edited: App):
    page.on("dialog", lambda dialog: dialog.accept())
    reader = Reader(page, edited)
    reader.open("2:0", "page")
    asked = next_checkpoint(reader)
    expect(page.locator("#cp-edit")).to_have_count(0)            # not before the answer has been seen
    page.get_by_role("button", name="Reveal answer").click()
    page.locator("#cp-edit").click()
    window = page.locator(".qf-dlg[open]")
    window.get_by_label("Answer", exact=True).fill("An answer I corrected while reading.")
    window.get_by_label("Answer", exact=True).press("c")                     # a key of the checkpoint, typed into the form
    window.locator("#qf-save").click()
    expect(page.locator(".cp .two")).to_contain_text("An answer I corrected while reading.c")
    assert reader.state()["checkpoint"]["index"] == 0 and asked["id"] not in reader.state()["grades"]
    page.locator(".cp").get_by_role("button", name="Incorrect").click()
    page.locator(".cp").get_by_role("button", name="Continue").click()
    assert reader.state()["grades"][asked["id"]] == "incorrect"
    # the second question: graded, then edited and started over, so that it is asked again
    second = page.evaluate(ASKED)
    page.get_by_role("button", name="Reveal answer").click()
    page.locator(".cp").get_by_role("button", name="Incorrect").click()
    page.locator("#cp-edit").click()
    window.get_by_label("Question", exact=True).fill("A question I rewrote into another one?")
    window.locator("#qf-save").click()
    expect(page.locator(".cp h1")).to_have_text("A question I rewrote into another one?")
    page.locator("#cp-edit").click()
    page.locator("#qf-restart").click()
    expect(page.locator(".qf-note")).to_contain_text("Started over")
    page.locator(".qf-dlg header button").click()
    assert second["id"] not in reader.state()["grades"]
    page.locator(".cp").get_by_role("button", name="Continue").click()
    saved = reader.saved()
    assert second["id"] not in saved["grades"] and saved["grades"][asked["id"]]["first"] == "incorrect"
    assert [a["id"] for a in saved["x_attempts"]] == [asked["id"], second["id"]]   # what was answered stays as history


def test_a_card_is_edited_in_practice_and_an_answer_in_the_notebook(page: Page, edited: App):
    sections = edited.call("GET", f"/jobs/{edited.id}/book")["sections"]
    first = sections[0]["prompts"][0]
    stamp = "2026-12-01T10:00:00"
    state = edited.reading()
    state.update(grades={first["id"]: {"first": "incorrect", "at": stamp}},
                 x_attempts=[{"id": first["id"], "at": stamp, "grade": "incorrect", "text": "a guess", "where": "read"}])
    edited.call("PUT", f"/jobs/{edited.id}/reading", json_body=state)
    page.goto(edited.url + "/#/practice")
    page.get_by_role("button", name="Reveal answer").click()
    page.locator("#pr-edit").click()
    window = page.locator(".qf-dlg[open]")
    window.get_by_label("Answer", exact=True).fill("An answer I corrected in practice.")
    window.get_by_label("Answer", exact=True).press("2")                     # "Remembered" on the card, typed into the form
    window.locator("#qf-save").click()
    expect(page.locator(".pr .two")).to_contain_text("An answer I corrected in practice.2")   # the card stays, as it now is
    cards = json.loads((edited.folder / "srs.json").read_text(encoding="utf-8"))["cards"]
    assert len(cards[first["id"]]["reviews"]) == 1               # the key did not answer the card
    page.goto(f"{edited.url}/#/notebook/{edited.id}")
    answer = page.locator(".nb .ans", has_text="An answer I corrected in practice.2")
    answer.get_by_role("button", name="Edit question").click()
    window.get_by_label("Question", exact=True).fill("A question I reworded in the notebook?")
    window.locator("#qf-save").click()
    expect(page.locator(".nb .ans", has_text="A question I reworded in the notebook?")).to_have_count(1)
    assert question(edited, first["id"])["question"] == "A question I reworded in the notebook?"


def test_a_highlights_own_question_can_be_of_any_kind_and_the_passage_is_set_in_settings(page: Page, edited: App):
    reader = Reader(page, edited)
    reader.open("2:0", "scroll")
    highlight(reader, 2, 7)
    page.goto(f"{edited.url}/#/notebook/{edited.id}")
    entry = page.locator(".nb .quote").first.locator("xpath=..")
    entry.get_by_role("button", name="Write my own").click()
    entry.locator(".qf-kinds button", has_text="True or false").click()
    entry.get_by_label("Statement").fill("The supply boat reached the rock after noon.")
    entry.get_by_label("Explanation").fill("A little after noon.")
    entry.get_by_role("button", name="Save").click()
    expect(entry.locator(".ans[data-status=accepted]")).to_contain_text("True or false: The supply boat reached the rock after noon.")
    assert edited.call("GET", f"/jobs/{edited.id}/questions")["questions"][0]["form"] == "truefalse"
    page.locator("#b-settings").click()
    page.locator(".stg nav button", has_text="Your questions").click()
    page.locator(".stg .st-row", has_text="Its passage").get_by_role("button", name="I pick the paragraphs").click()
    page.wait_for_function("() => AppSettings.values.own_passage === 'pick'")
    assert edited.call("GET", "/settings")["own_passage"] == "pick"
