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

import datetime as _dt
import json
import mimetypes
import os
import re
import shutil
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, notes, reading, srs
from ..llm import BackendError, ClaudeCodeBackend, CodexBackend, UsageLimitError, available_models
from ..outputs import load_report, stem_of
from ..pipeline import now, write_json
from ..prep.epub import EpubError, load_book_bytes
from ..runner import Cancelled, PipelineError, rebuild, review_data, run_book
from ..settings import DENSITIES, Settings, SettingsError, from_options

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 300 * 1024 * 1024
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
ACTIVE = ("queued", "running")
EVENTS_KEPT = 300
PAGES = {"index.html": "text/html; charset=utf-8", "reader.html": "text/html; charset=utf-8",
         "settings.js": "text/javascript; charset=utf-8"}   # the app's own files, by content type
SETTINGS_FILE = "reader-settings.json"  # what the settings window keeps, for every page of the app
CSRF_HEADER = "X-Ebook-Processor"  # a page of another site cannot send this header, so it cannot start runs


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "book"


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w .,()'&+-]", "_", name).strip(" .") or "book.epub"
    return name if name.lower().endswith(".epub") else name + ".epub"


class Jobs:
    """The runs on disk, and the one worker thread that processes them in turn."""

    def __init__(self, data: Path, backend_factory: Any = None):
        self.data = data
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.turn = threading.Lock()  # held by the run that is being processed
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.books: dict[str, tuple[int, reading.ReadingBook]] = {}
        self.backend_factory = backend_factory
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
                                       "done", "total", "counts", "words", "kind")}
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
        for job_id in ids:
            store = self.cards(job_id)
            if not store["cards"]:
                continue
            try:
                manifest = self.book(job_id).manifest()
            except ApiError:
                continue
            prompts = {p["id"]: (s, p) for s in manifest["sections"] for p in s["prompts"]}
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
            chapter = next((c["title"] for c in manifest["chapters"] if c["index"] == section["chapter"]), "")
            card = {"job": job_id, "id": pid, "book": manifest["title"], "chapter": chapter, "section": section["label"],
                    "question": prompt["prompt"], "answer": prompt["answer"], "passage": self.passage(job_id, pid),
                    "state": state["state"], "again": state["again"], "interval": round(state["interval"], 1),
                    "first": srs.kind_of(state, today) == "first",
                    "times": len(state["reviews"]), "go": f"{section['chapter']}:{section['start']}",
                    "preview": srs.preview(state, today, settings)}
        return {"counts": total, "left": len(line), "card": card, "settings": settings, "today": srs.day(today)}

    def passage(self, job_id: str, pid: str) -> list[list[dict[str, Any]]]:
        """The paragraphs that support a question's answer, when the book was processed here. Each paragraph is
        a list of runs ``{"t": text, "h": highlighted?}``, so that what the reader highlighted shows here too."""
        path = self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.questions.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        refs = next((q["passage"] for q in doc.get("questions", []) if q["id"] == pid), [])
        where = {b.ref: (sd, b) for sd in self.book(job_id).book.spine for b in sd.blocks}
        store = notes.load(self.folder(job_id))
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

    def notebook(self, job_id: str) -> dict[str, Any]:
        return notes.notebook(self.book(job_id), self.notes_do(job_id), reading.load_state(self.folder(job_id)),
                              self.sources(job_id))

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
                elif isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 200):
                    stored[key] = value
            if len(json.dumps(stored)) > 4000:
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
        return store

    def practice_answer(self, job_id: str, pid: str, remembered: bool, text: str) -> None:
        self.get(job_id)
        with self.lock:
            store = self.cards(job_id)
            card = store["cards"].get(pid)
            if card is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "no such card")
            today = self.today()  # the stamp carries the same local day the schedule is counted in
            at = f"{srs.day(today)}T{_dt.datetime.now().strftime('%H:%M:%S')}"
            srs.answer(card, remembered, today, self.srs_settings(), at, "review", text)
            write_json(self.folder(job_id) / srs.FILE, store)

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

    def import_prepared(self, name: str, data: bytes) -> dict[str, Any]:
        """Add a prepared EPUB made elsewhere. It can be read; it has no run to rebuild from."""
        name = _safe_name(name)
        stem = name[:-len(".prepared.epub")] if name.lower().endswith(".prepared.epub") else stem_of(name)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        with self.lock:
            job_id = f"{stamp}-{_slug(stem)}"
            while job_id in self.jobs:
                job_id = job_id[:55] + "x"
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
        job.update({k: v for k, v in self.summary(self.jobs[job_id]).items() if k in ("can_read", "reading", "practice", "notes")})
        if job["can_read"]:
            job["results"] = reading.results(reading.load_state(self.folder(job_id)))
        return job

    # ---- writing

    def _store(self, job: dict[str, Any]) -> None:
        job["updated"] = now()
        write_json(self.folder(job["id"]) / "job.json", {k: v for k, v in job.items() if k != "report"})

    def create(self, name: str, data: bytes, settings: Settings) -> dict[str, Any]:
        name = _safe_name(name)
        try:
            book = load_book_bytes(data, name)
        except EpubError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"This file cannot be read as an EPUB: {exc}") from exc
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        with self.lock:
            job_id = f"{stamp}-{_slug(stem_of(name))}"
            while job_id in self.jobs:
                job_id = job_id[:55] + "x"
            folder = self.folder(job_id)
            (folder / "input").mkdir(parents=True)
            (folder / "input" / name).write_bytes(data)
            job = {"id": job_id, "file": name, "title": book.title or stem_of(name), "created": now(),
                   "status": "queued", "message": "Waiting to start", "stage": None, "done": None, "total": None,
                   "words": sum(len(sd.words) for sd in book.spine), "settings": settings.as_json(), "events": [],
                   "counts": None}
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
        shutil.rmtree(self.folder(job_id), ignore_errors=True)

    def _event(self, job: dict[str, Any], event: dict[str, Any]) -> None:
        with self.lock:
            job["events"] = (job["events"] + [event])[-EVENTS_KEPT:]
            if event["stage"] != "warning":
                job.update(stage=event["stage"], message=event["message"], done=event.get("done"),
                           total=event.get("total"))

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
                report = run_book(folder / "input" / job["file"], folder, settings, backend=backend,
                                  progress=lambda e: self._event(job, e), cancel=cancel)
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

    def rebuild(self, job_id: str, removed: list[str] | None, cards: str | None = None) -> dict[str, Any]:
        with self.lock:
            job = self.get(job_id)
            if job["status"] != "done" or job.get("kind") == "imported":
                raise ApiError(HTTPStatus.CONFLICT, "this book has no finished run to rebuild from")
        try:
            report = rebuild(self.folder(job_id), removed=removed, cards=cards)
        except PipelineError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        with self.lock:
            job["counts"] = report["counts"]
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
                   "effort", "jobs", "epubcheck"}
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
                                       "densities": DENSITIES, "defaults": defaults.as_json(),
                                       "models": self.models})
            case ("GET", ["jobs"]):
                return self.send_json({"jobs": jobs.listing()})
            case ("POST", ["jobs"]):
                name = (query.get("name") or ["book.epub"])[0]
                try:
                    raw = json.loads((query.get("settings") or ["{}"])[0])
                except ValueError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "settings are not JSON") from exc
                settings = self.settings(raw)
                data = self.body()
                if not data:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "no file was sent")
                return self.send_json(jobs.summary(jobs.create(name, data, settings)), HTTPStatus.CREATED)
            case ("POST", ["jobs", "local"]):
                request = self.json_body()
                path = Path(str(request.get("path", ""))).expanduser()
                if not path.is_file():
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"There is no file at {path}")
                if path.stat().st_size > MAX_UPLOAD:
                    raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the file is larger than 300 MB")
                settings = self.settings(request.get("settings") or {})
                return self.send_json(jobs.summary(jobs.create(path.name, path.read_bytes(), settings)),
                                      HTTPStatus.CREATED)
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
                return self.send_json(jobs.notes_do(job_id, lambda st, bk: notes.remove_item(st, item)))
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
                return self.send_json(jobs.book(job_id).manifest())
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
            case ("POST", ["jobs", job_id, "cancel"]):
                jobs.cancel(job_id)
                return self.send_json({"ok": True})
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
                removed, cards = request.get("removed"), request.get("cards")
                if removed is not None and (not isinstance(removed, list) or not all(isinstance(r, str) for r in removed)):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'removed' must be a list of question ids")
                if cards is not None and not isinstance(cards, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'cards' must be all, missed or reached")
                return self.send_json(jobs.rebuild(job_id, removed, cards))
            case ("GET", ["jobs", job_id, "files", name]):
                job = jobs.get(job_id)
                folder = jobs.folder(job_id)
                stem = stem_of(job["file"])
                path = folder / name
                if Path(name).name != name or not name.startswith(stem + ".") or path.parent != folder \
                        or not path.is_file():
                    raise ApiError(HTTPStatus.NOT_FOUND, "no such file")
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
        models = available_models(ClaudeCodeBackend() if claude.get("ok") else None,
                                  CodexBackend() if codex.get("ok") else None)
    # The pages are read once, now. A page and the server behind it then always come from the same version of
    # the tool: updating the files while the app runs changes nothing until it is started again. Read on every
    # request, a new page would be served by old code, and fail on whatever the new page asks for.
    pages = {name: (STATIC / name).read_bytes() for name in PAGES}
    handler = type("BoundHandler", (Handler,), {"jobs": Jobs(data, backend_factory), "claude": claude, "codex": codex,
                                                "models": models, "pages": pages, "shutdown_token": shutdown_token})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(data: Path, host: str, port: int, open_browser: bool) -> None:
    from .. import update

    try:
        server = make_server(data, host, port)
    except OSError as exc:
        raise PipelineError(f"the app cannot listen on port {port} ({exc.strerror or exc}). Is it already running? "
                            "Give --port to use another port.") from exc
    port = server.server_address[1]
    # A record of this app while it runs, so that 'ebook-processor update' can find it and ask it to stop.
    record = update.write_record(host, port, data)
    server.RequestHandlerClass.shutdown_token = record["token"]
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"ebook-processor {__version__} is running at {url}\nRuns are kept in {data}\nPress Ctrl-C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
        print("Stopped on request.")
    except KeyboardInterrupt:
        print("\nStopping. Unfinished runs can be continued the next time.")
    finally:
        server.server_close()
        update.remove_record(port, os.getpid())
