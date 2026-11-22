"""The local web app: add a book or an article, watch the run, review the questions, download the results.

It is a small HTTP server from the standard library plus its own pages (the files in ``static/``). It listens on this computer only
(127.0.0.1) unless told otherwise, and runs one book at a time.

Each run lives in its own folder under the data folder:

    <data>/<run id>/job.json           what the page shows about the run
    <data>/<run id>/input/Book.epub    the uploaded book
    <data>/<run id>/Book.*             the outputs (see ebook_processor/outputs.py)
    <data>/<run id>/work/              the run's state
"""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import os
import secrets
import shutil
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, notes, reading, sources, srs
from .. import highlight_questions as hq  # noqa: F401  kept for the tests, which reach the module as server.hq
from .. import steer as steering
from ..llm import BackendCancelled, BackendError, ClaudeCodeBackend, CodexBackend, Completion, UsageLimitError, available_models
from ..outputs import load_report, stem_of
from ..pipeline import STATE_FILE
from ..prompts import CRITERIA_WORDS
from ..prep.epub import EpubError, load_book_bytes
from ..runner import (Cancelled, PipelineError, default_backend, models_for, rebuild, review_data,
                      run_book, trial_dir, try_chapter, work_dir)
from ..settings import DENSITIES, MAX_QUESTIONS, Settings, SettingsError, from_options
from ..steer import Steer, SteerError
from ..store import now, write_json
from ..vault import Conflict, Vault
from .common import (EVENTS_KEPT, JOB_ID_RE, LONG_SETTINGS, NOTES_FOLDER, SETTINGS_FILE, WAITING, ApiError, _is_epub, _looked,
                     _plain_name, _safe_name, _slug, _split_how, _text, _whole, estimate)
from .notes_folder import NotesFolder
from .questions import Questions
from .study import Study

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 300 * 1024 * 1024
ACTIVE = ("queued", "running")
PAGES = {"index.html": "text/html; charset=utf-8", "reader.html": "text/html; charset=utf-8",
         "base.js": "text/javascript; charset=utf-8",
         "graph.js": "text/javascript; charset=utf-8", "process.js": "text/javascript; charset=utf-8",
         "settings.js": "text/javascript; charset=utf-8", "forms.js": "text/javascript; charset=utf-8",
         "notes.js": "text/javascript; charset=utf-8", "editor.js": "text/javascript; charset=utf-8"}   # the app's own files, by content type
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CSRF_HEADER = "X-Ebook-Processor"  # a page of another site cannot send this header, so it cannot start runs


def look(name: str, data: bytes) -> dict[str, Any]:
    """What the Add form shows of an EPUB before anything is run: its length and its chapters."""
    try:
        book = load_book_bytes(data, _safe_name(name))
    except EpubError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"This file cannot be read as an EPUB: {exc}") from exc
    return {**_looked(book, book.title or stem_of(name)), "type": "book"}


