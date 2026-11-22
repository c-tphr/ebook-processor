"""The local web app: pick an EPUB, watch the run, review the questions, download the results.

It is a small HTTP server from the standard library plus its own pages (the files in ``static/``). It listens on this computer only
(127.0.0.1) unless told otherwise, and runs one book at a time.

Each run lives in its own folder under the data folder:

    <data>/<run id>/job.json           what the page shows about the run
    <data>/<run id>/input/Book.epub    the uploaded book
    <data>/<run id>/Book.*             the outputs (see ebook_processor/outputs.py)
    <data>/<run id>/work/              the run's state
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, assist, bookfile, editing, notes, process, reading, sources, srs
from .. import forms as qforms
from .. import highlight_questions as hq
from .. import steer as steering
from ..llm import BackendCancelled, BackendError, ClaudeCodeBackend, CodexBackend, Completion, UsageLimitError, available_models
from ..outputs import load_report, stem_of
from ..pipeline import STATE_FILE, now, write_json
from ..prep.epub import EpubError, load_book_bytes
from ..prep.limits import MAX_PROMPTS
from ..runner import (Cancelled, PipelineError, change_run, default_backend, rebuild, review_data, run_book, trial_dir,
                      try_chapter, work_dir)
from ..settings import DENSITIES, MAX_QUESTIONS, Settings, SettingsError, from_options
from ..steer import Steer, SteerError
from ..vault import SOURCES as SOURCE_KINDS
from ..vault import Conflict, Missing, Vault, VaultError, clean_title, compose

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 300 * 1024 * 1024
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
ACTIVE = ("queued", "running")
EVENTS_KEPT = 300
PAGES = {"index.html": "text/html; charset=utf-8", "reader.html": "text/html; charset=utf-8",
         "settings.js": "text/javascript; charset=utf-8", "forms.js": "text/javascript; charset=utf-8",
         "notes.js": "text/javascript; charset=utf-8", "editor.js": "text/javascript; charset=utf-8"}   # the app's own files, by content type
SETTINGS_FILE = "reader-settings.json"  # what the settings window keeps, for every page of the app
LONG_SETTINGS = ("steer_about", "steer_style")  # the reader's own texts: longer than any other setting
WAITING = ("asking", "tried")  # a run that waits for its reader: to confirm a reading, or after a trial chapter
NOTES_FOLDER = "ebook-processor-notes"  # the notes folder's name where none was chosen: beside the data folder
CSRF_HEADER = "X-Ebook-Processor"  # a page of another site cannot send this header, so it cannot start runs


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def void(_: Any) -> None:
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "book"


def _plain_name(name: str) -> str:
    """A file name that is safe to write, with the ending it came with."""
    return re.sub(r"[^\w .,()'&+-]", "_", Path(name.replace("\\", "/")).name).strip(" .") or "original"


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w .,()'&+-]", "_", name).strip(" .") or "book.epub"
    return name if name.lower().endswith(".epub") else name + ".epub"


def _text(value: Any, limit: int = steering.TEXT_MAX) -> str:
    try:
        return steering.clean(value, limit)
    except SteerError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc


def suggested_chapter(chapters: list[dict[str, Any]]) -> int | None:
    """The chapter to try a steer on: the first that is long enough to be a real one."""
    real = next((c for c in chapters if c["words"] >= 1500), None) or max(chapters, key=lambda c: c["words"], default=None)
    return real["index"] if real else None


def look(name: str, data: bytes) -> dict[str, Any]:
    """What the Add form shows of an EPUB before anything is run: its length and its chapters."""
    try:
        book = load_book_bytes(data, _safe_name(name))
    except EpubError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"This file cannot be read as an EPUB: {exc}") from exc
    return {**_looked(book, book.title or stem_of(name)), "type": "book"}


def _looked(book: Any, title: str) -> dict[str, Any]:
    chapters = steering.chapter_list(book)
    return {"title": title, "words": sum(c["words"] for c in chapters), "chapters": chapters,
            "suggested": suggested_chapter(chapters)}


def _is_epub(name: str, data: bytes) -> bool:
    """An EPUB by its name, or by its bytes when the name says nothing this app knows."""
    return name.lower().endswith(".epub") or (not sources.is_source(name) and data[:2] == b"PK" and b"mimetypeapplication/epub+zip" in data[:100])


def _split_how(value: Any) -> str:
    return "none" if value == "none" else "auto"


def _whole(value: Any) -> bool | None:
    """How much of a page is taken: all of it, only its article, or as the app guesses."""
    return value if isinstance(value, bool) else None


def estimate(words: Any, settings: Settings, steered: bool) -> dict[str, Any]:
    if not isinstance(words, list) or len(words) > 5000 or not all(isinstance(w, int) and not isinstance(w, bool) and 0 <= w < 10**8 for w in words):
        raise ApiError(HTTPStatus.BAD_REQUEST, "'words' is the list of the chapters' lengths")
    out = steering.estimate(words, settings, steered)
    out["max_questions"] = settings.max_questions
    out["steered"] = steered
    out["firmware_limit"] = MAX_PROMPTS if out["questions"][1] > MAX_PROMPTS else None
    return out


class Jobs:
    """The runs on disk, and the one worker thread that processes them in turn."""

    def __init__(self, data: Path, backend_factory: Any = None):
        self.data = data
        self.data.mkdir(parents=True, exist_ok=True)
        self.notes_default = Path(os.path.abspath(self.data)).parent / NOTES_FOLDER
        self._vault: Vault | None = None
        self.filed: dict[str, tuple[int, int]] = {}  # book id -> what its file in the notes folder was made from
        self.lock = threading.RLock()
        self.turn = threading.Lock()  # held by the run that is being processed
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.stopping = threading.Event()  # set when the app ends: every model call still running gives up
        self.books: dict[str, tuple[int, reading.ReadingBook]] = {}
        self.backend_factory = backend_factory
        # questions being written from highlights: book id -> what is under way (see ``propose``)
        self.asks: dict[str, dict[str, Any]] = {}
        # files that are no EPUB, converted for the Add form and waiting for "Process": token -> what was sent
        self.held: dict[str, dict[str, Any]] = {}
        self.writers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="questions")
        for path in sorted(self.data.glob("*/job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not JOB_ID_RE.match(str(job.get("id", ""))) or job["id"] != path.parent.name:
                continue
            if job["status"] in ACTIVE:  # the server stopped while this ran
                job.update(status="stopped", message="The app was closed during this run. Press Continue.")
            self.jobs[job["id"]] = job

    # ---- reading

    def folder(self, job_id: str) -> Path:
        return self.data / job_id

    def get(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.match(job_id) or job_id not in self.jobs:
            raise ApiError(HTTPStatus.NOT_FOUND, "no such run")
        return self.jobs[job_id]

    def summary(self, job: dict[str, Any]) -> dict[str, Any]:
        out = {k: job.get(k) for k in ("id", "file", "title", "created", "updated", "status", "message", "stage",
                                       "done", "total", "counts", "words", "kind", "origin")}
        out["type"] = self.type_of(job)
        out["can_read"] = job["status"] == "done" and self.prepared(job["id"]).is_file()
        out["reading"] = reading.summary(reading.load_state(self.folder(job["id"]))) if out["can_read"] else None
        out["practice"] = srs.counts(self.cards(job["id"]), self.today()) if out["can_read"] else None
        marked = notes.load(self.folder(job["id"])) if out["can_read"] else None
        out["notes"] = {"highlights": len(marked["highlights"]),
                        "notes": len(marked["notes"]) + sum(bool(h["note"]) for h in marked["highlights"])} if marked else None
        return out

    # ---- practice (spaced repetition)

    today = staticmethod(_dt.date.today)

    def srs_settings(self) -> dict[str, Any]:
        path = self.data / "srs-settings.json"
        try:
            return srs.clean_settings(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)
        except ValueError:
            return srs.clean_settings(None)

    def cards(self, job_id: str) -> dict[str, Any]:
        """The book's cards, brought up to date with what was answered in the reader."""
        folder = self.folder(job_id)
        with self.lock:
            store = srs.load(folder)
            if srs.take_in(store, reading.load_state(folder), self.srs_settings(), self.today()):
                write_json(folder / srs.FILE, store)
        return store

    def practice(self, only: str | None = None) -> dict[str, Any]:
        """What to practise now, across every book or in one: the counts and the next card."""
        today, settings = self.today(), self.srs_settings()
        with self.lock:
            ids = [j["id"] for j in sorted(self.jobs.values(), key=lambda j: j["created"])
                   if j["status"] == "done" and self.prepared(j["id"]).is_file() and (only is None or j["id"] == only)]
        if only is not None and only not in ids:
            self.get(only)
            raise ApiError(HTTPStatus.CONFLICT, "this book cannot be practised yet")
        total = {"due": 0, "again": 0, "new": 0, "cards": 0, "next_due": None, "next_count": 0}
        stores, lists = {}, {"due": [], "new": [], "again": [], "first": [], "later": []}
        new_done = 0
        decks: list[tuple[str, dict[str, Any], Any, dict[str, Any]]] = []
        for job_id in ids:
            store = self.cards(job_id)
            if not store["cards"]:
                continue
            try:
                manifest = self.manifest(job_id)
            except ApiError:
                continue
            prompts = {p["id"]: (s, p) for s in manifest["sections"] for p in s["prompts"]}
            prompts.update(hq.practice_prompts(manifest, notes.load(self.folder(job_id))))  # cards from highlights
            decks.append((job_id, store, manifest, prompts))
        if only is None:  # the cards made from notes are practised with those of the books
            store = srs.load(self.data / assist.FOLDER)
            if store["cards"]:
                vault = self.vault()
                vault.refresh()
                with vault.lock:
                    decks.append((assist.DECK, store, None, assist.practice_prompts(vault, assist.load(self.data))))
        for job_id, store, manifest, prompts in decks:
            order = {pid: n for n, pid in enumerate(prompts)}
            stores[job_id] = (store, manifest, prompts)
            new_done += srs.new_today(store, today)
            c = srs.counts(store, today, set(prompts))
            for key in ("due", "again", "new", "cards"):
                total[key] += c[key]
            if c["next_due"] and (total["next_due"] is None or c["next_due"] <= total["next_due"]):
                total["next_count"] = c["next_count"] + (total["next_count"] if c["next_due"] == total["next_due"] else 0)
                total["next_due"] = c["next_due"]
            for pid in srs.queue(store, today, order, None):
                card = store["cards"][pid]
                kind = srs.kind_of(card, today)
                key = srs._shuffle(pid, today) if kind in ("due", "first") else card["reviews"][-1]["at"] if card["reviews"] else ""
                lists[kind].append((card["due"] or "", key, job_id, pid))
        limit = settings["new_per_day"]
        room = None if limit is None else max(0, limit - new_done)
        fresh = lists["new"] if room is None else lists["new"][:room]
        later = sorted(lists["later"], key=lambda t: t[1])
        later = later if room is None else later[:max(0, room - len(fresh))]
        total["new"] = len(fresh) + len(later)
        line = sorted(lists["due"]) + sorted(lists["again"], key=lambda t: t[1]) + fresh + sorted(lists["first"], key=lambda t: t[1]) + later
        card = None
        if line:
            _, _, job_id, pid = line[0]
            store, manifest, prompts = stores[job_id]
            section, prompt = prompts[pid]
            state = store["cards"][pid]
            if job_id == assist.DECK:  # a card from a note: the note is its passage, and where it leads
                note = section
                card = {"job": job_id, "id": pid, "book": "Your notes", "chapter": "", "section": "A note without a title" if note.untitled else note.title,
                        "question": prompt["prompt"], "answer": prompt["answer"], **qforms.members(prompt), "editable": False, "note": note.id,
                        "passage": [[{"t": " ".join(part.split()), "h": False}] for part in note.body.split("\n\n") if part.strip()][:12],
                        "state": state["state"], "again": state["again"], "interval": round(state["interval"], 1),
                        "first": srs.kind_of(state, today) == "first", "times": len(state["reviews"]), "go": None,
                        "preview": srs.preview(state, today, settings)}
                return {"counts": total, "left": len(line), "card": card, "settings": settings, "today": srs.day(today)}
            chapter = next((c["title"] for c in manifest["chapters"] if c["index"] == section["chapter"]), "")
            card = {"job": job_id, "id": pid, "book": manifest["title"], "chapter": chapter, "section": section["label"],
                    "question": prompt["prompt"], "answer": prompt["answer"], **qforms.members(prompt),
                    "editable": not prompt.get("hq") and self.editable(job_id),
                    "passage": self.passage(job_id, pid),
                    "state": state["state"], "again": state["again"], "interval": round(state["interval"], 1),
                    "first": srs.kind_of(state, today) == "first",
                    "times": len(state["reviews"]), "go": f"{section['chapter']}:{section['start']}",
                    "preview": srs.preview(state, today, settings)}
        return {"counts": total, "left": len(line), "card": card, "settings": settings, "today": srs.day(today)}

    def passage(self, job_id: str, pid: str) -> list[list[dict[str, Any]]]:
        """The paragraphs that support a question's answer, when the book was processed here. Each paragraph is
        a list of runs ``{"t": text, "h": highlighted?}``, so that what the reader highlighted shows here too."""
        store = notes.load(self.folder(job_id))
        mine = next((q for q in hq.questions(store) if q["id"] == pid), None)
        if mine is not None:  # made from a highlight: the paragraphs the highlight lies in
            refs = [{"ref": b.ref, "text": " ".join(b.words)} for _, b in hq.passage_refs(self.book(job_id), mine)]
        else:
            path = self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.questions.json"
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
            refs = next((q["passage"] for q in doc.get("questions", []) if q["id"] == pid), [])
        where = {b.ref: (sd, b) for sd in self.book(job_id).book.spine for b in sd.blocks}
        out = []
        for entry in refs:
            sd, block = where.get(entry["ref"], (None, None))
            if block is None or block.placeholder is not None:
                out.append([{"t": entry["text"], "h": False}])
                continue
            lit = [False] * len(block.words)
            for start, end in notes.marked(store, sd.index, block.start, block.end):
                for i in range(start - block.start, end - block.start):
                    lit[i] = True
            runs: list[dict[str, Any]] = []
            for word, h in zip(block.words, lit):
                if runs and runs[-1]["h"] == h:
                    runs[-1]["t"] += " " + word
                else:
                    runs.append({"t": word, "h": h})
            out.append(runs)
        return out

    def sources(self, job_id: str) -> dict[str, dict[str, int]]:
        """For each question, where the passage behind its answer begins: ``{"chapter", "word"}``."""
        path = self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.questions.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        where = {b.ref: (sd, b) for sd in self.book(job_id).book.spine for b in sd.blocks}
        out = {}
        for q in doc.get("questions", []):
            for entry in q.get("passage") or []:
                if entry.get("ref") in where:
                    sd, block = where[entry["ref"]]
                    out[q["id"]] = {"chapter": sd.index, "word": block.start}
                    break
        return out

    def manifest(self, job_id: str) -> dict[str, Any]:
        """The book as the reader needs it, with the questions from highlights that its checkpoints ask."""
        made = hq.with_questions(self.book(job_id).manifest(), notes.load(self.folder(job_id)))
        return {**made, "editable": self.editable(job_id)}

    # ---- changing a book's own questions (see ``editing``)

    def editable(self, job_id: str) -> bool:
        """Whether the book's questions can be changed: it was processed here, and its run is finished."""
        job = self.get(job_id)
        return job["status"] == "done" and job.get("kind") != "imported"

    def _edit(self, job_id: str, change: Any, build: bool = True) -> Any:
        """Apply ``change(book, state)`` to the book's run. With ``build`` every file is written again, so the
        reader, Practice and the downloads hold the questions as they now are."""
        with self.lock:
            if not self.editable(job_id):
                raise ApiError(HTTPStatus.CONFLICT, "The questions of this book cannot be changed: it has no finished run made here.")
            job = self.jobs[job_id]
            try:
                result = change_run(self.folder(job_id), change, build)
            except editing.EditError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            except PipelineError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            if build:
                report = load_report(self.folder(job_id), job["file"])
                if report is not None:
                    job["counts"] = report["counts"]
                job.pop("exports_stale", None)
                self._store(job)
            return result

    def _models(self, job_id: str, roles: list[str]) -> tuple[Settings, Any, Steer | None]:
        """The run's settings, a backend for a call the reader asked for, and what the reader wanted from the book."""
        job = self.get(job_id)
        settings = Settings.from_json(job["settings"])
        if self.backend_factory:
            return settings, self.backend_factory(settings), self._run_steer(job)
        backend = default_backend(settings, cancel=self.stopping)
        backend.check_available([settings.checker_model or settings.model if r == "check" else settings.model for r in roles])
        return settings, backend, self._run_steer(job)

    def _asked(self, job_id: str, call: Any) -> Any:
        try:
            return call()
        except UsageLimitError as exc:
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, str(exc)) from exc
        except (BackendError, BackendCancelled) as exc:
            raise ApiError(HTTPStatus.BAD_GATEWAY, f"The model could not be asked: {exc}") from exc

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
            chosen = self._asked(job_id, lambda: self._edit(
                job_id, lambda book, state: editing.choose_passage(backend, book, state, raw.get("section"), raw, settings), build=False))
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
        with self.lock:
            self._edit(job_id, lambda book, state: editing.delete(state, qid))
            self._forget_answers(job_id, qid)
        return {"deleted": qid}

    def restart_question(self, job_id: str, qid: str) -> dict[str, Any]:
        """Start a question over: it counts as never answered. It is asked again at its checkpoint, or, when the
        reader is past that, it is a new card in Practice."""
        with self.lock:
            shown = self._edit(job_id, lambda book, state: editing.view(book, state, qid), build=False)
            self._forget_answers(job_id, qid)
            manifest = self.book(job_id).manifest()
            section = next((s for s in manifest["sections"] if s["id"] == shown["section"]["id"]), None)
            if shown["kept"] and section is not None and hq.passed(reading.load_state(self.folder(job_id)), manifest, section):
                cards = self.cards(job_id)
                cards["cards"][qid] = srs.new_card()
                write_json(self.folder(job_id) / srs.FILE, cards)
        return self.book_question(job_id, qid)

    def check_question(self, job_id: str, qid: str) -> dict[str, Any]:
        settings, backend, steer = self._models(job_id, ["check"])
        self._asked(job_id, lambda: self._edit(job_id, lambda book, state: editing.check(backend, book, state, qid, settings, steer), build=False))
        return self.book_question(job_id, qid)

    def rewrite_question(self, job_id: str, qid: str, raw: dict[str, Any]) -> dict[str, Any]:
        settings, backend, steer = self._models(job_id, ["draft"])
        draft = raw.get("draft") if isinstance(raw.get("draft"), dict) else None
        return self._asked(job_id, lambda: self._edit(
            job_id, lambda book, state: editing.rewrite(backend, book, state, qid, raw.get("wish"), settings, steer, draft), build=False))

    # ---- questions from highlights

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
            if self.backend_factory:
                backend = self.backend_factory(settings)
            else:
                backend = default_backend(settings, None, cancel)
                backend.check_available([settings.model, settings.checker_model or settings.model] if auto else [settings.model])
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
        except Exception as exc:  # a fault of the tool: say so instead of leaving the request "being written"
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
        if job.get("kind") != "imported" and not job.get("exports_stale"):
            job["exports_stale"] = True
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

    def notebook(self, job_id: str) -> dict[str, Any]:
        """The notebook's entries, and under ``asking`` what is being written for them at the same moment."""
        book = self.book(job_id)
        with self.lock:
            store = self.notes_do(job_id)
            asking = self._writing(job_id)
        made = notes.notebook(book, store, reading.load_state(self.folder(job_id)), self.sources(job_id))
        return {**made, "editable": self.editable(job_id), "asking": asking}

    # ---- what the settings window keeps

    def ui_settings(self, change: Any = None) -> dict[str, Any]:
        """The stored settings, after merging ``change`` into them. A value of ``None`` removes its key."""
        path = self.data / SETTINGS_FILE
        with self.lock:
            try:
                stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            except ValueError:
                stored = {}
            if not isinstance(stored, dict):
                stored = {}
            if change is None:
                return stored
            if not isinstance(change, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "settings are an object of names and values")
            for key, value in change.items():
                if not isinstance(key, str) or not 0 < len(key) <= 32:
                    continue
                if value is None:
                    stored.pop(key, None)
                elif key == "notes_folder":
                    stored[key] = self._folder_setting(value)
                elif isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 200):
                    stored[key] = value
                elif key in LONG_SETTINGS and isinstance(value, str):
                    try:
                        stored[key] = steering.clean(value)
                    except steering.SteerError as exc:
                        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                    if not stored[key]:
                        del stored[key]
            if len(json.dumps({k: v for k, v in stored.items() if k not in LONG_SETTINGS})) > 4000:
                raise ApiError(HTTPStatus.BAD_REQUEST, "too many settings")
            write_json(path, stored)
        return stored

    # ---- highlights and notes

    def notes_do(self, job_id: str, change: Any = None) -> dict[str, Any]:
        """The book's highlights and notes, after ``change(store, book)`` when one is given."""
        book = self.book(job_id)
        folder = self.folder(job_id)
        with self.lock:
            store = notes.load(folder)
            if change is not None:
                try:
                    change(store, book)
                except notes.NotesError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                write_json(folder / notes.FILE, store)
        if change is not None:
            self.file_book(job_id, quiet=True)
        return store

    # ---- the notes folder (see vault.py)

    @staticmethod
    def _folder_setting(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 400:
            raise ApiError(HTTPStatus.BAD_REQUEST, "The notes folder is a path on this computer.")
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise ApiError(HTTPStatus.BAD_REQUEST, "Give the notes folder as a full path, such as "
                           + str(Path.home() / NOTES_FOLDER))
        if path.exists() and not path.is_dir():
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{path} is a file, not a folder.")
        return str(path)

    def notes_folder(self) -> Path:
        stored = self.ui_settings().get("notes_folder")
        return Path(stored).expanduser() if isinstance(stored, str) and stored.strip() else self.notes_default

    def vault(self, make: bool = False) -> Vault:
        """The notes folder in use. ``make`` creates it when it is not there, and gives every book its file."""
        path = self.notes_folder()
        with self.lock:
            if self._vault is None or self._vault.root != path:
                self._vault, self.filed = Vault(path), {}
            vault = self._vault
        if make:
            try:
                vault.ensure()
            except VaultError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            self.file_books()
        return vault

    def folder_state(self) -> dict[str, Any]:
        path = self.notes_folder()
        return {"folder": str(path), "default": str(self.notes_default), "exists": path.is_dir(),
                "chosen": path != self.notes_default}

    def _readable(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        return bool(job) and job["status"] == "done" and self.prepared(job_id).is_file()

    def file_book(self, job_id: str, quiet: bool = False) -> None:
        """Write the book's file in the notes folder, when there is a notes folder. ``quiet``: a failure here
        must not undo what the reader just did in the book."""
        try:
            vault = self.vault()
            if not vault.exists or not self._readable(job_id):
                return
            book = self.book(job_id)
            marks = self.folder(job_id) / notes.FILE
            made_from = (self.prepared(job_id).stat().st_mtime_ns, marks.stat().st_mtime_ns if marks.is_file() else 0)
            store = notes.load(self.folder(job_id))
            job = self.jobs[job_id]
            kind = self.type_of(job)
            vault.put_book(bookfile.book_id(job_id), job.get("title") or stem_of(job["file"]),
                           lambda name: bookfile.text(name, job_id, book, store, kind, job.get("origin")), kind)
            self.filed[job_id] = made_from
        except (ApiError, VaultError, OSError) as exc:
            if not quiet:
                raise exc if isinstance(exc, ApiError) else ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def file_books(self) -> None:
        """Every book of the library has its file, made again when the book or what is marked in it changed."""
        for job_id in [j for j in list(self.jobs) if self._readable(j)]:
            marks = self.folder(job_id) / notes.FILE
            try:
                made_from = (self.prepared(job_id).stat().st_mtime_ns, marks.stat().st_mtime_ns if marks.is_file() else 0)
            except OSError:
                continue
            note = self.vault().book(bookfile.book_id(job_id))
            if note is not None and self.filed.get(job_id) is None and note.stamp[0] >= max(made_from):
                self.filed[job_id] = made_from  # written after the book and its marks last changed: it is current
            if self.filed.get(job_id) != made_from or note is None:
                self.file_book(job_id, quiet=True)

    def _book_left(self, job_id: str) -> None:
        """The book is being removed from the library: its file stays, and says so."""
        try:
            vault = self.vault()
            note = vault.book(bookfile.book_id(job_id)) if vault.exists else None
            if note is not None:
                current = compose(note.props, note.body)
                vault.put_book(note.id, note.title, lambda name: bookfile.mark_removed(current))
        except (VaultError, OSError):
            pass

    def _cited(self, to: dict[str, Any]) -> dict[str, Any]:
        """What a link into a book's file leads to, with the way to open the reader there."""
        job_id = bookfile.job_of(to.get("id") or "") if to.get("kind") in SOURCE_KINDS else None
        if job_id is None:
            return to
        out = {**to, "in_library": self._readable(job_id)}
        if out["in_library"]:
            out["job"] = job_id
            if to.get("block"):
                out["go"] = self._place(job_id, to["block"])
        return out

    def _place(self, job_id: str, block: str) -> dict[str, int] | None:
        try:
            sections = self.book(job_id).manifest()["sections"]
        except ApiError:
            sections = None
        return bookfile.place(notes.load(self.folder(job_id)), block, sections)

    def _note(self, detail: dict[str, Any]) -> dict[str, Any]:
        """A note as its page shows it: with the passages it cites, and where each can be read."""
        for link in detail["links"]:
            link["to"] = self._cited(link["to"])
        seen: set[tuple[str, str]] = set()
        detail["sources"] = []
        for link in detail["links"]:
            to = link["to"]
            if to.get("kind") in SOURCE_KINDS and to.get("block") and (to["id"], to["block"]) not in seen:
                seen.add((to["id"], to["block"]))
                detail["sources"].append({k: to.get(k) for k in ("id", "title", "block", "text", "found", "job", "go", "in_library")})
        held = {p["key"]: p.get("value") for p in detail["props"]}
        where = next((k["to"] for k in detail["links"] if not k["anchor"] and k["to"].get("kind") in SOURCE_KINDS
                      and held.get("book") == f"[[{k['target']}]]"), None)
        if where is not None:  # a quick note written in the reader: the book, and the place it was written at
            at = re.fullmatch(r"(\d+):(\d+)", str(held.get("place") or ""))
            detail["written"] = {"title": where["title"], "job": where.get("job"), "in_library": where.get("in_library"),
                                 "go": {"chapter": int(at.group(1)), "word": int(at.group(2))} if at and where.get("job") else None}
        vault = self.vault()
        with vault.lock:  # what a project holds, and which project a note belongs to or was gathered for
            mine = vault.by_id.get(detail["id"])
            if mine is not None and detail["kind"] == "project":
                detail["project"] = {"status": "finished" if held.get("status") == "finished" else "active",
                                     "notes": [vault.brief(n) for n in vault.project_notes(mine)]}
            elif mine is not None and detail["kind"] not in SOURCE_KINDS:
                named = next((k["to"] for k in detail["links"] if held.get("project") == f"[[{k['target']}]]" and k["to"].get("kind") == "project"), None)
                if detail["kind"] == "project-note" and named is not None:
                    detail["of"] = {"id": named["id"], "title": named["title"]}
                detail["gathered_for"] = [{"id": o.id, "title": o.title} for o in {o.id: o for o, _ in vault.backlinks(mine)}.values()
                                          if o.kind == "project"]
        if detail["kind"] in SOURCE_KINDS:
            detail["in_library"] = self._readable(bookfile.job_of(detail["id"]) or "")
            detail["job"] = bookfile.job_of(detail["id"]) if detail["in_library"] else None
        return detail

    def _vaulted(self, call: Any) -> Any:
        try:
            return call()
        except Missing as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
        except Conflict:
            raise
        except VaultError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def vault_home(self) -> dict[str, Any]:
        """The front page of Notes. Asking for it is what makes the folder, the first time."""
        vault = self.vault(make=True)
        rows = vault.listing()
        books = [{**r, "job": bookfile.job_of(r["id"]), "in_library": self._readable(bookfile.job_of(r["id"]) or ""),
                  "entries": len(vault.get(r["id"]).blocks)} for r in rows if r["kind"] in SOURCE_KINDS]
        aside = {r["id"] for r in rows if r["kind"] == "quick" and vault.prop_of(r["id"], "aside") is True}
        return {"folder": str(vault.root), "inbox": [r for r in rows if r["inbox"] and r["id"] not in aside],
                "waiting": self.process_view()["counts"]["waiting"], "revisit": vault.revisit(self.today())["today"],
                "recent": [r for r in rows if not r["inbox"] and r["kind"] in ("idea", "quick")][:40],
                "indexes": [r for r in rows if r["kind"] == "index" and not r["inbox"]], "projects": vault.projects(),
                "notes": sum(1 for r in rows if r["kind"] not in SOURCE_KINDS), "tags": vault.tags(),
                "books": sorted(books, key=lambda b: b["title"].casefold()), "trash": len(vault.trash())}

    # ---- processing: making something of what was marked (see process.py)

    def process_view(self, only: str | None = None) -> dict[str, Any]:
        """Everything that can be processed, with what became of each item: quick notes, then each book's
        highlights and notes in reading order. ``only`` narrows to one book."""
        if only is not None:
            self.get(only)
        vault = self.vault(make=True)
        books = []
        for job_id in [j for j in list(self.jobs) if self._readable(j) and only in (None, j)]:
            folder = self.folder(job_id)
            marked = notes.load(folder)
            # a book with nothing marked has nothing to process, and need not be opened to know it
            rows = process.items(self.book(job_id), marked) if marked["highlights"] or marked["notes"] else []
            with self.lock:
                state = process.load(folder)
                if process.tidy(state, {r["id"] for r in rows}):
                    write_json(folder / process.FILE, state)
            with vault.lock:
                note = vault.book(bookfile.book_id(job_id))
                cited = vault.citing(note) if note is not None else {}
                for r in rows:
                    r["notes"] = [{"id": n.id, "title": n.title, "untitled": n.untitled} for n in cited.get(r["id"], [])]
                    r["state"] = "done" if r["notes"] else "aside" if r["id"] in state["aside"] else "waiting"
                    r["job"] = job_id
            read = reading.summary(reading.load_state(folder))
            books.append({"job": job_id, "id": bookfile.book_id(job_id), "title": note.title if note is not None else self.jobs[job_id].get("title"),
                          "last_read": (read or {}).get("last_read") or "", "items": rows})
        books.sort(key=lambda b: b["last_read"], reverse=True)
        quick = []
        if only is None:
            with vault.lock:
                for brief in vault.listing(kind="quick"):
                    note = vault.get(brief["id"])
                    held = {p.key: p.value for p in note.props if p.key}
                    quick.append({"id": note.id, "kind": "quick", "text": "", "note": note.body.strip()[:1200], "created": note.created or note.modified,
                                  "title": "" if note.untitled else note.title, "state": "aside" if held.get("aside") is True else "waiting", "notes": []})
            quick.sort(key=lambda q: q["created"])
        every = quick + [r for b in books for r in b["items"]]
        return {"quick": quick, "books": books,
                "counts": {k: sum(1 for r in every if r["state"] == k) for k in ("waiting", "done", "aside")}}

    def process_waiting(self, job_id: str) -> int:
        """How many items of a book wait to be processed. Looking does not make the notes folder."""
        marked = notes.load(self.folder(job_id))
        if not marked["highlights"] and not marked["notes"]:
            return 0
        try:
            rows = process.items(self.book(job_id), marked)
        except ApiError:
            return 0
        aside = process.load(self.folder(job_id))["aside"]
        vault = self.vault()
        with vault.lock:
            note = vault.book(bookfile.book_id(job_id)) if vault.exists else None
            cited = vault.citing(note) if note is not None else {}
        return sum(1 for r in rows if r["id"] not in cited and r["id"] not in aside)

    def process_aside(self, r: dict[str, Any]) -> dict[str, Any]:
        """Set items aside, or bring them back: ``{job, ids, aside}`` for a book's items, ``{quick, aside}``
        for a quick note."""
        aside = r.get("aside") is not False
        if isinstance(r.get("quick"), str):
            vault = self.vault()
            self._vaulted(lambda: vault.set_props(r["quick"], {"aside": True if aside else None}))
            return {"ok": True}
        job_id, ids = str(r.get("job") or ""), r.get("ids")
        self.get(job_id)
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 5000:
            raise ApiError(HTTPStatus.BAD_REQUEST, "name the items of the book")
        known = {i["id"] for i in process.items(self.book(job_id), notes.load(self.folder(job_id)))}
        with self.lock:
            state = process.load(self.folder(job_id))
            process.set_aside(state, [i for i in ids if i in known], aside)
            write_json(self.folder(job_id) / process.FILE, state)
        return {"ok": True}

    def process_add(self, r: dict[str, Any]) -> dict[str, Any]:
        """Add an item to a note that exists: a book's item as a source, a quick note by its words (the quick
        note then goes to the trash, from which it can be put back). Gives the note as it now is."""
        vault = self.vault(make=True)
        target = str(r.get("note") or "")
        if isinstance(r.get("quick"), str):
            def merge() -> dict[str, Any]:
                with vault.lock:
                    quick = vault.get(r["quick"])
                    if quick.id == vault.get(target).id:
                        raise VaultError("A note cannot be added to itself.")
                    out = vault.add_to(target, words=quick.body)
                    vault.delete(quick.id)
                    return out
            return self._note(self._vaulted(merge))
        link = self.vault_cite(r)["link"]
        return self._note(self._vaulted(lambda: vault.add_to(target, source=link)))

    def vault_related(self, r: dict[str, Any]) -> dict[str, Any]:
        text = r.get("text") if isinstance(r.get("text"), str) else ""
        tags = [t for t in r.get("tags") or [] if isinstance(t, str)][:50] if isinstance(r.get("tags"), list) else []
        but = {b for b in r.get("but") or [] if isinstance(b, str)} if isinstance(r.get("but"), list) else set()
        found = self.vault().related(text[:20000], tags, but)
        for p in found["passages"]:
            job_id = bookfile.job_of(p["book_id"])
            p["in_library"] = self._readable(job_id or "")
            if p["in_library"] and p["block"]:
                p["job"], p["go"] = job_id, self._place(job_id, p["block"])
        return found

    # ---- the model's help in the notes folder (see assist.py)

    def _helper(self, roles: list[str]) -> tuple[Settings, Any]:
        """The settings and a backend for a call the reader asked for: the models of the Add a book tab."""
        settings = self.question_settings()
        if self.backend_factory:
            return settings, self.backend_factory(settings)
        try:
            backend = default_backend(settings, cancel=self.stopping)
            backend.check_available([settings.checker_model or settings.model if r == "check" else settings.model for r in roles])
        except BackendError as exc:
            raise ApiError(HTTPStatus.BAD_GATEWAY, f"The model could not be asked: {exc}") from exc
        return settings, backend

    def _helped(self, call: Any) -> Any:
        try:
            return self._asked("", lambda: self._vaulted(call))
        except (assist.AssistError, hq.QuestionError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def assist_sent(self, note_id: str) -> dict[str, Any]:
        """What "Find connections" would send for a note, and to which model, before anything is sent."""
        vault = self.vault()

        def count() -> dict[str, Any]:
            with vault.lock:
                return assist.connection_material(vault, vault.get(note_id))["sent"]
        settings = self.question_settings()
        return {"sent": self._vaulted(count), "model": settings.model or "the default model"}

    def assist_connections(self, note_id: str) -> dict[str, Any]:
        settings, backend = self._helper(["draft"])
        vault = self.vault()
        got = self._helped(lambda: assist.connections(backend, vault, note_id, settings))
        for c in got["connections"]:
            if c["kind"] == "passage":
                job_id = bookfile.job_of(c["id"])
                if self._readable(job_id or ""):
                    c["job"], c["go"] = job_id, self._place(job_id, c["block"])
        return got

    def assist_answer(self, r: dict[str, Any]) -> dict[str, Any]:
        settings, backend = self._helper(["draft"])
        vault = self.vault()
        got = self._helped(lambda: assist.answer(backend, vault, r.get("question"), settings))
        for s in got["sources"] + got["closest"]:
            job_id = bookfile.job_of(s["id"]) if s["kind"] == "passage" else None
            if job_id and self._readable(job_id):
                s["job"], s["go"] = job_id, self._place(job_id, s["block"])
        if got["covered"]:  # the text of the quick note that "Keep" would make
            got["keep"] = assist.answer_markdown(str(r.get("question")), got, srs.day(self.today()))
        return got

    def notes_practice(self) -> dict[str, Any] | None:
        """The counts of the cards made from notes, for the library's Practice button; None when there are none."""
        store = srs.load(self.data / assist.FOLDER)
        if not store["cards"]:
            return None
        vault = self.vault()
        vault.refresh()
        with vault.lock:
            known = set(assist.practice_prompts(vault, assist.load(self.data)))
        return srs.counts(store, self.today(), known)

    def _note_questions(self, change: Any = None) -> dict[str, Any]:
        with self.lock:
            store = assist.load(self.data)
            if change is not None:
                change(store)
                write_json(self.data / assist.FOLDER / assist.FILE, store)
        return store

    def note_questions(self, note_id: str) -> dict[str, Any]:
        vault = self.vault()
        store = self._note_questions()
        cards = srs.load(self.data / assist.FOLDER)["cards"]

        def rows() -> list[dict[str, Any]]:
            with vault.lock:
                return assist.of_note(store, vault.get(note_id))
        return {"questions": [{**q, "card": {"state": cards[q["id"]]["state"], "times": len(cards[q["id"]]["reviews"])} if q["id"] in cards else None}
                              for q in self._vaulted(rows)], "forms": list(self.question_settings().forms)}

    def _card_for(self, qid: str) -> None:
        with self.lock:
            cards = srs.load(self.data / assist.FOLDER)
            if qid not in cards["cards"]:
                cards["cards"][qid] = srs.new_card()
                write_json(self.data / assist.FOLDER / srs.FILE, cards)

    def note_question_propose(self, note_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """Ask the model for a question about a note, and check it. One that fails its check is held, with why."""
        settings, backend = self._helper(["draft", "check"])
        vault = self.vault()
        instruction = _text(r.get("instruction"), steering.INSTRUCTION_MAX)

        def work() -> None:
            note = vault.get(note_id)
            asked = [q["question"] for q in self._note_questions()["questions"] if q["note"] == note.id]
            wording = assist.propose(backend, vault, note, asked, settings, instruction)
            model = wording.pop("model")
            verdict = assist.check(backend, vault, note, wording, settings)

            def keep(store: dict[str, Any]) -> None:
                q = assist.add(store, note, wording, "model", model, "proposed" if verdict["passed"] else "held")
                q.update(checked=verdict, problem=verdict["problem"])
            self._note_questions(keep)
        self._helped(work)
        return self.note_questions(note_id)

    def note_question_write(self, note_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """A question the reader wrote themselves: accepted as it is, and a card from now on."""
        vault = self.vault()
        made: dict[str, Any] = {}

        def work() -> None:
            note = vault.get(note_id)
            wording = hq.formed(r.get("question"), r.get("answer"), r.get("form"))
            self._note_questions(lambda store: made.update(assist.add(store, note, wording, "you", None, "accepted")))
        self._helped(work)
        self._card_for(made["id"])
        return self.note_questions(note_id)

    def note_question_change(self, qid: str, r: dict[str, Any]) -> dict[str, Any]:
        """Accept a question, reword it, or have it checked again against the note as it now is."""
        vault = self.vault()
        found: dict[str, Any] = {}

        def work() -> None:
            q = assist.find(self._note_questions(), qid)
            note = vault.get(q["note"])
            found["note"] = note.id
            if r.get("check") is True:
                settings, backend = self._helper(["check"])
                verdict = assist.check(backend, vault, note, q, settings)

                def checked(store: dict[str, Any]) -> None:
                    mine = assist.find(store, qid)
                    mine.update(checked=verdict, problem=verdict["problem"], made_from=assist.mark_of(note))
                    if mine["status"] == "held" and verdict["passed"]:
                        mine["status"] = "proposed"
                return void(self._note_questions(checked))
            if any(k in r for k in ("question", "answer", "form")):
                self._note_questions(lambda store: assist.reword(store, note, qid, r.get("question"), r.get("answer"), r.get("form")))
            if r.get("accept") is True:
                self._note_questions(lambda store: assist.find(store, qid).update(status="accepted"))
                self._card_for(qid)
        self._helped(work)
        return self.note_questions(found["note"])

    def note_question_delete(self, qid: str) -> dict[str, Any]:
        found: dict[str, Any] = {}

        def drop(store: dict[str, Any]) -> None:
            found["note"] = assist.find(store, qid)["note"]
            store["questions"] = [q for q in store["questions"] if q["id"] != qid]
        self._helped(lambda: self._note_questions(drop))
        with self.lock:
            cards = srs.load(self.data / assist.FOLDER)
            if cards["cards"].pop(qid, None) is not None:
                write_json(self.data / assist.FOLDER / srs.FILE, cards)
        try:
            return self.note_questions(found["note"])
        except ApiError:
            return {"questions": [], "forms": list(self.question_settings().forms)}

    def timeline(self, before: str | None = None, most: int = 30) -> dict[str, Any]:
        """What was written and marked, day by day, the newest day first: notes on the day they were made (quick
        notes apart), notes on the day of their last change when that is another day, and for each book what
        was highlighted and noted in it. ``before`` gives the days earlier than a day. Days are those of this
        computer's clock."""
        def day(stamp: str) -> str | None:
            try:
                at = _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
            except (TypeError, ValueError):
                return None
            return at.astimezone().date().isoformat()

        days: dict[str, dict[str, Any]] = {}
        on = lambda d: days.setdefault(d, {"day": d, "written": [], "edited": [], "quick": [], "books": {}})
        vault = self.vault()
        vault.refresh()
        with vault.lock:
            for note in vault.notes.values():
                if note.source:
                    continue
                made, changed = day(note.created) or day(note.modified), day(note.modified)
                if made:
                    on(made)["quick" if note.kind == "quick" else "written"].append(vault.brief(note))
                if changed and changed != made:
                    on(changed)["edited"].append(vault.brief(note))
        clip = lambda text: text if len(text) <= 200 else text[:200].rsplit(" ", 1)[0] + " …"
        for job_id in [j for j in list(self.jobs) if self._readable(j)]:
            marked = notes.load(self.folder(job_id))
            rows = [(h.get("created"), "highlights", h) for h in marked["highlights"]] + [(n.get("created"), "notes", n) for n in marked["notes"]]
            for stamp, what, row in rows:
                d = day(stamp or "")
                if d:
                    book = on(d)["books"].setdefault(job_id, {"job": job_id, "title": self.jobs[job_id].get("title") or "", "highlights": [], "notes": []})
                    book[what].append({"id": row["id"], "text": clip(row["text"])})
        ordered = [d for d in sorted(days.values(), key=lambda d: d["day"], reverse=True) if before is None or d["day"] < before]
        for d in ordered[:most]:
            d["books"] = sorted(d["books"].values(), key=lambda b: b["title"].casefold())
            for name in ("written", "edited", "quick"):
                d[name].sort(key=lambda n: n["modified"], reverse=True)
        return {"days": ordered[:most], "more": len(ordered) > most}

    def vault_search(self, query: str, kind: str | None, tag: str | None, book: str | None) -> dict[str, Any]:
        found = self.vault().search(query[:200], kind or None, tag or None, book or None)
        for p in found["passages"]:
            job_id = bookfile.job_of(p["book_id"])
            p["in_library"] = self._readable(job_id or "")
            if p["in_library"] and p["block"]:
                p["job"], p["go"] = job_id, self._place(job_id, p["block"])
        return found

    def vault_note(self, note_id: str) -> dict[str, Any]:
        vault = self.vault()

        def read() -> dict[str, Any]:
            with vault.lock:
                return vault.detail(vault.get(note_id))
        return self._note(self._vaulted(read))

    def vault_peek(self, target: str, anchor: str, inside: str | None, full: bool = False) -> dict[str, Any]:
        vault = self.vault()
        vault.refresh()
        with vault.lock:
            found = vault.peek(target, anchor, vault.by_id.get(inside or ""), full)
        return self._cited(found)  # asks about the book, outside the folder's lock

    def vault_create(self, r: dict[str, Any]) -> dict[str, Any]:
        """A new note. ``source`` names a highlight or note of a book that it starts from; ``where`` the place in
        a book at which it was written."""
        vault = self.vault(make=True)
        body = r.get("body") if isinstance(r.get("body"), str) else ""
        props: dict[str, Any] = {}
        if isinstance(r.get("tags"), (list, str)):
            props["tags"] = r["tags"]
        source, where = r.get("source"), r.get("where")
        if isinstance(source, dict):
            job_id, block = str(source.get("job") or ""), str(source.get("block") or "")
            self.get(job_id)
            self.file_book(job_id)
            book = vault.book(bookfile.book_id(job_id))
            if book is None or block not in book.blocks:
                raise ApiError(HTTPStatus.BAD_REQUEST, "that highlight or note is not in the book's file")
            link = f"[[{book.title}#^{block}]]"
            props["sources"] = [link]
            body = (body.rstrip() + "\n\n" if body.strip() else "\n\n") + f"Source: {link}\n"
        elif isinstance(r.get("sources"), list):  # links the page was given by ``cite``
            if not all(isinstance(x, str) and re.fullmatch(r"\[\[[^\[\]\n]{1,300}\]\]", x) for x in r["sources"]) or len(r["sources"]) > 20:
                raise ApiError(HTTPStatus.BAD_REQUEST, "sources are links to passages")
            props["sources"] = r["sources"]
        if isinstance(where, dict):
            job_id = str(where.get("job") or "")
            self.get(job_id)
            self.file_book(job_id)
            book = vault.book(bookfile.book_id(job_id))
            if book is not None:
                props["book"] = f"[[{book.title}]]"
                if isinstance(where.get("chapter"), int) and isinstance(where.get("word"), int):
                    props["place"] = f"{where['chapter']}:{where['word']}"
        kind = r.get("kind") if isinstance(r.get("kind"), str) else "idea"
        if r.get("by") == "model":  # an answer kept from "Ask": the file says that its words are not the reader's
            props["by"] = "model"
        if kind == "project":  # a project has a name, and its file the two sections the app works on
            if not isinstance(r.get("title"), str) or not r["title"].strip():
                raise ApiError(HTTPStatus.BAD_REQUEST, "A project needs a name.")
            props["status"] = "active"
            body = body if body.strip() else "\n\n## Gathered\n\n## Outline\n"
        if kind == "project-note":
            with vault.lock:
                of = vault.by_id.get(str(r.get("project") or ""))
                if of is None or of.kind != "project":
                    raise ApiError(HTTPStatus.BAD_REQUEST, "A project note belongs to a project.")
                props["project"] = f"[[{of.title}]]"
        return self._note(self._vaulted(lambda: vault.create(r.get("title") or "", body, kind, props)))

    def vault_gather(self, project_id: str, r: dict[str, Any]) -> dict[str, Any]:
        vault = self.vault()
        got = self._vaulted(lambda: vault.gather(project_id, str(r.get("note") or "")))
        return {**self._note(got), "added": got["added"]}

    def vault_cite(self, r: dict[str, Any]) -> dict[str, Any]:
        """The link that cites a highlight or a note of a book, with the words it leads to. Makes the notes
        folder and the book's file when they are not there yet."""
        job_id, block = str(r.get("job") or ""), str(r.get("block") or "")
        self.get(job_id)
        vault = self.vault(make=True)
        self.file_book(job_id)
        book = vault.book(bookfile.book_id(job_id))
        if book is None or block not in book.blocks:
            raise ApiError(HTTPStatus.BAD_REQUEST, "that highlight or note is not in the book's file")
        to = self._cited(vault.peek(book.title, "^" + block))
        return {"link": f"[[{book.title}#^{block}]]", **{k: to.get(k) for k in ("id", "title", "block", "text", "found", "job", "go", "in_library")}}

    def vault_save(self, note_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """Save a note's text and properties, then give it the title that was sent, if it is a new one."""
        vault = self.vault()

        def work() -> dict[str, Any]:
            with vault.lock:
                note = vault.get(note_id)
                if note.source and bookfile.job_of(note.id):
                    raise VaultError("A book's file is kept by the app. Change the highlight or the note in the book.")
                title = r.get("title")
                if isinstance(title, str) and title.strip():
                    title = clean_title(title)  # refused before anything is written
                    taken = vault._taken(title, but=note)
                    if taken is not None:
                        raise VaultError(f"There is already a note called {taken.title}.")
                out = vault.save(note_id, r.get("body"), r.get("props"), r.get("token"), force=r.get("force") is True)
                if isinstance(title, str) and title.strip() and (title != out["title"] or out["inbox"]):
                    out = vault.rename(out["id"], title)
                return out
        return self._note(self._vaulted(work))

    def vault_delete(self, note_id: str) -> dict[str, Any]:
        vault = self.vault()

        def work() -> dict[str, Any]:
            note = vault.get(note_id)
            if note.source and self._readable(bookfile.job_of(note.id) or ""):
                raise VaultError("This book is in your library, so its file is kept. Remove the book first.")
            return vault.delete(note_id)
        return self._vaulted(work)

    def practice_answer(self, job_id: str, pid: str, remembered: bool, text: str) -> None:
        folder = self.data / assist.FOLDER if job_id == assist.DECK else self.folder(self.get(job_id)["id"])
        with self.lock:
            store = srs.load(folder) if job_id == assist.DECK else self.cards(job_id)
            card = store["cards"].get(pid)
            if card is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "no such card")
            today = self.today()  # the stamp carries the same local day the schedule is counted in
            at = f"{srs.day(today)}T{_dt.datetime.now().strftime('%H:%M:%S')}"
            srs.answer(card, remembered, today, self.srs_settings(), at, "review", text)
            write_json(folder / srs.FILE, store)

    # ---- reading

    def prepared(self, job_id: str) -> Path:
        return self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.prepared.epub"

    def book(self, job_id: str) -> reading.ReadingBook:
        """The run's prepared book, parsed once and kept until the file changes (a rebuild writes it again)."""
        self.get(job_id)
        path = self.prepared(job_id)
        if not path.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "this book has no file to read yet")
        stamp = path.stat().st_mtime_ns
        with self.lock:
            cached = self.books.get(job_id)
            if cached is None or cached[0] != stamp:
                try:
                    cached = (stamp, reading.open_reading_book(path))
                except reading.ReadingError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                self.books[job_id] = cached
            return cached[1]

    def reading_state(self, job_id: str) -> dict[str, Any]:
        book = self.book(job_id)
        return reading.load_state(self.folder(job_id)) or reading.new_state(book.data["book"]["source_sha256"])

    def save_reading(self, job_id: str, raw: Any) -> dict[str, Any]:
        book = self.book(job_id)
        known = {p["id"] for c in book.data["checkpoints"] for p in c["prompts"]}
        with self.lock:
            known |= hq.ids(notes.load(self.folder(job_id)))  # questions from highlights are graded too
            old = reading.load_state(self.folder(job_id))
            state = reading.merge_state(old, reading.clean_state(raw, book.data["book"]["source_sha256"], known))
            write_json(self.folder(job_id) / reading.STATE_FILE, state)
        return state

    def set_place(self, job_id: str, raw: Any) -> dict[str, Any]:
        """Move the reader's place in a book, from outside the reader."""
        book = self.book(job_id)
        if not isinstance(raw, dict) or not isinstance(raw.get("chapter"), int) or not isinstance(raw.get("word"), int) \
                or isinstance(raw["chapter"], bool) or isinstance(raw["word"], bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "a place needs a chapter and a word")
        with self.lock:
            state = reading.load_state(self.folder(job_id)) or reading.new_state(book.data["book"]["source_sha256"])
            try:
                reading.place(state, book.manifest(), raw["chapter"], raw["word"])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            write_json(self.folder(job_id) / reading.STATE_FILE, state)
        return state

    def _new_id(self, stem: str) -> str:
        """An id that no entry has: the time to the second and the name, with a counter when both are taken
        (the caller holds the lock)."""
        stamp, slug = _dt.datetime.now().strftime("%Y%m%d-%H%M%S"), _slug(stem)
        job_id, n = f"{stamp}-{slug}", 1
        while job_id in self.jobs or self.folder(job_id).exists():
            n += 1
            job_id = f"{stamp}-{slug[:40 - len(str(n)) - 1].rstrip('-')}-{n}"
        return job_id

    def import_prepared(self, name: str, data: bytes) -> dict[str, Any]:
        """Add a prepared EPUB made elsewhere. It can be read; it has no run to rebuild from."""
        name = _safe_name(name)
        stem = name[:-len(".prepared.epub")] if name.lower().endswith(".prepared.epub") else stem_of(name)
        with self.lock:
            job_id = self._new_id(stem)
            folder = self.folder(job_id)
            folder.mkdir(parents=True)
            path = folder / f"{stem}.prepared.epub"
            path.write_bytes(data)
            try:
                book = reading.open_reading_book(path)
            except reading.ReadingError as exc:
                shutil.rmtree(folder, ignore_errors=True)
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            manifest = book.manifest()
            questions = sum(len(s["prompts"]) for s in manifest["sections"])
            job = {"id": job_id, "file": f"{stem}.epub", "title": manifest["title"], "created": now(), "status": "done",
                   "message": "Added as a prepared book", "stage": "done", "done": None, "total": None,
                   "words": manifest["words"], "settings": {}, "events": [], "kind": "imported",
                   "counts": {"questions": questions, "sections": len(manifest["sections"]), "left_out": 0,
                              "checkpoints": sum(bool(s["prompts"]) for s in manifest["sections"])}}
            self.jobs[job_id] = job
            self._store(job)
        return job

    def listing(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.summary(j) for j in sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)]

    def detail(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = dict(self.get(job_id))
        report = load_report(self.folder(job_id), job["file"]) if job["status"] == "done" else None
        if report is not None:
            job["report"] = {k: report.get(k) for k in ("counts", "files", "firmware_format_ok", "notes", "warnings",
                                                        "usage", "complete", "doubted_breaks", "excluded", "cards")}
            job["report"]["preview"] = bool((report.get("settings") or {}).get("preview"))
        job.update({k: v for k, v in self.summary(self.jobs[job_id]).items() if k in ("can_read", "reading", "practice", "notes", "type")})
        if job["can_read"]:
            job["results"] = reading.results(reading.load_state(self.folder(job_id)))
            job["waiting"] = self.process_waiting(job_id)
        # what the reader wants from the book: their words for it, and the reading of them that is waiting or was used
        mine = job.pop("steer", None) or {}
        job.pop("spent", None), job.pop("again", None)
        job["steer"] = {"purpose": mine.get("purpose", ""), "interests": mine.get("interests", True),
                        "steered": bool(mine.get("purpose") or mine.get("about") or mine.get("style"))}
        if job.get("reading_state") != "ready":  # the reading is shown while it waits for the reader's word
            job["read_as"] = None
        if job.get("chapters") and job.get("settings"):
            try:
                job["estimate"] = estimate([c["words"] for c in job["chapters"]], Settings.from_json(job["settings"]),
                                           job["steer"]["steered"] and job.get("reading_state") != "without")
            except SettingsError:
                pass
        if job["status"] == "tried":
            try:
                job["tried"] = review_data(trial_dir(self.folder(job_id)))
            except PipelineError:
                job["tried"] = None
        return job

    # ---- what the reader wants from a book

    def _run_steer(self, job: dict[str, Any]) -> Steer | None:
        """What the question writers of this book's run are told: the confirmed reading, or without one the
        reader's own words as they were when the book was added."""
        state = job.get("reading_state")
        mine = job.get("steer") or {}
        if state == "without" or not mine:
            return None
        steer = Steer(mine.get("purpose", ""), mine.get("about", ""), mine.get("style", ""),
                      job.get("confirmed") if state == "confirmed" else None)
        return None if steer.empty else steer

    def steer_for(self, job_id: str) -> Steer | None:
        """What the writer of a question on a highlight is told: the reader's words as they are now. The reading
        confirmed for the book's run stands for them for as long as they are the words it was made from."""
        job = self.get(job_id)
        ui = self.ui_settings()
        mine = job.get("steer") or {}
        now_ = Steer(mine.get("purpose", ""), str(ui.get("steer_about") or "") if mine.get("interests", True) else "",
                     str(ui.get("steer_style") or ""))
        if job.get("reading_state") == "confirmed" and job.get("confirmed") and job.get("confirmed_for") == now_.raw_key():
            return Steer(now_.purpose, now_.about, now_.style, job["confirmed"])
        return None if now_.empty else now_

    def _plan(self, job: dict[str, Any], request: dict[str, Any]) -> None:
        """What the run does next, when the request says: the whole book, or a trial of one chapter."""
        step = request.get("next")
        if step == "trial":
            doc = request.get("doc")
            if not isinstance(doc, int) or isinstance(doc, bool) or doc not in {c["index"] for c in job.get("chapters") or []}:
                raise ApiError(HTTPStatus.BAD_REQUEST, "choose a chapter of the book to try")
            job["trial"] = {"doc": doc, "state": "wanted"}
        elif step == "whole":
            if job.get("trial") and job["trial"]["state"] == "wanted":
                job["trial"] = None
        elif step is not None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "'next' is whole or trial")

    def _waiting(self, job_id: str, *statuses: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] not in statuses or job_id in self.cancels:
            raise ApiError(HTTPStatus.CONFLICT, "this run is not waiting for that")
        return job

    def answer_reading(self, job_id: str, request: dict[str, Any]) -> None:
        """The reader's answer to the reading of their wishes: confirm it (as they edited it), have it written
        again with what they say about it, or go on without a steer."""
        with self.lock:
            job = self._waiting(job_id, "asking")
            shown = job.get("read_as") or {}
            given = request.get("answers") if isinstance(request.get("answers"), list) else []
            answers = [(q, _text(given[n], 600) if n < len(given) else "") for n, q in enumerate(shown.get("questions", []))]
            edited = {"focus": request["focus"] if isinstance(request.get("focus"), str) else shown.get("focus", ""),
                      "style": request["style"] if isinstance(request.get("style"), str) else shown.get("style", ""),
                      "chapters": [c for c in shown.get("chapters", [])
                                   if not isinstance(request.get("chapters"), list) or c["doc"] in request["chapters"]]}
            action = request.get("action")
            if action == "again":
                feedback = _text(request.get("feedback"), 1500)
                if not feedback and not any(a for _, a in answers):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Say what should change, or answer one of the questions.")
                job["again"] = {"previous": steering.clean_reading(edited), "feedback": feedback, "answers": answers}
                job["reading_state"] = "wanted"
            elif action == "confirm":
                mine = job["steer"]
                try:
                    job["confirmed"] = steering.confirm(Steer(mine["purpose"], mine["about"], mine["style"]), edited, answers).reading
                except SteerError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                job["reading_state"] = "confirmed"
                job["confirmed_for"] = Steer(mine["purpose"], mine["about"], mine["style"]).raw_key()
            elif action == "without":
                job["reading_state"] = "without"
            else:
                raise ApiError(HTTPStatus.BAD_REQUEST, "'action' is confirm, again or without")
            if action != "again":
                self._plan(job, request)
            self._store(job)
        self.start(job_id)

    def go_on(self, job_id: str, request: dict[str, Any]) -> None:
        """After a trial chapter: the whole book, or a trial of another chapter."""
        with self.lock:
            job = self._waiting(job_id, "tried")
            if request.get("next") not in ("whole", "trial"):
                raise ApiError(HTTPStatus.BAD_REQUEST, "'next' is whole or trial")
            self._plan(job, request)
            self._store(job)
        self.start(job_id)

    def set_steer(self, job_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """Change what the reader wants from a book. For a finished book it guides the questions asked for on
        highlights from then on. For a run that waits for its reader, the new words are read again and the run
        goes on with ``next`` from there."""
        purpose = _text(request.get("purpose"))
        with self.lock:
            job = self.get(job_id)
            waiting = job["status"] in WAITING and job_id not in self.cancels
            if not waiting and job["status"] != "done":
                raise ApiError(HTTPStatus.CONFLICT, "This can be changed when the run has finished, or while it waits for you.")
            mine = job.setdefault("steer", {"purpose": "", "interests": True, "about": "", "style": ""})
            mine["purpose"] = purpose
            if isinstance(request.get("interests"), bool):
                mine["interests"] = request["interests"]
            if waiting:
                ui = self.ui_settings()
                mine.update(about=str(ui.get("steer_about") or "") if mine["interests"] else "", style=str(ui.get("steer_style") or ""))
                steered = bool(purpose or mine["about"] or mine["style"])
                job.update(read_as=None, confirmed=None,
                           reading_state="wanted" if steered and ui.get("steer_confirm") is not False else "none")
                job.pop("again", None)
                self._plan(job, {"next": request.get("next") or ("trial" if job.get("trial") else "whole"),
                                 "doc": request.get("doc", (job.get("trial") or {}).get("doc"))})
            self._store(job)
        if waiting:
            self.start(job_id)
        return self.detail(job_id)

    # ---- writing

    def _store(self, job: dict[str, Any]) -> None:
        job["updated"] = now()
        write_json(self.folder(job["id"]) / "job.json", {k: v for k, v in job.items() if k != "report"})

    # ---- other sources than EPUB (docs/SOURCES-PLAN.md)

    HELD, HELD_SECONDS = 4, 30 * 60

    @staticmethod
    def type_of(job: dict[str, Any]) -> str:
        """What an entry of the library is: a book or an article."""
        return job["type"] if job.get("type") in sources.TYPES else "book"

    def _convert(self, entry: dict[str, Any], split_how: str, title: str | None = None, author: str | None = None,
                 whole: bool | None = None) -> sources.Made:
        """``entry`` is a held file. Its pictures are fetched once, however often it is converted."""
        cache = entry["pictures"]

        def remember(get: Any) -> Any:
            def cached(address: str) -> bytes | None:
                if address not in cache:
                    cache[address] = get(address)
                return cache[address]
            return cached

        try:
            return sources.convert(entry["name"], entry["data"], url=entry["url"], folder=entry["folder"], split_how=split_how,
                                   whole=whole, title=title, author=author, pictures=remember, fetched=entry.get("fetched"),
                                   charset=entry.get("charset"))
        except sources.SourceError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def look_source(self, name: str, data: bytes, split_how: str = "auto", folder: Path | None = None,
                    url: str | None = None) -> dict[str, Any]:
        """What the Add form shows of a file that is no EPUB. The file is converted and held, so that "Process"
        does not send it again."""
        entry = {"name": Path(name.replace("\\", "/")).name, "data": data, "folder": folder, "url": url, "pictures": {},
                 "time": time.monotonic()}
        return self._hold(entry, split_how)

    def look_url(self, url: Any, split_how: str = "auto") -> dict[str, Any]:
        """What the Add form shows of the page at a web address. The page is fetched once and held."""
        if not isinstance(url, str) or not url.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "Give a web address.")
        try:
            page = sources.fetch_page(url)
        except sources.SourceError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        entry = {"name": page.name, "data": page.data, "folder": None, "url": page.url, "pictures": {}, "time": time.monotonic(),
                 "fetched": page.fetched, "charset": page.charset}
        return self._hold(entry, split_how)

    def _hold(self, entry: dict[str, Any], split_how: str) -> dict[str, Any]:
        made = self._convert(entry, split_how)
        token = secrets.token_urlsafe(12)
        with self.lock:
            self.held[token] = entry
            for old in sorted(self.held, key=lambda t: self.held[t]["time"])[:-self.HELD]:
                del self.held[old]
        return self._seen(token, entry, made, split_how)

    def _held(self, token: Any) -> dict[str, Any]:
        with self.lock:
            entry = self.held.get(token) if isinstance(token, str) else None
            if entry is None or time.monotonic() - entry["time"] > self.HELD_SECONDS:
                if entry is not None:
                    del self.held[token]
                raise ApiError(HTTPStatus.GONE, "the app no longer holds this file; choose it again")
            return entry

    def look_held(self, token: Any, split_how: str, whole: Any = None) -> dict[str, Any]:
        """A held file converted again, with another choice for its chapters or for how much of a page is taken."""
        entry = self._held(token)
        return self._seen(token, entry, self._convert(entry, split_how, whole=_whole(whole)), split_how)

    def _seen(self, token: str, entry: dict[str, Any], made: sources.Made, split_how: str) -> dict[str, Any]:
        origin = made.origin
        with self.lock:
            same = [j for j in self.jobs.values() if isinstance(j.get("origin"), dict)
                    and (j["origin"].get("sha256") == origin["sha256"] or (origin["url"] and j["origin"].get("url") == origin["url"]))]
        return {**_looked(made.book, made.title), "type": made.type, "author": made.author, "origin": origin,
                "found": made.found, "split": split_how, "pictures": made.pictures, "warnings": made.warnings, "held": token,
                "page": made.page,
                "seen_before": max((j["created"] for j in same), default=None)}

    def create_held(self, token: Any, settings: Settings, plan: Any = None) -> dict[str, Any]:
        """Add what the form holds and start its run. ``plan`` may also say how the reader set it: ``title``,
        ``author``, ``split`` and ``type``."""
        entry = self._held(token)
        plan = plan if isinstance(plan, dict) else {}
        title, author = plan.get("title"), plan.get("author")
        made = self._convert(entry, _split_how(plan.get("split")), _text(title, 300) if isinstance(title, str) else None,
                             _text(author, 300) if isinstance(author, str) else None, _whole(plan.get("whole")))
        job = self.create(made.name, made.epub, settings, plan, made, entry)
        with self.lock:
            self.held.pop(token, None)
        return job

    def add(self, name: str, data: bytes, settings: Settings, plan: Any = None, folder: Path | None = None) -> dict[str, Any]:
        """Add a file of any kind the app takes, and start its run."""
        if _is_epub(name, data):
            return self.create(name, data, settings, plan)
        split_how = _split_how(plan.get("split") if isinstance(plan, dict) else None)
        return self.create_held(self.look_source(name, data, split_how, folder)["held"], settings, plan)

    def set_type(self, job_id: str, kind: Any) -> dict[str, Any]:
        """Say that an entry is a book, or an article. Its file in the notes folder moves with it."""
        if kind not in sources.TYPES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "an entry is a book or an article")
        with self.lock:
            job = self.get(job_id)
            job["type"] = kind
            self._store(job)
            self.filed.pop(job_id, None)
        self.file_book(job_id, quiet=True)
        return job

    def create(self, name: str, data: bytes, settings: Settings, plan: Any = None, made: sources.Made | None = None,
               original: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add a book and start its run. ``plan`` holds what the reader wants from it: ``purpose``, whether their
        interests from the settings apply (``interests``), ``trial``, a chapter to try before the whole book, and
        ``type`` (book or article). ``made`` and ``original`` come with a file that was no EPUB: the EPUB made
        from it is what is run, and the file as it was given is kept beside it."""
        name = _safe_name(name)
        if made is not None:
            book = made.book
        else:
            try:
                book = load_book_bytes(data, name)
            except EpubError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"This file cannot be read as an EPUB: {exc}") from exc
        plan = plan if isinstance(plan, dict) else {}
        ui = self.ui_settings()
        interests = plan.get("interests") is not False
        mine = {"purpose": _text(plan.get("purpose")), "interests": interests,
                "about": str(ui.get("steer_about") or "") if interests else "", "style": str(ui.get("steer_style") or "")}
        steered = bool(mine["purpose"] or mine["about"] or mine["style"])
        chapters = steering.chapter_list(book)
        trial = plan.get("trial")
        if trial is not None and (isinstance(trial, bool) or trial not in {c["index"] for c in chapters}):
            raise ApiError(HTTPStatus.BAD_REQUEST, "choose a chapter of the book to try")
        with self.lock:
            job_id = self._new_id(stem_of(name))
            folder = self.folder(job_id)
            (folder / "input").mkdir(parents=True)
            (folder / "input" / name).write_bytes(data)
            if made is not None and original is not None:
                (folder / "input" / "original").mkdir()
                (folder / "input" / "original" / _plain_name(original["name"])).write_bytes(original["data"])
            kind = plan.get("type") if plan.get("type") in sources.TYPES else made.type if made is not None else "book"
            job = {"id": job_id, "file": name, "title": book.title or stem_of(name), "created": now(), "type": kind,
                   "origin": made.origin if made is not None else None,
                   "status": "queued", "message": "Waiting to start", "stage": None, "done": None, "total": None,
                   "words": sum(len(sd.words) for sd in book.spine), "settings": settings.as_json(), "events": [],
                   "counts": None, "steer": mine, "chapters": chapters, "read_as": None, "confirmed": None, "spent": [],
                   "reading_state": "wanted" if steered and ui.get("steer_confirm") is not False else "none",
                   "trial": {"doc": trial, "state": "wanted"} if trial is not None else None}
            self.jobs[job_id] = job
            self._store(job)
        self.start(job_id)
        return job

    def start(self, job_id: str) -> None:
        with self.lock:
            job = self.get(job_id)
            if job_id in self.cancels:
                raise ApiError(HTTPStatus.CONFLICT, "this run is already going")
            job.update(status="queued", message="Waiting to start")
            self.cancels[job_id] = threading.Event()
            self._store(job)
        threading.Thread(target=self._work, args=(job_id,), name=f"run-{job_id}", daemon=True).start()

    def stop_all(self, wait: float = 5.0) -> None:
        """The app is ending: give up every model call, so that no model process is left behind and the runs save
        themselves as stopped. Waits a few seconds for the runs to do that."""
        self.stopping.set()
        with self.lock:
            for event in self.cancels.values():
                event.set()
            for asking in self.asks.values():
                asking["cancel"].set()
        until = time.monotonic() + wait
        while time.monotonic() < until:
            with self.lock:
                if not self.cancels:
                    return
            time.sleep(0.05)

    def cancel(self, job_id: str) -> None:
        with self.lock:
            self.get(job_id)
            event = self.cancels.get(job_id)
            if event is None:
                raise ApiError(HTTPStatus.CONFLICT, "this run is not going")
            event.set()

    def delete(self, job_id: str) -> None:
        with self.lock:
            self.get(job_id)
            if job_id in self.cancels:
                raise ApiError(HTTPStatus.CONFLICT, "stop the run before deleting it")
            del self.jobs[job_id]
            self.filed.pop(job_id, None)
        self._book_left(job_id)
        shutil.rmtree(self.folder(job_id), ignore_errors=True)

    def _event(self, job: dict[str, Any], event: dict[str, Any]) -> None:
        with self.lock:
            job["events"] = (job["events"] + [event])[-EVENTS_KEPT:]
            if event["stage"] == "network":  # waiting for the connection, or going on: the step stays what it was
                job.update(message=event["message"])
            elif event["stage"] != "warning":
                job.update(stage=event["stage"], message=event["message"], done=event.get("done"),
                           total=event.get("total"))

    def _read(self, job: dict[str, Any], epub: Path, settings: Settings, backend: Any, cancel: threading.Event) -> None:
        """Have the model state how it reads what the reader wants, and wait for the reader."""
        self._event(job, {"time": now(), "stage": "reading", "done": None, "total": None,
                          "message": "Reading what you want from the book, with a look at each chapter"})
        if backend is None:
            backend = default_backend(settings, lambda e: self._event(job, e), cancel)
            backend.check_available([settings.model])
        mine, again = job["steer"], job.get("again") or {}
        try:
            book = load_book_bytes(epub.read_bytes(), job["file"])
            shown, done = steering.read(backend, book, Steer(mine["purpose"], mine["about"], mine["style"]), settings,
                                        again.get("previous"), again.get("feedback", ""),
                                        [tuple(a) for a in again.get("answers", [])])
        except BackendCancelled:
            raise Cancelled() from None
        with self.lock:
            job.pop("again", None)
            job["spent"] = (job.get("spent") or []) + [{k: getattr(done, k) for k in ("model", "cost_usd", "input_tokens",
                                                                                      "output_tokens", "seconds", "priced")}]
            job.update(read_as=shown, reading_state="ready", status="asking", stage="reading", done=None, total=None,
                       message="Waiting for you: look at how your wishes were read.")

    def _work(self, job_id: str) -> None:
        job = self.jobs[job_id]
        cancel = self.cancels[job_id]
        folder = self.folder(job_id)
        with self.turn:  # one book at a time
            try:
                if cancel.is_set():
                    raise Cancelled()
                with self.lock:
                    job.update(status="running", message="Starting")
                    self._store(job)
                settings = Settings.from_json(job["settings"])
                backend = self.backend_factory(settings) if self.backend_factory else None
                epub = folder / "input" / job["file"]
                if job.get("reading_state") == "wanted":  # first the reading, and the reader's word on it
                    self._read(job, epub, settings, backend, cancel)
                    return
                steer = self._run_steer(job)
                spent = [Completion({}, **c) for c in job.get("spent") or []]
                trial = job.get("trial")
                progress = lambda e: self._event(job, e)  # noqa: E731
                try:
                    if trial and trial["state"] == "wanted":
                        tried = try_chapter(epub, folder, settings, trial["doc"], backend=backend, progress=progress,
                                            cancel=cancel, steer=steer, spent=spent)
                        with self.lock:
                            trial.update(state="done", sections=len(tried["sections"]),
                                         questions=sum(q["kept"] for sec in tried["sections"] for q in sec["questions"]))
                            job.update(status="tried", stage="tried", done=None, total=None,
                                       message="Waiting for you: the chapter you tried is ready to look at.")
                        return
                    report = run_book(epub, folder, settings, backend=backend, progress=progress, cancel=cancel,
                                      steer=steer, spent=spent)
                finally:  # the calls made before the run are in its totals from the moment it has a state
                    if any((work_dir(f) / STATE_FILE).is_file() for f in (folder, trial_dir(folder))):
                        job["spent"] = []
                with self.lock:
                    job.update(status="done", message="Finished", counts=report["counts"], stage="done")
            except Cancelled:
                with self.lock:
                    job.update(status="stopped", message="Stopped. Press Continue to go on where it left off.")
            except UsageLimitError as exc:
                with self.lock:
                    job.update(status="stopped", message=f"{exc} Press Continue when your limit has reset; nothing "
                                                         "done so far is lost.")
            except (PipelineError, BackendError, SettingsError) as exc:
                with self.lock:
                    job.update(status="failed", message=str(exc))
            except Exception as exc:  # a fault of the tool: show it instead of leaving the run "running" for ever
                traceback.print_exc()
                with self.lock:
                    job.update(status="failed", message=f"Unexpected error: {exc!r}")
            finally:
                with self.lock:
                    self.cancels.pop(job_id, None)
                    self._store(job)

    def rebuild(self, job_id: str, removed: list[str] | None, cards: str | None = None,
                preview: bool | None = None) -> dict[str, Any]:
        with self.lock:
            job = self.get(job_id)
            if job["status"] != "done" or job.get("kind") == "imported":
                raise ApiError(HTTPStatus.CONFLICT, "this book has no finished run to rebuild from")
        try:
            report = rebuild(self.folder(job_id), removed=removed, cards=cards, preview=preview)
        except PipelineError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        with self.lock:
            job["counts"] = report["counts"]
            job.pop("exports_stale", None)
            self._store(job)
        return self.detail(job_id)


class Handler(BaseHTTPRequestHandler):
    server_version = f"ebook-processor/{__version__}"
    jobs: Jobs
    claude: dict[str, Any]
    codex: dict[str, Any]
    models: list[dict[str, Any]]
    pages: dict[str, bytes]
    shutdown_token: str | None = None

    def log_message(self, format: str, *args: Any) -> None:  # keep the terminal quiet
        return

    # ---- plumbing

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_page(self, name: str) -> None:
        """One of the app's own pages, as it was when the app started (see ``make_server``)."""
        data = self.pages[name]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", PAGES[name])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        # The notebook shows the reader in a frame beside it. No other site may put these pages in a frame.
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, download: bool = False) -> None:
        data = path.read_bytes()
        kind = {".epub": "application/epub+zip", ".apkg": "application/octet-stream",
                ".tsv": "text/tab-separated-values; charset=utf-8", ".md": "text/markdown; charset=utf-8",
                ".json": "application/json; charset=utf-8", ".html": "text/html; charset=utf-8",
                }.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if download:
            ascii_name = path.name.encode("ascii", "replace").decode("ascii").replace('"', "_")
            self.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"')
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_UPLOAD:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the file is larger than 300 MB")
        return self.rfile.read(length) if length else b""

    def json_body(self) -> dict[str, Any]:
        try:
            data = json.loads(self.body() or b"{}")
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request is not JSON") from exc
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request is not a JSON object")
        return data

    def settings(self, raw: Any) -> Settings:
        if not isinstance(raw, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "settings must be an object")
        allowed = {"density", "section_words", "max_questions", "content", "kind", "rounds", "model", "checker_model",
                   "effort", "jobs", "epubcheck", "preview", "forms"}
        options = {k: (v if v != "" else None) for k, v in raw.items() if k in allowed}
        try:
            return from_options(**options)
        except (SettingsError, TypeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            if method != "GET" and self.headers.get(CSRF_HEADER) != "1":
                raise ApiError(HTTPStatus.FORBIDDEN, "this request did not come from the app's own page")
            self.route(method, parts, parse_qs(url.query))
        except ApiError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": f"Unexpected error: {exc!r}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    # ---- routes

    def route(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
        jobs = self.jobs
        if method == "GET" and not parts:
            return self.send_page("index.html")
        if method == "GET" and len(parts) == 2 and parts[0] == "read":
            jobs.get(parts[1])
            return self.send_page("reader.html")
        if method == "GET" and len(parts) == 2 and parts[0] == "static" and parts[1] in PAGES \
                and not parts[1].endswith(".html"):
            return self.send_page(parts[1])
        if not parts or parts[0] != "api":
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        match (method, parts[1:]):
            case ("GET", ["info"]):
                defaults = Settings()
                return self.send_json({"version": __version__, "claude": self.claude, "codex": self.codex, "data": str(jobs.data),
                                       "densities": DENSITIES, "defaults": defaults.as_json(), "max_questions": MAX_QUESTIONS,
                                       "limits": {"text": steering.TEXT_MAX, "instruction": steering.INSTRUCTION_MAX},
                                       "models": self.models})
            case ("GET", ["jobs"]):
                return self.send_json({"jobs": jobs.listing(), "notes_practice": jobs.notes_practice()})
            case ("POST", ["jobs"]):
                name = (query.get("name") or ["book.epub"])[0]
                try:
                    raw = json.loads((query.get("settings") or ["{}"])[0])
                except ValueError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "settings are not JSON") from exc
                settings = self.settings(raw)
                try:
                    plan = json.loads((query.get("plan") or ["{}"])[0])
                except ValueError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "the plan is not JSON") from exc
                data = self.body()
                if not data:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "no file was sent")
                return self.send_json(jobs.summary(jobs.add(name, data, settings, plan)), HTTPStatus.CREATED)
            case ("POST", ["jobs", "held"]):  # a file that is no EPUB, as the Add form holds it
                request = self.json_body()
                settings = self.settings(request.get("settings") or {})
                return self.send_json(jobs.summary(jobs.create_held(request.get("held"), settings, request.get("plan"))),
                                      HTTPStatus.CREATED)
            case ("POST", ["jobs", "local"]):
                request = self.json_body()
                path = Path(str(request.get("path", ""))).expanduser()
                if not path.is_file():
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"There is no file at {path}")
                if path.stat().st_size > MAX_UPLOAD:
                    raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the file is larger than 300 MB")
                settings = self.settings(request.get("settings") or {})
                return self.send_json(jobs.summary(jobs.add(path.name, path.read_bytes(), settings, request.get("plan"),
                                                            path.parent)), HTTPStatus.CREATED)
            case ("POST", ["look"]):  # a book's length and chapters, before anything is run or kept
                data = self.body()
                if not data:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "no file was sent")
                name = (query.get("name") or ["book.epub"])[0]
                if _is_epub(name, data):
                    return self.send_json(look(name, data))
                return self.send_json(jobs.look_source(name, data, _split_how((query.get("split") or [""])[0])))
            case ("POST", ["look", "local"]):
                request = self.json_body()
                path = Path(str(request.get("path", ""))).expanduser()
                if not path.is_file():
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"There is no file at {path}")
                if path.stat().st_size > MAX_UPLOAD:
                    raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the file is larger than 300 MB")
                data = path.read_bytes()
                if _is_epub(path.name, data):
                    return self.send_json(look(path.name, data))
                return self.send_json(jobs.look_source(path.name, data, _split_how(request.get("split")), folder=path.parent))
            case ("POST", ["look", "held"]):
                request = self.json_body()
                return self.send_json(jobs.look_held(request.get("held"), _split_how(request.get("split")), request.get("whole")))
            case ("POST", ["look", "url"]):  # the page at a web address, fetched once and held
                request = self.json_body()
                return self.send_json(jobs.look_url(request.get("url"), _split_how(request.get("split"))))
            case ("POST", ["jobs", "url"]):
                request = self.json_body()
                settings = self.settings(request.get("settings") or {})
                plan = request.get("plan")
                held = jobs.look_url(request.get("url"), _split_how(plan.get("split") if isinstance(plan, dict) else None))["held"]
                return self.send_json(jobs.summary(jobs.create_held(held, settings, plan)), HTTPStatus.CREATED)
            case ("POST", ["estimate"]):
                request = self.json_body()
                return self.send_json(estimate(request.get("words"), self.settings(request.get("settings") or {}),
                                               request.get("steered") is True))
            case ("POST", ["books", "import"]):
                data = self.body()
                if not data:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "no file was sent")
                return self.send_json(jobs.summary(jobs.import_prepared((query.get("name") or ["book.epub"])[0], data)),
                                      HTTPStatus.CREATED)
            case ("POST", ["shutdown"]):  # asked for by 'ebook-processor update', which holds the token
                token = self.shutdown_token
                if not token or self.headers.get("X-Shutdown-Token") != token:
                    raise ApiError(HTTPStatus.FORBIDDEN, "stopping the app needs its token")
                self.send_json({"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            case ("GET", ["practice"]):
                return self.send_json(jobs.practice((query.get("job") or [None])[0]))
            case ("POST", ["practice", "answer"]):
                request = self.json_body()
                if not isinstance(request.get("job"), str) or not isinstance(request.get("id"), str) \
                        or not isinstance(request.get("remembered"), bool):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "an answer needs job, id and remembered")
                jobs.practice_answer(request["job"], request["id"], request["remembered"], str(request.get("text") or ""))
                return self.send_json(jobs.practice(request["only"] if isinstance(request.get("only"), str) else None))
            case ("PUT", ["practice", "settings"]):
                settings = srs.clean_settings(self.json_body())
                write_json(jobs.data / "srs-settings.json", settings)
                return self.send_json(settings)
            case ("GET", ["practice", "settings"]):
                return self.send_json(jobs.srs_settings())
            case ("GET", ["settings"]):
                return self.send_json(jobs.ui_settings())
            case ("PUT", ["settings"]):
                return self.send_json(jobs.ui_settings(self.json_body()))
            case ("GET", ["vault"]):
                return self.send_json(jobs.vault_home())
            case ("GET", ["vault", "folder"]):
                return self.send_json(jobs.folder_state())
            case ("GET", ["vault", "search"]):
                one = lambda name: (query.get(name) or [""])[0]
                return self.send_json(jobs.vault_search(one("q"), one("kind"), one("tag"), one("book")))
            case ("GET", ["vault", "complete"]):
                return self.send_json({"notes": jobs.vault().complete((query.get("q") or [""])[0][:200])})
            case ("GET", ["vault", "anchors"]):
                return self.send_json(jobs.vault().anchors((query.get("target") or [""])[0][:300]))
            case ("GET", ["vault", "peek"]):
                one = lambda name: (query.get(name) or [""])[0][:300]
                return self.send_json(jobs.vault_peek(one("target"), one("anchor"), one("from") or None, one("full") == "1"))
            case ("GET", ["vault", "tags"]):
                return self.send_json({"tags": jobs.vault().tags()})
            case ("GET", ["vault", "trash"]):
                return self.send_json({"notes": jobs.vault().trash()})
            case ("POST", ["vault", "trash", "restore"]):
                vault = jobs.vault()
                return self.send_json(jobs._note(jobs._vaulted(lambda: vault.restore(self.json_body().get("name")))))
            case ("GET", ["process"]):
                return self.send_json(jobs.process_view((query.get("job") or [None])[0]))
            case ("POST", ["process", "aside"]):
                return self.send_json(jobs.process_aside(self.json_body()))
            case ("POST", ["process", "add"]):
                return self.send_json(jobs.process_add(self.json_body()))
            case ("POST", ["vault", "related"]):
                return self.send_json(jobs.vault_related(self.json_body()))
            case ("POST", ["vault", "cite"]):
                return self.send_json(jobs.vault_cite(self.json_body()))
            case ("POST", ["vault", "notes"]):
                return self.send_json(jobs.vault_create(self.json_body()), HTTPStatus.CREATED)
            case ("GET", ["vault", "notes", note_id]):
                return self.send_json(jobs.vault_note(note_id))
            case ("GET", ["assist", "connections"]):
                return self.send_json(jobs.assist_sent((query.get("note") or [""])[0]))
            case ("POST", ["assist", "connections"]):
                return self.send_json(jobs.assist_connections(str(self.json_body().get("note") or "")))
            case ("POST", ["assist", "answer"]):
                return self.send_json(jobs.assist_answer(self.json_body()))
            case ("GET", ["assist", "questions"]):
                return self.send_json(jobs.note_questions((query.get("note") or [""])[0]))
            case ("POST", ["assist", "questions"]):
                r = self.json_body()
                note_id = str(r.get("note") or "")
                return self.send_json(jobs.note_question_write(note_id, r) if "question" in r else jobs.note_question_propose(note_id, r))
            case ("PUT", ["assist", "questions", qid]):
                return self.send_json(jobs.note_question_change(qid, self.json_body()))
            case ("DELETE", ["assist", "questions", qid]):
                return self.send_json(jobs.note_question_delete(qid))
            case ("GET", ["vault", "graph"]):
                return self.send_json(jobs.vault().graph())
            case ("GET", ["vault", "revisit"]):
                return self.send_json(jobs.vault().revisit(jobs.today()))
            case ("GET", ["timeline"]):
                before = (query.get("before") or [None])[0]
                if before is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", before):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'before' is a day, as 2026-11-19")
                return self.send_json(jobs.timeline(before))
            case ("GET", ["vault", "notes", note_id, "neighbourhood"]):
                vault = jobs.vault()
                return self.send_json(jobs._vaulted(lambda: vault.neighbourhood(note_id)))
            case ("GET", ["vault", "projects"]):
                return self.send_json({"projects": jobs.vault().projects()})
            case ("POST", ["vault", "projects", project_id, "gather"]):
                return self.send_json(jobs.vault_gather(project_id, self.json_body()))
            case ("GET", ["vault", "notes", note_id, "export.md"]):
                vault = jobs.vault()
                text = jobs._vaulted(lambda: vault.export(note_id)).encode("utf-8")
                name = jobs._vaulted(lambda: vault.get(note_id)).title.encode("ascii", "replace").decode("ascii").replace('"', "_")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(text)))
                self.send_header("Content-Disposition", f'attachment; filename="{name}.md"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(text)
                return None
            case ("GET", ["vault", "notes", note_id, "token"]):
                vault = jobs.vault()
                return self.send_json({"token": jobs._vaulted(lambda: vault.get(note_id)).token})
            case ("PUT", ["vault", "notes", note_id]):
                try:
                    return self.send_json(jobs.vault_save(note_id, self.json_body()))
                except Conflict as exc:  # changed on disk since the page loaded it: the page asks which to keep
                    return self.send_json({"error": str(exc), "current": jobs._note(exc.current)}, HTTPStatus.CONFLICT)
            case ("DELETE", ["vault", "notes", note_id]):
                return self.send_json(jobs.vault_delete(note_id))
            case ("GET", ["jobs", job_id, "notes"]):
                return self.send_json(jobs.notes_do(job_id))
            case ("POST", ["jobs", job_id, "notes", "highlight"]):
                r = self.json_body()
                made: dict[str, Any] = {}
                store = jobs.notes_do(job_id, lambda st, bk: made.update(
                    notes.add_highlight(st, bk, r.get("chapter"), r.get("start"), r.get("end"), r.get("note"))))
                return self.send_json({**store, "made": made.get("id")})
            case ("PUT", ["jobs", job_id, "notes", "target"]):
                r = self.json_body()
                target = r.get("target") if isinstance(r.get("target"), dict) else {}
                return self.send_json(jobs.notes_do(job_id, lambda st, bk: notes.set_note(st, bk, target, r.get("text"))))
            case ("PUT", ["jobs", job_id, "notes", "item", item]):
                r = self.json_body()
                return self.send_json(jobs.notes_do(job_id, lambda st, bk: notes.update_item(st, item, r.get("text"))))
            case ("DELETE", ["jobs", job_id, "notes", "item", item]):
                keep = (query.get("questions") or ["remove"])[0] == "keep"
                return self.send_json(jobs.remove_marked(job_id, item, keep))
            case ("GET", ["jobs", job_id, "questions"]):
                return self.send_json(jobs.questions_state(job_id))
            case ("POST", ["jobs", job_id, "questions", "propose"]):
                r = self.json_body()
                if isinstance(r.get("highlight"), str):
                    return self.send_json(jobs.propose(job_id, [r["highlight"]], auto=r.get("auto") is True,
                                                       instruction=_text(r.get("instruction"), steering.INSTRUCTION_MAX)))
                chapter = r.get("chapter")
                if chapter is not None and (not isinstance(chapter, int) or isinstance(chapter, bool)):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'chapter' must be a number")
                if chapter is None and r.get("book") is not True:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "name a highlight, a chapter or the book")
                return self.send_json(jobs.propose(job_id, None, chapter=chapter))
            case ("POST", ["jobs", job_id, "questions", "stop"]):
                return self.send_json(jobs.stop_questions(job_id))
            case ("POST", ["jobs", job_id, "questions"]):
                r = self.json_body()
                return self.send_json(jobs.write_question(job_id, r.get("highlight"), r.get("question"), r.get("answer"), r.get("form")))
            case ("PUT", ["jobs", job_id, "questions", qid]):
                return self.send_json(jobs.change_question(job_id, qid, self.json_body()))
            case ("DELETE", ["jobs", job_id, "questions", qid]):
                return self.send_json(jobs.discard_question(job_id, qid))
            case ("GET", ["jobs", job_id, "notebook"]):
                return self.send_json(jobs.notebook(job_id))
            case ("GET", ["jobs", job_id, "notebook.md"]):
                data = jobs.notebook(job_id)
                text = notes.markdown(data, (query.get("answers") or ["1"])[0] != "0").encode("utf-8")
                name = stem_of(jobs.get(job_id)["file"]).encode("ascii", "replace").decode("ascii").replace('"', "_")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(text)))
                self.send_header("Content-Disposition", f'attachment; filename="{name}.notes.md"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(text)
                return None
            case ("GET", ["jobs", job_id, "book"]):
                return self.send_json(jobs.manifest(job_id))
            case ("GET", ["jobs", job_id, "book-questions", qid]):
                return self.send_json(jobs.book_question(job_id, qid))
            case ("PUT", ["jobs", job_id, "book-questions", qid]):
                return self.send_json(jobs.edit_question(job_id, qid, self.json_body()))
            case ("DELETE", ["jobs", job_id, "book-questions", qid]):
                return self.send_json(jobs.delete_question(job_id, qid))
            case ("POST", ["jobs", job_id, "book-questions"]):
                return self.send_json(jobs.add_question(job_id, self.json_body()))
            case ("POST", ["jobs", job_id, "book-questions", qid, "restore"]):
                return self.send_json(jobs.restore_question(job_id, qid))
            case ("POST", ["jobs", job_id, "book-questions", qid, "restart"]):
                return self.send_json(jobs.restart_question(job_id, qid))
            case ("POST", ["jobs", job_id, "book-questions", qid, "check"]):
                return self.send_json(jobs.check_question(job_id, qid))
            case ("POST", ["jobs", job_id, "book-questions", qid, "rewrite"]):
                return self.send_json(jobs.rewrite_question(job_id, qid, self.json_body()))
            case ("GET", ["jobs", job_id, "sections", sid, "paragraphs"]):
                return self.send_json(jobs.section_paragraphs(job_id, sid))
            case ("GET", ["jobs", job_id, "book", "chapter", number]):
                try:
                    return self.send_json(jobs.book(job_id).chapter(int(number)))
                except (ValueError, reading.ReadingError) as exc:
                    raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            case ("GET", ["jobs", job_id, "book", "asset"]):
                try:
                    data, kind = jobs.book(job_id).asset((query.get("path") or [""])[0])
                except reading.ReadingError as exc:
                    raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Security-Policy", "sandbox")  # an SVG from a book never runs as a page
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return None
            case ("GET", ["jobs", job_id, "book", "search"]):
                return self.send_json({"results": jobs.book(job_id).search((query.get("q") or [""])[0])})
            case ("GET", ["jobs", job_id, "reading"]):
                return self.send_json(jobs.reading_state(job_id))
            case ("PUT", ["jobs", job_id, "reading"]):
                state = jobs.save_reading(job_id, self.json_body())
                return self.send_json({"saved": True, "grades": state["grades"]})
            case ("PUT", ["jobs", job_id, "reading", "place"]):
                state = jobs.set_place(job_id, self.json_body())
                return self.send_json({"saved": True, "position": state["position"], "progress": state["x_progress"]})
            case ("GET", ["jobs", job_id]):
                return self.send_json(jobs.detail(job_id))
            case ("DELETE", ["jobs", job_id]):
                jobs.delete(job_id)
                return self.send_json({"deleted": job_id})
            case ("POST", ["jobs", job_id, "type"]):
                return self.send_json(jobs.summary(jobs.set_type(job_id, self.json_body().get("type"))))
            case ("POST", ["jobs", job_id, "cancel"]):
                jobs.cancel(job_id)
                return self.send_json({"ok": True})
            case ("POST", ["jobs", job_id, "reading"]):
                jobs.answer_reading(job_id, self.json_body())
                return self.send_json({"ok": True})
            case ("POST", ["jobs", job_id, "next"]):
                jobs.go_on(job_id, self.json_body())
                return self.send_json({"ok": True})
            case ("PUT", ["jobs", job_id, "steer"]):
                return self.send_json(jobs.set_steer(job_id, self.json_body()))
            case ("POST", ["jobs", job_id, "continue"]):
                if jobs.get(job_id)["status"] not in ("stopped", "failed"):
                    raise ApiError(HTTPStatus.CONFLICT, "this run cannot be continued")
                jobs.start(job_id)
                return self.send_json({"ok": True})
            case ("GET", ["jobs", job_id, "review"]):
                if jobs.get(job_id)["status"] != "done" or jobs.get(job_id).get("kind") == "imported":
                    raise ApiError(HTTPStatus.CONFLICT, "this book has no finished run to review")
                try:
                    return self.send_json(review_data(jobs.folder(job_id)))
                except PipelineError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            case ("POST", ["jobs", job_id, "rebuild"]):
                request = self.json_body()
                removed, cards, preview = request.get("removed"), request.get("cards"), request.get("preview")
                if preview is not None and not isinstance(preview, bool):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'preview' must be true or false")
                if removed is not None and (not isinstance(removed, list) or not all(isinstance(r, str) for r in removed)):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'removed' must be a list of question ids")
                if cards is not None and not isinstance(cards, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'cards' must be all, missed or reached")
                return self.send_json(jobs.rebuild(job_id, removed, cards, preview))
            case ("GET", ["jobs", job_id, "files", name]):
                job = jobs.get(job_id)
                folder = jobs.folder(job_id)
                stem = stem_of(job["file"])
                path = folder / name
                if Path(name).name != name or not name.startswith(stem + ".") or path.parent != folder \
                        or not path.is_file():
                    raise ApiError(HTTPStatus.NOT_FOUND, "no such file")
                if job.get("exports_stale") and name.endswith((".anki.apkg", ".cards.tsv", ".questions.json")):
                    jobs.rebuild(job_id, None)  # questions from highlights changed since the files were written
                return self.send_file(path, download=True)
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")


def claude_status() -> dict[str, Any]:
    try:
        return {"ok": True, "version": ClaudeCodeBackend().check_available()}
    except BackendError as exc:
        return {"ok": False, "error": str(exc)}


def codex_status() -> dict[str, Any]:
    """Codex is optional: ``installed`` tells a missing program from one that is there but not signed in."""
    try:
        return {"ok": True, "installed": True, "version": CodexBackend().check_available()}
    except BackendError as exc:
        return {"ok": False, "installed": "was not found" not in str(exc), "error": str(exc)}


def make_server(data: Path, host: str = "127.0.0.1", port: int = 8765, backend_factory: Any = None,
                claude: dict[str, Any] | None = None, models: list[dict[str, Any]] | None = None,
                shutdown_token: str | None = None, codex: dict[str, Any] | None = None) -> ThreadingHTTPServer:
    """``claude``, ``codex`` and ``models`` are asked of the two tools once, when the app starts, unless given."""
    claude = claude if claude is not None else claude_status()
    if codex is None:  # a caller that gives the models has said what there is: Codex is not looked for
        codex = codex_status() if models is None else {"ok": False, "installed": False, "error": "not looked for"}
    if models is None:
        asked = CodexBackend() if codex.get("ok") else None
        models = available_models(ClaudeCodeBackend() if claude.get("ok") else None, asked)
        if asked and asked.models_note:  # Codex is ready but would not say which models it has
            codex = {**codex, "models_note": asked.models_note}
    # The pages are read once, now. A page and the server behind it then always come from the same version of
    # the tool: updating the files while the app runs changes nothing until it is started again. Read on every
    # request, a new page would be served by old code, and fail on whatever the new page asks for.
    pages = {name: (STATIC / name).read_bytes() for name in PAGES}
    handler = type("BoundHandler", (Handler,), {"jobs": Jobs(data, backend_factory), "claude": claude, "codex": codex,
                                                "models": models, "pages": pages, "shutdown_token": shutdown_token})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(data: Path, host: str, port: int, open_browser: bool, notes_folder: Path | None = None) -> None:
    """``notes_folder`` points the app at a notes folder, as the field in Settings does; it is remembered."""
    from .. import update

    try:
        server = make_server(data, host, port)
    except OSError as exc:
        raise PipelineError(f"the app cannot listen on port {port} ({exc.strerror or exc}). Is it already running? "
                            "Give --port to use another port.") from exc
    port = server.server_address[1]
    library: Jobs = server.RequestHandlerClass.jobs
    if notes_folder is not None:
        try:
            library.ui_settings({"notes_folder": str(notes_folder)})
        except ApiError as exc:
            server.server_close()
            raise PipelineError(str(exc)) from exc
    # A record of this app while it runs, so that 'ebook-processor update' can find it and ask it to stop.
    record = update.write_record(host, port, data)
    server.RequestHandlerClass.shutdown_token = record["token"]
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"ebook-processor {__version__} is running at {url}\nRuns are kept in {data}\n"
          f"Notes are kept in {library.notes_folder()}\nPress Ctrl-C to stop.", flush=True)
    note = server.RequestHandlerClass.codex.get("models_note")
    if note:
        print(f"Codex is ready, but its models could not be listed ({note}). The Model fields offer Codex's default "
              "and 'Another Codex model', where a name can be typed.", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
        print("Stopped on request.")
    except KeyboardInterrupt:
        print("\nStopping. Unfinished runs can be continued the next time.")
    finally:
        library.stop_all()
        server.server_close()
        update.remove_record(port, os.getpid())
