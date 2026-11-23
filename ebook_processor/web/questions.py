"""The questions' part of the local web app: changing a book's own questions, and the questions written from
highlights.

This is one of the parts the class ``Jobs`` of ``server.py`` is put together from. Its methods use the fields that
``Jobs.__init__`` makes, and the methods of the other parts, through ``self``. It does not import ``server.py``.
"""

from __future__ import annotations

import copy
import threading
import traceback
from http import HTTPStatus
from typing import Any

from .. import editing, notes, reading, srs
from .. import highlight_questions as hq
from .. import steer as steering
from ..llm import BackendCancelled, BackendError, UsageLimitError
from ..outputs import load_report
from ..runner import PipelineError, change_run, open_run
from ..settings import Settings, SettingsError, from_options
from ..steer import Steer
from ..store import write_json
from .common import ApiError, _text

# The locks, as in every part of ``Jobs``: a book's lock (``self.booked(id)``) is taken first, the library's lock
# (``self.lock``) second and for moments only, never across a call to a model or while a book's files are built. The
# whole rule is in ``Jobs.__init__`` (server.py).


class Questions:
    """What ``Jobs`` does with a book's questions (see editing.py and highlight_questions.py)."""

    # ---- changing a book's own questions (see editing.py)

    def _editable(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if not self.editable(job_id):
                raise ApiError(HTTPStatus.CONFLICT, "The questions of this book cannot be changed: it has no finished run made here.")
            return self.jobs[job_id]

    def _edit(self, job_id: str, change: Any, build: bool = True) -> Any:
        """Apply ``change(book, state)`` to the book's run. With ``build`` every file is written again, so the
        reader, Practice and the downloads hold the questions as they now are. The book's lock is held; the
        library's is not, so the rest of the app goes on while the files are written."""
        with self.booked(job_id):
            job = self._editable(job_id)
            stale = job.get("exports_stale")
            try:
                result = change_run(self.folder(job_id), change, build)
            except editing.EditError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            except PipelineError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            if build:
                report = load_report(self.folder(job_id), job["file"])
                self._built(job, stale, report["counts"] if report is not None else None)
            return result

    def _look(self, job_id: str, read: Any) -> Any:
        """``read(book, state)`` on the book's run as it is now, with no lock held while it runs: for a call to a
        model, which takes seconds or minutes. Nothing ``read`` changes is kept."""
        with self.booked(job_id):
            self._editable(job_id)
            try:
                book, state = open_run(self.folder(job_id))
            except PipelineError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        try:
            return read(book, state)
        except editing.EditError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def _models(self, job_id: str, roles: list[str]) -> tuple[Settings, Any, Steer | None]:
        """The run's settings, a backend for a call the reader asked for, and what the reader wanted from the book."""
        job = self.get(job_id)
        settings = Settings.from_json(job["settings"])
        return settings, self._backend(settings, roles), self._run_steer(job)

    def book_question(self, job_id: str, qid: str) -> dict[str, Any]:
        """A question as the editor shows it, with whether the reader has answered or practised it."""
        shown = self._edit(job_id, lambda book, state: editing.view(book, state, qid), build=False)
        state = reading.load_state(self.folder(job_id)) or {}
        shown["answered"] = qid in (state.get("grades") or {}) or qid in self.cards(job_id)["cards"]
        return shown

    def section_paragraphs(self, job_id: str, sid: str) -> dict[str, Any]:
        def read(book: Any, state: dict[str, Any]) -> dict[str, Any]:
            section = editing.section_of(state, sid)
            return {"section": {"id": section["id"], "label": section["label"]},
                    "paragraphs": editing.paragraphs(book, state, sid)}
        return self._edit(job_id, read, build=False)

    def edit_question(self, job_id: str, qid: str, raw: dict[str, Any]) -> dict[str, Any]:
        self._edit(job_id, lambda book, state: editing.edit(state, book, qid, raw))
        return self.book_question(job_id, qid)

    def restore_question(self, job_id: str, qid: str) -> dict[str, Any]:
        self._edit(job_id, lambda book, state: editing.restore(state, qid))
        return self.book_question(job_id, qid)

    def add_question(self, job_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        """A question of the reader's own. ``passage`` says how it gets its paragraphs when none are given: the
        whole section, or the model's choice, which with ``model_review`` comes back for the reader to confirm
        (``{"review": [...]}``) before anything is kept."""
        mode = raw.get("passage") if raw.get("passage") in editing.PASSAGES else "section"
        if raw.get("supporting_paragraphs") is None and mode in ("model", "model_review"):
            settings, backend, _ = self._models(job_id, ["draft"])
            chosen = self._asked(lambda: self._look(
                job_id, lambda book, state: editing.choose_passage(backend, book, state, raw.get("section"), raw, settings)))
            if mode == "model_review":
                return {"review": chosen}
            raw = {**raw, "supporting_paragraphs": chosen}
        made = self._edit(job_id, lambda book, state: copy.deepcopy(editing.add(state, book, raw.get("section"), raw)))
        with self.lock:  # past its section already: a card from the start, as a question from a highlight would be
            manifest = self.book(job_id).manifest()
            section = next((s for s in manifest["sections"] if s["id"] == made["section"]), None)
            if section is not None and hq.passed(reading.load_state(self.folder(job_id)), manifest, section):
                cards = self.cards(job_id)
                cards["cards"].setdefault(made["id"], srs.new_card())
                write_json(self.folder(job_id) / srs.FILE, cards)
        return self.book_question(job_id, made["id"])

    def _forget_answers(self, job_id: str, qid: str) -> None:
        """Take a question's grade and its card away (the caller holds the lock). What was answered stays as history."""
        folder = self.folder(job_id)
        cards = self.cards(job_id)  # first, so that answers already given are not taken in again as new ones
        if cards["cards"].pop(qid, None) is not None:
            write_json(folder / srs.FILE, cards)
        state = reading.load_state(folder)
        if state is not None:
            was = state.get("grades", {}).get(qid)
            if was:  # a page that still shows this grade must not bring it back with its next save
                state.setdefault("x_restarted", {})[qid] = was.get("at")
            for part in ("grades", "x_cleared", "x_previewed"):
                state.get(part, {}).pop(qid, None)
            write_json(folder / reading.STATE_FILE, state)

    def delete_question(self, job_id: str, qid: str) -> dict[str, Any]:
        with self.booked(job_id):
            self._edit(job_id, lambda book, state: editing.delete(state, qid))
            with self.lock:
                self._forget_answers(job_id, qid)
        return {"deleted": qid}

    def restart_question(self, job_id: str, qid: str) -> dict[str, Any]:
        """Start a question over: it counts as never answered. It is asked again at its checkpoint, or, when the
        reader is past that or the question was promoted from an ephemeral one, it is a new card in Practice."""
        with self.booked(job_id):
            shown = self._edit(job_id, lambda book, state: editing.view(book, state, qid), build=False)
            with self.lock:
                self._forget_answers(job_id, qid)
                manifest = self.book(job_id).manifest()
                section = next((s for s in manifest["sections"] if s["id"] == shown["section"]["id"]), None)
                # a question made of an ephemeral one is not asked at a checkpoint: it is a card wherever the reader is
                if shown["kept"] and section is not None and (
                        shown.get("promoted_from") or hq.passed(reading.load_state(self.folder(job_id)), manifest, section)):
                    cards = self.cards(job_id)
                    cards["cards"][qid] = srs.new_card()
                    write_json(self.folder(job_id) / srs.FILE, cards)
        return self.book_question(job_id, qid)

    def check_question(self, job_id: str, qid: str) -> dict[str, Any]:
        settings, backend, steer = self._models(job_id, ["check"])

        def ask(book: Any, state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            return editing.wording_of(editing.find(state, qid)), editing.check(backend, book, state, qid, settings, steer)
        was, verdict = self._asked(lambda: self._look(job_id, ask))  # the model is asked with no lock held
        self._edit(job_id, lambda book, state: editing.keep_verdict(state, qid, verdict, was), build=False)
        return self.book_question(job_id, qid)

    def rewrite_question(self, job_id: str, qid: str, raw: dict[str, Any]) -> dict[str, Any]:
        """A model's rewrite of a question to the reader's wish, for the reader to take or change. ``qid`` may be
        an ephemeral question's: it is then rewritten as the practice question the reader is making of it."""
        settings, backend, steer = self._models(job_id, ["draft"])
        draft = raw.get("draft") if isinstance(raw.get("draft"), dict) else None
        return self._asked(lambda: self._look(
            job_id, lambda book, state: editing.rewrite(backend, book, state, qid, raw.get("wish"), settings, steer, draft)))

    # ---- the graduated pathway: an ephemeral question kept for Practice (see pathway.py)

    def ephemeral_question(self, job_id: str, eid: str) -> dict[str, Any]:
        """An ephemeral question as the window that promotes it shows it."""
        return self._edit(job_id, lambda book, state: editing.ephemeral_view(book, state, eid), build=False)

    def promote_ephemeral(self, job_id: str, eid: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Keep an ephemeral question for Practice, as it is written or with the wording ``raw`` gives. It is a new
        card at once. The app's reader does not ask it at the section's checkpoint, which stays as it was."""
        with self.booked(job_id):
            made = self._edit(job_id, lambda book, state: copy.deepcopy(editing.promote(state, book, eid, raw)))
            with self.lock:
                cards = self.cards(job_id)
                cards["cards"].setdefault(made["id"], srs.new_card())
                write_json(self.folder(job_id) / srs.FILE, cards)
        return self.book_question(job_id, made["id"])

    # ---- questions from highlights (see highlight_questions.py)

    def question_settings(self) -> Settings:
        """The models that write and check these questions: the Add a book defaults of the settings window."""
        ui = self.ui_settings()
        options = {k: ui.get("add_" + k) or None for k in ("model", "checker_model", "effort", "forms")}
        try:
            return from_options(**options)
        except SettingsError:
            return from_options(model=options["model"], checker_model=options["checker_model"])

    def _asking(self, job_id: str) -> dict[str, Any]:
        return self.asks.setdefault(job_id, {"pending": {}, "failed": {}, "batch": None, "cancel": threading.Event()})

    def _writing(self, job_id: str) -> dict[str, Any]:
        """What is being written right now (the caller holds the lock). A page stops looking for a question when
        this says none is pending, so it must be read under the same lock as the store the page draws from: read
        apart, a question finished between the two is in neither."""
        asking = self._asking(job_id)
        return {"pending": sorted(asking["pending"]), "failed": dict(asking["failed"]),
                "batch": dict(asking["batch"]) if asking["batch"] else None}

    def questions_state(self, job_id: str) -> dict[str, Any]:
        """The book's questions from highlights, and what is being written right now."""
        self.get(job_id)
        with self.lock:
            store = notes.load(self.folder(job_id))
            return {"questions": hq.questions(store), **self._writing(job_id)}

    def propose(self, job_id: str, highlights: list[str] | None, auto: bool = False, chapter: int | None = None,
                instruction: str = "") -> dict[str, Any]:
        """Have a question written for each of the highlights, in the background. With ``highlights`` None it is
        every highlight of the book (or of ``chapter``) that has no question yet: the batch. ``instruction`` is
        the reader's wish for this one question."""
        self.book(job_id)
        with self.lock:
            store = notes.load(self.folder(job_id))
            asking = self._asking(job_id)
            batch = highlights is None
            if batch:
                if asking["batch"] and asking["batch"]["running"]:
                    raise ApiError(HTTPStatus.CONFLICT, "questions are already being written for this book")
                have = {q["highlight"] for q in hq.questions(store)}
                highlights = [h["id"] for h in store["highlights"]
                              if h["id"] not in have and (chapter is None or h["chapter"] == chapter)]
            known = {h["id"] for h in store["highlights"]}
            todo = [h for h in highlights if h in known and h not in asking["pending"]]
            if not batch and not todo and not any(h in asking["pending"] for h in highlights):
                raise ApiError(HTTPStatus.NOT_FOUND, "no such highlight")
            if todo and not asking["pending"]:
                asking["cancel"] = threading.Event()  # a stop that ended earlier work must not end this
            if batch:
                asking["batch"] = {"total": len(todo), "done": 0, "running": bool(todo), "error": ""}
            for hid in todo:
                asking["pending"][hid] = batch
                asking["failed"].pop(hid, None)
            cancel = asking["cancel"]
        for hid in todo:
            self.writers.submit(self._propose_one, job_id, hid, auto, cancel, instruction)
        return self.questions_state(job_id)

    def propose_asked(self, job_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """Have written what a page asks for: a question for one highlight, or for every highlight of a chapter or
        of the book that has none yet."""
        if isinstance(r.get("highlight"), str):
            return self.propose(job_id, [r["highlight"]], auto=r.get("auto") is True,
                                instruction=_text(r.get("instruction"), steering.INSTRUCTION_MAX))
        chapter = r.get("chapter")
        if chapter is not None and (not isinstance(chapter, int) or isinstance(chapter, bool)):
            raise ApiError(HTTPStatus.BAD_REQUEST, "'chapter' must be a number")
        if chapter is None and r.get("book") is not True:
            raise ApiError(HTTPStatus.BAD_REQUEST, "name a highlight, a chapter or the book")
        return self.propose(job_id, None, chapter=chapter)

    def stop_questions(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            asking = self._asking(job_id)
            asking["cancel"].set()
            if asking["batch"]:
                asking["batch"]["running"] = False
        return self.questions_state(job_id)

    def _propose_one(self, job_id: str, hid: str, auto: bool, cancel: threading.Event, instruction: str = "") -> None:
        folder = self.folder(job_id)
        asking = self._asking(job_id)
        try:
            if cancel.is_set():
                return
            book = self.book(job_id)
            settings = self.question_settings()
            with self.lock:
                steer = self.steer_for(job_id)
                store = notes.load(folder)
                highlight = next((copy.deepcopy(h) for h in store["highlights"] if h["id"] == hid), None)
                manifest = hq.with_questions(book.manifest(), store)
            if highlight is None:
                return
            backend = self._backend(settings, ["draft", "check"] if auto else ["draft"], cancel)
            made = hq.propose(backend, book, manifest, store, highlight, settings, steer, instruction)
            with self.lock:
                store = notes.load(folder)
                # the highlight may have grown into another meanwhile: the question goes to whichever holds its start
                now_h = next((h for h in store["highlights"] if h["id"] == hid), None) or next(
                    (h for h in store["highlights"] if h["chapter"] == highlight["chapter"] and h["start"] <= highlight["start"] < h["end"]), None)
                if now_h is None or cancel.is_set():
                    return
                q = hq.add(store, manifest, now_h, made["question"], made["answer"], "model", made["model"], made["form"])
                write_json(folder / notes.FILE, store)
                asked = copy.deepcopy(q)
            if auto:  # accepted without the reader seeing it: checked first, and held back when it fails
                verdict = hq.check(backend, book, manifest, asked, settings, steer)
                with self.lock:
                    store = notes.load(folder)
                    q = next((x for x in hq.questions(store) if x["id"] == asked["id"]), None)
                    if q is not None and q["status"] == "proposed":
                        q["checked"] = verdict
                        if verdict["passed"]:
                            self._accept(job_id, store, q["id"])
                        else:
                            q.update(status="held", problem=verdict["problem"])
                        write_json(folder / notes.FILE, store)
        except BackendCancelled:
            pass
        except UsageLimitError as exc:  # no point in going on: the rest of a batch is given up
            with self.lock:
                asking["failed"][hid] = str(exc)
                if asking["batch"] and asking["pending"].get(hid):
                    asking["batch"]["error"] = str(exc)
                    cancel.set()
        except (BackendError, hq.QuestionError, ApiError) as exc:
            with self.lock:
                asking["failed"][hid] = str(exc)
        except Exception as exc:  # noqa: BLE001 a fault of the tool: say so instead of leaving the request "being written"
            traceback.print_exc()
            with self.lock:
                asking["failed"][hid] = f"Unexpected error: {exc!r}"
        finally:
            with self.lock:
                in_batch = asking["pending"].pop(hid, False)
                if in_batch and asking["batch"]:
                    asking["batch"]["done"] += 1
                    if asking["batch"]["done"] >= asking["batch"]["total"] or cancel.is_set():
                        asking["batch"]["running"] = False

    def _accept(self, job_id: str, store: dict[str, Any], qid: str) -> dict[str, Any]:
        """Accept a question (the caller holds the lock and writes the store). One that goes straight to Practice
        gets its card now; one for a checkpoint gets it when it is answered there."""
        q = hq.accept(store, self.book(job_id).manifest(), reading.load_state(self.folder(job_id)), qid)
        if q["ask"] == "practice":
            cards = self.cards(job_id)
            if q["id"] not in cards["cards"]:
                cards["cards"][q["id"]] = srs.new_card()
                write_json(self.folder(job_id) / srs.FILE, cards)
        self._exports_stale(job_id)
        return q

    def _exports_stale(self, job_id: str) -> None:
        """The deck and the other exports no longer hold the book's questions from highlights as they are now."""
        job = self.jobs[job_id]
        if not self.imported(job):  # a number, not a yes: a build must see a change made while it ran
            job["exports_stale"] = int(job.get("exports_stale") or 0) + 1
            self._store(job)

    def question_do(self, job_id: str, change: Any) -> dict[str, Any]:
        """Change the book's questions from highlights with ``change(store)``, and return their state."""
        self.book(job_id)
        with self.lock:
            store = notes.load(self.folder(job_id))
            try:
                change(store)
            except hq.QuestionError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            write_json(self.folder(job_id) / notes.FILE, store)
        return self.questions_state(job_id)

    def write_question(self, job_id: str, hid: Any, question: Any, answer: Any, form: Any = None) -> dict[str, Any]:
        """The reader's own question for a highlight, of any form: accepted as it is written."""
        def change(store: dict[str, Any]) -> None:
            highlight = next((h for h in store["highlights"] if h["id"] == hid), None)
            if highlight is None:
                raise hq.QuestionError("no such highlight")
            q = hq.add(store, self.book(job_id).manifest(), highlight, question, answer, "you", form=form)
            self._accept(job_id, store, q["id"])
        return self.question_do(job_id, change)

    def change_question(self, job_id: str, qid: str, request: dict[str, Any]) -> dict[str, Any]:
        def change(store: dict[str, Any]) -> None:
            if "question" in request or "answer" in request or "form" in request:
                hq.reword(store, qid, request.get("question"), request.get("answer"), request.get("form"))
                if hq.find(store, qid)["status"] == "accepted":
                    self._exports_stale(job_id)
            if request.get("accept") is True:
                self._accept(job_id, store, qid)
        return self.question_do(job_id, change)

    def discard_question(self, job_id: str, qid: str) -> dict[str, Any]:
        def change(store: dict[str, Any]) -> None:
            self._forget(job_id, [hq.discard(store, qid)])
        return self.question_do(job_id, change)

    def _forget(self, job_id: str, gone: list[dict[str, Any]]) -> None:
        """Questions that were removed take their practice cards with them."""
        accepted = [q["id"] for q in gone if q["status"] == "accepted"]
        if not accepted:
            return
        cards = self.cards(job_id)
        if any(cards["cards"].pop(qid, None) is not None for qid in accepted):
            write_json(self.folder(job_id) / srs.FILE, cards)
        self._exports_stale(job_id)

    def remove_marked(self, job_id: str, item: str, keep_questions: bool) -> dict[str, Any]:
        """Remove a highlight or a note. A highlight's accepted questions stay, without it, when asked to."""
        def change(store: dict[str, Any], book: Any) -> None:
            notes.remove_item(store, item)
            self._forget(job_id, hq.highlight_removed(store, item, keep_questions))
        return self.notes_do(job_id, change)
