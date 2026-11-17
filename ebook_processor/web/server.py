"""The local web app: pick an EPUB, watch the run, review the questions, download the results.

It is a small HTTP server from the standard library plus one page of HTML. It listens on this computer only
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

from .. import __version__
from ..llm import BackendError, ClaudeCodeBackend, UsageLimitError
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
        return {k: job.get(k) for k in ("id", "file", "title", "created", "updated", "status", "message", "stage",
                                        "done", "total", "counts")}

    def listing(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.summary(j) for j in sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)]

    def detail(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = dict(self.get(job_id))
        report = load_report(self.folder(job_id), job["file"]) if job["status"] == "done" else None
        if report is not None:
            job["report"] = {k: report[k] for k in ("counts", "files", "firmware_format_ok", "notes", "warnings",
                                                    "usage", "complete", "doubted_breaks", "excluded")}
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

    def rebuild(self, job_id: str, removed: list[str]) -> dict[str, Any]:
        with self.lock:
            job = self.get(job_id)
            if job["status"] != "done":
                raise ApiError(HTTPStatus.CONFLICT, "the run is not finished")
        try:
            report = rebuild(self.folder(job_id), removed=removed)
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
    models: list[dict[str, Any]]

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

    # ---- routes

    def route(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
        jobs = self.jobs
        if method == "GET" and not parts:
            return self.send_file(STATIC / "index.html")
        if not parts or parts[0] != "api":
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        match (method, parts[1:]):
            case ("GET", ["info"]):
                defaults = Settings()
                return self.send_json({"version": __version__, "claude": self.claude, "data": str(jobs.data),
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
                if jobs.get(job_id)["status"] != "done":
                    raise ApiError(HTTPStatus.CONFLICT, "the run is not finished")
                try:
                    return self.send_json(review_data(jobs.folder(job_id)))
                except PipelineError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            case ("POST", ["jobs", job_id, "rebuild"]):
                removed = self.json_body().get("removed")
                if not isinstance(removed, list) or not all(isinstance(r, str) for r in removed):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "'removed' must be a list of question ids")
                return self.send_json(jobs.rebuild(job_id, removed))
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


def make_server(data: Path, host: str = "127.0.0.1", port: int = 8765, backend_factory: Any = None,
                claude: dict[str, Any] | None = None, models: list[dict[str, Any]] | None = None) -> ThreadingHTTPServer:
    """``claude`` and ``models`` are asked of Claude Code once, when the app starts, unless given."""
    claude = claude if claude is not None else claude_status()
    if models is None:
        models = ClaudeCodeBackend().list_models() if claude.get("ok") else []
    handler = type("BoundHandler", (Handler,), {"jobs": Jobs(data, backend_factory), "claude": claude,
                                                "models": models})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(data: Path, host: str, port: int, open_browser: bool) -> None:
    server = make_server(data, host, port)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{server.server_address[1]}/"
    print(f"ebook-processor is running at {url}\nRuns are kept in {data}\nPress Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping. Unfinished runs can be continued the next time.")
    finally:
        server.server_close()
