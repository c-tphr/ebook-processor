"""The graduated pathway (``docs/COMPREHENSION-PLAN.md``) outside the browser: what a run holds of it, the rules a
model's reply is held to, what the checker is shown, the reader's record, Practice, and promotion.

The book is the plan's worked chapter (``tests/messenger.py``), prepared both ways, and the made-up book of the
other tests for what needs several sections.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ebook_processor import editing, pathway, prompts, reading, srs
from ebook_processor import forms as qforms
from ebook_processor.llm import BackendError
from ebook_processor.pipeline import BookIndex, PipelineError, check_part, draft_document, kept, read_check, read_revisions
from ebook_processor.prep.epub import load_book
from ebook_processor.runner import change_run, open_run, rebuild, review_data, run_book, try_chapter
from ebook_processor.settings import Settings, from_options
from tests import messenger
from tests.helpers import run, state_of
from tests.paths import FIXTURE
from tests.scripted import EMPTY_FORM, EMPTY_PATHWAY, Script

ATTENTION = from_options(density="short", ephemeral="attention")
LINKED = from_options(density="short", ephemeral="linked")
REACTIONS = "c0001-p1"


def letter(tmp_path: Path, settings: Settings, **more):
    """The worked chapter, prepared with these settings: (the report, the folder of its outputs)."""
    epub = tmp_path / "The Forged Letter.epub"
    epub.write_bytes(messenger.book())
    return run_book(epub, tmp_path / "out", settings, backend=messenger.script().backend(), **more), tmp_path / "out"


def pathway_of(folder: Path) -> dict:
    return json.loads((folder / pathway.FILE).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Preparing a book both ways


def test_prepared_for_attention_a_check_about_the_road_needs_no_link(tmp_path):
    report, out = letter(tmp_path, ATTENTION)
    state = state_of(out)
    road, forgery, gate = state["ephemeral"]
    assert road["prompt"] == "Which road does Tomas take to the council, and why?"
    assert all(c["purpose"] == "attention" and c["supports"] == [] and c["status"] == "passed" for c in state["ephemeral"])
    assert [c["id"] for c in state["ephemeral"]] == ["s0001-e1", "s0001-e2", "s0001-e3"]
    question = state["questions"][0]
    assert question["id"] == REACTIONS and question["reasoning"] == "explain" and len(question["accounts_for"]) == 3
    assert question["cue"] == {"prompt": "Who knows that the steward's letter was forged?", "answer": "Only the steward Maren and the scribe Ilse."}
    assert report["counts"]["ephemeral"] == 3 and report["counts"]["questions"] == 2 and report["counts"]["higher_order"] == 1
    assert "asks 3 ephemeral questions" in (out / "The Forged Letter.report.md").read_text(encoding="utf-8")
    made = pathway_of(out)
    assert made["mode"] == "attention" and [c["id"] for c in made["ephemeral"]] == ["s0001-e1", "s0001-e2", "s0001-e3"]
    assert made["questions"][REACTIONS]["cue"]["prompt"].startswith("Who knows") and "help" not in made["questions"][REACTIONS]


def test_prepared_with_links_the_check_about_the_forgery_names_the_question_it_builds_toward(tmp_path):
    report, out = letter(tmp_path, LINKED)
    state = state_of(out)
    (check,) = state["ephemeral"]
    assert check["prompt"] == "Who knows that the letter Tomas carries was forged?"
    assert check["purpose"] == "linked" and check["supports"] == [REACTIONS]      # the link is kept
    book = load_book(out / "work" / "source.epub")
    where = BookIndex(book).where
    # it is asked once the reader has learnt who knows: after that paragraph, and before the councillors speak
    assert check["supporting_paragraphs"] == [check["after"]]
    assert "Only those two knew" in " ".join(where[check["after"]][0].blocks[where[check["after"]][1]].words)
    assert all(where[check["after"]][1] < where[ref][1] for ref in state["questions"][0]["supporting_paragraphs"])
    made = pathway_of(out)
    assert made["questions"][REACTIONS]["help"] == [check["id"]] and "cue" not in made["questions"][REACTIONS]
    assert made["ephemeral"][0]["supports"] == [REACTIONS]


def test_the_book_for_the_e_reader_holds_the_practice_questions_only(tmp_path):
    _, out = letter(tmp_path, LINKED)
    state = state_of(out)
    draft = draft_document(state)
    assert [p["id"] for c in draft["checkpoints"] for p in c["prompts"]] == [REACTIONS, "c0002-p1"]
    opened = reading.open_reading_book(out / "The Forged Letter.prepared.epub")
    assert [p["id"] for c in opened.data["checkpoints"] for p in c["prompts"]] == [REACTIONS, "c0002-p1"]
    assert "s0001-e1" not in json.dumps(opened.data)
    doc = json.loads((out / "The Forged Letter.questions.json").read_text(encoding="utf-8"))
    assert [q["id"] for q in doc["questions"]] == [REACTIONS, "c0002-p1"]          # so the deck has no ephemeral question
    assert doc["questions"][0]["reasoning"] == "explain" and len(doc["questions"][0]["accounts_for"]) == 3
    tsv = (out / "The Forged Letter.cards.tsv").read_text(encoding="utf-8")
    assert "A well-supported answer accounts for:" in tsv and "One such answer:" in tsv and "Who knows that the letter" not in tsv


def test_a_book_prepared_without_ephemeral_questions_is_as_it_was(tmp_path):
    report, backend, _ = run(tmp_path)
    state = state_of(tmp_path / "out")
    assert state["ephemeral"] == [] and not (tmp_path / "out" / pathway.FILE).exists()
    assert all(not any(k in q for k in pathway.EXTRAS) for q in state["questions"])
    assert report["counts"]["ephemeral"] == 0 and "ephemeral" not in (tmp_path / "out" / "fixture.report.md").read_text(encoding="utf-8").split("## How it was made")[0]
    plain, with_them = Settings(), Settings(ephemeral="attention")
    assert "graduated pathway" not in prompts.drafter_system(plain) + prompts.checker_system(plain)
    assert "checks" not in json.dumps(prompts.draft_schema(plain)) and "reasoning" not in json.dumps(prompts.revise_schema(plain))
    assert "## 12. The graduated pathway" in prompts.drafter_system(with_them) and "## The graduated pathway" in prompts.checker_system(with_them)
    assert "sustain their attention generally" in prompts.drafter_system(with_them)
    assert "build toward practice questions explicitly" in prompts.drafter_system(Settings(ephemeral="linked"))


def test_a_run_made_before_the_pathway_is_built_again_as_it_is(tmp_path):
    run(tmp_path)
    path = tmp_path / "out" / "work" / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["ephemeral"], state["settings"]["ephemeral"]      # as an earlier version wrote it
    path.write_text(json.dumps(state), encoding="utf-8")
    report = rebuild(tmp_path / "out")
    assert report["counts"]["questions"] == 8 and report["counts"]["ephemeral"] == 0
    assert review_data(tmp_path / "out")["ephemeral"] == "none"
    assert pathway.into_manifest({"sections": []}, pathway.load(tmp_path / "out"), {}) == {"sections": []}


# --------------------------------------------------------------------------
# The rules a draft is held to


def part(settings: Settings = LINKED):
    book = load_book(FIXTURE)
    sd = next(d for d in book.spine if d.prefix == "d02")
    refs = [b.ref for b in sd.blocks if b.kind == "paragraph" and b.words]
    return book, sd, refs


def section(sd, question: dict | None = None, checks: list[dict] | None = None) -> dict:
    refs = [b.ref for b in sd.blocks if b.kind == "paragraph" and b.words]
    return {"ranges": [{"kind": "section", "first_paragraph": sd.blocks[0].ref, "last_paragraph": sd.blocks[-1].ref, "label": "All of it",
                        "reason": "", "checks": checks or [],
                        "prompts": [{**EMPTY_PATHWAY, "key": "q1", "prompt": "Why does the keeper wind the clockwork at dusk?",
                                     "answer": "So that the lamp turns all night.", "supporting_paragraphs": [refs[-1]], **(question or {})}]}]}


def a_check(after: str, cite: str | None = None, supports: list[str] | None = None) -> dict:
    return {"prompt": "Who keeps the light?", "answer": "The keeper.", "supporting_paragraphs": [cite or after],
            "after_paragraph": after, "supports": ["q1"] if supports is None else supports}


def problems(reply: dict, sd, settings: Settings = LINKED, context: set[str] | None = None) -> list[str]:
    _, structural, flaws = check_part(reply, [(sd, sd.blocks)], False, settings, context)
    assert not structural, structural
    return [message for _, _, message in flaws]


def test_a_check_is_placed_after_the_material_it_needs():
    _, sd, refs = part()
    assert problems(section(sd, checks=[a_check(refs[1])]), sd) == []
    (late,) = problems(section(sd, checks=[a_check(refs[1], cite=refs[3])]), sd)
    assert f"supporting paragraph {refs[3]} comes after {refs[1]}, where the question is asked" in late
    (last,) = problems(section(sd, checks=[a_check(refs[-1])]), sd)
    assert "is the section's last paragraph, where the checkpoint is" in last
    heading = next(b.ref for b in sd.blocks if b.kind == "heading")
    assert any("is a heading or has no words" in p for p in problems(section(sd, checks=[a_check(heading, cite=refs[0])]), sd))
    assert any("is not a paragraph of the question's section" in p for p in problems(section(sd, checks=[a_check("d09-p001", cite=refs[0])]), sd))
    long = {**a_check(refs[1]), "prompt": "Who " + "really " * 40 + "keeps the light?"}
    assert any("an ephemeral question is brief" in p for p in problems(section(sd, checks=[long]), sd))


def test_links_are_asked_for_only_where_the_reader_chose_them():
    _, sd, refs = part()
    (unlinked,) = problems(section(sd, checks=[a_check(refs[1], supports=[])]), sd)
    assert "supports is empty" in unlinked
    (unknown,) = [p for p in problems(section(sd, checks=[a_check(refs[1], supports=["q7"])]), sd) if "q7" in p]
    assert "is the key of no practice question in your reply" in unknown
    ranges, _, flaws = check_part(section(sd, checks=[a_check(refs[1], supports=["q1"])]), [(sd, sd.blocks)], False, ATTENTION)
    assert ranges[0]["checks"][0]["supports"] == [] and not [f for f in flaws if f[1] is not None]   # general attention: no link is kept
    ranges, _, _ = check_part(section(sd, checks=[a_check(refs[1])]), [(sd, sd.blocks)], False, LINKED)
    assert ranges[0]["checks"][0]["supports"] == [ranges[0]["prompts"][0]["key"]]


def test_a_question_may_draw_on_earlier_text_only_where_it_was_shown_and_is_cited():
    _, sd, refs = part()
    higher = {"reasoning": "synthesize", "earlier_paragraphs": ["d01-p002"], "cue_prompt": "Who keeps the light?", "cue_answer": "The keeper."}
    assert problems(section(sd, higher), sd, context={"d01-p002"}) == []
    (unseen,) = problems(section(sd, higher), sd, context=set())
    assert "earlier_paragraphs names d01-p002, which is not text you were shown before the question's section" in unseen
    recall = {**higher, "reasoning": "", "cue_prompt": "", "cue_answer": ""}
    assert any("is a higher-order question" in p for p in problems(section(sd, recall), sd, context={"d01-p002"}))
    # its own section is cited too, as the book for the e-reader needs it
    assert any("supporting_paragraphs is empty" in p for p in problems(section(sd, {**higher, "supporting_paragraphs": []}), sd, context={"d01-p002"}))
    # a higher-order question without anything for "Help me recall" is said, and is no reason to leave it out
    _, _, flaws = check_part(section(sd, {"reasoning": "explain"}), [(sd, sd.blocks)], False, ATTENTION)
    assert [(k, "Help me recall" in m) for _, k, m in flaws] == [(None, True)]
    assert any("give at least two pieces of evidence" in p for p in problems(section(sd, {**higher, "accounts_for": ["One thing."]}), sd, context={"d01-p002"}))


def test_a_checkpoint_opens_with_its_higher_order_question_and_help_is_there_for_it(tmp_path):
    def hook(reply: dict, prompt: str) -> dict:
        for r in reply["ranges"]:
            if r["kind"] == "section" and r["prompts"][0]["reasoning"]:
                first = r["prompts"][0]
                r["prompts"].insert(0, {**EMPTY_PATHWAY, "key": first["key"] + "b", "prompt": "And in a word: " + first["prompt"],
                                        "answer": "Briefly: " + first["answer"], "supporting_paragraphs": first["supporting_paragraphs"]})
        return reply
    run(tmp_path, Script(draft_hook=hook), ATTENTION)
    state = state_of(tmp_path / "out")
    first = [q for q in state["questions"] if q["id"].endswith("-p1")]
    assert all(q["reasoning"] == "explain" and q["cue"] for q in first)              # the recall question drafted first comes second
    assert all(not q["reasoning"] for q in state["questions"] if q["id"].endswith("-p2"))
    made = pathway_of(tmp_path / "out")
    assert all(made["questions"][q["id"]]["cue"] for q in first)


# --------------------------------------------------------------------------
# What the checker is shown, and what it may cite


def test_the_checker_reads_each_question_by_its_role_and_against_every_passage_it_uses(tmp_path):
    _, backend, _ = run(tmp_path, Script(), LINKED)
    state = state_of(tmp_path / "out")
    crossing = next(q for q in state["questions"] if q["earlier_paragraphs"])       # draws on the section before its own
    assert crossing["status"] == "passed" and crossing["supporting_paragraphs"] and crossing["earlier_paragraphs"][0] not in crossing["supporting_paragraphs"]
    message = next(c["prompt"] for c in backend.calls if c["role"] == "check" and f"QUESTION [{crossing['id']}]" in c["prompt"])
    mine = message.split(f"QUESTION [{crossing['id']}]", 1)[1].split("QUESTION [", 1)[0]
    assert "Role: practice question, higher-order (explain a mechanism)" in mine
    assert f"It also draws on these earlier paragraphs, given under EARLIER PARAGRAPHS: {crossing['earlier_paragraphs'][0]}" in mine
    assert f"EARLIER PARAGRAPHS (from before this section; the reader has read them)\n[{crossing['earlier_paragraphs'][0]}]" in message
    check = next(c for c in state["ephemeral"] if c["section"] == crossing["section"])
    theirs = message.split(f"QUESTION [{check['id']}]", 1)[1].split("QUESTION [", 1)[0]
    assert f"Role: ephemeral question, asked inside the section right after [{check['after']}]" in theirs
    assert f"It supports the practice question [{crossing['id']}]" in theirs
    # the passage of the card holds the earlier paragraph too, first, marked as such
    doc = json.loads((tmp_path / "out" / "fixture.questions.json").read_text(encoding="utf-8"))
    card = next(q for q in doc["questions"] if q["id"] == crossing["id"])
    assert [(p["ref"], p.get("earlier", False)) for p in card["passage"]] == [(crossing["earlier_paragraphs"][0], True), (crossing["supporting_paragraphs"][0], False)]


def verdict(qid: str, citations: list[str]) -> dict:
    return {"prompts": [{"id": qid, **{c: True for c in prompts.CRITERIA}, "citations": citations, "evidence": "", "problem": "",
                         "suggested_fix": "", "minor_note": ""}], "sections": []}


def test_a_citation_is_held_to_the_passages_the_question_may_use(tmp_path):
    _, out = letter(tmp_path, LINKED)
    book, state = open_run(out)
    from ebook_processor.pipeline import Run
    engine = Run(out / "work" / "source.epub", out / "work", LINKED, messenger.script().backend())
    engine.state = state
    item = engine.section_item(state["sections"][0])
    check, question = state["ephemeral"][0], state["questions"][0]
    refs = [b.ref for b in item["blocks"]]
    seen = engine.inside(item, check)
    assert seen == set(refs[: refs.index(check["after"]) + 1])                       # only what was read when it is asked
    later = refs[refs.index(check["after"]) + 1]
    _, _, wrong = read_check(verdict(check["id"], [later]), {check["id"]: (check, seen)}, {})
    assert "is not a paragraph of the question's own section" in wrong[0]
    usable, _, wrong = read_check(verdict(check["id"], [check["after"]]), {check["id"]: (check, seen)}, {})
    assert not wrong and usable[check["id"]]["citations"] == [check["after"]]
    question = {**question, "earlier_paragraphs": ["d00-p009"]}
    assert engine.inside(item, question) == set(refs) | {"d00-p009"}                 # a check may cite an earlier paragraph it draws on
    assert engine.inside(item, question, citing=False) == set(refs)                  # a revision's own passage stays in the section


def test_a_failed_ephemeral_question_is_revised_in_its_place_or_left_out(tmp_path):
    _, _, script = run(tmp_path, Script(fail={"s0001-e1": 1, "s0003-e1": 9}), ATTENTION)
    state = state_of(tmp_path / "out")
    revised = next(c for c in state["ephemeral"] if c["id"] == "s0001-e1")
    assert revised["status"] == "passed" and len(revised["history"]) == 1 and revised["after"] and revised["purpose"] == "attention"
    gone = next(c for c in state["ephemeral"] if c["id"] == "s0003-e1")
    assert gone["status"] == "failed" and not kept(gone)
    assert "s0003-e1" not in [c["id"] for c in pathway_of(tmp_path / "out")["ephemeral"]]
    review = review_data(tmp_path / "out")
    row = next(c for s in review["sections"] for c in s["ephemeral"] if c["id"] == "s0003-e1")
    assert not row["kept"] and row["failed_criteria"] == ["supported_by_section"]


def test_a_chapter_that_was_tried_is_taken_over_with_its_ephemeral_questions_and_links(tmp_path):
    epub = tmp_path / "The Forged Letter.epub"
    epub.write_bytes(messenger.book())
    script = messenger.script()
    chapter = next(sd.index for sd in load_book(epub).spine if sd.blocks)
    trial = try_chapter(epub, tmp_path / "out", LINKED, chapter, backend=script.backend())
    assert trial["ephemeral"] == "linked" and trial["sections"][0]["ephemeral"][0]["supports"] == [REACTIONS]
    drafts = script.drafts
    run_book(epub, tmp_path / "out", LINKED, backend=script.backend())
    state = state_of(tmp_path / "out")
    assert script.drafts == drafts                                                   # nothing was drafted again
    assert [(c["id"], c["supports"], c["status"]) for c in state["ephemeral"]] == [("s0001-e1", [REACTIONS], "passed")]
    assert state["questions"][0]["reasoning"] == "explain" and len(state["questions"][0]["accounts_for"]) == 3


# --------------------------------------------------------------------------
# The reader's record, and Practice


def test_what_the_reader_does_with_an_ephemeral_question_is_kept_and_is_never_a_grade():
    known, checks = {"c0001-p1"}, {"s0001-e1", "s0001-e2"}
    raw = {"x_checks": {"s0001-e1": {"at": "2026-11-23T10:00:00", "outcome": "done", "revisits": 2, "right": True, "grade": "correct"},
                        "s0001-e2": {"at": "2026-11-23T10:01:00", "outcome": "correct"},     # no such outcome
                        "s0009-e1": {"at": "2026-11-23T10:02:00", "outcome": "skipped"},     # no such question
                        "c0001-p1": {"at": "2026-11-23T10:03:00", "outcome": "done"}},       # a practice question is answered, not met
           "x_helped": {"c0001-p1": "2026-11-23T10:04:00", "s0001-e1": "2026-11-23T10:04:00"},
           "x_attempts": [{"id": "s0001-e1", "at": "2026-11-23T10:00:00", "grade": "correct"}],
           "grades": {"s0001-e1": {"first": "correct", "at": "2026-11-23T10:00:00"}}}
    state = reading.clean_state(raw, "sha", known, checks)
    assert state["x_checks"] == {"s0001-e1": {"at": "2026-11-23T10:00:00", "outcome": "done", "right": True, "revisits": 2}}
    assert state["x_helped"] == {"c0001-p1": "2026-11-23T10:04:00"}
    assert state["grades"] == {} and state["x_attempts"] == []                      # an ephemeral question takes no grade and no answer
    assert reading.clean_state(raw, "sha", known)["x_checks"] == {}                 # a book without ephemeral questions
    # two pages of the same book: what either saved stays, and of two records of one question the later wins
    old = {**reading.new_state("sha"), "x_checks": {"s0001-e1": {"at": "2026-11-23T09:00:00", "outcome": "skipped"},
                                                    "s0001-e2": {"at": "2026-11-23T09:30:00", "outcome": "done"}},
           "x_helped": {"c0001-p1": "2026-11-23T09:00:00"}}
    merged = reading.merge_state(old, state, known)
    assert merged["x_checks"]["s0001-e1"]["outcome"] == "done" and merged["x_checks"]["s0001-e2"]["outcome"] == "done"
    assert merged["x_helped"] == {"c0001-p1": "2026-11-23T09:00:00"}
    # and no card comes of it
    store = {"version": 1, "taken": {}, "cards": {}}
    srs.take_in(store, merged, srs.clean_settings(None), dt.date(2026, 11, 23))
    assert store["cards"] == {}


def test_the_app_gives_the_reader_the_pathway_and_practice_only_the_practice_questions(make_web_app):
    app = make_web_app(model=lambda s: messenger.script().backend(), today=dt.date(2026, 11, 23))
    job = app.wait(app.add_fixture(name="The Forged Letter.epub", book=messenger.book(), density="short", ephemeral="linked"))
    assert job["status"] == "done" and job["settings"]["ephemeral"] == "linked"
    jid = job["id"]
    book = app.call("GET", f"/api/jobs/{jid}/book").data
    assert book["ephemeral"] == "linked"
    first, second = book["sections"]
    (check,) = first["checks"]
    assert check["id"] == "s0001-e1" and check["supports"] == [REACTIONS] and first["start"] <= check["from"] < check["at"] < first["end"]
    assert first["prompts"][0]["help"] == ["s0001-e1"] and first["prompts"][0]["reasoning"] == "explain" and second["checks"] == []
    # the reader skips the check, answers the checkpoint's question with help, and reads on
    sent = {"position": {"chapter": first["chapter"], "word": first["end"], "mode": "page"}, "x_furthest": {"chapter": first["chapter"], "word": first["end"]},
            "x_checks": {"s0001-e1": {"at": "2026-11-23T10:00:00", "outcome": "skipped"}},
            "x_helped": {REACTIONS: "2026-11-23T10:05:00"},
            "grades": {REACTIONS: {"first": "correct", "at": "2026-11-23T10:06:00"}},
            "x_attempts": [{"id": REACTIONS, "at": "2026-11-23T10:06:00", "grade": "correct", "text": "", "where": "read"},
                           {"id": "s0001-e1", "at": "2026-11-23T10:00:00", "grade": "correct", "text": "", "where": "read"}]}
    assert app.call("PUT", f"/api/jobs/{jid}/reading", json=sent).status == 200
    stored = app.call("GET", f"/api/jobs/{jid}/reading").data
    assert stored["x_checks"] == sent["x_checks"] and stored["x_helped"] == sent["x_helped"]
    assert [a["id"] for a in stored["x_attempts"]] == [REACTIONS]
    practice = app.call("GET", f"/api/practice?job={jid}").data
    assert practice["counts"]["cards"] == 1 and practice["card"]["id"] == REACTIONS   # the same question, now a card
    assert len(practice["card"]["accounts_for"]) == 3
    assert list(app.jobs.cards(jid)["cards"]) == [REACTIONS]
    # promoted, the ephemeral question is a card at once, and the checkpoint is as it was
    seen = app.call("GET", f"/api/jobs/{jid}/ephemeral/s0001-e1").data
    assert seen["question"] == check["prompt"] and seen["promoted"] is None
    made = app.call("POST", f"/api/jobs/{jid}/ephemeral/s0001-e1/promote", json={}).data
    assert made["id"] == "c0001-p2" and made["question"] == check["prompt"] and made["promoted_from"] == "s0001-e1" and made["added"]
    cards = app.jobs.cards(jid)["cards"]
    assert cards["c0001-p2"]["state"] == "new" and cards[REACTIONS]["state"] == "learned" and cards[REACTIONS]["reviews"][0]["kind"] == "checkpoint"
    book = app.call("GET", f"/api/jobs/{jid}/book").data
    assert [(p["id"], p.get("promoted", False)) for p in book["sections"][0]["prompts"]] == [(REACTIONS, False), ("c0001-p2", True)]
    assert book["sections"][0]["checks"][0]["promoted"] == "c0001-p2"
    assert app.call("GET", f"/api/practice?job={jid}").data["counts"]["cards"] == 2
    assert app.call("POST", f"/api/jobs/{jid}/ephemeral/s0001-e1/promote", json={}).status == 400     # once
    # the reader's place and grade are as they were
    assert app.call("GET", f"/api/jobs/{jid}/reading").data["grades"][REACTIONS]["first"] == "correct"
    # taken back, it is ephemeral again, and its card goes with it
    assert app.call("DELETE", f"/api/jobs/{jid}/book-questions/c0001-p2").status == 200
    assert app.call("GET", f"/api/jobs/{jid}/book").data["sections"][0]["checks"][0]["promoted"] is None
    assert list(app.jobs.cards(jid)["cards"]) == [REACTIONS]
    assert app.call("GET", f"/api/jobs/{jid}/ephemeral/no-such").status == 400


def test_an_ephemeral_question_is_promoted_as_written_changed_or_rewritten_to_a_wish(tmp_path):
    _, out = letter(tmp_path, ATTENTION)
    road, forgery, gate = (c["id"] for c in state_of(out)["ephemeral"])
    kept_as_is = change_run(out, lambda book, state: dict(editing.promote(state, book, road)))
    assert kept_as_is["prompt"] == "Which road does Tomas take to the council, and why?" and kept_as_is["status"] == "edited"
    assert kept_as_is["promoted_from"] == road and kept_as_is["ask"] == "practice" and kept_as_is["id"] == "c0001-p2"
    changed = change_run(out, lambda book, state: dict(editing.promote(state, book, forgery, {"question": "Which two people know that the duke's letter is a forgery?"})))
    assert changed["prompt"] == "Which two people know that the duke's letter is a forgery?" and changed["answer"].startswith("Only the steward Maren")
    with pytest.raises(editing.EditError, match="already kept for Practice"):
        change_run(out, lambda book, state: editing.promote(state, book, road))
    with pytest.raises(editing.EditError, match="The question is empty"):
        change_run(out, lambda book, state: editing.promote(state, book, gate, {"question": " "}))
    # steered: the model is told that the question is now kept for practice, and the reader's wish
    script = messenger.script()
    backend = script.backend()
    book, state = open_run(out)
    made = editing.rewrite(backend, book, state, gate, "the lie told at the gate", ATTENTION)
    assert made["answer"].endswith("(as asked: the lie told at the gate)") and made["supporting_paragraphs"]
    task = backend.calls[-1]
    assert editing.FOR_PRACTICE in task["prompt"] and "Role: ephemeral question" not in task["prompt"]
    assert "graduated pathway" not in task["system"]                                # it is written by the rules of a practice question
    kept_rewritten = change_run(out, lambda book, state: dict(editing.promote(state, book, gate, made)))
    assert kept_rewritten["answer"] == made["answer"]
    state = state_of(out)
    assert [c["promoted"] for c in state["ephemeral"]] == ["c0001-p2", "c0001-p3", "c0001-p4"]
    assert [c["purpose"] for c in state["ephemeral"]] == ["attention"] * 3            # each stays what it was for the reader
    made_of = pathway_of(out)
    assert {q: e["promoted_from"] for q, e in made_of["questions"].items() if e.get("promoted_from")} == {"c0001-p2": road, "c0001-p3": forgery, "c0001-p4": gate}
    # a promoted question is a question of the book: in its files, and taken out or put back like any other
    doc = json.loads((out / "The Forged Letter.questions.json").read_text(encoding="utf-8"))
    assert [q["id"] for q in doc["questions"]] == [REACTIONS, "c0001-p2", "c0001-p3", "c0001-p4", "c0002-p1"]
    rebuild(out, remove=["c0001-p2"])
    assert pathway_of(out)["ephemeral"][0]["promoted"] is None


def test_a_question_with_several_defensible_answers_is_edited_with_its_evidence(tmp_path):
    _, out = letter(tmp_path, ATTENTION)
    shown = change_run(out, lambda book, state: editing.view(book, state, REACTIONS), build=False)
    assert len(shown["accounts_for"]) == 3 and shown["reasoning"] == "explain" and shown["cue"]["prompt"].startswith("Who knows")
    evidence = ["The seal arrived unbroken.", "The wax was bright and fresh."]
    change_run(out, lambda book, state: editing.edit(state, book, REACTIONS, {"accounts_for": evidence}))
    state = state_of(out)
    assert state["questions"][0]["accounts_for"] == evidence and state["questions"][0]["status"] == "edited"
    assert len(state["questions"][0]["original"]["accounts_for"]) == 3
    assert pathway_of(out)["questions"][REACTIONS]["accounts_for"] == evidence
    with pytest.raises(editing.EditError, match="give at least two pieces of evidence"):
        change_run(out, lambda book, state: editing.edit(state, book, REACTIONS, {"accounts_for": ["One."]}))
    change_run(out, lambda book, state: editing.restore(state, REACTIONS))
    assert len(state_of(out)["questions"][0]["accounts_for"]) == 3


def test_the_command_line_takes_the_choice(tmp_path, capsys):
    from ebook_processor.cli import make_parser, settings_from
    args = make_parser().parse_args(["process", "book.epub", "--ephemeral", "linked"])
    assert settings_from(args).ephemeral == "linked" and settings_from(args).pathway
    assert settings_from(make_parser().parse_args(["process", "book.epub"])).ephemeral == "none"
    with pytest.raises(SystemExit):
        make_parser().parse_args(["process", "book.epub", "--ephemeral", "often"])
    capsys.readouterr()


# --------------------------------------------------------------------------
# What a review of this work found (November 2026)


def test_a_check_stands_after_a_paragraph_of_text_never_after_a_table_or_a_picture():
    book = load_book(FIXTURE)
    sd = next(d for d in book.spine if any(b.placeholder is not None and b.words for b in d.blocks))
    table = next(b for b in sd.blocks if b.placeholder is not None and b.words)
    before = next(b.ref for b in sd.blocks if b.kind == "paragraph" and b.words)
    reply = section(sd, checks=[a_check(table.ref, cite=before)])
    assert any("is a table, a listing, a formula or a picture" in p for p in problems(reply, sd))


def test_the_passage_of_a_check_begins_at_the_earliest_paragraph_it_rests_on():
    blocks = {"d01-p003": [1, 30, 60], "d01-p006": [1, 120, 150]}
    doc = {"format": pathway.FORMAT, "version": pathway.VERSION, "mode": "attention", "questions": {},
           "ephemeral": [{"id": "s0001-e1", "section": "s0001", "after": "d01-p006", "purpose": "attention", "prompt": "Q?", "answer": "A.",
                          "supporting_paragraphs": ["d01-p006", "d01-p003"], "supports": [], "promoted": None}]}
    manifest = {"sections": [{"id": "s0001", "chapter": 1, "start": 0, "end": 400, "prompts": []}]}
    (check,) = pathway.into_manifest(manifest, doc, blocks)["sections"][0]["checks"]
    assert (check["from"], check["at"]) == (30, 150)


def test_over_the_limit_a_recall_question_goes_before_a_higher_order_one():
    _, sd, refs = part()
    reply = section(sd, {"reasoning": "explain", "cue_prompt": "Who keeps the light?", "cue_answer": "The keeper."})
    reply["ranges"][0]["prompts"].insert(0, {**EMPTY_PATHWAY, "key": "q0", "prompt": "Who winds the clockwork?", "answer": "The keeper.",
                                             "supporting_paragraphs": [refs[0]]})
    one = from_options(density="short", ephemeral="attention", max_questions=1)
    _, _, flaws = check_part(reply, [(sd, sd.blocks)], False, one)
    assert [k for _, k, m in flaws if "at most 1 are allowed" in m] == [0]         # the recall question, though it was written first


def test_a_check_with_a_problem_of_its_own_is_no_help_for_the_question_it_supports():
    _, sd, refs = part()
    reply = section(sd, {"reasoning": "explain"}, checks=[a_check(refs[1], cite=refs[3])])   # it cites what the reader has not read
    _, _, flaws = check_part(reply, [(sd, sd.blocks)], False, LINKED)
    assert any(k is None and "Help me recall" in m for _, k, m in flaws)
    reply = section(sd, {"reasoning": "explain"}, checks=[a_check(refs[1])])
    assert check_part(reply, [(sd, sd.blocks)], False, LINKED)[2] == []


def test_a_promoted_question_that_was_taken_out_does_not_stand_in_the_way(tmp_path):
    _, out = letter(tmp_path, ATTENTION)
    road = state_of(out)["ephemeral"][0]["id"]
    change_run(out, lambda book, state: editing.promote(state, book, road) and None)
    rebuild(out, remove=["c0001-p2"])
    again = change_run(out, lambda book, state: dict(editing.promote(state, book, road)))
    assert again["id"] == "c0001-p3" and pathway_of(out)["ephemeral"][0]["promoted"] == "c0001-p3"


def test_the_notebook_opens_the_passage_of_an_answer_in_its_own_section(make_web_app):
    app = make_web_app()
    job = app.process_fixture(density="short", ephemeral="linked")
    doc = json.loads((app.data / job["id"] / "fixture.questions.json").read_text(encoding="utf-8"))
    crossing = next(q for q in doc["questions"] if q.get("earlier_paragraphs"))
    assert crossing["passage"][0]["earlier"]
    book = app.jobs.book(job["id"])
    own = book.blocks[crossing["supporting_paragraphs"][0]]
    assert app.jobs.sources(job["id"])[crossing["id"]] == {"chapter": own[0], "word": own[1]}


# --------------------------------------------------------------------------
# The forms of each role: practice questions in some, ephemeral questions in others

SPLIT = from_options(density="short", ephemeral="attention", forms="open", ephemeral_forms="choice,truefalse")
GRADED_CHECKS = from_options(density="short", ephemeral="attention", forms="open", ephemeral_forms="choice")


def test_open_practice_questions_and_graded_ephemeral_ones_are_drafted_checked_and_revised_each_in_their_own_forms(tmp_path):
    _, backend, script = run(tmp_path, Script(fail={"s0001-e1": 1, "c0001-p1": 1}), SPLIT)
    state = state_of(tmp_path / "out")
    assert [qforms.form_of(q) for q in state["questions"]] == ["open"] * len(state["questions"])
    assert {qforms.form_of(c) for c in state["ephemeral"]} == {"choice", "truefalse"} and all(c["status"] == "passed" for c in state["ephemeral"])
    # each failed question is revised in the forms of its own role: the ephemeral one is not made an open question
    revised = next(c for c in state["ephemeral"] if c["id"] == "s0001-e1")
    assert len(revised["history"]) == 1 and qforms.form_of(revised) == "choice" and revised["after"]
    practice = next(q for q in state["questions"] if q["id"] == "c0001-p1")
    assert len(practice["history"]) == 1 and qforms.form_of(practice) == "open"
    # the drafter is told the forms of each role, and its reply has the members of a form only where a role needs them
    schema = prompts.draft_schema(SPLIT)["properties"]["ranges"]["items"]["properties"]
    assert "form" not in schema["prompts"]["items"]["properties"]
    assert schema["checks"]["items"]["properties"]["form"]["enum"] == ["choice", "truefalse"]
    system = next(c["system"] for c in backend.calls if c["role"] == "draft")
    assert "Practice questions are open questions and have no `form`." in system
    assert "Ephemeral questions may take these forms: **multiple choice, true or false**." in system
    assert "**Which form for an ephemeral question.** Open questions are not allowed for this role" in system
    assert system.count("- **Multiple choice** (`\"form\": \"choice\"`)") == 1             # each form's guide once
    assert "The forms of ephemeral questions, quick checks in the middle of reading, are **multiple choice, true or false**." in system
    assert "An ephemeral question of a form the app grades is answered on the spot" in system
    # the checker judges whether a graded ephemeral question has one right answer, though the practice questions are open
    check = next(c for c in backend.calls if c["role"] == "check")
    assert "There is a sixth criterion, **`one_right_answer`**." in check["system"] and prompts.criteria(SPLIT)[-1] == prompts.ONE_RIGHT
    assert prompts.ONE_RIGHT in prompts.check_schema(SPLIT)["properties"]["prompts"]["items"]["properties"]
    revise = next(c for c in backend.calls if c["role"] == "revise")
    assert "A practice question takes one of these forms: open question; an ephemeral question one of these: multiple choice, true or false." in revise["prompt"]
    assert prompts.revise_schema(SPLIT)["properties"]["revisions"]["items"]["properties"]["form"]["enum"] == ["open", "choice", "truefalse"]


def test_a_question_in_a_form_its_role_does_not_allow_is_sent_back_with_the_forms_it_may_take():
    _, sd, refs = part()
    settings = from_options(density="short", ephemeral="linked", forms="open", ephemeral_forms="choice")
    choice = {**EMPTY_FORM, "form": "choice", "answer": "The keeper keeps the light.", "correct_choice": "The keeper",
              "wrong_choices": ["The mayor", "The baker", "The priest"]}
    assert problems(section(sd, checks=[{**a_check(refs[1]), **choice}]), sd, settings) == []
    (open_check,) = problems(section(sd, checks=[a_check(refs[1])]), sd, settings)
    assert open_check.endswith("ephemeral question 1: its form is open question, which is not allowed here; the allowed forms are multiple choice")
    (choice_question,) = problems(section(sd, {**choice, "prompt": "Who winds the clockwork at dusk?"}, checks=[{**a_check(refs[1]), **choice}]), sd, settings)
    assert choice_question.endswith("question 1: its form is multiple choice, which is not allowed here; the allowed forms are open question")
    # a revision too is held to the forms of its question's role
    inside = {b.ref for b in sd.blocks}
    check = {**a_check(refs[1]), **choice, "after": refs[1], "supporting_paragraphs": [refs[1]]}
    question = {"prompt": "Why does the keeper wind the clockwork at dusk?", "answer": "So that the lamp turns all night.",
                "supporting_paragraphs": [refs[-1]], **{k: v for k, v in EMPTY_PATHWAY.items() if k not in ("cue_prompt", "cue_answer")}}
    wanted = {"s0001-e1": (check, inside), "c0001-p1": (question, inside)}
    as_open = {"id": "s0001-e1", "action": "revise", "prompt": "Who keeps the light at night?", "answer": "The keeper.", "supporting_paragraphs": [refs[1]]}
    as_choice = {**as_open, **choice, "id": "c0001-p1", "prompt": "Who winds the clockwork?", "supporting_paragraphs": [refs[-1]]}
    good, said = read_revisions({"revisions": [as_open, as_choice]}, wanted, settings.forms, True, settings.ephemeral_forms)
    assert good == {} and said == ["s0001-e1: its form is open question, which is not allowed here; the allowed forms are multiple choice",
                                   "c0001-p1: its form is multiple choice, which is not allowed here; the allowed forms are open question"]
    good, said = read_revisions({"revisions": [{**as_open, **choice, "prompt": "Who keeps the light at night?"},
                                               {**as_choice, **{k: "" if isinstance(v, str) else v for k, v in EMPTY_FORM.items()}, "form": "open"}]},
                                wanted, settings.forms, True, settings.ephemeral_forms)
    assert said == [] and qforms.form_of(good["s0001-e1"]) == "choice" and qforms.form_of(good["c0001-p1"]) == "open"


def test_without_ephemeral_questions_their_forms_change_nothing_the_models_are_given():
    for forms in [("open",), ("open", "choice"), ("choice", "truefalse", "cloze")]:
        plain, other = Settings(forms=forms), Settings(forms=forms, ephemeral_forms=("cloze",))
        for make in (prompts.drafter_system, prompts.checker_system, prompts.draft_schema, prompts.revise_schema, prompts.check_schema):
            assert make(plain) == make(other), (forms, make.__name__)
        assert "ephemeral" not in prompts.drafter_system(plain).lower()
    assert prompts.draft_schema(Settings()) is prompts.DRAFT_SCHEMA and prompts.check_schema(Settings()) is prompts.CHECK_SCHEMA
    assert prompts.revise_schema(Settings()) is prompts.REVISE_SCHEMA and prompts.criteria(Settings(ephemeral_forms=("choice",))) == prompts.CRITERIA


def test_with_the_same_forms_for_both_roles_every_question_is_told_the_same_forms():
    same = from_options(density="short", ephemeral="attention", forms="open,cloze", ephemeral_forms="open,cloze")
    schema = prompts.draft_schema(same)["properties"]["ranges"]["items"]["properties"]
    assert schema["prompts"]["items"]["properties"]["form"]["enum"] == schema["checks"]["items"]["properties"]["form"]["enum"] == ["open", "cloze"]
    system = prompts.drafter_system(same)
    assert "The reader has allowed these forms: **open question, fill in the blank**. Every question says which it is, in `form`." in system
    assert "In this book the reader allowed the same forms for both roles: **open question, fill in the blank**." in system
    assert "Which form for" not in system and "separately" not in system


def test_a_run_saved_before_the_two_roles_had_forms_of_their_own_resumes_with_the_same_forms(tmp_path):
    both = from_options(density="short", ephemeral="attention", forms="open,choice", ephemeral_forms="open,choice")
    run(tmp_path, Script(), both)
    path = tmp_path / "out" / "work" / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["settings"]["ephemeral_forms"]                    # as an earlier version wrote it
    path.write_text(json.dumps(state), encoding="utf-8")
    assert Settings.from_json(state["settings"]).ephemeral_forms == ("open", "choice")
    report, backend, _ = run(tmp_path, Script(), both)          # continued: nothing is drafted again
    assert report["counts"]["ephemeral"] == len(state["ephemeral"]) and not [c for c in backend.calls if c["role"] == "draft"]
    with pytest.raises(PipelineError, match=r"ephemeral_forms: \['open', 'choice'\] then, \['open'\] now"):
        run(tmp_path, Script(), from_options(density="short", ephemeral="attention", forms="open,choice"))
    with pytest.raises(PipelineError, match=r"ephemeral: attention then, none now, ephemeral_forms: \['open', 'choice'\] then, not used now"):
        run(tmp_path, Script(), from_options(density="short", forms="open,choice"))


def test_the_command_line_takes_the_forms_of_the_ephemeral_questions():
    from ebook_processor.cli import make_parser, settings_from
    args = make_parser().parse_args(["process", "book.epub", "--ephemeral", "attention", "--forms", "open", "--ephemeral-forms", "truefalse,choice"])
    settings = settings_from(args)
    assert settings.forms == ("open",) and settings.ephemeral_forms == ("choice", "truefalse")
    assert settings_from(make_parser().parse_args(["process", "book.epub"])).ephemeral_forms == ("open",)


# --------------------------------------------------------------------------
# Promotion when the forms of the two roles differ


def test_a_graded_ephemeral_question_is_kept_in_its_own_kind_or_in_one_of_the_practice_kinds(tmp_path):
    _, out = letter(tmp_path, GRADED_CHECKS)
    road, forgery, gate = (c["id"] for c in state_of(out)["ephemeral"])
    shown = change_run(out, lambda book, state: editing.ephemeral_view(book, state, road), build=False)
    assert shown["form"] == "choice" and len(shown["choices"]) == 4
    assert shown["practice_forms"] == ["open"]                                     # so the window can tell the kinds differ
    # as it is: it keeps its kind, as before
    kept_as_is = change_run(out, lambda book, state: dict(editing.promote(state, book, road)))
    assert qforms.form_of(kept_as_is) == "choice" and kept_as_is["choices"] == state_of(out)["ephemeral"][0]["choices"]
    # in another kind, as the reader or the model wrote it
    open_one = {"question": "Who knows that the duke's letter is forged?", "answer": "Only Maren and Ilse.", "form": {"form": "open"}}
    made = change_run(out, lambda book, state: dict(editing.promote(state, book, forgery, open_one)))
    assert qforms.form_of(made) == "open" and "choices" not in made and made["answer"] == "Only Maren and Ilse."
    with pytest.raises(editing.EditError, match="A question keeps its kind"):   # a practice question's edit still keeps its kind
        change_run(out, lambda book, state: editing.edit(state, book, kept_as_is["id"], {"form": {"form": "open"}}))
    # the model rewrites it in a practice kind; no wish is needed for that
    backend = messenger.script().backend()
    book, state = open_run(out)
    converted = editing.rewrite(backend, book, state, gate, "", GRADED_CHECKS, convert=True)
    assert converted["form"] == {"form": "open"} and converted["question"] and converted["supporting_paragraphs"]
    task = backend.calls[-1]
    assert editing.CONVERT in task["prompt"] and editing.FOR_PRACTICE in task["prompt"] and "THE READER ASKS" not in task["prompt"]
    assert "it takes, in place of its own form, the form of an open question." in task["prompt"] and "Form: multiple choice" in task["prompt"]
    assert "graduated pathway" not in task["system"]
    kept = change_run(out, lambda book, state: dict(editing.promote(state, book, gate, converted)))
    assert qforms.form_of(kept) == "open" and kept["promoted_from"] == gate
    # what cannot be converted, and what needs a wish
    with pytest.raises(editing.EditError, match="Only a question asked while reading"):
        editing.rewrite(backend, book, state, REACTIONS, "", GRADED_CHECKS, convert=True)
    with pytest.raises(editing.EditError, match="Say what should be different"):
        editing.rewrite(backend, book, state, gate, "", GRADED_CHECKS)
    with pytest.raises(editing.EditError, match="already of a kind the book's practice questions take"):
        editing.rewrite(backend, book, state, gate, "", GRADED_CHECKS, convert=True, draft=converted)
    # a practice question of such a book is rewritten in its own form, and the model is told of no other
    rewritten = editing.rewrite(backend, book, state, REACTIONS, "make it shorter", GRADED_CHECKS)
    assert rewritten["form"] == {"form": "open"} and "Forms of question" not in backend.calls[-1]["system"]


def test_a_conversion_the_model_cannot_make_says_so_and_keeps_nothing(tmp_path):
    _, out = letter(tmp_path, GRADED_CHECKS)
    road = state_of(out)["ephemeral"][0]["id"]
    script = messenger.script()
    script.withdraw = {road}
    book, state = open_run(out)
    with pytest.raises(BackendError, match="could not rewrite the question"):
        editing.rewrite(script.backend(), book, state, road, "", GRADED_CHECKS, convert=True)
    assert state_of(out)["ephemeral"][0]["promoted"] is None


def test_the_app_promotes_a_question_into_a_practice_kind_through_its_routes(make_web_app):
    app = make_web_app(model=lambda s: messenger.script().backend())
    job = app.wait(app.add_fixture(name="The Forged Letter.epub", book=messenger.book(), density="short", ephemeral="attention",
                                   forms="open", ephemeral_forms="choice"))
    assert job["status"] == "done" and job["settings"]["ephemeral_forms"] == ["choice"] and job["settings"]["forms"] == ["open"]
    jid = job["id"]
    seen = app.call("GET", f"/api/jobs/{jid}/ephemeral/s0001-e1").data
    assert seen["form"] == "choice" and seen["practice_forms"] == ["open"]
    made = app.call("POST", f"/api/jobs/{jid}/ephemeral/s0001-e1/rewrite", json={"convert": True, "draft": {"question": seen["question"],
                    "answer": seen["answer"], "form": {k: seen[k] for k in ("form", "choices", "correct")}}}).data
    assert made["form"] == {"form": "open"}
    kept = app.call("POST", f"/api/jobs/{jid}/ephemeral/s0001-e1/promote", json=made).data
    assert kept["promoted_from"] == "s0001-e1" and "form" not in kept and kept["question"] == made["question"]
    assert app.call("POST", f"/api/jobs/{jid}/ephemeral/s0001-e2/rewrite", json={"wish": ""}).status == 400