class LibraryLock:
    """The lock of the library (``Jobs.lock``): one thread at a time, and the same thread any number of times. It
    knows whether the calling thread holds it, which the order of the locks is checked with (see ``Jobs``)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mine = threading.local()

    def __enter__(self) -> "LibraryLock":
        self._lock.acquire()
        self._mine.depth = getattr(self._mine, "depth", 0) + 1
        return self

    def __exit__(self, *exc: Any) -> None:
        self._mine.depth -= 1
        self._lock.release()

    def held(self) -> bool:
        return getattr(self._mine, "depth", 0) > 0


class Jobs(Study, Questions, NotesFolder):
    """The runs on disk, and the one worker thread that processes them in turn."""

    def __init__(self, data: Path, backend_factory: Any = None):
        self.data = data
        self.data.mkdir(parents=True, exist_ok=True)
        self.notes_default = Path(os.path.abspath(self.data)).parent / NOTES_FOLDER
        self._vault: Vault | None = None
        self.filed: dict[str, tuple[int, int]] = {}  # book id -> what its file in the notes folder was made from
        # Two kinds of lock, and one order. ``self.lock`` guards the library: the entries, the small stores of a
        # book (reading.json, notes.json, srs.json, process.json, job.json) and the caches. It is held for moments
        # only: never across a model call, and never while a book's files are built. A book's own lock
        # (``self.booked(id)``) guards its run and its built files: an edit, a rebuild and a download that must
        # rebuild first follow one another. THE ORDER: a book's lock is taken first, the library's lock second. A
        # thread that holds the library's lock must not ask for a book's lock; ``booked`` refuses it, so a wrong
        # order fails at once and cannot become a deadlock that shows once a year.
        self.lock = LibraryLock()
        self.book_locks: dict[str, threading.RLock] = {}
        self._book_locks = threading.Lock()   # guards the dictionary above
        self._booked = threading.local()      # the book locks this thread holds
        self.turn = threading.Lock()  # held by the run that is being processed
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.stopping = threading.Event()  # set when the app ends: every model call still running gives up
        self.looked: dict[tuple[str, ...], float] = {}  # when the tools for these models were last found ready
        # A book's outline is kept for every book (it is small); the text of the last few books that were read.
        self.books: dict[str, tuple[list[int], reading.Outline]] = {}
        self.parsed: dict[str, tuple[list[int], reading.ReadingBook]] = {}
        self.studied: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}  # see _study
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
            if not isinstance(job, dict) or not all(isinstance(job.get(k), str) for k in ("id", "status", "created", "file")):
                print(f"Skipped {path}: it is not an entry of the library as the app writes one.", flush=True)
                continue
            if not JOB_ID_RE.match(job["id"]) or job["id"] != path.parent.name:
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
        out.update(self._study(job["id"]) if out["can_read"] else {"reading": None, "practice": None, "notes": None})
        return out

    def _study(self, job_id: str) -> dict[str, Any]:
        """What the library shows of the reader's work on a book: reading, practice, highlights and notes. It is
        worked out again only when one of the three files it comes from has changed, or the day has: the library
        page asks for every book every few seconds while a run is going."""
        folder, today = self.folder(job_id), self.today()

        def stamps() -> tuple[Any, ...]:
            found = []
            for name in (reading.STATE_FILE, srs.FILE, notes.FILE):
                try:
                    at = (folder / name).stat()
                    found.append((at.st_mtime_ns, at.st_size))
                except OSError:
                    found.append(None)
            return (*found, today)
        with self.lock:
            cached = self.studied.get(job_id)
            if cached is not None and cached[0] == stamps():
                return dict(cached[1])
            cards = self.cards(job_id)  # takes in new answers, which may write the cards' file: the stamps are read after it
            marked = notes.load(folder)
            made = {"reading": reading.summary(reading.load_state(folder)), "practice": srs.counts(cards, today),
                    "notes": {"highlights": len(marked["highlights"]),
                              "notes": len(marked["notes"]) + sum(bool(h.get("note")) for h in marked["highlights"])}}
            self.studied[job_id] = (stamps(), made)
            return dict(made)

    # ---- practice (spaced repetition)

    today = staticmethod(_dt.date.today)

    def editable(self, job_id: str) -> bool:
        """Whether the book's questions can be changed: it was processed here, and its run is finished."""
        job = self.get(job_id)
        return job["status"] == "done" and not self.imported(job)

    @staticmethod
    def imported(job: dict[str, Any]) -> bool:
        """Whether an entry is a prepared book that was made elsewhere and added: it can be read, and has no run
        to change or to build from. (An entry's ``kind`` says only this. Whether it is a book or an article is its
        ``type``; the kind of book the questions are written for is in its settings.)"""
        return job.get("kind") == "imported"

    @contextmanager
    def booked(self, job_id: str) -> Any:
        """Hold the lock of a book's run and its built files. See ``__init__`` for the order of the locks."""
        mine = self._booked.__dict__.setdefault("held", {})
        if self.lock.held() and not mine.get(job_id):
            raise RuntimeError("a book's lock was asked for while the library's lock is held: a book's lock comes first")
        with self._book_locks:
            lock = self.book_locks.setdefault(job_id, threading.RLock())
        with lock:
            mine[job_id] = mine.get(job_id, 0) + 1
            try:
                yield
            finally:
                mine[job_id] -= 1

    def _built(self, job: dict[str, Any], stale: Any, counts: dict[str, Any] | None) -> None:
        """A book's files were built again: keep the new counts. The exports are fresh again unless a question from
        a highlight changed while they were built (``stale`` is the mark as it was before the build)."""
        with self.lock:
            if counts is not None:
                job["counts"] = counts
            if job.get("exports_stale") == stale:
                job.pop("exports_stale", None)
            self._store(job)

    def _backend(self, settings: Settings, roles: list[str], cancel: threading.Event | None = None,
                 progress: Any = None, waiting: bool = True) -> Any:
        """A backend for these roles, its tools looked for: the stand-in of a test, or Claude Code and Codex. The
        look at the tools (a program started, one request to the service) is good for a minute. ``waiting`` gives
        the calls the short limits of a request the reader waits for; a set ``cancel``, or the end of the app,
        gives them up."""
        if self.backend_factory:
            return self.backend_factory(settings)
        models = tuple(sorted(set(models_for(settings, roles))))
        backend = default_backend(settings, progress, cancel or self.stopping, waiting)
        if time.monotonic() - self.looked.get(models, float("-inf")) > 60:
            backend.check_available(list(models))
            self.looked[models] = time.monotonic()
        return backend

    def _asked(self, call: Any) -> Any:
        try:
            return call()
        except UsageLimitError as exc:
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, str(exc)) from exc
        except (BackendError, BackendCancelled) as exc:
            raise ApiError(HTTPStatus.BAD_GATEWAY, f"The model could not be asked: {exc}") from exc

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

    def _readable(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        return bool(job) and job["status"] == "done" and self.prepared(job_id).is_file()

    # ---- reading

    def prepared(self, job_id: str) -> Path:
        return self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.prepared.epub"

    def book(self, job_id: str) -> reading.Outline:
        """The run's prepared book. Its outline comes from a small file beside it, and is kept until the book changes
        (a rebuild writes it again). Its text is read from the EPUB only when it is asked for."""
        self.get(job_id)
        path = self.prepared(job_id)
        try:
            found = path.stat()
        except OSError:
            raise ApiError(HTTPStatus.NOT_FOUND, "this book has no file to read yet") from None
        stamp = [found.st_mtime_ns, found.st_size]
        with self.lock:
            cached = self.books.get(job_id)
            if cached is not None and cached[0] == stamp:
                return cached[1]
        folder = self.folder(job_id)
        kept = reading.load_outline(folder, stamp)
        if kept is None:  # the first time, or the book was built again
            kept = reading.outline_of(self._text(job_id, path, stamp))
            reading.save_outline(folder, stamp, kept)
        made = reading.Outline(kept, lambda: self._text(job_id, path, stamp))
        with self.lock:
            self.books[job_id] = (stamp, made)
        return made

    PARSED_KEPT = 4

    def _text(self, job_id: str, path: Path, stamp: list[int]) -> reading.ReadingBook:
        """The whole book, read from its EPUB. The last few are kept: a book of 50,000 words holds about 13 MB."""
        with self.lock:
            cached = self.parsed.get(job_id)
            if cached is not None and cached[0] == stamp:
                self.parsed[job_id] = self.parsed.pop(job_id)  # used last: dropped last
                return cached[1]
        try:
            full = reading.open_reading_book(path)
        except reading.ReadingError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        with self.lock:
            self.parsed.pop(job_id, None)
            self.parsed[job_id] = (stamp, full)
            while len(self.parsed) > self.PARSED_KEPT:
                self.parsed.pop(next(iter(self.parsed)))
        return full

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
        def one(job: dict[str, Any]) -> dict[str, Any]:
            try:
                return self.summary(job)
            except Exception as exc:  # noqa: BLE001  a file of this entry changed by hand: it costs this entry only
                return {**{k: job.get(k) for k in ("id", "file", "title", "created", "updated", "counts", "words", "kind", "origin")},
                        "type": self.type_of(job), "status": "failed", "stage": None, "done": None, "total": None,
                        "message": f"The files of this entry cannot be read ({exc!r}). They are in {self.folder(job['id'])}.",
                        "can_read": False, "reading": None, "practice": None, "notes": None}
        with self.lock:  # held to copy the list only: a long library does not hold up every other request
            self._forget_held()
            jobs = sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)
        return [one(j) for j in jobs]

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

    @staticmethod
    def _steer_of(job: dict[str, Any]) -> Steer:
        """The reader's own words for a book that waits for them, as the entry keeps them."""
        mine = job["steer"]
        return Steer(mine["purpose"], mine["about"], mine["style"])

    @staticmethod
    def _own_texts(ui: dict[str, Any], interests: Any) -> dict[str, str]:
        """The reader's two texts from the settings: ``about``, what matters to them (empty when their interests do
        not apply to this book), and ``style``, how they like questions."""
        return {"about": str(ui.get("steer_about") or "") if interests else "", "style": str(ui.get("steer_style") or "")}

    def steer_for(self, job_id: str) -> Steer | None:
        """What the writer of a question on a highlight is told: the reader's words as they are now. The reading
        confirmed for the book's run stands for them for as long as they are the words it was made from."""
        job = self.get(job_id)
        ui = self.ui_settings()
        mine = job.get("steer") or {}
        own = self._own_texts(ui, mine.get("interests", True))
        now_ = Steer(mine.get("purpose", ""), own["about"], own["style"])
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
                try:
                    job["confirmed"] = steering.confirm(self._steer_of(job), edited, answers).reading
                except SteerError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                job["reading_state"] = "confirmed"
                job["confirmed_for"] = self._steer_of(job).raw_key()
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
                mine.update(self._own_texts(ui, mine["interests"]))
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

    def _forget_held(self) -> None:
        """Drop the files of the Add form that were looked at and left (the caller holds the lock). A held file is up
        to 50 MB with its pictures on top, and without this it stayed until the form was used again."""
        for token in [t for t, e in self.held.items() if time.monotonic() - e["time"] > self.HELD_SECONDS]:
            del self.held[token]

    def _hold(self, entry: dict[str, Any], split_how: str) -> dict[str, Any]:
        made = self._convert(entry, split_how)
        token = secrets.token_urlsafe(12)
        with self.lock:
            self._forget_held()
            self.held[token] = entry
            for old in sorted(self.held, key=lambda t: self.held[t]["time"])[:-self.HELD]:
                del self.held[old]
        return self._seen(token, made, split_how)

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
        return self._seen(token, self._convert(entry, split_how, whole=_whole(whole)), split_how)

    def _seen(self, token: str, made: sources.Made, split_how: str) -> dict[str, Any]:
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
        mine = {"purpose": _text(plan.get("purpose")), "interests": interests, **self._own_texts(ui, interests)}
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
        with self.booked(job_id):  # not while its files are being built: the build would write into a folder that is gone
            with self.lock:
                self.get(job_id)
                if job_id in self.cancels:
                    raise ApiError(HTTPStatus.CONFLICT, "stop the run before deleting it")
                del self.jobs[job_id]
                self.filed.pop(job_id, None)
                self.books.pop(job_id, None)
                self.parsed.pop(job_id, None)
                self.studied.pop(job_id, None)
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
            backend = self._backend(settings, ["draft"], cancel, lambda e: self._event(job, e), waiting=False)
        again = job.get("again") or {}
        try:
            book = load_book_bytes(epub.read_bytes(), job["file"])
            shown, done = steering.read(backend, book, self._steer_of(job), settings,
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
            except Exception as exc:  # noqa: BLE001 a fault of the tool: show it instead of leaving the run "running" for ever
                traceback.print_exc()
                with self.lock:
                    job.update(status="failed", message=f"Unexpected error: {exc!r}")
            finally:
                with self.lock:
                    self.cancels.pop(job_id, None)
                    self._store(job)

    def rebuild(self, job_id: str, removed: list[str] | None, cards: str | None = None,
                preview: bool | None = None) -> dict[str, Any]:
        if preview is not None and not isinstance(preview, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "'preview' must be true or false")
        if removed is not None and (not isinstance(removed, list) or not all(isinstance(r, str) for r in removed)):
            raise ApiError(HTTPStatus.BAD_REQUEST, "'removed' must be a list of question ids")
        if cards is not None and not isinstance(cards, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "'cards' must be all, missed or reached")
        with self.booked(job_id):  # one build of a book's files at a time, and none during an edit
            with self.lock:
                job = self.get(job_id)
                if job["status"] != "done" or self.imported(job):
                    raise ApiError(HTTPStatus.CONFLICT, "this book has no finished run to rebuild from")
                stale = job.get("exports_stale")
            try:
                report = rebuild(self.folder(job_id), removed=removed, cards=cards, preview=preview)
            except PipelineError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            self._built(job, stale, report["counts"])
        return self.detail(job_id)

    def fresh_exports(self, job_id: str) -> None:
        """Build the files again when questions from highlights changed since they were written. Several downloads
        asked for together wait for the first one's build and find the files fresh."""
        with self.booked(job_id):
            with self.lock:
                stale = self.get(job_id).get("exports_stale")
            if stale:
                self.rebuild(job_id, None)


class Handler(BaseHTTPRequestHandler):
    server_version = f"ebook-processor/{__version__}"
    jobs: Jobs
    claude: dict[str, Any]
    codex: dict[str, Any]
    models: list[dict[str, Any]]
    pages: dict[str, bytes]
    shutdown_token: str | None = None
    # The names this app answers to; None when it was told to listen on another address than this computer.
    hosts: set[str] | None = LOCAL_HOSTS

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

    def send_text(self, text: str, name: str, kind: str) -> None:
        """A text as a file to keep: a download called ``name``, of the content type ``kind``."""
        data = text.encode("utf-8")
        ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 0:  # read(-1) would read until the sender stops, past every limit
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request gives a length below zero")
        if length > MAX_UPLOAD:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the file is larger than 300 MB")
        return self.rfile.read(length) if length else b""

    def upload(self) -> bytes:
        """The file a request carries as its body."""
        data = self.body()
        if not data:
            raise ApiError(HTTPStatus.BAD_REQUEST, "no file was sent")
        return data

    def json_body(self) -> dict[str, Any]:
        try:
            data = json.loads(self.body() or b"{}")
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request is not JSON") from exc
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request is not a JSON object")
        return data

    def local_file(self, request: dict[str, Any]) -> Path:
        """The file on this computer that a request names by its ``path``."""
        path = Path(str(request.get("path", ""))).expanduser()
        if not path.is_file():
            raise ApiError(HTTPStatus.BAD_REQUEST, f"There is no file at {path}")
        if path.stat().st_size > MAX_UPLOAD:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the file is larger than 300 MB")
        return path

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
            # A page of another site cannot send the app's header. It can, however, give its own name this
            # computer's address (DNS rebinding), and the browser then takes the app for that page's own site. Such
            # a request still carries the other name, so only this computer's names are answered.
            if self.hosts is not None and _host_name(self.headers.get("Host")) not in {*self.hosts, ""}:
                raise ApiError(HTTPStatus.FORBIDDEN, "this app answers only at localhost, on this computer")
            if method != "GET" and self.headers.get(CSRF_HEADER) != "1":
                raise ApiError(HTTPStatus.FORBIDDEN, "this request did not come from the app's own page")
            origin = self.headers.get("Origin")
            if method != "GET" and origin and self.hosts is not None \
                    and _host_name(urlparse(origin).netloc) not in self.hosts:
                raise ApiError(HTTPStatus.FORBIDDEN, "this request did not come from the app's own page")
            self.route(method, parts, parse_qs(url.query))
        except ApiError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
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
        # The cases stand by area. No two of them take the same request, so their order decides nothing.
        match (method, parts[1:]):

            # ---- the library and its runs: the list, and what the Add form sends

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
                data = self.upload()
                return self.send_json(jobs.summary(jobs.add(name, data, settings, plan)), HTTPStatus.CREATED)
            case ("POST", ["jobs", "held"]):  # a file that is no EPUB, as the Add form holds it
                request = self.json_body()
                settings = self.settings(request.get("settings") or {})
                return self.send_json(jobs.summary(jobs.create_held(request.get("held"), settings, request.get("plan"))),
                                      HTTPStatus.CREATED)
            case ("POST", ["jobs", "local"]):
                request = self.json_body()
                path = self.local_file(request)
                settings = self.settings(request.get("settings") or {})
                return self.send_json(jobs.summary(jobs.add(path.name, path.read_bytes(), settings, request.get("plan"),
                                                            path.parent)), HTTPStatus.CREATED)
            case ("POST", ["jobs", "url"]):
                request = self.json_body()
                settings = self.settings(request.get("settings") or {})
                plan = request.get("plan")
                held = jobs.look_url(request.get("url"), _split_how(plan.get("split") if isinstance(plan, dict) else None))["held"]
                return self.send_json(jobs.summary(jobs.create_held(held, settings, plan)), HTTPStatus.CREATED)
            case ("POST", ["books", "import"]):
                data = self.upload()
                return self.send_json(jobs.summary(jobs.import_prepared((query.get("name") or ["book.epub"])[0], data)),
                                      HTTPStatus.CREATED)
            case ("POST", ["look"]):  # a book's length and chapters, before anything is run or kept
                data = self.upload()
                name = (query.get("name") or ["book.epub"])[0]
                if _is_epub(name, data):
                    return self.send_json(look(name, data))
                return self.send_json(jobs.look_source(name, data, _split_how((query.get("split") or [""])[0])))
            case ("POST", ["look", "local"]):
                request = self.json_body()
                path = self.local_file(request)
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
            case ("POST", ["estimate"]):
                request = self.json_body()
                return self.send_json(estimate(request.get("words"), self.settings(request.get("settings") or {}),
                                               request.get("steered") is True))

            # ---- one run

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
            case ("POST", ["jobs", job_id, "continue"]):
                if jobs.get(job_id)["status"] not in ("stopped", "failed"):
                    raise ApiError(HTTPStatus.CONFLICT, "this run cannot be continued")
                jobs.start(job_id)
                return self.send_json({"ok": True})
            # The path ``reading`` has two meanings, and they differ. POST, here, answers the model's reading of
            # what the reader wants from the book, while its run waits. GET and PUT, under "reading" below, are the
            # reader's place and grades in the book. The path keeps its name: stored pages and the tests use it.
            case ("POST", ["jobs", job_id, "reading"]):
                jobs.answer_reading(job_id, self.json_body())
                return self.send_json({"ok": True})
            case ("POST", ["jobs", job_id, "next"]):
                jobs.go_on(job_id, self.json_body())
                return self.send_json({"ok": True})
            case ("PUT", ["jobs", job_id, "steer"]):
                return self.send_json(jobs.set_steer(job_id, self.json_body()))
            case ("GET", ["jobs", job_id, "review"]):
                if jobs.get(job_id)["status"] != "done" or jobs.imported(jobs.get(job_id)):
                    raise ApiError(HTTPStatus.CONFLICT, "this book has no finished run to review")
                try:
                    return self.send_json(review_data(jobs.folder(job_id)))
                except PipelineError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            case ("POST", ["jobs", job_id, "rebuild"]):
                request = self.json_body()
                return self.send_json(jobs.rebuild(job_id, request.get("removed"), request.get("cards"), request.get("preview")))
            case ("GET", ["jobs", job_id, "files", name]):
                job = jobs.get(job_id)
                folder = jobs.folder(job_id)
                stem = stem_of(job["file"])
                path = folder / name
                if Path(name).name != name or not name.startswith(stem + ".") or path.parent != folder \
                        or not path.is_file():
                    raise ApiError(HTTPStatus.NOT_FOUND, "no such file")
                if job.get("exports_stale") and name.endswith((".anki.apkg", ".cards.tsv", ".questions.json")):
                    jobs.fresh_exports(job_id)  # questions from highlights changed since the files were written
                return self.send_file(path, download=True)

            # ---- a book's questions (the editor)

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

            # ---- questions from highlights

            case ("GET", ["jobs", job_id, "questions"]):
                return self.send_json(jobs.questions_state(job_id))
            case ("POST", ["jobs", job_id, "questions", "propose"]):
                return self.send_json(jobs.propose_asked(job_id, self.json_body()))
            case ("POST", ["jobs", job_id, "questions", "stop"]):
                return self.send_json(jobs.stop_questions(job_id))
            case ("POST", ["jobs", job_id, "questions"]):
                r = self.json_body()
                return self.send_json(jobs.write_question(job_id, r.get("highlight"), r.get("question"), r.get("answer"), r.get("form")))
            case ("PUT", ["jobs", job_id, "questions", qid]):
                return self.send_json(jobs.change_question(job_id, qid, self.json_body()))
            case ("DELETE", ["jobs", job_id, "questions", qid]):
                return self.send_json(jobs.discard_question(job_id, qid))

            # ---- highlights and notes

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
            case ("GET", ["jobs", job_id, "notebook"]):
                return self.send_json(jobs.notebook(job_id))
            case ("GET", ["jobs", job_id, "notebook.md"]):
                text = notes.markdown(jobs.notebook(job_id), (query.get("answers") or ["1"])[0] != "0")
                return self.send_text(text, f"{stem_of(jobs.get(job_id)['file'])}.notes.md", "text/markdown; charset=utf-8")

            # ---- reading: the book as the reader shows it, and the reader's place and grades

            case ("GET", ["jobs", job_id, "book"]):
                return self.send_json(jobs.manifest(job_id))
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
            # ``reading`` here is the reader's place and grades. (POST to the same path, under "one run", is another
            # thing: the answer to the model's reading of the reader's wishes.)
            case ("GET", ["jobs", job_id, "reading"]):
                return self.send_json(jobs.reading_state(job_id))
            case ("PUT", ["jobs", job_id, "reading"]):
                state = jobs.save_reading(job_id, self.json_body())
                return self.send_json({"saved": True, "grades": state["grades"]})
            case ("PUT", ["jobs", job_id, "reading", "place"]):
                state = jobs.set_place(job_id, self.json_body())
                return self.send_json({"saved": True, "position": state["position"], "progress": state["x_progress"]})

            # ---- practice

            case ("GET", ["practice"]):
                return self.send_json(jobs.practice((query.get("job") or [None])[0]))
            case ("POST", ["practice", "answer"]):
                request = self.json_body()
                jobs.practice_answer(request.get("job"), request.get("id"), request.get("remembered"), str(request.get("text") or ""))
                return self.send_json(jobs.practice(request["only"] if isinstance(request.get("only"), str) else None))
            case ("GET", ["practice", "settings"]):
                return self.send_json(jobs.srs_settings())
            case ("PUT", ["practice", "settings"]):
                return self.send_json(jobs.save_srs_settings(self.json_body()))

            # ---- the notes folder

            case ("GET", ["vault"]):
                return self.send_json(jobs.vault_home())
            case ("GET", ["vault", "folder"]):
                return self.send_json(jobs.folder_state())
            case ("GET", ["vault", "search"]):
                one = lambda name: (query.get(name) or [""])[0]  # noqa: E731
                return self.send_json(jobs.vault_search(one("q"), one("kind"), one("tag"), one("book")))
            case ("GET", ["vault", "complete"]):
                return self.send_json({"notes": jobs.vault().complete((query.get("q") or [""])[0][:200])})
            case ("GET", ["vault", "anchors"]):
                return self.send_json(jobs.vault().anchors((query.get("target") or [""])[0][:300]))
            case ("GET", ["vault", "peek"]):
                one = lambda name: (query.get(name) or [""])[0][:300]  # noqa: E731
                return self.send_json(jobs.vault_peek(one("target"), one("anchor"), one("from") or None, one("full") == "1"))
            case ("GET", ["vault", "graph"]):
                return self.send_json(jobs.vault().graph())
            case ("GET", ["vault", "revisit"]):
                return self.send_json(jobs.vault().revisit(jobs.today()))
            case ("GET", ["timeline"]):
                return self.send_json(jobs.timeline((query.get("before") or [None])[0]))
            case ("GET", ["vault", "trash"]):
                return self.send_json({"notes": jobs.vault().trash()})
            case ("POST", ["vault", "trash", "restore"]):
                return self.send_json(jobs.vault_restore(self.json_body().get("name")))
            case ("POST", ["vault", "related"]):
                return self.send_json(jobs.vault_related(self.json_body()))
            case ("POST", ["vault", "cite"]):
                return self.send_json(jobs.vault_cite(self.json_body()))
            case ("POST", ["vault", "notes"]):
                return self.send_json(jobs.vault_create(self.json_body()), HTTPStatus.CREATED)
            case ("GET", ["vault", "notes", note_id]):
                return self.send_json(jobs.vault_note(note_id))
            case ("PUT", ["vault", "notes", note_id]):
                try:
                    return self.send_json(jobs.vault_save(note_id, self.json_body()))
                except Conflict as exc:  # changed on disk since the page loaded it: the page asks which to keep
                    return self.send_json(jobs.vault_conflict(exc), HTTPStatus.CONFLICT)
            case ("DELETE", ["vault", "notes", note_id]):
                return self.send_json(jobs.vault_delete(note_id))
            case ("GET", ["vault", "notes", note_id, "token"]):
                return self.send_json({"token": jobs.vault_token(note_id)})
            case ("GET", ["vault", "notes", note_id, "neighbourhood"]):
                return self.send_json(jobs.vault_neighbourhood(note_id))
            case ("GET", ["vault", "notes", note_id, "export.md"]):
                text, title = jobs.vault_export(note_id)
                return self.send_text(text, f"{title}.md", "text/markdown; charset=utf-8")
            case ("GET", ["vault", "projects"]):
                return self.send_json({"projects": jobs.vault().projects()})
            case ("POST", ["vault", "projects", project_id, "gather"]):
                return self.send_json(jobs.vault_gather(project_id, self.json_body()))

            # ---- processing: making something of what was marked

            case ("GET", ["process"]):
                return self.send_json(jobs.process_view((query.get("job") or [None])[0]))
            case ("POST", ["process", "aside"]):
                return self.send_json(jobs.process_aside(self.json_body()))
            case ("POST", ["process", "add"]):
                return self.send_json(jobs.process_add(self.json_body()))

            # ---- the model's help in the notes folder

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

            # ---- settings, and the app itself

            case ("GET", ["info"]):
                defaults = Settings()
                return self.send_json({"version": __version__, "claude": self.claude, "codex": self.codex, "data": str(jobs.data),
                                       "densities": DENSITIES, "criteria": CRITERIA_WORDS, "defaults": defaults.as_json(), "max_questions": MAX_QUESTIONS,
                                       "limits": {"text": steering.TEXT_MAX, "instruction": steering.INSTRUCTION_MAX},
                                       "models": self.models})
            case ("GET", ["settings"]):
                return self.send_json(jobs.ui_settings())
            case ("PUT", ["settings"]):
                return self.send_json(jobs.ui_settings(self.json_body()))
            case ("POST", ["shutdown"]):  # asked for by 'ebook-processor update', which holds the token
                token = self.shutdown_token
                if not token or self.headers.get("X-Shutdown-Token") != token:
                    raise ApiError(HTTPStatus.FORBIDDEN, "stopping the app needs its token")
                self.send_json({"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
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


def _host_name(value: str | None) -> str:
    """The name in a ``Host`` header or the like, without its port, in small letters."""
    value = (value or "").strip().lower()
    if value.startswith("["):  # an IPv6 address stands in brackets
        return value[:value.find("]") + 1]
    return value.rsplit(":", 1)[0] if ":" in value else value


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
                                                "models": models, "pages": pages, "shutdown_token": shutdown_token,
                                                "hosts": LOCAL_HOSTS if _host_name(host) in LOCAL_HOSTS else None})
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
