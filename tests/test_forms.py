"""The forms of question: open, multiple choice, true or false, fill in the blank. What the models are told, what
is kept, what each output shows, and the app's side. No test calls a model."""

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

from ebook_processor import cli, forms, pipeline, prompts, reading, steer
from ebook_processor import highlight_questions as hq
from ebook_processor.runner import review_data, run_book
from ebook_processor.settings import Settings, SettingsError, from_options
from ebook_processor.web.server import make_server
from tests.scripted import EMPTY_FORM, Script, formed
from tests.test_pipeline import FIXTURE

ALL = from_options(density="short", forms="all")
CHOICE = {"prompt": "What did the keeper wind each evening?", "answer": "The log says the clockwork had to be wound.",
          "form": "choice", "choices": ["The lamp", "The clockwork", "The bell", "The winch"], "correct": 1}
TRUE = {"prompt": "The lamp turned by clockwork.", "answer": "The weight drove the clockwork that turned it.", "form": "truefalse",
        "truth": True}
CLOZE = {"prompt": "The lens was turned by ____ driven by a falling weight.", "answer": "A weight drove the clockwork.",
         "form": "cloze", "accept": ["clockwork", "a clockwork"]}
OPEN = {"prompt": "Why was the weight wound up?", "answer": "So that the lens kept turning."}


