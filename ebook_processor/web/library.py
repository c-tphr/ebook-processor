"""The library's part of the local web app: the entries on disk and what the pages show of them, the two kinds of
lock, a book's kept outline and its text, and what the settings window keeps.

This is one of the parts the class ``Jobs`` of ``server.py`` is put together from. Its methods use the fields that
``Jobs.__init__`` makes, and the methods of the other parts, through ``self``. It does not import ``server.py``.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import threading
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .. import notes, reading, sources, srs
from .. import steer as steering
from ..outputs import load_report, stem_of
from ..runner import PipelineError, review_data, trial_dir
from ..settings import Settings, SettingsError
from ..store import now, write_json
from .common import JOB_ID_RE, LONG_SETTINGS, SETTINGS_FILE, ApiError, _safe_name, _slug, estimate

# The locks, as in every part of ``Jobs``: a book's lock (``self.booked(id)``) is taken first, the library's lock
# (``self.lock``) second and for moments only, never across a call to a model or while a book's files are built. The
# whole rule is in ``Jobs.__init__`` (server.py).


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


class Library:
    """What ``Jobs`` keeps of the entries of the library: the books and articles, each with its run and its files."""

    # ---- the entries

    def folder(self, job_id: str) -> Path:
        return self.data / job_id

    def get(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.match(job_id) or job_id not in self.jobs:
            raise ApiError(HTTPStatus.NOT_FOUND, "no such run")
        return self.jobs[job_id]

    @staticmethod
    def type_of(job: dict[str, Any]) -> str:
        """What an entry of the library is: a book or an article."""
        return job["type"] if job.get("type") in sources.TYPES else "book"

    @staticmethod
    def imported(job: dict[str, Any]) -> bool:
        """Whether an entry is a prepared book that was made elsewhere and added: it can be read, and has no run
        to change or to build from. (An entry's ``kind`` says only this. Whether it is a book or an article is its
        ``type``; the kind of book the questions are written for is in its settings.)"""
        return job.get("kind") == "imported"

    def editable(self, job_id: str) -> bool:
        """Whether the book's questions can be changed: it was processed here, and its run is finished."""
        job = self.get(job_id)
        return job["status"] == "done" and not self.imported(job)

    def prepared(self, job_id: str) -> Path:
        return self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.prepared.epub"

    def _readable(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        return bool(job) and job["status"] == "done" and self.prepared(job_id).is_file()

    def _store(self, job: dict[str, Any]) -> None:
        job["updated"] = now()
        write_json(self.folder(job["id"]) / "job.json", {k: v for k, v in job.items() if k != "report"})

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

    # ---- what the pages show of the entries

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

    # ---- a book's own lock

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

    # ---- a book's outline, which is kept, and its text (see reading.py)

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
