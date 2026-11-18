"""Reading a book: the three views, the checkpoints, and the keys."""

from __future__ import annotations

from tests.browser.conftest import Reader


def test_the_page_view_turns_pages_and_stops_at_the_end_of_a_section(reader: Reader):
    reader.open("2:0", "page")
    first = reader.sections(2)[0]
    assert reader.page.locator(".pv .cols [data-w]").first.is_visible()
    opened = reader.read_to_checkpoint()
    assert opened == {"section": first["id"], "index": 0, "all": False}
    assert "Question 1 of 2" in reader.page.locator(".cp .head").text_content()
    reader.answer("Correct")
    assert "Question 2 of 2" in reader.page.locator(".cp .head").text_content()
    reader.answer("Incorrect")
    state = reader.state()
    assert state["checkpoint"] is None and state["grades"] == {first["prompts"][0]: "correct", first["prompts"][1]: "incorrect"}
    stored = reader.saved()
    assert {k: v["first"] for k, v in stored["grades"].items()} == state["grades"] and stored["position"]["chapter"] == 2


def test_a_completed_checkpoint_stays_closed_and_a_skipped_one_comes_back_with_what_was_skipped(reader: Reader):
    reader.open("2:0", "page")
    first = reader.sections(2)[0]
    reader.read_to_checkpoint()
    reader.answer("Correct")
    reader.answer("skip")                      # one answered, one skipped
    reader.dismiss_offer()
    assert reader.state()["checkpoint"] is None
    reader.key("ArrowLeft")
    again = reader.read_to_checkpoint()
    assert again == {"section": first["id"], "index": 1, "all": False}   # only the question that has no grade
    assert "already answered are not asked again" in reader.page.locator(".cp").text_content()
    reader.answer("Correct")
    reader.dismiss_offer()
    assert set(reader.state()["grades"]) == set(first["prompts"])
    reader.key("ArrowLeft")
    for _ in range(3):                         # reading through its end again opens nothing
        reader.key("ArrowRight")
        opened = reader.state()["checkpoint"]
        assert opened is None or opened["section"] != first["id"]
    # by choice it can be gone through again, from the contents, with every question
    if reader.state()["checkpoint"]:
        reader.page.locator(".cp").get_by_role("button", name="Skip checkpoint").click()
    reader.page.locator("#b-toc").click()
    reader.page.locator(".toc button.link", has_text="Again").first.click()
    assert reader.state()["checkpoint"] == {"section": first["id"], "index": 0, "all": True}


def test_the_scroll_view_holds_the_text_back_until_the_checkpoint_is_done(reader: Reader):
    reader.open("2:0", "scroll")
    gate = reader.page.locator(".gate").first
    assert "Begin" in gate.text_content() and "2 questions" in gate.text_content()
    hidden = reader.page.evaluate("() => { const t = document.querySelector('.sv .text'); return t.scrollHeight - t.clientHeight; }")
    assert hidden > 50                         # text beyond the checkpoint is not shown yet
    gate.get_by_role("button", name="Begin").click()
    reader.answer("Correct")
    reader.answer("Correct")
    reader.dismiss_offer()
    assert "2 of 2 recalled on the first try" in reader.page.locator(".gate").first.text_content()
    assert reader.page.locator(".gate").first.get_by_role("button", name="Go through it again").is_visible()


def test_rsvp_plays_and_pauses_with_space_and_steps_with_the_arrows(reader: Reader):
    reader.open("2:30", "rsvp")
    start = reader.state()["word"]
    reader.key(" ")
    assert reader.state()["playing"]
    reader.page.wait_for_timeout(1200)
    reader.key(" ")
    paused = reader.state()
    assert not paused["playing"] and paused["word"] > start
    reader.key("ArrowRight")
    forward = reader.state()["word"]
    reader.key("ArrowLeft")
    assert forward > paused["word"] and reader.state()["word"] < forward


def test_the_rsvp_focus_point_does_not_move_from_word_to_word(reader: Reader):
    """The marks and the centre of the focus letter stay on one spot, whatever the letter's width."""
    reader.open("2:0", "rsvp")
    seen = reader.page.evaluate("""() => {
        const el = document.querySelector(".rv .word"), out = [];
        for (let n = 0; n < 60; n++) {
            view.next();
            const f = el.querySelector(".focus");
            if (!f || !f.firstChild) continue;
            const r = document.createRange(); r.selectNodeContents(f.firstChild);
            const letter = r.getBoundingClientRect(), box = el.getBoundingClientRect(), mark = getComputedStyle(el, "::before");
            out.push({ word: el.textContent, centre: letter.left + letter.width / 2 - box.left, top: letter.top - box.top,
                       mark: parseFloat(mark.left) + parseFloat(mark.width) / 2, width: letter.width });
        }
        return out;
    }""")
    assert len(seen) > 20 and len({round(s["width"]) for s in seen}) > 3, "the sample needs letters of several widths"
    centres, marks, tops = [s["centre"] for s in seen], [s["mark"] for s in seen], [s["top"] for s in seen]
    assert max(centres) - min(centres) < 0.05, f"the focus letter moved: {sorted(set(round(c, 2) for c in centres))}"
    assert max(marks) - min(marks) < 0.05 and max(tops) - min(tops) < 0.05
    assert abs(centres[0] - marks[0]) < 0.6, "the letter is not centred on the marks"


def test_space_still_plays_after_settings_was_used_with_the_mouse(reader: Reader):
    """Firefox presses a focused button when Space comes up: left on the Settings button, Space opened Settings."""
    reader.open("2:30", "rsvp")
    reader.page.locator("#b-set").click()
    reader.page.locator(".stg[open] .tab h3").wait_for()
    reader.page.locator(".stg .st-row", has_text="Line spacing").get_by_role("button", name="Open").click()
    reader.page.locator(".stg > header button").click()
    assert reader.state()["focus"] == "BODY"
    reader.key(" ")
    state = reader.state()
    assert state["playing"] and not state["settings"]
    reader.key(" ")
    assert not reader.state()["playing"] and not reader.state()["settings"]
    for button in ("#b-toc", "#b-find", "#b-notes"):   # the panels: the keyboard goes back to the reader as well
        reader.page.locator(button).click()
        reader.page.locator("#d-close").click()
    assert reader.state()["focus"] == "BODY"
    reader.key(" ")
    assert reader.state()["playing"] and not reader.state()["drawer"]


def test_the_other_keys_of_the_reader(reader: Reader):
    reader.open("2:0", "page")
    reader.key("2")
    assert reader.state()["mode"] == "scroll"
    reader.key("3")
    assert reader.state()["mode"] == "rsvp"
    reader.key("1")
    assert reader.state()["mode"] == "page"
    reader.key("c")
    assert reader.state()["drawer"] and reader.page.locator("#d-title").text_content() == "Contents"
    reader.key("Escape")
    assert not reader.state()["drawer"]
    reader.key("/")
    assert reader.page.locator("#d-title").text_content() == "Search"