def state_of(folder: Path) -> dict:
    return json.loads((folder / "work" / "state.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The forms themselves


def test_the_allowed_forms_are_a_setting_of_the_run():
    assert Settings().forms == ("open",) and Settings().open_only
    assert from_options(forms="cloze, open").forms == ("open", "cloze")       # kept in the fixed order
    assert from_options(forms=["truefalse"]).forms == ("truefalse",) and from_options(forms="all").forms == forms.FORMS
    assert Settings.from_json(ALL.as_json()) == ALL and ALL.as_json()["forms"] == list(forms.FORMS)
    assert Settings.from_json({"min_words": 300, "max_words": 1000}).forms == ("open",)   # a run from before forms existed
    assert from_options(forms="open,choice").drafting_key() != from_options().drafting_key()
    for bad in ("essay", "", [], "open;choice", 7):
        with pytest.raises(SettingsError):
            from_options(forms=bad)
    with pytest.raises(SettingsError):
        Settings(forms=("choice", "open")).validate()


def test_a_models_reply_becomes_the_members_that_are_kept():
    clean = lambda text: " ".join(str(text or "").split())  # noqa: E731
    made = forms.from_model({"form": "choice", "correct_choice": " The clockwork ", "wrong_choices": ["The lamp", "The bell", "The winch"]},
                            "What did the keeper wind?", clean)
    assert sorted(made["choices"]) == ["The bell", "The clockwork", "The lamp", "The winch"] and made["choices"][made["correct"]] == "The clockwork"
    again = forms.from_model({"form": "choice", "correct_choice": "The clockwork", "wrong_choices": ["The lamp", "The bell", "The winch"]},
                             "What did the keeper wind?", clean)
    assert again == made                                                          # the same question, the same order
    places = {forms.with_choices("right", ["a", "b", "c"], f"Question {n}?")["correct"] for n in range(40)}
    assert places == {0, 1, 2, 3}                                                # the right choice has no place of its own
    assert forms.from_model({"form": "truefalse", "truth": False}, "S.", clean) == {"form": "truefalse", "truth": False}
    assert forms.from_model({"form": "cloze", "blank": "clockwork", "also_accept": ["Clockwork", "a clockwork", ""]}, "S ____.", clean) == {
        "form": "cloze", "accept": ["clockwork", "a clockwork"]}
    assert forms.from_model({"form": "open", **EMPTY_FORM}, "Q?", clean) == {} and forms.from_model({"form": "poem"}, "Q?", clean) == {}


def test_what_is_wrong_with_a_form_is_said_in_words():
    for good in (OPEN, CHOICE, TRUE, CLOZE):
        assert forms.problems(good, forms.FORMS) == []
    assert "not allowed here" in forms.problems(CHOICE, ("open", "cloze"))[0]
    bad = [
        ({**CHOICE, "choices": CHOICE["choices"][:3]}, "exactly 3 wrong_choices"),
        ({**CHOICE, "choices": ["The lamp", "the lamp", "The bell", "The winch"]}, "two of the choices are the same"),
        ({**CHOICE, "choices": ["x" * 151, "b", "c", "d"]}, "a choice has 151 characters"),
        ({**CHOICE, "answer": "y" * 401}, "the explanation has 401 characters"),
        ({**TRUE, "truth": None}, "needs truth"),
        ({**TRUE, "prompt": "Did the lamp turn?"}, "a statement, not a question"),
        ({**TRUE, "prompt": "The lamp " + "turned " * 45}, "the limit for a true-or-false statement is 280"),
        ({**CLOZE, "prompt": "The lens was turned by clockwork."}, "four underscores"),
        ({**CLOZE, "prompt": "The ____ was turned by ____."}, "exactly once"),
        ({**CLOZE, "prompt": "The clockwork lens was turned by ____."}, "contains the missing words"),
        ({**CLOZE, "prompt": "It is ____."}, "too short"),
        ({**CLOZE, "accept": []}, "needs blank"),
        ({**CLOZE, "accept": ["x" * 61]}, "the missing words has 61 characters"),
        ({**CLOZE, "accept": ["a", "b", "c", "d", "e", "f"]}, "more than 4 entries"),
    ]
    for q, said in bad:
        assert any(said in p for p in forms.problems(q)), (q, forms.problems(q))


def test_every_form_can_be_shown_as_a_plain_question_and_answer():
    assert forms.as_text(OPEN) == (OPEN["prompt"], OPEN["answer"])
    assert forms.as_text(CHOICE) == (CHOICE["prompt"], "The clockwork. The log says the clockwork had to be wound.")
    assert forms.as_text(TRUE) == ("True or false: The lamp turned by clockwork.", "True. The weight drove the clockwork that turned it.")
    assert forms.as_text({**TRUE, "truth": False})[1].startswith("False. ")
    assert forms.as_text(CLOZE) == (CLOZE["prompt"], "clockwork. A weight drove the clockwork.")
    assert [forms.key(q) for q in (OPEN, CHOICE, TRUE, CLOZE)] == ["", "The clockwork", "True", "clockwork"]
    # the longest a question may be still fits what the firmware holds
    longest = {"form": "choice", "prompt": "p" * 300, "answer": "e" * 400, "choices": ["c" * 150] * 4, "correct": 0}
    assert len(forms.as_text(longest)[1]) <= 600
    assert len(forms.as_text({"form": "truefalse", "prompt": "s" * 280, "answer": "e" * 400, "truth": False})[0]) <= 300


def test_the_form_travels_in_the_firmware_format_book_beside_the_plain_text():
    assert forms.to_x(OPEN) is None
    for q in (CHOICE, TRUE, CLOZE):
        prompt, answer = forms.as_text(q)
        entry = {"id": "c0001-p1", "prompt": prompt, "answer": answer, "x_form": forms.to_x(q)}
        assert forms.from_x(entry) == {"id": "c0001-p1", **q}
        assert forms.from_x({k: v for k, v in entry.items() if k != "x_form"}) == {"id": "c0001-p1", "prompt": prompt, "answer": answer}
    assert forms.to_x(TRUE) == {"form": "truefalse", "truth": True, "statement": TRUE["prompt"], "explanation": TRUE["answer"]}
    # a book made elsewhere with something else in that member is read as open questions
    for odd in ("choice", {"form": "essay"}, {"form": "choice", "choices": ["a"], "correct": 0, "explanation": "e"},
                {"form": "truefalse", "truth": True, "explanation": "e"}):
        plain = forms.from_x({"id": "c1-p1", "prompt": "Q?", "answer": "A.", "x_form": odd})
        assert plain == {"id": "c1-p1", "prompt": "Q?", "answer": "A."}


# --------------------------------------------------------------------------
# What the models are told


def test_with_open_questions_only_nothing_about_forms_is_said():
    plain = from_options()
    assert "Forms of question" not in prompts.drafter_system(plain) and "one_right_answer" not in prompts.checker_system(plain)
    assert prompts.draft_schema(plain) is prompts.DRAFT_SCHEMA and prompts.check_schema(plain) is prompts.CHECK_SCHEMA
    assert prompts.revise_schema(plain) is prompts.REVISE_SCHEMA and prompts.criteria(plain) == prompts.CRITERIA
    assert hq.propose_schema(plain) is hq.PROPOSE_SCHEMA and "The form of the question" not in hq.system(plain)
    assert "no multiple choice" in steer.reading_system(plain) and "Kinds of question" in steer.reading_system(plain)
    assert "it is the only form of question" in prompts.drafter_system(plain, steer.Steer(purpose="x"))


def test_the_drafter_is_told_the_allowed_forms_and_how_each_is_written():
    told = prompts.drafter_system(from_options(forms="open,choice,cloze"))
    assert "$" not in told and "**open question, multiple choice, fill in the blank**" in told
    assert "- **Multiple choice**" in told and "- **Fill in the blank**" in told and "- **True or false**" not in told
    assert "A why or a how stays an open question" in told and '`"truth": false`' in told
    only = prompts.drafter_system(from_options(forms="truefalse"))
    assert "Open questions are not allowed: every question takes this form." in only and "- **Open question**" not in only
    schema = prompts.draft_schema(from_options(forms="open,choice"))
    prompt = schema["properties"]["ranges"]["items"]["properties"]["prompts"]["items"]
    assert prompt["properties"]["form"]["enum"] == ["open", "choice"] and set(EMPTY_FORM) <= set(prompt["required"])
    assert "form" in prompts.revise_schema(ALL)["properties"]["revisions"]["items"]["required"]
    steered = prompts.drafter_system(ALL, steer.Steer(style="Mostly blanks."))
    assert "which forms of question are allowed" in steered and "never add another" in steered
    assert "has ticked these kinds of question: open questions recalled from memory, multiple choice" in steer.reading_system(
        from_options(forms="open,choice"))


def test_the_checker_judges_a_sixth_point_where_the_app_grades():
    told = prompts.checker_system(ALL)
    assert "$" not in told and "**`one_right_answer`**" in told and "all six are true" in told
    assert prompts.criteria(ALL) == prompts.CRITERIA + ("one_right_answer",)
    assert "one_right_answer" in prompts.check_schema(ALL)["properties"]["prompts"]["items"]["required"]
    lines = prompts.question_lines({"id": "c1-p1", **CHOICE}, "QUESTION [c1-p1]")
    assert lines == ["QUESTION [c1-p1]", "Form: multiple choice", "Correct choice: The clockwork", "Wrong choice: The lamp",
                     "Wrong choice: The bell", "Wrong choice: The winch", "Question: What did the keeper wind each evening?",
                     "Explanation shown after answering: The log says the clockwork had to be wound."]
    assert prompts.question_lines({**TRUE}, "Q")[1:4] == ["Form: true or false (the question is the statement)", "The statement is: true",
                                                           "Statement: The lamp turned by clockwork."]
    assert "Also accepted: a clockwork" in prompts.question_lines({**CLOZE}, "Q")
    assert prompts.question_lines(OPEN, "Q") == ["Q", "Question: Why was the weight wound up?", "Reference answer: So that the lens kept turning."]


# --------------------------------------------------------------------------
# A run


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("forms") / "out"
    script = Script(fail={"c0002-p1": 1})
    backend = script.backend()
    report = run_book(FIXTURE, out, ALL, backend=backend)
    return out, report, backend


def test_a_run_writes_every_allowed_form_and_all_its_outputs(run):
    out, report, backend = run
    state = state_of(out)
    by = {q["id"]: q for q in state["questions"]}
    assert [forms.form_of(q) for q in state["questions"]] == ["open", "open", "truefalse", "cloze", "open", "choice", "truefalse", "cloze"]
    assert report["firmware_format_ok"] and report["counts"]["questions"] == 8 and report["counts"]["revised"] == 1
    assert all(q["criteria"]["one_right_answer"] for q in state["questions"])
    # the question that failed was a multiple-choice one; revised as an open one, it has lost its choices
    assert by["c0002-p1"]["history"][0]["form"] == "choice" and "choices" not in by["c0002-p1"] and "form" not in by["c0002-p1"]
    check = next(c for c in backend.calls if c["role"] == "check")["prompt"]
    assert "Form: multiple choice" in check and "Explanation shown after answering:" in check
    revise = next(c for c in backend.calls if c["role"] == "revise")["prompt"]
    assert "Correct choice: What d02-p005 says" in revise and "Keep a question's form" in revise
    # the firmware-format book: plain text for a firmware that knows open questions, the form beside it
    with zipfile.ZipFile(out / "fixture.prepared.epub") as z:
        data = json.loads(z.read(next(n for n in z.namelist() if n.endswith("checkpoints.json"))))
    prompts_ = {p["id"]: p for c in data["checkpoints"] for p in c["prompts"]}
    assert prompts_["c0003-p1"]["prompt"].startswith("True or false: The passage starting at d02-p009")
    assert prompts_["c0003-p1"]["answer"].startswith("True. ") and prompts_["c0003-p1"]["x_form"]["truth"] is True
    assert prompts_["c0006-p1"]["answer"].startswith("What d03-p009 says. It says") and len(prompts_["c0006-p1"]["x_form"]["choices"]) == 4
    assert "x_form" not in prompts_["c0001-p1"] and prompts_["c0004-p1"]["x_form"]["accept"] == ["d03-p002", "d03 p002"]
    # the reader gets the questions with their forms back
    manifest = reading.open_reading_book(out / "fixture.prepared.epub").manifest()
    asked = {p["id"]: p for s in manifest["sections"] for p in s["prompts"]}
    assert asked["c0003-p1"] == {"id": "c0003-p1", "prompt": by["c0003-p1"]["prompt"], "answer": by["c0003-p1"]["answer"],
                                 "form": "truefalse", "truth": True}
    assert asked["c0006-p1"]["choices"] == by["c0006-p1"]["choices"] and asked["c0006-p1"]["correct"] == by["c0006-p1"]["correct"]
    assert review_data(out)["sections"][5]["questions"][0]["choices"] == by["c0006-p1"]["choices"]


def test_the_question_data_the_cards_and_the_visible_book_show_each_form(run):
    from jsonschema import Draft202012Validator

    out, _, _ = run
    doc = json.loads((out / "fixture.questions.json").read_text(encoding="utf-8"))
    schema = json.loads((Path(pipeline.__file__).parent / "schema" / "questions.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(doc)) == []
    by = {q["id"]: q for q in doc["questions"]}
    assert by["c0001-p1"]["form"] == "open" and "choices" not in by["c0001-p1"]
    assert by["c0006-p1"]["form"] == "choice" and by["c0006-p1"]["choices"][by["c0006-p1"]["correct"]] == "What d03-p009 says"
    assert by["c0004-p1"]["accept"][0] == "d03-p002" and by["c0007-p1"]["truth"] is True and doc["settings"]["forms"] == list(forms.FORMS)
    cards = (out / "fixture.cards.tsv").read_text(encoding="utf-8")
    assert "\nA. " in cards and "\nD. " in cards and "True or false: The passage starting at d04-p002" in cards
    assert "\tWhat d03-p009 says. It says what d03-p009 says.\t" in cards and "paragraph ____ of it." in cards
    with zipfile.ZipFile(out / "fixture.checkpoints.epub") as z:
        chapter = z.read(next(n for n in z.namelist() if n.endswith("chapter-2.xhtml"))).decode("utf-8")
    assert chapter.count('class="ebp-choice"') == 4 and "Choose one, then turn the page." in chapter
    assert 'class="ebp-key"' in chapter and "Recall the missing words, then turn the page." in chapter
    with zipfile.ZipFile(out / "fixture.checkpoints.epub") as z:
        log = z.read(next(n for n in z.namelist() if n.endswith("chapter-3.xhtml"))).decode("utf-8")
    assert "True or false?" in log and "Decide whether this is true" in log and ">True.<" in log
    import sqlite3
    import tempfile
    with zipfile.ZipFile(out / "fixture.anki.apkg") as z, tempfile.TemporaryDirectory() as folder:
        z.extract("collection.anki2", folder)
        db = sqlite3.connect(str(Path(folder) / "collection.anki2"))
        fields = [row[0] for row in db.execute("select flds from notes")]
        db.close()
    assert any('<ol type="A"><li>' in f for f in fields) and any("True or false: " in f for f in fields)


def test_a_form_that_is_not_allowed_or_not_well_formed_is_sent_back(tmp_path):
    def wrong(reply, prompt):
        for r in reply["ranges"]:
            if r["kind"] == "section" and "PROBLEMS WITH YOUR EARLIER REPLY" not in prompt:
                r["prompts"][0] = {**r["prompts"][0], **formed("truefalse", "d02-p002", "x"), "supporting_paragraphs": r["prompts"][0]["supporting_paragraphs"]}
        return reply

    backend = Script(draft_hook=wrong).backend()
    report = run_book(FIXTURE, tmp_path / "a", from_options(density="short", forms="open,cloze"), backend=backend)
    assert any("its form is true or false, which is not allowed here" in c["prompt"] for c in backend.calls if c["role"] == "draft")
    assert report["counts"]["questions"] == 8

    def no_gap(reply, prompt):  # never corrected: the question is left out, and the report says so
        for r in reply["ranges"]:
            for q in r["prompts"]:
                if q.get("form") == "cloze":
                    q["prompt"] = "A statement without a gap."
        return reply

    report = run_book(FIXTURE, tmp_path / "b", from_options(density="short", forms="cloze"), backend=Script(draft_hook=no_gap).backend())
    assert report["counts"]["questions"] == 0 and any("left out because the drafter did not correct" in w for w in report["warnings"])


def test_a_key_that_is_not_the_one_right_answer_fails_the_check(tmp_path):
    class Doubting(Script):
        def check(self, prompt):
            reply = super().check(prompt)
            for entry in reply["prompts"]:
                if entry["id"] == "c0001-p1" and self.checks_seen[entry["id"]] == 1:
                    entry.update(one_right_answer=False, problem="A wrong choice is also right.", suggested_fix="Change it.")
            return reply

    script = Doubting()
    backend = script.backend()
    run_book(FIXTURE, tmp_path / "out", from_options(density="short", forms="choice"), backend=backend)
    q = state_of(tmp_path / "out")["questions"][0]
    assert q["history"][0]["failed"] == ["one_right_answer"] and q["status"] == "passed"
    assert "Failed: one_right_answer" in next(c for c in backend.calls if c["role"] == "revise")["prompt"]


# --------------------------------------------------------------------------
# Questions from highlights


def test_a_question_on_a_highlight_can_take_an_allowed_form_and_be_changed():
    told = hq.system(from_options(forms="open,choice"))
    assert "$" not in told and "## The form of the question" in told and "`question` is a question that could be answered" in told
    assert hq.propose_schema(from_options(forms="open,choice"))["required"][:3] == ["question", "form", "answer"]
    made = hq.formed(" What did the keeper  wind? ", "The log says so.", {"form": "choice", "choices": ["The lamp ", "The clockwork", "The bell", "The winch"], "correct": 1})
    assert made == {"question": "What did the keeper wind?", "answer": "The log says so.", "form": "choice",
                    "choices": ["The lamp", "The clockwork", "The bell", "The winch"], "correct": 1}
    assert hq.formed("Why?", "Because.") == {"question": "Why?", "answer": "Because."}
    for bad in ({"form": "choice", "choices": ["a", "a", "b", "c"], "correct": 0}, {"form": "cloze", "accept": ["x"]},
                {"form": "truefalse", "truth": "yes"}):
        with pytest.raises(hq.QuestionError):
            hq.formed("A statement about it.", "Because.", bad)
    store = {"highlights": [], "notes": [], "questions": [{"id": "hq1", "question": "The lamp turned by clockwork.", "answer": "It did.",
                                                         "form": "truefalse", "truth": True, "by": "model", "checked": {"passed": True}}]}
    q = hq.reword(store, "hq1", form={"truth": False})
    assert q["truth"] is False and q["by"] == "you" and q["checked"] is None
    with pytest.raises(hq.QuestionError, match="keeps its form"):
        hq.reword(store, "hq1", form={"form": "cloze", "accept": ["x"]})
    with pytest.raises(hq.QuestionError):
        hq.reword(store, "hq1", question="Did it turn?")   # still a statement


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
                raw = response.read()
                return response.status, (json.loads(raw) if response.headers.get("Content-Type", "").startswith("application/json") else raw)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    call.script, call.jobs, call.data = script, server.RequestHandlerClass.jobs, tmp_path / "data"
    yield call
    server.shutdown()
    server.server_close()


def added(call, settings: dict) -> str:
    query = urllib.parse.urlencode({"name": "fixture.epub", "settings": json.dumps(settings)})
    status, job = call("POST", f"/jobs?{query}", FIXTURE.read_bytes())
    assert status == 201, job
    for _ in range(400):
        status, job = call("GET", f"/jobs/{job['id']}")
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job["status"] == "done", job["message"]
    return job["id"]


def test_the_app_processes_reads_practises_and_notes_the_forms(app):
    assert app("GET", "/info")[1]["defaults"]["forms"] == ["open"]
    assert app("POST", "/jobs?name=f.epub&settings=" + urllib.parse.quote(json.dumps({"forms": ["essay"]})), FIXTURE.read_bytes())[0] == 400
    job_id = added(app, {"density": "short", "forms": "open,choice,truefalse,cloze"})   # as the settings window keeps it
    assert app("GET", f"/jobs/{job_id}")[1]["settings"]["forms"] == list(forms.FORMS)
    sections = app("GET", f"/jobs/{job_id}/book")[1]["sections"]
    asked = [p for s in sections for p in s["prompts"]]
    assert [p.get("form", "open") for p in asked] == ["open", "choice", "truefalse", "cloze", "open", "choice", "truefalse", "cloze"]
    review = app("GET", f"/jobs/{job_id}/review")[1]
    assert review["sections"][1]["questions"][0]["form"] == "choice" and "correct" in review["sections"][1]["questions"][0]
    # answered in the reader: a card in Practice with its form, an answer in the notebook with its form
    stamp = "2026-12-01T10:00:00"
    choice, cloze = asked[1], asked[3]
    state = {"format": "rsvp-state", "version": 1, "position": {"chapter": 4, "word": 0, "mode": "page"}, "x_furthest": {"chapter": 4, "word": 0},
             "grades": {choice["id"]: {"first": "incorrect", "at": stamp}, cloze["id"]: {"first": "correct", "at": stamp}},
             "x_attempts": [{"id": choice["id"], "at": stamp, "grade": "incorrect", "text": choice["choices"][0], "where": "read"},
                            {"id": cloze["id"], "at": stamp, "grade": "correct", "text": "d03-p002", "where": "read", "overruled": True}]}
    current = app("GET", f"/jobs/{job_id}/reading")[1]
    assert app("PUT", f"/jobs/{job_id}/reading", json_body={**current, **state})[0] == 200
    card = app("GET", "/practice")[1]["card"]
    assert card["form"] == "choice" and card["choices"] == choice["choices"] and card["correct"] == choice["correct"]
    notebook = app("GET", f"/jobs/{job_id}/notebook")[1]
    answers = [a for c in notebook["chapters"] for s in c["sections"] for a in s["answers"]]
    assert {a["id"]: a.get("form") for a in answers} == {choice["id"]: "choice", cloze["id"]: "cloze"}
    text = app("GET", f"/jobs/{job_id}/notebook.md")[1].decode("utf-8")
    assert f"- **{cloze['prompt']}** (recalled)" in text and "  - Reference: d03-p002. It starts at d03-p002." in text
    assert "  - Answered: " + choice["choices"][0] in text


def test_questions_on_highlights_take_the_forms_ticked_in_the_settings(app):
    job_id = added(app, {"density": "short"})
    app("PUT", "/settings", json_body={"add_forms": "open,choice,truefalse"})
    assert app.jobs.question_settings().forms == ("open", "choice", "truefalse")
    hid = app("POST", f"/jobs/{job_id}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9})[1]["made"]

    def proposed(body: dict) -> dict:
        before = {q["id"] for q in app("GET", f"/jobs/{job_id}/questions")[1]["questions"]}
        assert app("POST", f"/jobs/{job_id}/questions/propose", json_body={"highlight": hid, **body})[0] == 200
        for _ in range(200):
            state = app("GET", f"/jobs/{job_id}/questions")[1]
            new = [q for q in state["questions"] if q["id"] not in before]
            if new and not state["pending"]:
                assert not state["failed"], state["failed"]
                return new[0]
            time.sleep(0.05)
        raise AssertionError(app("GET", f"/jobs/{job_id}/questions")[1])

    first = proposed({})
    assert first["form"] == "choice" and len(first["choices"]) == 4 and first["status"] == "proposed"
    asked = proposed({"instruction": "As a truefalse statement, please."})
    assert asked["form"] == "truefalse" and asked["truth"] in (True, False)
    # the reader changes which choice is right, and one of the wrong ones; then accepts: it is asked with its form
    choices = [*first["choices"]]
    choices[(first["correct"] + 1) % 4] = "A choice of my own"
    status, state = app("PUT", f"/jobs/{job_id}/questions/{first['id']}", json_body={"form": {"choices": choices, "correct": 0}, "accept": True})
    changed = next(q for q in state["questions"] if q["id"] == first["id"])
    assert status == 200 and changed["correct"] == 0 and "A choice of my own" in changed["choices"] and changed["by"] == "you"
    assert app("PUT", f"/jobs/{job_id}/questions/{first['id']}", json_body={"form": {"choices": ["a", "b"], "correct": 0}})[0] == 400
    section = next(s for s in app("GET", f"/jobs/{job_id}/book")[1]["sections"] if any(p["id"] == first["id"] for p in s["prompts"]))
    mine = section["prompts"][-1]
    assert mine["hq"] is True and mine["form"] == "choice" and mine["correct"] == 0
    # accepted without asking: checked on six points first
    held = proposed({"auto": True})
    assert held["status"] == "accepted" and set(held["checked"]["criteria"]) >= {"one_right_answer"} or held["form"] == "open"
    # with a form unticked again, a question written earlier is still checked and exported as what it is
    app("PUT", "/settings", json_body={"add_forms": "open"})
    doc = app("GET", f"/jobs/{job_id}/files/fixture.questions.json")[1]
    exported = next(q for q in doc["questions"] if q["id"] == first["id"])
    assert exported["form"] == "choice" and exported["origin"] == "highlight" and exported["choices"] == changed["choices"]


# --------------------------------------------------------------------------
# The command line


def test_the_command_line_takes_the_forms(capsys):
    args = cli.make_parser().parse_args(["process", "Book.epub", "--forms", "open,cloze"])
    assert cli.settings_from(args).forms == ("open", "cloze")
    with pytest.raises(SystemExit):
        cli.main(["estimate", str(FIXTURE), "--forms", "essay"])
    assert "unknown form of question: essay" in capsys.readouterr().err
