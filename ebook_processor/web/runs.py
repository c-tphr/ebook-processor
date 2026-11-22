"""The runs' part of the local web app: adding a book or another source, the run that writes its questions, what
the reader wants from the book, and the build of a book's files.

This is one of the parts the class ``Jobs`` of ``server.py`` is put together from. Its methods use the fields that
``Jobs.__init__`` makes, and the methods of the other parts, through ``self``. It does not import ``server.py``.
"""

from __future__ import annotations

import secrets
import threading
import time
import traceback
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .. import sources
from .. import steer as steering
from ..llm import BackendCancelled, BackendError, Completion, UsageLimitError
from ..outputs import stem_of
from ..pipeline import STATE_FILE
from ..prep.epub import EpubError, load_book_bytes
from ..runner import Cancelled, PipelineError, default_backend, models_for, rebuild, run_book, trial_dir, try_chapter, work_dir
from ..settings import Settings, SettingsError
from ..steer import Steer, SteerError
from ..store import now
from .common import EVENTS_KEPT, WAITING, ApiError, _is_epub, _looked, _plain_name, _safe_name, _split_how, _text, _whole

# The locks, as in every part of ``Jobs``: a book's lock (``self.booked(id)``) is taken first, the library's lock
# (``self.lock``) second and for moments only, never across a call to a model or while a book's files are built. The
# whole rule is in ``Jobs.__init__`` (server.py).


class Runs:
    """What ``Jobs`` does to add a book and to run it (see runner.py, steer.py and sources/)."""

    # ---- a model to ask

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

    # ---- what the reader wants from a book (see steer.py)

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

    # ---- other sources than EPUB (docs/SOURCES-PLAN.md), and the files the Add form holds

    HELD, HELD_SECONDS = 4, 30 * 60

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

    # ---- a run

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

    # ---- a book's files, built again

    def _built(self, job: dict[str, Any], stale: Any, counts: dict[str, Any] | None) -> None:
        """A book's files were built again: keep the new counts. The exports are fresh again unless a question from
        a highlight changed while they were built (``stale`` is the mark as it was before the build)."""
        with self.lock:
            if counts is not None:
                job["counts"] = counts
            if job.get("exports_stale") == stale:
                job.pop("exports_stale", None)
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
