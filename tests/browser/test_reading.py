"""Reading a book: the three views, the checkpoints, and the keys."""

from __future__ import annotations

from playwright.sync_api import Page, expect

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
            const pre = el.querySelector(".pre").getBoundingClientRect(), post = el.querySelector(".post").getBoundingClientRect();
            out.push({ word: el.textContent, centre: letter.left + letter.width / 2 - box.left, top: letter.top - box.top,
                       mark: parseFloat(mark.left) + parseFloat(mark.width) / 2, width: letter.width,
                       before: pre.width ? letter.left - pre.right : 0, after: post.width ? post.left - letter.right : 0 });
        }
        return out;
    }""")
    assert len(seen) > 20 and len({round(s["width"]) for s in seen}) > 3, "the sample needs letters of several widths"
    centres, marks, tops = [s["centre"] for s in seen], [s["mark"] for s in seen], [s["top"] for s in seen]
    assert max(centres) - min(centres) < 0.05, f"the focus letter moved: {sorted(set(round(c, 2) for c in centres))}"
    assert max(marks) - min(marks) < 0.05 and max(tops) - min(tops) < 0.05
    assert abs(centres[0] - marks[0]) < 0.05, "the letter is not centred on the marks"
    # the letters before and after sit against the focus letter, at the same distance for every word
    assert max(abs(s["before"]) for s in seen) < 0.6, [s for s in seen if abs(s["before"]) >= 0.6][:3]
    gaps = [s["after"] for s in seen if s["after"]]
    assert max(gaps) - min(gaps) < 0.6, sorted(set(round(g, 2) for g in gaps))


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


def test_rsvp_shows_a_picture_where_the_words_stand_and_says_what_to_do_below(reader: Reader, page: Page):
    figure = page.request.get(f"{reader.app.url}/api/jobs/{reader.app.id}/book/chapter/2").json()
    at = next(a for kind, a, b in figure["markers"] if kind == "IMAGE")
    reader.open(f"2:{at}", "rsvp")
    page.keyboard.press("Space")                # play: the reader stops at the picture at once
    expect(page.locator(".rv .ctx .cue")).to_have_text("A picture: look, then press play")
    expect(page.locator(".rv .word.stop figcaption")).to_be_visible()
    page.wait_for_function("() => { const i = document.querySelector('.rv .word img'); return !!i && i.complete && i.naturalWidth > 0; }")
    box = page.evaluate("""() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
        return { img: r(".rv .word img"), caption: r(".rv .word figcaption"), area: r(".rv .word"), ctl: r(".rv .ctl"), cue: r(".rv .ctx .cue") }; }""")
    assert box["area"]["top"] - 1 <= box["img"]["top"] and box["caption"]["bottom"] <= box["area"]["bottom"] + 1   # all of it in view
    assert box["caption"]["bottom"] <= box["ctl"]["top"] < box["cue"]["top"]                                    # above the controls; the cue below
    page.keyboard.press("Space")                # on: the words are back where they were
    page.keyboard.press("Space")
    expect(page.locator(".rv .word.stop")).to_have_count(0)
    assert not reader.state()["playing"]


def test_a_large_reading_state_is_still_saved(reader: Reader, page: Page):
    """A request that may outlive the page carries 64 KiB at most, and a browser refuses a larger one without a
    word. The reader made every save such a request, so a long book stopped being saved."""
    reader.open("2:0", "page")
    first = reader.sections(2)[0]["prompts"][0]
    page.evaluate("""(id) => {
        for (let i = 0; i < 40; i++) S.x_attempts.push({ id, at: `2026-12-01T10:${String(i).padStart(2, "0")}:00`, grade: "incorrect", text: "x".repeat(3000), where: "read" });
        save(true);
    }""", first)
    page.wait_for_function("() => sentAttempts.size === 40")
    assert len(reader.app.reading()["x_attempts"]) == 40
    page.evaluate("(id) => { S.x_attempts.push({ id, at: '2026-12-01T11:00:00', grade: 'correct', text: 'short', where: 'read' }); save(true, true); }", first)
    page.wait_for_function("() => sentAttempts.size === 41")   # only the new answer is sent: the app keeps the others
    kept = reader.app.reading()["x_attempts"]
    assert len(kept) == 41 and kept[-1]["text"] == "short" and len(kept[0]["text"]) == 3000


def test_a_save_that_does_not_arrive_is_said_and_tried_again(reader: Reader, page: Page):
    reader.open("2:0", "page")
    page.route("**/api/jobs/*/reading", lambda route: route.abort() if route.request.method == "PUT" else route.continue_())
    page.evaluate("() => { save(true); }")
    page.evaluate("() => { save(true); }")
    expect(page.locator("#unsaved")).to_contain_text("not being saved")
    page.unroute("**/api/jobs/*/reading")
    expect(page.locator("#unsaved")).to_have_count(0, timeout=9000)   # the page tries again by itself


def test_answers_are_stamped_in_local_time(reader: Reader, page: Page):
    """Practice counts in local days, so the day of an answer must be the local day too."""
    reader.open("2:0", "page")
    stamp, local = page.evaluate("""() => { const d = new Date(), two = (n) => String(n).padStart(2, "0");
        return [stamp(), `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}`]; }""")
    assert stamp.startswith(local)
