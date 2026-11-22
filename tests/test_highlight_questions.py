"""Questions made from highlights: the store, what the model is sent, and the app's side of it."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from ebook_processor import highlight_questions as hq
from ebook_processor import notes, pipeline, reading
from ebook_processor.llm import BackendError, Completion, ScriptedBackend
from ebook_processor.settings import Settings
from ebook_processor.web import server as web
from tests.scripted import Script
from tests.test_pipeline import FIXTURE

PREPARED = Path(__file__).resolve().parent / "data" / "books" / "fixture.prepared.epub"


@pytest.fixture(scope="module")
def book():
    return reading.open_reading_book(PREPARED)


def marked(book, store, chapter=2, start=3, end=9, note=None):
    return notes.add_highlight(store, book, chapter, start, end, note)


def empty():
    return {"version": 1, "highlights": [], "notes": []}


# --------------------------------------------------------------------------
# The store


def test_a_question_waits_for_the_reader_and_is_then_asked_at_its_checkpoint_or_in_practice(book):
    store, manifest = empty(), book.manifest()
    h = marked(book, store)
    q = hq.add(store, manifest, h, "  What reached  Gannet Rock?  ", "The supply boat.", "model", "m-1")
    section = hq.section_at(manifest, 2, 3)
    assert q["status"] == "proposed" and q["question"] == "What reached Gannet Rock?" and q["section"] == section["id"]
    assert q["id"].startswith("hq") and hq.ids(store) == set() and hq.with_questions(manifest, store) is manifest
    # not yet past the end of the section: it joins the section's checkpoint, after the book's own questions
    hq.accept(store, manifest, {"x_furthest": {"chapter": 2, "word": section["end"] - 1}}, q["id"])
    assert (q["status"], q["ask"]) == ("accepted", "checkpoint") and hq.ids(store) == {q["id"]}
    prompts = next(s for s in hq.with_questions(manifest, store)["sections"] if s["id"] == section["id"])["prompts"]
    assert [p["id"] for p in prompts] == [p["id"] for p in section["prompts"]] + [q["id"]] and prompts[-1]["hq"] is True
    assert len(section["prompts"]) == len(prompts) - 1  # the book's own manifest is not changed
    assert hq.practice_prompts(manifest, store) == {}
    # already past it: a practice card from the start
    later = hq.add(store, manifest, h, "And what does it carry?", "Supplies.", "you")
    hq.accept(store, manifest, {"x_furthest": {"chapter": 3, "word": 0}}, later["id"])
    assert later["ask"] == "practice"
    where, prompt = hq.practice_prompts(manifest, store)[later["id"]]
    assert (where["chapter"], where["start"], where["label"]) == (2, 3, section["label"]) and prompt["prompt"] == "And what does it carry?"


def test_rewording_discarding_and_what_is_refused(book):
    store, manifest = empty(), book.manifest()
    h = marked(book, store)
    q = hq.add(store, manifest, h, "What reached the rock?", "The boat.", "model")
    q["checked"] = {"passed": True, "criteria": {}, "problem": ""}
    hq.reword(store, q["id"], question="What arrived at Gannet Rock a little after noon?")
    assert q["by"] == "you" and q["checked"] is None and q["answer"] == "The boat."   # a check of the old wording says nothing
    for bad in ({"question": " "}, {"answer": 7}, {"question": "x" * 301}, {"answer": "y" * 601}):
        with pytest.raises(hq.QuestionError):
            hq.reword(store, q["id"], **bad)
    with pytest.raises(hq.QuestionError):
        hq.find(store, "hq00000000")
    hq.discard(store, q["id"])
    assert hq.questions(store) == []


def test_questions_follow_highlights_that_join_and_outlive_one_that_is_removed_when_asked_to(book):
    store, manifest = empty(), book.manifest()
    first, second = marked(book, store, start=3, end=9), marked(book, store, start=20, end=25)
    a = hq.add(store, manifest, first, "First question?", "A.", "model")
    b = hq.add(store, manifest, second, "Second question?", "B.", "model")
    joined = marked(book, store, start=8, end=21)   # touches both: one highlight now
    assert len(store["highlights"]) == 1 and {a["highlight"], b["highlight"]} == {joined["id"]}
    hq.accept(store, manifest, None, a["id"])
    gone = hq.highlight_removed(store, joined["id"], keep=True)
    assert [q["id"] for q in gone] == [b["id"]]     # a proposal goes with its highlight; the accepted one stays
    assert a["highlight"] is None and hq.questions(store) == [a]
    other = marked(book, store, start=40, end=44)
    c = hq.add(store, manifest, other, "Third?", "C.", "you")
    hq.accept(store, manifest, None, c["id"])
    assert [q["id"] for q in hq.highlight_removed(store, other["id"], keep=False)] == [c["id"]]


# --------------------------------------------------------------------------
# The model


def test_the_model_sees_the_highlight_inside_its_section_with_the_note_and_what_was_asked(book):
    store, manifest = empty(), book.manifest()
    h = marked(book, store, note="Why does the speed matter?")
    hq.add(store, manifest, h, "An earlier question about it?", "Yes.", "model")
    place = hq.around(book, manifest, 2, 3, 9)
    section = hq.section_at(manifest, 2, 3)
    assert place["section"]["id"] == section["id"] and place["blocks"][0].start >= section["start"] and place["blocks"][-1].start < section["end"]
    message = hq.propose_message(book, place, h, ["An earlier question about it?"], Settings())
    assert "Title: The Lighthouse Keeper's Almanac" in message and f"HIGHLIGHT (in {place['marked'][0].ref})\n{h['text']}" in message
    assert "THE READER'S NOTE\nWhy does the speed matter?" in message and "ALREADY ASKED\n- An earlier question about it?" in message
    assert all(f"[{b.ref}]" in message for b in place["blocks"])  # the whole section, as numbered blocks
    bare = hq.propose_message(book, place, {**h, "note": ""}, [], Settings())
    assert "THE READER'S NOTE" not in bare and "ALREADY ASKED" not in bare
    rules = hq.system(Settings())
    assert "Stands alone" in rules and "300 characters" in rules and "$" not in rules
    # a highlight outside every section still gets the paragraphs around it
    assert hq.around(book, manifest, 1, 0, 2)["blocks"]


def test_a_reply_that_breaks_a_plain_rule_is_sent_back_once(book):
    store, manifest = empty(), book.manifest()
    h = marked(book, store)
    replies = [{"question": "x" * 400, "answer": "Too long a question."}, {"question": "What reached Gannet Rock?", "answer": "The supply boat."}]
    backend = ScriptedBackend(lambda role, system, prompt, schema: replies.pop(0))
    made = hq.propose(backend, book, manifest, store, h, Settings(model="codex:gpt-x"))
    assert made["question"] == "What reached Gannet Rock?" and len(backend.calls) == 2
    assert backend.calls[0]["model"] == "codex:gpt-x" and "PROBLEM WITH YOUR EARLIER REPLY" in backend.calls[1]["prompt"]
    always_bad = ScriptedBackend(lambda *a: {"question": "", "answer": "No question."})
    with pytest.raises(BackendError, match="could not be used"):
        hq.propose(always_bad, book, manifest, store, h, Settings())


def test_the_check_is_the_one_the_books_questions_get(book):
    store, manifest = empty(), book.manifest()
    q = hq.add(store, manifest, marked(book, store), "What reached Gannet Rock a little after noon?", "The supply boat.", "model")
    seen = {}

    def reply(role, system, prompt, schema):
        seen.update(role=role, system=system, prompt=prompt)
        return {"prompts": [{"id": q["id"], "supported_by_section": True, "accepts_paraphrase": True, "answerable_from_section": True,
                             "does_not_give_away_answer": seen.get("ok", True), "stands_alone": True, "problem": "The question quotes its answer."}],
                "sections": []}

    verdict = hq.check(ScriptedBackend(reply), book, manifest, q, Settings(checker_model="opus"))
    assert verdict["passed"] and verdict["problem"] == "" and seen["role"] == "check" and "checker" in seen["system"].lower()
    assert f"QUESTION [{q['id']}]" in seen["prompt"] and "Reference answer: The supply boat." in seen["prompt"]
    seen["ok"] = False
    failed = hq.check(ScriptedBackend(reply), book, manifest, q, Settings())
    assert not failed["passed"] and failed["problem"] == "The question quotes its answer." and failed["criteria"]["does_not_give_away_answer"] is False
    assert not hq.check(ScriptedBackend(lambda *a: {"prompts": [], "sections": []}), book, manifest, q, Settings())["passed"]


# --------------------------------------------------------------------------
# The app


class Stand:
    """The stand-in model of a test app, which can be told to fail checks, to fail outright, or to wait."""

    def __init__(self) -> None:
        self.script = Script()
        self.fail_check = False
        self.error: Exception | None = None
        self.gate = threading.Event()
        self.gate.set()
        self.models: list[str | None] = []

    def backend(self, settings: Settings) -> ScriptedBackend:
        def reply(role: str, system: str, prompt: str, schema: dict) -> dict:
            self.gate.wait(10)
            if self.error:
                raise self.error
            out = self.script.reply(role, system, prompt, schema)
            if role == "check" and self.fail_check and "hq" in prompt:
                for p in out["prompts"]:
                    p.update(stands_alone=False, problem="It points at the page.")
            return out

        backend = ScriptedBackend(reply)
        complete = backend.complete

        def tracked(**kwargs) -> Completion:
            self.models.append(kwargs.get("model"))
            return complete(**kwargs)

        backend.complete = tracked  # type: ignore[method-assign]
        return backend


@pytest.fixture
def app(tmp_path):
    stand = Stand()
    jobs = web.Jobs(tmp_path / "data", backend_factory=stand.backend)
    job = jobs.create("fixture.epub", FIXTURE.read_bytes(), web.from_options(density="short"))
    for _ in range(400):
        if jobs.get(job["id"])["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert jobs.get(job["id"])["status"] == "done"
    return jobs, job["id"], stand


def settle(jobs, jid) -> dict:
    for _ in range(400):
        state = jobs.questions_state(jid)
        if not state["pending"]:
            return state
        time.sleep(0.02)
    raise AssertionError("the questions were not written")


def highlight(jobs, jid, chapter, start, end, note=None) -> dict:
    made = {}
    jobs.notes_do(jid, lambda st, bk: made.update(notes.add_highlight(st, bk, chapter, start, end, note)))
    return made


def test_a_question_is_written_in_the_background_reviewed_and_asked_at_the_checkpoint(app):
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    h = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9, "The point of it.")
    stand.gate.clear()                          # the model takes its time
    state = jobs.propose(jid, [h["id"]])
    assert state["pending"] == [h["id"]] and state["questions"] == []
    stand.gate.set()
    q = settle(jobs, jid)["questions"][0]
    assert q["status"] == "proposed" and q["highlight"] == h["id"] and "steered by the note" in q["question"] and q["by"] == "model"
    assert jobs.manifest(jid)["sections"] == jobs.book(jid).manifest()["sections"]   # a proposal is not asked anywhere yet
    another = settle(jobs, jobs.propose(jid, [h["id"]]) and jid)["questions"][1]
    assert another["question"] != q["question"]  # asked again: about something else
    jobs.discard_question(jid, another["id"])
    jobs.change_question(jid, q["id"], {"question": "What is the point of it?", "accept": True})
    q = jobs.questions_state(jid)["questions"][0]
    assert (q["status"], q["ask"], q["question"], q["by"]) == ("accepted", "checkpoint", "What is the point of it?", "you")
    asked = next(s for s in jobs.manifest(jid)["sections"] if s["id"] == section["id"])["prompts"]
    assert asked[-1] == {"id": q["id"], "prompt": "What is the point of it?", "answer": q["answer"], "hq": True}
    # answered at the checkpoint, its grade is kept like any other, and it becomes a practice card
    read = jobs.reading_state(jid)
    read.update(position={"chapter": section["chapter"], "word": section["end"], "mode": "page"},
                grades={q["id"]: {"first": "correct", "at": "2026-12-01T10:00:00"}},
                x_attempts=[{"id": q["id"], "at": "2026-12-01T10:00:00", "grade": "correct", "text": "the point", "where": "read"}])
    assert jobs.save_reading(jid, read)["grades"][q["id"]]["first"] == "correct"
    assert q["id"] in jobs.cards(jid)["cards"]
    shown = next(i for c in jobs.notebook(jid)["chapters"] for s in c["sections"] for i in s["items"] if i["type"] == "highlight")
    assert shown["questions"][0]["first"] == "correct" and shown["questions"][0]["attempts"][0]["text"] == "the point"
    assert all(a["id"] != q["id"] for c in jobs.notebook(jid)["chapters"] for s in c["sections"] for a in s["answers"])
    assert jobs.notebook(jid)["counts"]["questions"] == 1 and "- **What is the point of it?** (recalled)" in notes.markdown(jobs.notebook(jid))


def test_a_question_for_a_section_already_read_goes_straight_to_practice(app):
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    read = jobs.reading_state(jid)
    read.update(position={"chapter": section["chapter"], "word": section["end"], "mode": "page"},
                x_furthest={"chapter": section["chapter"], "word": section["end"]})
    jobs.save_reading(jid, read)
    h = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9)
    q = jobs.write_question(jid, h["id"], "My own question?", "My own answer.")["questions"][0]
    assert (q["status"], q["ask"], q["by"]) == ("accepted", "practice", "you")
    assert jobs.manifest(jid)["sections"] == jobs.book(jid).manifest()["sections"]   # not asked at the checkpoint
    assert jobs.cards(jid)["cards"][q["id"]]["state"] == "new"
    practice = jobs.practice(jid)
    assert practice["counts"]["new"] == 1 and practice["card"]["question"] == "My own question?"
    assert practice["card"]["go"] == f"{section['chapter']}:{section['start'] + 3}"
    assert any(run["h"] for paragraph in practice["card"]["passage"] for run in paragraph)   # its highlight, in its paragraph
    jobs.practice_answer(jid, q["id"], True, "")
    assert jobs.cards(jid)["cards"][q["id"]]["state"] == "learned"
    jobs.discard_question(jid, q["id"])         # a discarded question takes its card with it
    assert q["id"] not in jobs.cards(jid)["cards"] and jobs.practice(jid)["counts"]["cards"] == 0


def test_accepted_without_review_a_question_is_checked_first_and_held_when_it_fails(app):
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    h = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9)
    jobs.ui_settings({"add_model": "codex:gpt-x", "add_checker_model": "opus"})
    jobs.propose(jid, [h["id"]], auto=True)
    q = settle(jobs, jid)["questions"][0]
    assert (q["status"], q["ask"]) == ("accepted", "checkpoint") and q["checked"]["passed"]
    assert stand.models[-2:] == ["codex:gpt-x", "opus"]   # the Add a book defaults: one writes, the other checks
    stand.fail_check = True
    jobs.propose(jid, [h["id"]], auto=True)
    held = settle(jobs, jid)["questions"][1]
    assert held["status"] == "held" and held["problem"] == "It points at the page." and held["ask"] is None
    assert hq.ids(notes.load(jobs.folder(jid))) == {q["id"]}
    jobs.change_question(jid, held["id"], {"accept": True})   # the reader may still take it
    assert jobs.questions_state(jid)["questions"][1]["status"] == "accepted"


def test_the_notebook_never_shows_a_question_as_neither_written_nor_being_written(app):
    """The notebook page stops looking for a question when none is pending, so what is pending comes with the
    entries, as of the same moment. Read apart, a question finished in between was missing from both."""
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    h = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9)
    asked, lost, done = [0], [], threading.Event()

    def look() -> None:
        while not done.is_set() and not lost:
            wanted = asked[0]               # every one of these is pending or written by now
            try:
                seen = jobs.notebook(jid)
                shown = next(i for c in seen["chapters"] for s in c["sections"] for i in s["items"] if i["type"] == "highlight")
                if not seen["asking"]["pending"] and len(shown["questions"]) < wanted:
                    lost.append((wanted, len(shown["questions"])))
            except Exception as exc:        # a fault here must fail the test, not end the thread unseen
                lost.append(repr(exc))

    watcher = threading.Thread(target=look)
    watcher.start()
    try:
        for _ in range(25):
            jobs.propose(jid, [h["id"]])
            asked[0] += 1
            settle(jobs, jid)
    finally:
        done.set()
        watcher.join()
    assert not lost and len(jobs.questions_state(jid)["questions"]) == 25


def test_questions_for_every_highlight_that_has_none_and_a_failure_is_said(app):
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    c, at = section["chapter"], section["start"]
    hs = [highlight(jobs, jid, c, at + n, at + n + 4) for n in (2, 12, 22)]
    other = next(s for s in jobs.manifest(jid)["sections"] if s["chapter"] != c and s["prompts"])
    elsewhere = highlight(jobs, jid, other["chapter"], other["start"] + 2, other["start"] + 6)
    jobs.write_question(jid, hs[0]["id"], "Mine?", "Mine.")
    state = jobs.propose(jid, None, chapter=c)
    assert state["batch"]["total"] == 2           # the chapter's highlights without a question
    done = settle(jobs, jid)
    assert done["batch"] == {"total": 2, "done": 2, "running": False, "error": ""}
    assert sorted(q["highlight"] for q in done["questions"]) == sorted(h["id"] for h in hs)
    assert settle(jobs, jobs.propose(jid, None) and jid)["batch"]["total"] == 1   # the book: only the one elsewhere is left
    assert jobs.propose(jid, None)["batch"]["total"] == 0
    stand.error = BackendError("Claude Code reported an error: no such model")
    failed = settle(jobs, jobs.propose(jid, [elsewhere["id"]]) and jid)
    assert failed["failed"] == {elsewhere["id"]: "Claude Code reported an error: no such model"}
    stand.error = None
    assert settle(jobs, jobs.propose(jid, [elsewhere["id"]]) and jid)["failed"] == {}   # asking again clears it
    with pytest.raises(web.ApiError):
        jobs.propose(jid, ["h00000000"])


def test_removing_a_highlight_keeps_or_removes_its_questions(app):
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    h = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9)
    kept = jobs.write_question(jid, h["id"], "Kept?", "Kept.")["questions"][0]
    settle(jobs, jobs.propose(jid, [h["id"]]) and jid)
    store = jobs.remove_marked(jid, h["id"], keep_questions=True)
    assert store["highlights"] == [] and [(q["id"], q["highlight"]) for q in store["questions"]] == [(kept["id"], None)]
    item = next(i for c in jobs.notebook(jid)["chapters"] for s in c["sections"] for i in s["items"])
    assert item["type"] == "question" and item["text"] == h["text"] and item["question"] == "Kept?"
    assert f"> {h['text']}" in notes.markdown(jobs.notebook(jid))
    again = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9)
    jobs.write_question(jid, again["id"], "Goes?", "Goes.")
    assert [q["id"] for q in jobs.remove_marked(jid, again["id"], keep_questions=False)["questions"]] == [kept["id"]]


def test_the_exports_hold_the_questions_from_highlights_and_are_refreshed_when_they_change(app):
    from jsonschema import Draft202012Validator
    jobs, jid, stand = app
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    h = highlight(jobs, jid, section["chapter"], section["start"] + 3, section["start"] + 9)
    assert "exports_stale" not in jobs.get(jid)
    q = jobs.write_question(jid, h["id"], "What is asked from the highlight?", "This.")["questions"][0]
    assert jobs.get(jid)["exports_stale"]   # a count of the changes since the files were built
    server = web.make_server(jobs.data, "127.0.0.1", 0, backend_factory=stand.backend, claude={"ok": True, "version": "t"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/jobs/{jid}"
    try:
        def get(path):
            with urllib.request.urlopen(urllib.request.Request(base + path, headers={"X-Ebook-Processor": "1"}), timeout=30) as r:
                return r.read()
        doc = json.loads(get("/files/fixture.questions.json"))     # asked for while stale: built again first
        mine = doc["questions"][-1]
        assert (mine["id"], mine["origin"], mine["question"], mine["highlight"]) == (q["id"], "highlight", q["question"], h["text"])
        assert mine["section"] == section["id"] and mine["checkpoint"] and mine["status"] == "unchecked" and "from::highlight" in mine["tags"]
        assert mine["passage"] and h["text"].split()[0] in mine["passage"][0]["text"]
        schema = json.loads((Path(pipeline.__file__).parent / "schema" / "questions.schema.json").read_text(encoding="utf-8"))
        assert not [e.message for e in Draft202012Validator(schema).iter_errors(doc)]
        assert q["question"] in get("/files/fixture.cards.tsv").decode()
        assert json.loads(get(""))["report"]["counts"]["from_highlights"] == 1 and "exports_stale" not in json.loads(get(""))
        # the routes, once through
        def call(method, path, body=None):
            request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                             headers={"X-Ebook-Processor": "1"})
            try:
                with urllib.request.urlopen(request, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())
        assert call("POST", "/questions/propose", {"highlight": h["id"]})[0] == 200
        for _ in range(400):                    # the server has its own app; ask it, not ``jobs``
            state = call("GET", "/questions")[1]
            if not state["pending"]:
                break
            time.sleep(0.02)
        proposed = state["questions"][-1]
        assert proposed["status"] == "proposed"
        assert call("PUT", f"/questions/{proposed['id']}", {"answer": "A better answer."})[1]["questions"][-1]["answer"] == "A better answer."
        assert call("DELETE", f"/questions/{proposed['id']}")[0] == 200 and len(call("GET", "/questions")[1]["questions"]) == 1
        assert call("POST", "/questions", {"highlight": h["id"], "question": "", "answer": "x"})[0] == 400
        assert call("POST", "/questions/propose", {})[0] == 400 and call("PUT", "/questions/hq00000000", {"accept": True})[0] == 400
        assert call("POST", "/questions/propose", {"book": True})[1]["batch"]["total"] == 0
        assert call("POST", "/questions/stop")[0] == 200
        status, kept = call("DELETE", f"/notes/item/{h['id']}?questions=keep")
        assert status == 200 and kept["highlights"] == [] and kept["questions"][0]["highlight"] is None
    finally:
        server.shutdown()
        server.server_close()
    assert zipfile.is_zipfile(jobs.folder(jid) / "fixture.anki.apkg")
