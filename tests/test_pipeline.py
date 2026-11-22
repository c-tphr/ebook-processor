"""The run from end to end, with a scripted stand-in for Claude (tests/scripted.py)."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import zipfile
from pathlib import Path

import pytest

from ebook_processor import cli, pipeline
from ebook_processor.llm import ScriptedBackend, UsageLimitError
from ebook_processor.prep.build import CHECKPOINTS_HREF
from ebook_processor.prep.epub import load_book
from ebook_processor.prep.validate import validate_files
from ebook_processor.runner import PipelineError, rebuild, review_data, run_book
from ebook_processor.settings import SettingsError, from_options
from tests import helpers
from tests.helpers import SHORT, run
from tests.paths import FIXTURE
from tests.prep.bookmaker import make_epub, xhtml
from tests.prep.prepkit import ok_epubcheck
from tests.scripted import Script

def state_of(tmp_path: Path) -> dict:
    return helpers.state_of(tmp_path / "out")


def long_book(tmp_path: Path, paragraphs: int = 400) -> Path:
    body = "".join(f"<p>Paragraph {n} of the long chapter has exactly these twelve words in it.</p>"
                   for n in range(1, paragraphs + 1))
    return make_epub(tmp_path / "long.epub", [("text/long.xhtml", xhtml("<h1>The Long Chapter</h1>" + body))])


# --------------------------------------------------------------------------
# A whole run


def test_a_run_writes_every_output_and_the_firmware_book_validates(tmp_path):
    report, backend, _ = run(tmp_path)
    out = tmp_path / "out"
    names = {p.name for p in out.iterdir()}
    assert names == {"fixture.prepared.epub", "fixture.checkpoints.epub", "fixture.questions.json",
                     "fixture.anki.apkg", "fixture.cards.tsv", "fixture.report.md", "fixture.report.json", "work"}
    assert report["complete"] and report["firmware_format_ok"]
    assert report["counts"]["questions"] == report["counts"]["passed"] == 8 and report["counts"]["left_out"] == 0
    assert {c["role"] for c in backend.calls} == {"draft", "check", "exclusions"}
    # the firmware-format book passes the engine's own validator, with the draft's exclusions
    outcome = validate_files(FIXTURE, out / "fixture.prepared.epub", out / "work" / "draft.json", epubcheck=ok_epubcheck)
    assert outcome.passed, [c.failures for c in outcome.checks.values() if c.failures]
    assert FIXTURE.read_bytes() == (out / "work" / "source.epub").read_bytes()


def test_the_original_is_never_changed(tmp_path):
    before = FIXTURE.read_bytes()
    run(tmp_path)
    assert FIXTURE.read_bytes() == before


def test_the_checker_never_sees_the_drafters_citations_or_other_calls(tmp_path):
    _, backend, _ = run(tmp_path)
    for call in backend.calls:
        if call["role"] == "check":
            assert "supporting_paragraphs" not in call["prompt"] and "Section at" in call["prompt"]
            assert "checker" in call["system"].lower() and "drafting instructions" not in call["system"]


# --------------------------------------------------------------------------
# Check, revise, leave out


def test_a_failed_question_is_revised_and_checked_again(tmp_path):
    report, backend, _ = run(tmp_path, Script(fail={"c0002-p1": 1}))
    q = next(q for q in state_of(tmp_path)["questions"] if q["id"] == "c0002-p1")
    assert q["status"] == "passed" and q["checks"] == 2 and len(q["history"]) == 1
    assert "revised after check 1" in q["answer"] and q["history"][0]["failed"] == ["supported_by_section"]
    assert report["counts"]["revised"] == 1 and report["counts"]["left_out"] == 0
    assert [c["role"] for c in backend.calls].count("revise") == 1
    revise = next(c for c in backend.calls if c["role"] == "revise")
    assert "QUESTION TO FIX [c0002-p1]" in revise["prompt"] and "The answer adds a detail." in revise["prompt"]
    # the second check judges only the revised question
    second = [c for c in backend.calls if c["role"] == "check"][-1]["prompt"]
    assert second.count("QUESTION [") == 1 and "JUDGE THE BREAK of s0002: no" in second


def test_a_question_that_fails_every_check_is_left_out_and_reported(tmp_path):
    report, _, script = run(tmp_path, Script(fail={"c0003-p1": 99}))
    assert script.checks_seen["c0003-p1"] == 3  # the default: three checks, then no more
    assert report["counts"]["left_out"] == report["counts"]["failed"] == 1 and report["counts"]["questions"] == 7
    left = report["left_out"][0]
    assert left["id"] == "c0003-p1" and left["failed_criteria"] == ["supported_by_section"]
    out = tmp_path / "out"
    assert "c0003-p1" not in (out / "fixture.cards.tsv").read_text(encoding="utf-8")
    with zipfile.ZipFile(out / "fixture.prepared.epub") as z:
        data = json.loads(z.read("OEBPS/" + CHECKPOINTS_HREF))
    assert "c0003" not in {c["id"] for c in data["checkpoints"]}  # its only question is gone, so is the checkpoint
    assert "s0003" in {s["id"] for s in data["sections"]}  # the section stays
    md = (out / "fixture.report.md").read_text(encoding="utf-8")
    assert "## Questions left out" in md and "c0003-p1" in md


def test_a_withdrawn_question_is_left_out(tmp_path):
    report, _, _ = run(tmp_path, Script(fail={"c0001-p1": 99}, withdraw={"c0001-p1"}))
    assert report["counts"]["withdrawn"] == 1 and report["left_out"][0]["status"] == "withdrawn"


def test_rounds_zero_keeps_every_question_unchecked(tmp_path):
    report, backend, _ = run(tmp_path, settings=from_options(density="short", rounds=0))
    assert {c["role"] for c in backend.calls} == {"draft"}
    assert report["counts"]["unchecked"] == report["counts"]["questions"] == 8
    assert "were not checked" in (tmp_path / "out" / "fixture.report.md").read_text(encoding="utf-8")


def test_one_round_checks_without_revising(tmp_path):
    report, backend, _ = run(tmp_path, Script(fail={"c0002-p1": 1}), from_options(density="short", rounds=1))
    assert "revise" not in {c["role"] for c in backend.calls} and report["counts"]["failed"] == 1


def test_an_unusable_check_result_keeps_the_question_as_unverified(tmp_path):
    script = Script()
    inner = script.check

    def check(prompt):
        reply = inner(prompt)
        for entry in reply["prompts"]:
            if entry["id"] == "c0001-p1":
                entry["citations"] = ["d04-p001"]  # outside the question's section, both times
        return reply

    script.check = check
    report, backend, _ = run(tmp_path, script)
    q = next(q for q in state_of(tmp_path)["questions"] if q["id"] == "c0001-p1")
    assert q["status"] == "unverified" and report["counts"]["unchecked"] == 1 and report["counts"]["questions"] == 8
    checks = [c for c in backend.calls if c["role"] == "check"]
    assert len(checks) == 2 and "is not a paragraph of the question's own section" in checks[1]["prompt"]


# --------------------------------------------------------------------------
# Drafts that need correcting


def test_a_draft_with_a_gap_is_sent_back_with_the_problem(tmp_path):
    seen = []

    def hook(reply, prompt):
        seen.append(prompt)
        if len(seen) == 1:
            del reply["ranges"][2]
        return reply

    report, _, _ = run(tmp_path, Script(draft_hook=hook))
    assert len(seen) == 2 and "PROBLEMS WITH YOUR EARLIER REPLY" in seen[1] and "must start at" in seen[1]
    assert report["counts"]["sections"] == 8 and not report["warnings"]


def test_bad_questions_are_dropped_when_the_drafter_does_not_correct_them(tmp_path):
    def hook(reply, prompt):
        section = next(r for r in reply["ranges"] if r["kind"] == "section")
        section["prompts"][0]["answer"] = "x" * 601
        return reply

    report, backend, _ = run(tmp_path, Script(draft_hook=hook))
    assert [c["role"] for c in backend.calls].count("draft") == pipeline.DRAFT_ATTEMPTS
    assert report["counts"]["questions"] == 7 and "the limit is 600" in report["warnings"][0]


def test_an_unusable_division_falls_back_to_sections_by_length(tmp_path):
    report, _, _ = run(tmp_path, Script(draft_hook=lambda reply, prompt: {"ranges": []}))
    assert report["counts"]["questions"] == 0 and report["counts"]["sections"] >= 4
    assert "cut into sections by length" in report["warnings"][0] and report["firmware_format_ok"]


def test_an_exclusion_must_name_its_kind(tmp_path):
    def hook(reply, prompt):
        if "PROBLEMS WITH" not in prompt:
            reply["ranges"][0]["reason"] = "Not interesting."
        return reply

    _, backend, _ = run(tmp_path, Script(draft_hook=hook))
    second = [c for c in backend.calls if c["role"] == "draft"][1]["prompt"]
    assert "must start with 'Front matter:'" in second


def test_a_rejected_exclusion_is_divided_into_sections(tmp_path):
    report, backend, _ = run(tmp_path, Script(reject_exclusions=True))
    state = state_of(tmp_path)
    assert state["excluded"] == [] and report["counts"]["sections"] == 9
    new = state["sections"][0]
    assert new["first_paragraph"] == "d01-p001" and new["id"] == "s0009"  # reading order, with the next free id
    assert next(q for q in state["questions"] if q["section"] == "s0009")["status"] == "passed"
    assert [c["role"] for c in backend.calls].count("exclusions") == 1  # reviewed once, never again


# --------------------------------------------------------------------------
# Long documents, parts, running side by side


def test_a_long_document_is_drafted_in_parts_that_continue_each_other(tmp_path):
    epub = long_book(tmp_path)
    settings = from_options(section_words="100-400", part_words=2000, rounds=0)
    script = Script(blocks_per_section=10, stop_early=True)
    report, backend, _ = run(tmp_path, script, settings, epub)
    drafts = [c["prompt"] for c in backend.calls if c["role"] == "draft"]
    assert len(drafts) > 1 and "so you may stop" in drafts[0] and "are already divided" in drafts[1]
    assert "Every paragraph listed must be in exactly one range." in drafts[-1]
    book = load_book(epub)
    state = state_of(tmp_path)
    covered = [b.ref for s in state["sections"] for b in pipeline.BookIndex(book).blocks(s)]
    assert covered == [b.ref for b in book.spine[0].blocks]  # every block once, in order
    assert report["firmware_format_ok"]


def test_stopping_too_early_is_sent_back(tmp_path):
    epub = long_book(tmp_path)
    first = []

    def hook(reply, prompt):
        if "so you may stop" in prompt and not first:
            first.append(1)
            reply["ranges"] = reply["ranges"][:1]
        return reply

    _, backend, _ = run(tmp_path, Script(blocks_per_section=10, draft_hook=hook),
                        from_options(section_words="100-400", part_words=2000, rounds=0), epub)
    assert "go on at least to the middle" in [c["prompt"] for c in backend.calls if c["role"] == "draft"][1]


def test_units_group_short_documents_and_split_off_long_ones(tmp_path):
    book = load_book(FIXTURE)
    units = pipeline.plan_units(book, from_options(density="short"))
    assert [u.key for u in units] == ["d01-d04"] and not units[0].long
    units = pipeline.plan_units(book, from_options(section_words="50-100", part_words=2000))
    assert all(not u.long for u in units) and len(units) == 1
    long = load_book(long_book(tmp_path))
    assert pipeline.plan_units(long, from_options(section_words="100-400", part_words=2000))[0].long


# --------------------------------------------------------------------------
# Stopping and continuing


def test_a_run_stopped_by_the_usage_limit_continues_without_repeating_work(tmp_path):
    script = Script()
    calls = {"n": 0}

    def reply(role, system, prompt, schema):
        calls["n"] += 1
        if role == "check" and calls["n"] <= 3:
            raise UsageLimitError("Claude's usage limit is reached (resets 3pm).")
        return script.reply(role, system, prompt, schema)

    backend = ScriptedBackend(reply)
    with pytest.raises(UsageLimitError):
        run_book(FIXTURE, tmp_path / "out", SHORT, backend=backend)
    assert state_of(tmp_path)["stage"] == "check" and script.drafts == 1
    report = run_book(FIXTURE, tmp_path / "out", SHORT, backend=ScriptedBackend(script.reply))
    assert script.drafts == 1  # the draft was not made again
    assert report["complete"] and report["counts"]["passed"] == 8


def test_cancelling_stops_before_the_next_call(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(pipeline.Cancelled):
        run(tmp_path, cancel=cancel)
    assert state_of(tmp_path)["usage"]["calls"] == 0


def test_a_run_cannot_continue_with_other_drafting_options_or_another_book(tmp_path):
    run(tmp_path)
    with pytest.raises(PipelineError, match="other options"):
        run_book(FIXTURE, tmp_path / "out", from_options(density="long"), backend=Script().backend())
    other = long_book(tmp_path)
    with pytest.raises(PipelineError, match="another book"):
        run_book(other, tmp_path / "out", SHORT, backend=Script().backend())


def test_a_finished_run_given_again_calls_claude_no_more(tmp_path):
    run(tmp_path)
    backend = Script().backend()
    report = run_book(FIXTURE, tmp_path / "out", SHORT, backend=backend)
    assert backend.calls == [] and report["complete"]


def test_a_prepared_book_is_refused(tmp_path):
    run(tmp_path)
    with pytest.raises(PipelineError, match="already holds checkpoints"):
        run_book(tmp_path / "out" / "fixture.prepared.epub", tmp_path / "again", SHORT, backend=Script().backend())


# --------------------------------------------------------------------------
# Rebuild after a review


def test_rebuild_removes_and_restores_questions_without_calling_claude(tmp_path):
    run(tmp_path, Script(fail={"c0003-p1": 99}))
    out = tmp_path / "out"
    report = rebuild(out, remove=["c0001-p1", "c0002-p1"])
    assert report["counts"]["questions"] == 5 and report["counts"]["removed"] == 2
    tsv = (out / "fixture.cards.tsv").read_text(encoding="utf-8")
    assert "c0001-p1" not in tsv and "c0004-p1" in tsv
    assert {q["id"]: q["kept"] for s in review_data(out)["sections"] for q in s["questions"]}["c0001-p1"] is False
    report = rebuild(out, restore=["c0001-p1"])
    assert report["counts"]["questions"] == 6
    report = rebuild(out, removed=[])  # the complete list, as the web app sends it
    assert report["counts"]["questions"] == 7 and report["counts"]["failed"] == 1  # a failed question stays out
    with pytest.raises(PipelineError, match="no such question"):
        rebuild(out, remove=["c9999-p1"])


def test_rebuild_keeps_anki_card_identities(tmp_path):
    run(tmp_path)
    out = tmp_path / "out"

    def guids() -> dict[str, str]:
        with zipfile.ZipFile(out / "fixture.anki.apkg") as z:
            (tmp_path / "c.anki2").write_bytes(z.read("collection.anki2"))
        db = sqlite3.connect(tmp_path / "c.anki2")
        try:
            return {flds.split("\x1f")[-1]: guid for guid, flds in db.execute("select guid, flds from notes")}
        finally:
            db.close()

    before = guids()
    rebuild(out, remove=["c0001-p1"])
    after = guids()
    assert len(before) == 8 and len(after) == 7 and all(before[k] == v for k, v in after.items())


# --------------------------------------------------------------------------
# The exports


def test_the_questions_file_holds_passages_and_reading_order(tmp_path):
    run(tmp_path)
    doc = json.loads((tmp_path / "out" / "fixture.questions.json").read_text(encoding="utf-8"))
    assert doc["format"] == "ebook-processor-questions" and doc["book"]["title"] == "The Lighthouse Keeper's Almanac"
    assert [q["order"] for q in doc["questions"]] == list(range(1, 9))
    q = doc["questions"][0]
    assert q["chapter"] == "The Lamp Room" and q["passage"][0]["ref"] == q["supporting_paragraphs"][0]
    assert q["passage"][0]["text"].startswith("The supply boat reached Gannet Rock")
    assert q["check"]["criteria"]["stands_alone"] is True and q["status"] == "passed"
    assert doc["excluded"][0]["reason"].startswith("Front matter")


def test_the_text_file_is_readable_as_tab_separated_values(tmp_path):
    run(tmp_path)
    rows = list(csv.reader(io.StringIO((tmp_path / "out" / "fixture.cards.tsv").read_text(encoding="utf-8")),
                           delimiter="\t"))
    assert rows[0] == ["id", "question", "answer", "book", "chapter", "section", "passage", "tags"]
    assert len(rows) == 9 and all(len(r) == 8 for r in rows) and rows[1][0] == "c0001-p1"


def test_the_anki_deck_has_one_note_per_question(tmp_path):
    run(tmp_path)
    with zipfile.ZipFile(tmp_path / "out" / "fixture.anki.apkg") as z:
        (tmp_path / "c.anki2").write_bytes(z.read("collection.anki2"))
    db = sqlite3.connect(tmp_path / "c.anki2")
    try:
        notes = list(db.execute("select flds, tags from notes"))
        decks = json.loads(db.execute("select decks from col").fetchone()[0])
    finally:
        db.close()
    assert len(notes) == 8
    assert "Books::The Lighthouse Keeper's Almanac" in {d["name"] for d in decks.values()}
    fields = notes[0][0].split("\x1f")
    assert fields[2] == "The Lighthouse Keeper's Almanac" and fields[-1] == "c0001-p1" and "the-lighthouse-keeper-s-almanac" in notes[0][1]


# --------------------------------------------------------------------------
# Settings and the command line


def test_settings_presets_and_limits():
    assert (from_options(density="long").min_words, from_options(density="long").max_words) == (1500, 4000)
    assert from_options(section_words="400-1200").max_words == 1200
    assert from_options(section_words="3000-8000").part_words >= 16000  # a part holds at least two sections
    for bad in ({"section_words": "abc"}, {"section_words": "900-300"}, {"max_questions": 9}, {"rounds": 9},
                {"content": "everything"}, {"density": "tiny"}):
        with pytest.raises(SettingsError):
            from_options(**bad)


def test_the_command_line_reports_usage_and_input_errors(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["process", str(FIXTURE), "--section-words", "nonsense"])
    assert exc.value.code == 2
    assert cli.main(["rebuild", str(tmp_path / "nothing")]) == 1
    assert "holds no run" in capsys.readouterr().err


def test_the_command_line_rebuilds(tmp_path, capsys):
    run(tmp_path)
    assert cli.main(["rebuild", str(tmp_path / "out"), "--remove", "c0001-p1"]) == 0
    assert "7 questions at 7 checkpoints" in capsys.readouterr().out


def test_the_questions_file_fits_its_published_schema(tmp_path):
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(pipeline.__file__).parent / "schema" / "questions.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    run(tmp_path, Script(fail={"c0003-p1": 99, "c0002-p1": 1}))
    rebuild(tmp_path / "out", remove=["c0001-p1"])
    doc = json.loads((tmp_path / "out" / "fixture.questions.json").read_text(encoding="utf-8"))
    errors = [f"{list(e.absolute_path)}: {e.message}" for e in Draft202012Validator(schema).iter_errors(doc)]
    assert not errors, errors
    assert {q["status"] for q in doc["left_out"]} == {"failed", "removed"}
