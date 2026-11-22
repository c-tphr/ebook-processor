"""What the reader wants from a book: what the models are told, the reading to confirm, trying one chapter, the
estimate of questions, and the app's side of each. No test calls a model."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from ebook_processor import prompts, steer
from ebook_processor import highlight_questions as hq
from ebook_processor.llm import BackendError, ScriptedBackend
from ebook_processor.pipeline import PipelineError
from ebook_processor.prep.epub import load_book
from ebook_processor.runner import review_data, run_book, trial_dir, try_chapter
from ebook_processor.settings import MAX_QUESTIONS, Settings, SettingsError, from_options
from ebook_processor.steer import Steer
from tests.helpers import state_of
from tests.paths import FIXTURE
from tests.scripted import Script

SHORT = from_options(density="short")
WANT = Steer(purpose="I want to know how the lamp is kept burning.", about="I keep a lighthouse.",
             style="Short answers. I do want the dates.")
READ = {"focus": "Favour how the lamp works.", "style": "One sentence. Dates are welcome.",
        "chapters": [{"doc": "d02", "why": "It is about the lamp room."}]}


# --------------------------------------------------------------------------
# The steer, and what the question writers are told


def test_a_steer_is_the_readers_words_until_a_reading_is_confirmed():
    assert Steer().empty and Steer().key() == "" and not WANT.empty
    assert "I want to know how the lamp" in WANT.focus() and "I keep a lighthouse." in WANT.focus()
    assert WANT.manner() == WANT.style and WANT.chapters() == {}
    confirmed = Steer(WANT.purpose, WANT.about, WANT.style, READ)
    assert confirmed.focus() == "Favour how the lamp works." and confirmed.manner().startswith("One sentence")
    assert confirmed.chapters() == {"d02": "It is about the lamp room."}
    # the writers are told something else, so it is another run; the reader's own words are the same
    assert confirmed.key() != WANT.key() and confirmed.raw_key() == WANT.raw_key()
    assert Steer.from_json(confirmed.as_json()) == confirmed and Steer.from_json(None).empty
    assert Steer(purpose="x").raw_key() != Steer(about="x").raw_key()


def test_a_text_is_kept_plain_and_within_its_limit():
    assert steer.clean("  two\r\nlines  \n") == "two\nlines" and steer.clean(None) == ""
    with pytest.raises(steer.SteerError):
        steer.clean("x" * (steer.TEXT_MAX + 1))
    with pytest.raises(steer.SteerError):
        steer.clean(7)


def test_the_drafter_is_told_what_matters_and_what_no_wish_changes():
    plain = prompts.drafter_system(SHORT)
    assert "The reader gave no purpose of their own." in plain and "$" not in plain
    told = prompts.drafter_system(SHORT, WANT)
    assert "in their own words" in told and "I want to know how the lamp is kept burning." in told
    assert "I do want the dates" in told and "the reader's wishes win" in told
    assert "an answer is supported entirely by its own section" in told and "it never removes a section's questions" in told
    confirmed = prompts.drafter_system(SHORT, Steer(WANT.purpose, WANT.about, WANT.style, READ))
    assert "that the reader has read and confirmed" in confirmed and "Favour how the lamp works." in confirmed
    assert "I keep a lighthouse." not in confirmed  # the confirmed reading stands for the reader's words
    only_style = prompts.drafter_system(SHORT, Steer(style="No dates."))
    assert "**Manner.**" in only_style and "**Which ideas.**" not in only_style


def test_the_number_of_questions_is_the_models_to_choose_up_to_the_limit():
    eight = prompts.drafter_system(from_options(max_questions=8))
    assert "from 1 up to **8**" in eight and "Its length." in eight and "Its density." in eight and "What the reader wants" in eight
    assert Settings(max_questions=MAX_QUESTIONS).validate() is None
    with pytest.raises(SettingsError):
        from_options(max_questions=MAX_QUESTIONS + 1)


def test_the_checker_learns_the_manner_only_and_judges_the_same_five_things():
    assert prompts.checker_system(SHORT) == prompts.checker_system(SHORT, Steer(purpose="Only a purpose."))
    told = prompts.checker_system(SHORT, WANT)
    assert "I do want the dates" in told and "The criteria are unchanged" in told
    assert "I keep a lighthouse" not in told and "how the lamp is kept" not in told


def test_a_drafting_task_names_the_chapters_the_purpose_bears_on():
    book = load_book(FIXTURE)
    docs = [sd for sd in book.spine if sd.blocks]
    confirmed = Steer(purpose="x", reading=READ)
    message = prompts.draft_message(book, [(docs[1], docs[1].blocks)], SHORT, None, steer=confirmed)
    assert "bears strongly on these documents" in message and "- d02: It is about the lamp room." in message
    other = prompts.draft_message(book, [(docs[2], docs[2].blocks)], SHORT, None, steer=confirmed)
    assert "bears strongly" not in other and "bears strongly" not in prompts.draft_message(book, [(docs[1], docs[1].blocks)], SHORT, None)


# --------------------------------------------------------------------------
# The reading


def test_the_model_reads_the_wishes_with_a_skim_of_the_book():
    book = load_book(FIXTURE)
    script = Script()
    backend = script.backend()
    reading, done = steer.read(backend, book, WANT, SHORT)
    sent = backend.calls[0]
    assert sent["role"] == "reading" and "Reading what the reader wants" in sent["system"] and "$" not in sent["system"]
    assert "I want to know how the lamp is kept burning." in sent["prompt"] and "I keep a lighthouse." in sent["prompt"]
    assert 'd02 "The Lamp Room", 451 words' in sent["prompt"] and "Opens: " in sent["prompt"]
    assert reading["focus"].startswith("The reader wants") and reading["style"] == "Short answers."
    assert reading["chapters"] == [{"doc": "d02", "why": "It opens on the reader's subject.", "title": "The Lamp Room", "words": 451}]
    assert reading["questions"] == ["Do you mean the lamp or the fog?"] and reading["cannot"] == ""
    # again, with what the reader said about it and their answer
    again, _ = steer.read(backend, book, WANT, SHORT, reading, "More about the fog.", [(reading["questions"][0], "The lamp.")])
    second = backend.calls[1]["prompt"]
    assert "YOUR EARLIER READING" in second and "More about the fog." in second and "  The lamp." in second
    assert "Changed as asked: More about the fog." in again["focus"] and again["questions"] == []


def test_a_reading_is_cleaned_and_an_empty_one_is_refused():
    book = load_book(FIXTURE)
    wild = {"focus": "  Favour   the lamp. ", "style": "x" * 5000, "cannot": None, "gaps": "g",
            "chapters": [{"doc": "d02", "why": "a"}, {"doc": "d99", "why": "no such"}, {"doc": "d02", "why": "twice"}, "junk"],
            "questions": ["one?", "two?", "three?", "four?", 5, " "]}
    reading, _ = steer.read(ScriptedBackend(lambda *a: wild), book, WANT, SHORT)
    assert reading["focus"] == "Favour the lamp." and len(reading["style"]) == steer.MANNER_MAX
    assert [c["doc"] for c in reading["chapters"]] == ["d02"] and reading["questions"] == ["one?", "two?", "three?"]
    with pytest.raises(BackendError):
        steer.read(ScriptedBackend(lambda *a: {"focus": "", "style": ""}), book, WANT, SHORT)


def test_confirming_keeps_the_readers_edits_and_adds_answers_in_their_words():
    confirmed = steer.confirm(WANT, {**READ, "focus": "Favour the fog."}, [("Lamp or fog?", " The fog. "), ("And?", "")])
    assert confirmed.reading["focus"] == 'Favour the fog.\nAsked "Lamp or fog?", the reader said: The fog.'
    assert confirmed.purpose == WANT.purpose and confirmed.chapters() == {"d02": "It is about the lamp room."}
    with pytest.raises(steer.SteerError):
        steer.confirm(WANT, {"focus": " ", "style": ""})


# --------------------------------------------------------------------------
# A run with a steer, and trying one chapter first


def test_a_run_tells_every_writer_and_records_what_it_told(tmp_path):
    confirmed = Steer(WANT.purpose, WANT.about, WANT.style, READ)
    backend = Script(fail={"c0002-p1": 1}).backend()
    report = run_book(FIXTURE, tmp_path / "out", SHORT, backend=backend, steer=confirmed)
    by_role = {role: [c for c in backend.calls if c["role"] == role] for role in ("draft", "check", "revise")}
    assert all("Favour how the lamp works." in c["system"] for c in by_role["draft"] + by_role["revise"])
    assert all("Dates are welcome." in c["system"] and "Favour how the lamp" not in c["system"] for c in by_role["check"])
    assert any("- d02: It is about the lamp room." in c["prompt"] for c in by_role["draft"])
    assert state_of(tmp_path / "out")["steer"]["reading"] == READ
    assert report["steer"] == {"confirmed": True, "focus": READ["focus"], "style": READ["style"],
                               "chapters": [{"doc": "d02", "why": "It is about the lamp room."}]}
    text = (tmp_path / "out" / "fixture.report.md").read_text(encoding="utf-8")
    assert "## What the questions were written for" in text and "> Favour how the lamp works." in text
    # continuing with other wishes, or with none, is refused: the questions so far were written for these
    for other in (Steer(purpose="Something else."), None):
        with pytest.raises(PipelineError, match="what you want from the book"):
            run_book(FIXTURE, tmp_path / "out", SHORT, backend=Script().backend(), steer=other)
    plain = run_book(FIXTURE, tmp_path / "plain", SHORT, backend=Script().backend())
    assert plain["steer"] is None and "What the questions were written for" not in (tmp_path / "plain" / "fixture.report.md").read_text()


def test_one_chapter_is_tried_and_then_taken_over_by_the_run_of_the_book(tmp_path):
    out = tmp_path / "out"
    trial = Script(fail={"c0002-p1": 1})
    backend = trial.backend()
    tried = try_chapter(FIXTURE, out, SHORT, 3, backend=backend, steer=WANT)
    drafts = [c for c in backend.calls if c["role"] == "draft"]
    assert len(drafts) == 1 and "=== d03 " in drafts[0]["prompt"] and "=== d02 " not in drafts[0]["prompt"]
    assert [s["first_paragraph"][:3] for s in tried["sections"]] == ["d03"] * 3 and not (out / "fixture.prepared.epub").exists()
    revised = tried["sections"][1]["questions"][0]
    assert revised["revisions"] == 1 and "revised after check 1" in revised["answer"]
    assert state_of(trial_dir(out))["only"] == [3]
    # the whole book: the chapter is not written or checked again, and is there as the reader saw it
    whole = Script()
    backend = whole.backend()
    report = run_book(FIXTURE, out, SHORT, backend=backend, steer=WANT)
    assert not any("=== d03 " in c["prompt"] for c in backend.calls if c["role"] == "draft")
    assert not any("(d03 " in c["prompt"] for c in backend.calls if c["role"] == "check")
    state = state_of(out)
    assert state["carried"] == ["d03"] and [s["id"] for s in state["sections"]] == [f"s{n:04d}" for n in range(1, 9)]
    kept = next(q for q in state["questions"] if "revised after check 1" in q["answer"])
    assert kept["status"] == "passed" and kept["checks"] == 2 and len(kept["history"]) == 1 and kept["section"] == "s0005"
    assert report["counts"]["questions"] == 8 and report["counts"]["revised"] == 1
    trial_calls = state_of(trial_dir(out))["usage"]["calls"]
    assert state["usage"]["calls"] == trial_calls + len(backend.calls)  # what the trial cost is in the book's total
    assert review_data(out)["sections"][4]["questions"][0]["answer"] == revised["answer"]


def test_a_trial_made_another_way_is_not_taken_over_and_another_chapter_replaces_it(tmp_path):
    out = tmp_path / "out"
    try_chapter(FIXTURE, out, SHORT, 3, backend=Script().backend(), steer=WANT)
    backend = Script().backend()
    try_chapter(FIXTURE, out, SHORT, 2, backend=backend, steer=WANT)  # another chapter: the first trial is gone
    assert state_of(trial_dir(out))["only"] == [2] and len(backend.calls) > 0
    again = Script().backend()
    try_chapter(FIXTURE, out, SHORT, 2, backend=again, steer=WANT)  # the same again: nothing to do
    assert again.calls == []
    backend = Script().backend()
    run_book(FIXTURE, out, SHORT, backend=backend, steer=Steer(purpose="Other wishes."))
    assert state_of(out)["carried"] == [] and any("=== d02 " in c["prompt"] for c in backend.calls if c["role"] == "draft")


def test_more_questions_than_the_limit_are_sent_back(tmp_path):
    def many(reply, prompt):
        for r in reply["ranges"]:
            if r["kind"] == "section" and "PROBLEMS WITH YOUR EARLIER REPLY" not in prompt:
                r["prompts"] = [{**r["prompts"][0], "prompt": f"Question {n}: " + r["prompts"][0]["prompt"]} for n in range(6)]
        return reply

    five = from_options(density="short", max_questions=8)
    report = run_book(FIXTURE, tmp_path / "eight", five, backend=Script(draft_hook=many).backend())
    assert report["counts"]["questions"] == 6 * report["counts"]["sections"]
    backend = Script(draft_hook=many).backend()
    report = run_book(FIXTURE, tmp_path / "three", SHORT, backend=backend)
    assert report["counts"]["questions"] == report["counts"]["sections"]
    assert any("at most 3 are allowed" in c["prompt"] for c in backend.calls if c["role"] == "draft")


# --------------------------------------------------------------------------
# The estimate


def test_the_estimate_follows_the_options_and_matches_real_runs():
    paine = [192, 19335, 657, 2563, 847, 8704, 9624, 8076, 164, 345]  # a real book: 41 sections and 100 questions at medium, 3
    medium = steer.estimate(paine, from_options())
    assert medium["sections"][0] <= 41 <= medium["sections"][1] and medium["questions"][0] <= 100 <= medium["questions"][1]
    assert medium["ceiling"] == medium["sections"][1] * 3 and medium["words"] == sum(paine)
    eight = steer.estimate(paine, from_options(max_questions=8))
    steered = steer.estimate(paine, from_options(max_questions=8), steered=True)
    assert medium["questions"][1] <= eight["questions"][1] < steered["questions"][1] <= eight["ceiling"]
    assert steer.estimate(paine, from_options(density="long"))["sections"][1] < medium["sections"][0] + 20
    assert steer.estimate(paine, from_options(density="short"))["sections"][0] > medium["sections"][0]
    # one real chapter of 2,563 words gave 3 sections: 7 questions at a limit of 3, 10 at 8, and 17 at 8 with a steer
    for limit, steered_, got in ((3, False, 7), (8, False, 10), (8, True, 17)):
        e = steer.estimate([2563], from_options(max_questions=limit), steered_)
        assert e["sections"][0] <= 3 <= e["sections"][1] and e["questions"][0] <= got <= e["questions"][1]
    one = steer.estimate([20, 451, 608, 321], SHORT)  # the fixture: a title page and three short chapters
    assert one["sections"] == [3, 4] and one["questions"][0] <= 8 and one["questions"][1] >= 10  # real runs: 4 sections, 8 to 10 questions
    assert steer.estimate([], SHORT)["questions"] == [0, 0] and steer.estimate([30], SHORT)["sections"] == [0, 0]


def test_the_limit_is_one_cap_for_every_value_and_the_estimate_says_where_it_stops_mattering():
    paine = [192, 19335, 657, 2563, 847, 8704, 9624, 8076, 164, 345]
    for density in ("short", "medium", "long"):
        for steered in (False, True):
            rows = [steer.estimate(paine, from_options(density=density, max_questions=n), steered) for n in range(1, 9)]
            sections = rows[0]["sections"]
            assert all(r["sections"] == sections for r in rows)
            # what the limit allows rises with every value from 1 to 8, by the same step
            assert [r["ceiling"] for r in rows] == [sections[1] * n for n in range(1, 9)]
            # the estimate is the ceiling while the limit is below what the sections call for, and then stays there
            tops = [r["questions"][1] for r in rows]
            assert tops == sorted(tops) and all(t <= r["ceiling"] for t, r in zip(tops, rows))
            usual = rows[-1]["usual"]
            assert [r["usual"] for r in rows] == [min(n, usual) for n in range(1, 9)]
            for n, r in enumerate(rows, start=1):
                if n < usual:
                    assert r["questions"][1] == r["ceiling"], (density, steered, n)
                if n > usual:
                    assert r["questions"][1] == rows[-1]["questions"][1], (density, steered, n)
    # where the point lies follows the section length and the steer, not the limit: real runs of one chapter at
    # medium length without a steer gave 10, 10, 8 and 10 questions at limits of 4, 5, 6 and 8
    assert steer.estimate(paine, from_options(density="short", max_questions=8))["usual"] == 3
    assert steer.estimate(paine, from_options(max_questions=8))["usual"] == 4
    assert steer.estimate(paine, from_options(max_questions=8), steered=True)["usual"] == 6
    assert steer.estimate(paine, from_options(density="long", max_questions=8), steered=True)["usual"] == 8
    for limit in (4, 5, 6, 8):
        low, high = steer.estimate([2563], from_options(max_questions=limit))["questions"]
        assert low <= 8 and high >= 10


# --------------------------------------------------------------------------
# A question on a highlight


def test_a_question_on_a_highlight_follows_the_wishes_and_a_wish_for_this_one_question():
    assert "$" not in hq.system(SHORT) and "## The reader" not in hq.system(SHORT)
    told = hq.system(SHORT, WANT)
    assert "## The reader" in told and "I want to know how the lamp is kept burning." in told and "I do want the dates" in told
    assert "THE READER ASKS OF THIS QUESTION" in told


# --------------------------------------------------------------------------
# The app


@pytest.fixture
def app(make_web_app):
    return make_web_app(model=Script()).function("/api")


def add(call, plan: dict | None = None, settings: dict | None = None) -> str:
    return call.add_fixture(plan or {}, **(settings or {"density": "short"}))


def settled(call, job_id: str) -> dict:
    return call.wait(job_id)


def test_the_app_looks_at_a_book_and_estimates_before_anything_runs(app):
    status, seen = app("POST", "/look?name=fixture.epub", FIXTURE.read_bytes())
    assert status == 200 and seen["title"] == "The Lighthouse Keeper's Almanac" and seen["words"] == 1400
    assert [(c["index"], c["title"], c["words"]) for c in seen["chapters"]] == [
        (1, "Title page", 20), (2, "The Lamp Room", 451), (3, "The Fog Season", 608), (4, "The Keeper's Log", 321)]
    assert seen["suggested"] == 3  # no chapter is long: the longest
    assert app("GET", "/jobs")[1]["jobs"] == []  # looking keeps nothing
    assert app("POST", "/look/local", json_body={"path": str(FIXTURE)})[1]["words"] == 1400
    assert app("POST", "/look?name=x.epub", b"not a zip")[0] == 400
    words = [c["words"] for c in seen["chapters"]]
    status, e = app("POST", "/estimate", json_body={"words": words, "settings": {"density": "short"}})
    assert status == 200 and e["sections"] == [3, 4] and e["max_questions"] == 3 and e["firmware_limit"] is None
    more = app("POST", "/estimate", json_body={"words": words, "settings": {"density": "short", "max_questions": 8}, "steered": True})[1]
    assert more["questions"][1] > e["questions"][1] and more["max_questions"] == 8
    assert more["steered"] is True and e["steered"] is False and e["usual"] == 3 and more["ceiling"] == more["sections"][1] * 8
    big = app("POST", "/estimate", json_body={"words": [400000], "settings": {"density": "short", "max_questions": 8}})[1]
    assert big["firmware_limit"] == 1024
    assert app("POST", "/estimate", json_body={"words": "many", "settings": {}})[0] == 400
    assert app("POST", "/estimate", json_body={"words": words, "settings": {"max_questions": 9}})[0] == 400
    assert app("GET", "/info")[1]["max_questions"] == 8


def test_the_reading_is_shown_corrected_and_confirmed_before_a_trial_and_the_book(app):
    assert app("PUT", "/settings", json_body={"steer_about": "I keep a lighthouse. " * 20, "steer_style": "Short answers."})[0] == 200
    assert app("PUT", "/settings", json_body={"steer_style": "x" * 2001})[0] == 400
    assert len(app("GET", "/settings")[1]["steer_about"]) > 200
    job_id = add(app, {"purpose": "How the lamp is kept burning.", "trial": 3})
    job = settled(app, job_id)
    assert job["status"] == "asking" and job["reading_state"] == "ready" and job["trial"] == {"doc": 3, "state": "wanted"}
    assert job["read_as"]["focus"] == "The reader wants: How the lamp is kept burning." and job["read_as"]["style"] == "Short answers."
    assert job["read_as"]["questions"] and job["steer"] == {"purpose": "How the lamp is kept burning.", "interests": True, "steered": True}
    assert job["estimate"]["sections"] == [3, 4] and "spent" not in job and not (app.data / job_id / "work").exists()
    # it is not a run to continue, and an empty request to read again is refused
    assert app("POST", f"/jobs/{job_id}/continue")[0] == 409 and app("POST", f"/jobs/{job_id}/next", json_body={"next": "whole"})[0] == 409
    assert app("POST", f"/jobs/{job_id}/reading", json_body={"action": "again"})[0] == 400
    assert app("POST", f"/jobs/{job_id}/reading", json_body={"action": "again", "feedback": "More about the fog.", "answers": ["The lamp."]})[0] == 200
    job = settled(app, job_id)
    assert job["status"] == "asking" and "Changed as asked: More about the fog." in job["read_as"]["focus"] and "Answered: The lamp." in job["read_as"]["focus"]
    # confirmed as the reader edited it, with one chapter unticked: the trial is written from exactly that
    edited = job["read_as"]["focus"] + " Edited by hand."
    assert app("POST", f"/jobs/{job_id}/reading", json_body={"action": "confirm", "focus": edited, "chapters": [], "next": "trial", "doc": 2})[0] == 200
    job = settled(app, job_id)
    assert job["status"] == "tried" and job["trial"]["doc"] == 2 and job["trial"]["questions"] == 3 and job["read_as"] is None
    assert [s["first_paragraph"][:3] for s in job["tried"]["sections"]] == ["d02"] * 3
    trial = state_of(app.data / job_id / "trial")
    assert trial["steer"]["reading"] == {"focus": edited, "style": "Short answers.", "chapters": []}
    assert trial["usage"]["by_role"]["reading"]["calls"] == 2  # the two readings are in the run's total
    assert app("GET", "/jobs")[1]["jobs"][0]["status"] == "tried" and app("GET", "/jobs")[1]["jobs"][0]["can_read"] is False
    # the whole book takes the chapter over
    assert app("POST", f"/jobs/{job_id}/next", json_body={"next": "sideways"})[0] == 400
    assert app("POST", f"/jobs/{job_id}/next", json_body={"next": "whole"})[0] == 200
    job = settled(app, job_id)
    assert job["status"] == "done" and job["counts"]["questions"] == 8 and job["confirmed"]["focus"] == edited
    state = state_of(app.data / job_id)
    assert state["carried"] == ["d02"] and state["usage"]["by_role"]["reading"]["calls"] == 2 and app.script.readings == 2


def test_changed_wishes_are_read_again_and_a_run_can_go_on_without_any(app):
    job_id = add(app, {"purpose": "The lamp.", "trial": 3})
    steered = settled(app, job_id)
    assert steered["status"] == "asking"
    assert app("POST", f"/jobs/{job_id}/reading", json_body={"action": "confirm"})[0] == 200
    assert settled(app, job_id)["status"] == "tried"
    # other wishes after the trial: read again, and after the confirmation the chapter is tried again
    status, job = app("PUT", f"/jobs/{job_id}/steer", json_body={"purpose": "The fog.", "next": "trial", "doc": 3})
    assert status == 200
    job = settled(app, job_id)
    assert job["status"] == "asking" and job["read_as"]["focus"] == "The reader wants: The fog." and job["confirmed"] is None
    assert app("POST", f"/jobs/{job_id}/reading", json_body={"action": "without"})[0] == 200
    job = settled(app, job_id)
    assert job["status"] == "tried" and state_of(app.data / job_id / "trial")["steer"] is None
    assert job["estimate"]["questions"][1] < steered["estimate"]["questions"][1]  # no steer: fewer at the most
    assert app("POST", f"/jobs/{job_id}/next", json_body={"next": "whole"})[0] == 200
    assert settled(app, job_id)["status"] == "done" and state_of(app.data / job_id)["steer"] is None


def test_without_the_confirmation_the_readers_words_go_to_the_writers_as_they_are(app):
    app("PUT", "/settings", json_body={"steer_confirm": False, "steer_about": "I keep a lighthouse.", "steer_style": "Short answers."})
    job_id = add(app, {"purpose": "The lamp.", "interests": False})
    job = settled(app, job_id)
    assert job["status"] == "done" and job["reading_state"] == "none" and app.script.readings == 0
    told = state_of(app.data / job_id)["steer"]
    assert told == {"purpose": "The lamp.", "about": "", "style": "Short answers.", "reading": None}
    plain = settled(app, add(app))  # nothing wanted at all is still a steer here: the manner from the settings
    assert state_of(app.data / plain["id"])["steer"]["style"] == "Short answers."
    app("PUT", "/settings", json_body={"steer_about": None, "steer_style": None})
    assert state_of(app.data / settled(app, add(app))["id"])["steer"] is None
    assert add(app, {"purpose": "x" * 2000}) and app("POST", "/jobs?name=f.epub&plan=" + urllib.parse.quote(json.dumps({"purpose": "x" * 2001})), FIXTURE.read_bytes())[0] == 400
    assert app("POST", "/jobs?name=f.epub&plan=" + urllib.parse.quote(json.dumps({"trial": 99})), FIXTURE.read_bytes())[0] == 400


def test_questions_on_highlights_follow_the_wishes_as_they_are_now(app):
    job_id = add(app, {"purpose": "The lamp."})
    assert settled(app, job_id)["status"] == "asking"
    app("POST", f"/jobs/{job_id}/reading", json_body={"action": "confirm", "focus": "Favour the lamp.", "next": "whole"})
    assert settled(app, job_id)["status"] == "done"
    jobs = app.jobs
    assert jobs.steer_for(job_id).focus() == "Favour the lamp."  # the confirmed reading, while the words are the same
    status, job = app("PUT", f"/jobs/{job_id}/steer", json_body={"purpose": "Now the fog.", "interests": True})
    assert status == 200 and job["steer"]["purpose"] == "Now the fog." and job["status"] == "done"
    assert "Now the fog." in jobs.steer_for(job_id).focus() and jobs.steer_for(job_id).reading is None
    app("PUT", "/settings", json_body={"steer_style": "One sentence."})
    assert jobs.steer_for(job_id).manner() == "One sentence."
    app("PUT", f"/jobs/{job_id}/steer", json_body={"purpose": ""})
    app("PUT", "/settings", json_body={"steer_style": None})
    assert jobs.steer_for(job_id) is None
    # a wish for one question reaches the model, with the wishes for the book in its instructions
    app("PUT", f"/jobs/{job_id}/steer", json_body={"purpose": "Now the fog."})
    status, store = app("POST", f"/jobs/{job_id}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9})
    hid = store["made"]
    assert app("POST", f"/jobs/{job_id}/questions/propose", json_body={"highlight": hid, "instruction": "x" * 301})[0] == 400
    assert app("POST", f"/jobs/{job_id}/questions/propose", json_body={"highlight": hid, "instruction": "Ask about the cause."})[0] == 200
    for _ in range(200):
        state = app("GET", f"/jobs/{job_id}/questions")[1]
        if state["questions"] and not state["pending"]:
            break
        time.sleep(0.05)
    assert "(as asked: Ask about the cause.)" in state["questions"][0]["question"]


def test_a_running_book_keeps_its_wishes(app):
    job_id = add(app)
    assert settled(app, job_id)["status"] == "done"
    assert app("PUT", f"/jobs/{job_id}/steer", json_body={"purpose": "x" * 2001})[0] == 400
    app.jobs.jobs[job_id]["status"] = "stopped"
    assert app("PUT", f"/jobs/{job_id}/steer", json_body={"purpose": "Later."})[0] == 409
