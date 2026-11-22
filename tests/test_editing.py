"""Changing a book's questions by hand: the wording, the answer key and the passage of one the run wrote, a
question of the reader's own, the model's help on request, and the app's side of each. No test calls a model."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pytest

from ebook_processor import editing, pipeline, reading
from ebook_processor.llm import BackendError, ScriptedBackend
from ebook_processor.runner import change_run, rebuild, review_data, run_book
from ebook_processor.settings import from_options
from ebook_processor.steer import Steer
from ebook_processor.web.server import make_server
from tests.scripted import Script
from tests.paths import FIXTURE

ALL = from_options(density="short", forms="all")


@pytest.fixture
def out(tmp_path):
    """A finished run with every kind of question, one of which the check left out (c0003-p1)."""
    folder = tmp_path / "out"
    run_book(FIXTURE, folder, ALL, backend=Script(fail={"c0003-p1": 9}).backend(), steer=Steer(style="Short answers."))
    return folder


def state_of(folder: Path) -> dict:
    return json.loads((folder / "work" / "state.json").read_text(encoding="utf-8"))


def question(folder: Path, qid: str) -> dict:
    return next(q for q in state_of(folder)["questions"] if q["id"] == qid)


def shown(folder: Path, qid: str) -> dict:
    return next(q for s in review_data(folder)["sections"] for q in s["questions"] if q["id"] == qid)


# --------------------------------------------------------------------------
# By hand


def test_an_edited_question_is_the_readers_and_is_in_every_output(out):
    made = change_run(out, lambda book, state: editing.edit(state, book, "c0001-p1", {
        "question": "  What reached Gannet Rock a little after noon? ", "answer": "The supply boat."}))
    assert made["status"] == "edited" and made["prompt"] == "What reached Gannet Rock a little after noon?"
    kept = question(out, "c0001-p1")
    assert kept["original"]["prompt"].startswith("What does the passage starting") and kept["original"]["status"] == "passed"
    assert kept["criteria"] == {} and kept["edited"] and shown(out, "c0001-p1")["edited"] is True
    manifest = reading.open_reading_book(out / "fixture.prepared.epub").manifest()
    assert manifest["sections"][0]["prompts"][0]["prompt"] == "What reached Gannet Rock a little after noon?"
    doc = json.loads((out / "fixture.questions.json").read_text(encoding="utf-8"))
    assert doc["questions"][0]["status"] == "edited" and doc["questions"][0]["answer"] == "The supply boat."
    assert "The supply boat." in (out / "fixture.cards.tsv").read_text(encoding="utf-8")
    report = json.loads((out / "fixture.report.json").read_text(encoding="utf-8"))
    assert report["counts"]["edited"] == 1 and report["counts"]["passed"] == 6 and report["firmware_format_ok"]
    assert all(q["id"] != "c0001-p1" for q in report["unchecked"])
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(pipeline.__file__).parent / "schema" / "questions.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(doc)) == []
    # one field at a time, and the same wording again changes nothing
    before = question(out, "c0001-p1")
    change_run(out, lambda book, state: editing.edit(state, book, "c0001-p1", {"answer": "The supply boat."}))
    assert question(out, "c0001-p1") == before
    # the original can be put back, with what its check found
    change_run(out, lambda book, state: editing.restore(state, "c0001-p1"))
    back = question(out, "c0001-p1")
    assert back["status"] == "passed" and back["criteria"]["supported_by_section"] and "original" not in back and "edited" not in back
    with pytest.raises(editing.EditError, match="as the run wrote it"):
        change_run(out, lambda book, state: editing.restore(state, "c0001-p1"))


def test_the_answer_key_can_be_corrected_but_a_question_keeps_its_kind(out):
    choice = question(out, "c0002-p1")
    assert choice["form"] == "choice"
    choices = [*choice["choices"]]
    choices[0] = "A better wrong choice" if choice["correct"] != 0 else "A better right choice"
    change_run(out, lambda book, state: editing.edit(state, book, "c0002-p1", {"form": {"choices": choices, "correct": 3}}))
    after = question(out, "c0002-p1")
    assert after["choices"] == choices and after["correct"] == 3 and after["status"] == "edited" and after["original"]["correct"] == choice["correct"]
    with zipfile.ZipFile(out / "fixture.prepared.epub") as z:
        data = json.loads(z.read(next(n for n in z.namelist() if n.endswith("checkpoints.json"))))
    in_book = next(p for c in data["checkpoints"] for p in c["prompts"] if p["id"] == "c0002-p1")
    assert in_book["x_form"]["correct"] == 3 and in_book["answer"].startswith(choices[3])
    change_run(out, lambda book, state: editing.edit(state, book, "c0007-p1", {"form": {"truth": False}}))
    assert question(out, "c0007-p1")["truth"] is False
    change_run(out, lambda book, state: editing.edit(state, book, "c0004-p1", {"form": {"accept": ["d03-p002", " the second paragraph ", ""]}}))
    assert question(out, "c0004-p1")["accept"] == ["d03-p002", "the second paragraph"]
    refused = [
        ("c0001-p1", {"form": {"form": "choice", "choices": ["a", "b", "c", "d"], "correct": 0}}, "keeps its kind"),
        ("c0002-p1", {"form": {"choices": ["a", "a", "b", "c"]}}, "Two of the choices are the same"),
        ("c0001-p1", {"question": "x" * 301}, "the limit is 300"),
        ("c0001-p1", {"answer": " "}, "The answer is empty"),
        ("c0001-p1", {"answer": "<b>bold</b>"}, "plain text"),
        ("c0004-p1", {"question": "No gap in this statement."}, "four underscores"),
        ("c0001-p1", {"supporting_paragraphs": ["d04-p001"]}, "at least one paragraph of the section"),
        ("c0001-p1", {"supporting_paragraphs": "d02-p002"}, "a list of paragraphs"),
        ("c9999-p1", {"answer": "x"}, "no such question"),
    ]
    before = state_of(out)
    for qid, raw, said in refused:
        with pytest.raises(editing.EditError, match=said):
            change_run(out, lambda book, state: editing.edit(state, book, qid, raw))
    assert state_of(out) == before  # a refused change writes nothing


def test_a_question_the_check_left_out_is_kept_once_the_reader_has_changed_it(out):
    assert shown(out, "c0003-p1")["kept"] is False and question(out, "c0003-p1")["status"] == "failed"
    view = change_run(out, lambda book, state: editing.view(book, state, "c0003-p1"), build=False)
    assert view["left_out_because"] == "The answer adds a detail." and view["kept"] is False
    change_run(out, lambda book, state: editing.edit(state, book, "c0003-p1", {"answer": "Only what the paragraph says."}))
    assert shown(out, "c0003-p1")["kept"] is True and question(out, "c0003-p1")["status"] == "edited"
    report = json.loads((out / "fixture.report.json").read_text(encoding="utf-8"))
    assert report["counts"]["questions"] == 8 and report["counts"]["left_out"] == 0
    # it can still be taken out, like any question, and put back
    rebuild(out, remove=["c0003-p1"])
    assert shown(out, "c0003-p1")["kept"] is False and shown(out, "c0003-p1")["can_keep"] is True


def test_the_passage_of_a_question_can_be_picked(out):
    view = change_run(out, lambda book, state: editing.view(book, state, "c0002-p1"), build=False)
    assert [p["ref"] for p in view["paragraphs"]] == ["d02-p005", "d02-p006", "d02-p008"]   # no heading, no bare picture
    assert [p["cited"] for p in view["paragraphs"]] == [True, False, False] and view["paragraphs"][0]["text"].startswith("There were one hundred")
    change_run(out, lambda book, state: editing.edit(state, book, "c0002-p1", {"supporting_paragraphs": ["d02-p008", "d02-p006", "d09-p001"]}))
    assert question(out, "c0002-p1")["supporting_paragraphs"] == ["d02-p006", "d02-p008"]   # of the section, in reading order
    doc = json.loads((out / "fixture.questions.json").read_text(encoding="utf-8"))
    assert [p["ref"] for p in next(q for q in doc["questions"] if q["id"] == "c0002-p1")["passage"]] == ["d02-p006", "d02-p008"]


def test_a_question_of_the_readers_own_joins_its_section(out):
    added = change_run(out, lambda book, state: editing.add(state, book, "s0002", {
        "question": "The lamp room was ____ and bright.", "answer": "It was round, with glass on every side.",
        "form": {"form": "cloze", "accept": ["round"]}}))
    assert added["id"] == "c0002-p2" and added["status"] == "edited" and added["added"]
    assert added["supporting_paragraphs"] == ["d02-p005", "d02-p006", "d02-p008"]             # the whole section
    assert [q["id"] for q in state_of(out)["questions"]][:4] == ["c0001-p1", "c0002-p1", "c0002-p2", "c0003-p1"]
    assert shown(out, "c0002-p2")["added"] is True and shown(out, "c0002-p2")["kept"] is True
    manifest = reading.open_reading_book(out / "fixture.prepared.epub").manifest()
    assert manifest["sections"][1]["prompts"][1] == {"id": "c0002-p2", "prompt": "The lamp room was ____ and bright.",
                                                     "answer": "It was round, with glass on every side.", "form": "cloze", "accept": ["round"]}
    report = json.loads((out / "fixture.report.json").read_text(encoding="utf-8"))
    assert report["counts"]["added"] == 1 and report["counts"]["edited"] == 0 and report["firmware_format_ok"]
    picked = change_run(out, lambda book, state: editing.add(state, book, "s0002", {
        "question": "How many steps led to the lamp room?", "answer": "One hundred and twelve.", "supporting_paragraphs": ["d02-p005"]}))
    assert picked["id"] == "c0002-p3" and picked["supporting_paragraphs"] == ["d02-p005"] and "form" not in picked
    # only the reader's own can be deleted, and its number is not given out again
    change_run(out, lambda book, state: editing.delete(state, "c0002-p2"))
    with pytest.raises(editing.EditError, match="take this one out instead"):
        change_run(out, lambda book, state: editing.delete(state, "c0002-p1"))
    again = change_run(out, lambda book, state: editing.add(state, book, "s0002", {"question": "Why?", "answer": "Because."}))
    assert again["id"] == "c0002-p4"
    for raw, said in (({"question": "Why?", "answer": ""}, "The answer is empty"), ({"question": "Why?", "answer": "x", "form": {"form": "essay"}}, "the kinds of question"),
                      ({"question": "Why?", "answer": "x", "supporting_paragraphs": []}, "at least one paragraph")):
        with pytest.raises(editing.EditError, match=said):
            change_run(out, lambda book, state: editing.add(state, book, "s0002", raw))
    with pytest.raises(editing.EditError, match="no such section"):
        change_run(out, lambda book, state: editing.add(state, book, "s0099", {"question": "Why?", "answer": "x"}))


def test_a_section_without_questions_gets_its_checkpoint_with_the_first_one_added(tmp_path):
    def none(reply, prompt):
        for r in reply["ranges"][:2]:
            r["prompts"] = []
        return reply

    folder = tmp_path / "out"
    run_book(FIXTURE, folder, from_options(density="short"), backend=Script(draft_hook=none).backend())
    bare = next(s for s in state_of(folder)["sections"] if s["checkpoint"] is None)
    before = json.loads((folder / "fixture.report.json").read_text(encoding="utf-8"))["counts"]["checkpoints"]
    added = change_run(folder, lambda book, state: editing.add(state, book, bare["id"], {"question": "Why?", "answer": "Because."}))
    section = next(s for s in state_of(folder)["sections"] if s["id"] == bare["id"])
    assert section["checkpoint"] == added["id"].rsplit("-p", 1)[0] and added["id"].endswith("-p1")
    report = json.loads((folder / "fixture.report.json").read_text(encoding="utf-8"))
    assert report["firmware_format_ok"] and report["counts"]["checkpoints"] == before + 1


# --------------------------------------------------------------------------
# With the model, on request


def test_the_model_rewrites_a_question_to_a_wish_in_the_same_form(out):
    backend = Script().backend()
    made = change_run(out, lambda book, state: editing.rewrite(backend, book, state, "c0002-p1", " Make it harder. ", ALL, Steer(style="Short answers.")), build=False)
    sent = backend.calls[-1]
    assert sent["role"] == "revise" and "QUESTION TO CHANGE [c0002-p1]" in sent["prompt"] and "THE READER ASKS\n~~~\nMake it harder.\n~~~" in sent["prompt"]
    assert "Open questions are not allowed: every question takes this form." in sent["system"] and "Short answers." in sent["system"]
    assert made["form"]["form"] == "choice" and len(made["form"]["choices"]) == 4 and "(as asked: Make it harder.)" in made["answer"]
    assert question(out, "c0002-p1")["status"] == "passed"                       # nothing is kept until the reader saves
    # what the reader has typed and not saved is what gets rewritten
    change_run(out, lambda book, state: editing.rewrite(backend, book, state, "c0001-p1", "Shorter.", ALL, None,
                                                        {"question": "My unsaved wording?", "answer": "Mine."}), build=False)
    assert "Question: My unsaved wording?" in backend.calls[-1]["prompt"] and "it is the only form" not in backend.calls[-1]["system"]
    for wish, said in (("", "Say what should be different"), ("x" * 301, "300 characters")):
        with pytest.raises(editing.EditError, match=said):
            change_run(out, lambda book, state: editing.rewrite(backend, book, state, "c0001-p1", wish, ALL), build=False)
    withdrawn = ScriptedBackend(lambda *a: {"revisions": [{"id": "c0001-p1", "action": "withdraw", "prompt": "", "answer": "", "supporting_paragraphs": []}]})
    with pytest.raises(BackendError, match="could not rewrite"):
        change_run(out, lambda book, state: editing.rewrite(withdrawn, book, state, "c0001-p1", "Harder.", from_options(density="short")), build=False)


def test_a_check_on_request_gives_a_verdict_and_removes_nothing(out):
    change_run(out, lambda book, state: editing.edit(state, book, "c0002-p1", {"answer": "My own explanation."}))

    class Doubting(Script):
        def check(self, prompt):
            reply = super().check(prompt)
            reply["prompts"][0].update(one_right_answer=False, problem="A wrong choice is also right.", suggested_fix="Change it.")
            return reply

    backend = Doubting().backend()
    verdict = change_run(out, lambda book, state: editing.check(backend, book, state, "c0002-p1", from_options(density="short")), build=False)
    assert "one_right_answer" in backend.calls[-1]["system"]                    # the question's own kind is known to the check
    assert verdict["passed"] is False and verdict["problem"] == "A wrong choice is also right." and verdict["criteria"]["one_right_answer"] is False
    kept = question(out, "c0002-p1")
    assert kept["status"] == "edited" and kept["verdict"] == verdict and shown(out, "c0002-p1")["kept"] is True
    assert shown(out, "c0002-p1")["verdict"]["passed"] is False
    passed = change_run(out, lambda book, state: editing.check(Script().backend(), book, state, "c0002-p1", ALL), build=False)
    assert passed["passed"] is True and passed["problem"] == ""
    change_run(out, lambda book, state: editing.edit(state, book, "c0002-p1", {"answer": "Changed again."}))
    assert "verdict" not in question(out, "c0002-p1")                             # a verdict is about the wording it saw


def test_the_model_can_choose_the_passage_of_an_added_question(out):
    backend = Script().backend()
    raw = {"question": "How many steps led to the lamp room?", "answer": "One hundred and twelve."}
    chosen = change_run(out, lambda book, state: editing.choose_passage(backend, book, state, "s0002", raw, ALL), build=False)
    assert chosen == ["d02-p006"]                                                # of the section; what it named elsewhere is dropped
    sent = backend.calls[-1]
    assert sent["role"] == "passage" and "THE READER'S QUESTION" in sent["prompt"] and "[d02-p005] paragraph" in sent["prompt"]
    nothing = ScriptedBackend(lambda *a: {"supporting_paragraphs": ["d99-p001"]})
    assert change_run(out, lambda book, state: editing.choose_passage(nothing, book, state, "s0002", raw, ALL), build=False) == [
        "d02-p005", "d02-p006", "d02-p008"]                                      # nothing usable: the whole section


# --------------------------------------------------------------------------
# What a reader's saved record does with a changed first grade


def test_an_overruled_verdict_and_a_question_started_over_survive_a_later_save():
    old = {"grades": {"c1-p1": {"first": "incorrect", "at": "T1"}, "c1-p2": {"first": "incorrect", "at": "T2"}}, "x_attempts": []}
    new = {"grades": {"c1-p1": {"first": "correct", "at": "T1"}, "c1-p2": {"first": "correct", "at": "T2"}},
           "x_attempts": [{"id": "c1-p1", "at": "T1", "grade": "correct", "overruled": True}, {"id": "c1-p2", "at": "T2", "grade": "incorrect"}]}
    merged = reading.merge_state(old, new)
    assert merged["grades"]["c1-p1"]["first"] == "correct"       # the reader's word on a typed answer
    assert merged["grades"]["c1-p2"]["first"] == "incorrect"     # otherwise a first grade is written once
    # started over on the server: a page that still holds the old grade does not bring it back, a new answer counts
    old = {"grades": {}, "x_restarted": {"c1-p1": "T1"}}
    stale = reading.merge_state(old, {"grades": {"c1-p1": {"first": "correct", "at": "T1"}}, "x_cleared": {"c1-p1": "T1"}, "x_attempts": []})
    assert stale["grades"] == {} and stale["x_cleared"] == {} and stale["x_restarted"] == {"c1-p1": "T1"}
    fresh = reading.merge_state(old, {"grades": {"c1-p1": {"first": "incorrect", "at": "T9"}}, "x_attempts": []})
    assert fresh["grades"]["c1-p1"] == {"first": "incorrect", "at": "T9"}


# --------------------------------------------------------------------------
# The app


@pytest.fixture
def app(tmp_path):
    script = Script()
    server = make_server(tmp_path / "data", "127.0.0.1", 0, backend_factory=lambda settings: script.backend(),
                         claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"

    def call(method: str, path: str, body: bytes | None = None, json_body=None):
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(base + path, data=body, method=method, headers={"X-Ebook-Processor": "1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    query = urllib.parse.urlencode({"name": "fixture.epub", "settings": json.dumps({"density": "short", "forms": "all"})})
    job_id = call("POST", f"/jobs?{query}", FIXTURE.read_bytes())[1]["id"]
    for _ in range(400):
        if call("GET", f"/jobs/{job_id}")[1]["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    call.id, call.script, call.jobs, call.data = job_id, script, server.RequestHandlerClass.jobs, tmp_path / "data"
    yield call
    server.shutdown()
    server.server_close()


def test_the_app_edits_adds_checks_and_rewrites(app):
    at = f"/jobs/{app.id}/book-questions"
    status, shown_ = app("GET", f"{at}/c0002-p1")
    assert status == 200 and shown_["form"] == "choice" and shown_["answered"] is False and len(shown_["paragraphs"]) == 3
    assert app("GET", f"/jobs/{app.id}/book")[1]["editable"] is True and app("GET", f"/jobs/{app.id}/notebook")[1]["editable"] is True
    status, saved = app("PUT", f"{at}/c0002-p1", json_body={"answer": "My own explanation.", "supporting_paragraphs": ["d02-p006"]})
    assert status == 200 and saved["status"] == "edited" and saved["has_original"] and saved["supporting_paragraphs"] == ["d02-p006"]
    # at once: the reader's book, the job's counts and the files hold it
    asked = app("GET", f"/jobs/{app.id}/book")[1]["sections"][1]["prompts"][0]
    assert asked["answer"] == "My own explanation." and app("GET", f"/jobs/{app.id}")[1]["counts"]["edited"] == 1
    assert "My own explanation." in (app.data / app.id / "fixture.cards.tsv").read_text(encoding="utf-8")
    assert app("PUT", f"{at}/c0002-p1", json_body={"form": {"choices": ["a", "a", "b", "c"]}})[0] == 400
    assert app("PUT", f"{at}/c9999-p1", json_body={"answer": "x"})[0] == 400
    status, checked = app("POST", f"{at}/c0002-p1/check", json_body={})
    assert status == 200 and checked["verdict"]["passed"] is True and checked["status"] == "edited"
    status, made = app("POST", f"{at}/c0002-p1/rewrite", json_body={"wish": "Make it harder.", "draft": {"question": "A draft of mine?", "answer": "Draft."}})
    assert status == 200 and "(as asked: Make it harder.)" in made["answer"] and made["form"]["form"] == "choice"
    assert app("POST", f"{at}/c0002-p1/rewrite", json_body={"wish": ""})[0] == 400
    assert app("POST", f"{at}/c0002-p1/restore", json_body={})[1]["status"] == "passed"
    # a question of the reader's own: the whole section, the reader's pick, or the model's choice with or without a look
    own = {"section": "s0002", "question": "How many steps led to the lamp room?", "answer": "One hundred and twelve."}
    assert app("GET", f"/jobs/{app.id}/sections/s0002/paragraphs")[1]["paragraphs"][0]["ref"] == "d02-p005"
    whole = app("POST", at, json_body=own)[1]
    assert whole["id"] == "c0002-p2" and whole["added"] and len(whole["supporting_paragraphs"]) == 3
    picked = app("POST", at, json_body={**own, "passage": "pick", "supporting_paragraphs": ["d02-p008"]})[1]
    assert picked["supporting_paragraphs"] == ["d02-p008"]
    assert app("POST", at, json_body={**own, "passage": "model_review"})[1] == {"review": ["d02-p006"]}
    assert len(app("GET", f"/jobs/{app.id}/review")[1]["sections"][1]["questions"]) == 3          # nothing kept yet
    by_model = app("POST", at, json_body={**own, "passage": "model", "form": {"form": "truefalse", "truth": True}, "question": "There were 112 steps."})[1]
    assert by_model["supporting_paragraphs"] == ["d02-p006"] and by_model["form"] == "truefalse"
    assert app("DELETE", f"{at}/{whole['id']}")[1] == {"deleted": "c0002-p2"} and app("DELETE", f"{at}/c0002-p1")[0] == 400
    assert app("GET", f"/jobs/{app.id}")[1]["counts"]["added"] == 2


def test_a_question_started_over_counts_as_never_answered(app):
    at, stamp = f"/jobs/{app.id}/book-questions", "2026-12-01T10:00:00"
    state = app("GET", f"/jobs/{app.id}/reading")[1]
    state.update(position={"chapter": 3, "word": 0, "mode": "page"}, x_furthest={"chapter": 3, "word": 0},
                 grades={"c0001-p1": {"first": "incorrect", "at": stamp}},
                 x_attempts=[{"id": "c0001-p1", "at": stamp, "grade": "incorrect", "text": "a guess", "where": "read"}])
    assert app("PUT", f"/jobs/{app.id}/reading", json_body=state)[0] == 200
    assert app("GET", "/practice")[1]["counts"]["cards"] == 1 and app("GET", f"{at}/c0001-p1")[1]["answered"] is True
    assert app("GET", "/practice")[1]["card"]["editable"] is True
    status, fresh = app("POST", f"{at}/c0001-p1/restart", json_body={})
    kept = app("GET", f"/jobs/{app.id}/reading")[1]
    assert status == 200 and "c0001-p1" not in kept["grades"] and len(kept["x_attempts"]) == 1   # the answer stays as history
    # the reader is past its section: it is a new card at once
    card = app("GET", "/practice")[1]
    assert card["counts"]["new"] == 1 and card["card"]["id"] == "c0001-p1" and card["card"]["state"] == "new" and card["card"]["times"] == 0
    # a page that was open with the old grade saves again: the grade does not come back
    assert app("PUT", f"/jobs/{app.id}/reading", json_body=state)[0] == 200
    assert "c0001-p1" not in app("GET", f"/jobs/{app.id}/reading")[1]["grades"]
    # a question added for a section already read is a card from the start; deleted, it takes its card with it
    added = app("POST", at, json_body={"section": "s0001", "question": "Why?", "answer": "Because."})[1]
    assert app("GET", "/practice")[1]["counts"]["cards"] == 2
    app("DELETE", f"{at}/{added['id']}")
    assert app("GET", "/practice")[1]["counts"]["cards"] == 1
    ahead = app("POST", at, json_body={"section": "s0008", "question": "Why?", "answer": "Because."})[1]
    assert app("GET", "/practice")[1]["counts"]["cards"] == 1 and ahead["answered"] is False   # met at its checkpoint first


def test_a_book_made_elsewhere_cannot_be_edited_and_a_highlights_own_question_can_be_any_kind(app):
    prepared = (app.data / app.id / "fixture.prepared.epub").read_bytes()
    status, imported = app("POST", "/books/import?name=other.prepared.epub", prepared)
    assert status == 201 and app("GET", f"/jobs/{imported['id']}/book")[1]["editable"] is False
    assert app("PUT", f"/jobs/{imported['id']}/book-questions/c0001-p1", json_body={"answer": "x"})[0] == 409
    assert app("POST", f"/jobs/{imported['id']}/book-questions", json_body={"section": "s0001", "question": "Why?", "answer": "x"})[0] == 409
    hid = app("POST", f"/jobs/{app.id}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9})[1]["made"]
    status, state = app("POST", f"/jobs/{app.id}/questions", json_body={
        "highlight": hid, "question": "The supply boat reached Gannet Rock after noon.", "answer": "A little after noon.",
        "form": {"form": "truefalse", "truth": True}})
    assert status == 200 and state["questions"][0]["form"] == "truefalse" and state["questions"][0]["status"] == "accepted"
    assert app("POST", f"/jobs/{app.id}/questions", json_body={"highlight": hid, "question": "Did it?", "answer": "Yes.", "form": {"form": "truefalse", "truth": True}})[0] == 400


def test_a_question_too_many_for_a_section_is_refused_and_the_book_stays(app):
    """The e-reader's file holds 32 questions for one checkpoint. The 33rd used to be written into the run, the
    build then failed, and the book's file was deleted: the book could no longer be opened."""
    folder = app.data / app.id
    sid = app("GET", f"/jobs/{app.id}/book")[1]["sections"][0]["id"]
    state_file = folder / "work" / "state.json"
    before = state_file.read_bytes()

    def many(book, state):
        for n in range(32):
            editing.add(state, book, sid, {"question": f"What is point {n}?", "answer": f"It is point {n} of the text."})
    with pytest.raises(pipeline.PipelineError, match="A section can have 32 questions"):
        change_run(folder, many)
    assert state_file.read_bytes() == before and (folder / "fixture.prepared.epub").is_file()
    assert app("GET", f"/jobs/{app.id}/book")[0] == 200
    status, made = app("POST", f"/jobs/{app.id}/book-questions", json_body={"section": sid, "question": "One more?", "answer": "Yes, one."})
    assert status == 201 or status == 200, made


def test_looking_at_a_question_writes_nothing(app):
    """Opening the editor went through the path that writes the run's state, and could undo a change made meanwhile."""
    state_file = app.data / app.id / "work" / "state.json"
    stamp = state_file.stat().st_mtime_ns
    sid = app("GET", f"/jobs/{app.id}/book")[1]["sections"][0]["id"]
    assert app("GET", f"/jobs/{app.id}/book-questions/c0002-p1")[0] == 200
    assert app("GET", f"/jobs/{app.id}/sections/{sid}/paragraphs")[0] == 200
    assert app("GET", f"/jobs/{app.id}/review")[0] == 200
    assert state_file.stat().st_mtime_ns == stamp
    assert app("POST", f"/jobs/{app.id}/book-questions/c0002-p1/check")[0] == 200   # a check is kept with the question
    assert state_file.stat().st_mtime_ns != stamp
