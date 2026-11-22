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
import threading
import time  # noqa: F401  kept for the tests, which reach the module as server.time
import traceback
import webbrowser
from http import HTTPStatus
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, notes, reading
from .. import highlight_questions as hq  # noqa: F401  kept for the tests, which reach the module as server.hq
from .. import srs  # noqa: F401  kept for the tests, which reach the module as server.srs
from .. import steer as steering
from ..llm import BackendError, ClaudeCodeBackend, CodexBackend, available_models
from ..outputs import stem_of
from ..prompts import CRITERIA_WORDS
from ..prep.epub import EpubError, load_book_bytes
from ..runner import default_backend  # noqa: F401  kept for the tests, which look for it here
from ..runner import PipelineError, review_data
from ..settings import DENSITIES, MAX_QUESTIONS, Settings, SettingsError, from_options
from ..vault import Conflict, Vault
from .common import JOB_ID_RE, NOTES_FOLDER, ApiError, _is_epub, _looked, _safe_name, _split_how, estimate
from .library import Library, LibraryLock
from .notes_folder import NotesFolder
from .questions import Questions
from .runs import Runs
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


class Jobs(Library, Runs, Study, Questions, NotesFolder):
    """The runs on disk, and the one worker thread that processes them in turn.

    The class is put together from five parts, each in a file of its own: the entries of the library and the locks
    (``library.py``), adding a book and running it (``runs.py``), the reader's work on a book (``study.py``), a book's
    questions (``questions.py``) and the notes folder (``notes_folder.py``). The parts call each other through
    ``self``. What they share is made here, in ``__init__``, and here is the rule for the two kinds of lock."""

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

    # The day, as every part asks for it. It stays on this class: the tests give ``Jobs`` another day here.
    today = staticmethod(_dt.date.today)


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
