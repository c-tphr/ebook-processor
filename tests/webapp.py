"""The app, started for a test.

Starting the server, the header every request needs, the wait for a run and the shutdown are written here, once.
``tests/conftest.py`` offers them as the fixtures ``web_app`` and ``make_web_app``. A fixture that must live longer
than one test (the browser tests keep one app for a session) calls ``start_web_app`` and ``close`` itself.

No model is ever called: an app gets a stand-in (``tests/scripted.py``), or none at all when its tests run no book.
"""

from __future__ import annotations

import datetime as dt
import json as _json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ebook_processor.web import server as web
from tests.paths import FIXTURE, PREPARED
from tests.scripted import Script

HEADERS = {"X-Ebook-Processor": "1"}   # the app's own pages send this; without it the app refuses every change
ACTIVE = ("queued", "running")
FRESH = "fresh"                        # the default stand-in for the model: a new Script for every run


def settled(look: Callable[[], dict[str, Any]], seconds: float = 60.0) -> dict[str, Any]:
    """Ask for a run with ``look`` until it is no longer queued or running, and return it as it then is.

    A run that waits for the reader ("asking", "tried") or was stopped has settled too."""
    deadline = time.monotonic() + seconds
    while True:
        job = look()
        if job["status"] not in ACTIVE:
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"the run did not settle in {seconds:.0f} seconds: {job.get('message')}")
        time.sleep(0.05)


def wait_for_run(jobs: Any, job_id: str, seconds: float = 60.0) -> dict[str, Any]:
    """The same wait for a test that holds a ``Jobs`` object and no server."""
    return settled(lambda: jobs.get(job_id), seconds)


@dataclass
class Reply:
    """What the app answered: also for a refusal, which ``urllib`` alone would raise as an error."""

    status: int
    raw: bytes
    headers: Any

    @property
    def is_json(self) -> bool:
        return (self.headers.get("Content-Type") or "").startswith("application/json")

    @property
    def data(self) -> Any:
        """The answer read as JSON where the app says it is JSON; the bytes as they came otherwise."""
        return _json.loads(self.raw) if self.is_json else self.raw

    def json(self) -> Any:
        """The answer read as JSON, whatever the app says it is."""
        return _json.loads(self.raw)

    @property
    def text(self) -> str:
        return self.raw.decode()


class WebApp:
    """One running app with a library of its own.

    ``url`` is its address, ``data`` the folder of its library, ``jobs`` the ``Jobs`` object behind the routes,
    ``notes_folder`` where its notes go unless a test chooses another folder, and ``script`` the stand-in for the
    model when the test gave one to look at."""

    def __init__(self, server: Any, data: Path, script: Script | None = None):
        self.server, self.data, self.script = server, data, script
        self.port: int = server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.jobs: web.Jobs = server.RequestHandlerClass.jobs
        self.notes_folder = data.parent / web.NOTES_FOLDER
        self._closed = False
        threading.Thread(target=server.serve_forever, daemon=True, name=f"test-app-{self.port}").start()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.server.shutdown()
            self.server.server_close()

    def call(self, method: str, path: str, *, body: bytes | None = None, json: Any = None, csrf: bool = True,
             headers: dict[str, str] | None = None, timeout: float = 30) -> Reply:
        """One request. ``path`` is whole, as the browser sends it ("/api/jobs", "/read/..."). ``json`` is sent as
        the body. ``csrf=False`` leaves the app's header out, as a page of another site would have to."""
        if json is not None:
            body = _json.dumps(json).encode()
        request = urllib.request.Request(self.url + path, data=body, method=method,
                                         headers={**(HEADERS if csrf else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Reply(response.status, response.read(), response.headers)
        except urllib.error.HTTPError as exc:
            return Reply(exc.code, exc.read(), exc.headers)

    def wait(self, job_id: str, seconds: float = 60.0) -> dict[str, Any]:
        """Wait until a run is no longer queued or running, and return it as the app then shows it."""
        return settled(lambda: self.call("GET", f"/api/jobs/{job_id}").data, seconds)

    def add_fixture(self, plan: dict[str, Any] | None = None, name: str = "fixture.epub", book: bytes | None = None,
                    **settings: Any) -> str:
        """Add the made-up book (or ``book``) as the Add form does, with these settings, and return the id of its
        run without waiting for it."""
        query = {"name": name, "settings": _json.dumps(settings)}
        if plan is not None:
            query["plan"] = _json.dumps(plan)
        reply = self.call("POST", "/api/jobs?" + urllib.parse.urlencode(query), body=FIXTURE.read_bytes() if book is None else book)
        assert reply.status == 201, reply.data
        return reply.data["id"]

    def process_fixture(self, plan: dict[str, Any] | None = None, **settings: Any) -> dict[str, Any]:
        """Add the made-up book, wait for its run and return the run, which must have finished."""
        job = self.wait(self.add_fixture(plan, **settings))
        assert job["status"] == "done", job.get("message")
        return job

    def import_fixture(self, name: str = "fixture.prepared.epub") -> str:
        """Put the made-up book into the library as a book that is already prepared, and return its id."""
        reply = self.call("POST", "/api/books/import?" + urllib.parse.urlencode({"name": name}), body=PREPARED.read_bytes())
        assert reply.status == 201, reply.data
        return reply.data["id"]

    def function(self, prefix: str = "", answer: Callable[[Reply], Any] | None = None) -> "Calls":
        """This app as a function, for the test files that were written with one (see ``Calls``)."""
        return Calls(self, prefix, answer)


class Calls:
    """An app as a function: ``app(method, path, body=None, json_body=None)`` answers ``(status, data)``.

    Many test files were written against a function of this shape, each with a small difference: some give the
    paths without "/api" (``prefix``), one wants the headers as a third value (``answer``). This adapter keeps
    their calls as they are. Everything else asked of it (``app.jobs``, ``app.data``, ``app.wait``) is the
    ``WebApp``'s, and a test file may set names of its own on it (``app.id``)."""

    def __init__(self, app: WebApp, prefix: str = "", answer: Callable[[Reply], Any] | None = None):
        self.app, self.prefix = app, prefix
        self.answer = answer or (lambda reply: (reply.status, reply.data))

    def __call__(self, method: str, path: str, body: bytes | None = None, json_body: Any = None, csrf: bool = True) -> Any:
        return self.answer(self.app.call(method, self.prefix + path, body=body, json=json_body, csrf=csrf))

    def __getattr__(self, name: str) -> Any:
        if name == "app":  # not set yet: there is nothing to pass the question on to
            raise AttributeError(name)
        return getattr(self.app, name)


def start_web_app(data: Path, *, model: Any = FRESH, today: dt.date | Callable[[], dt.date] | None = None,
                  claude: dict[str, Any] | None = None, models: list[dict[str, Any]] | None = None, **more: Any) -> WebApp:
    """Start an app whose library is the folder ``data``. The caller closes it.

    ``model`` is the stand-in for the model: by default a new ``Script`` for every run; a ``Script`` of the test's
    own, which the test can then look at as ``app.script``; any function from settings to a backend; or ``None``
    for an app whose tests run no book and ask no model. ``today`` fixes the app's date: a date, or a function
    that gives one when the test moves the clock. ``claude`` and ``models`` are what the app tells its pages about
    the tools; by default it says Claude Code is ready, so nothing looks for the real tool. What else is given
    goes to ``make_server``."""
    script = model if isinstance(model, Script) else None
    if model == FRESH:
        factory: Any = lambda settings: Script().backend()  # noqa: E731
    elif script is not None:
        factory = lambda settings: script.backend()  # noqa: E731
    else:
        factory = model
    server = web.make_server(data, "127.0.0.1", 0, backend_factory=factory,
                             claude={"ok": True, "version": "test"} if claude is None else claude,
                             models=[] if models is None else models, **more)
    app = WebApp(server, data, script)
    if today is not None:
        app.jobs.today = today if callable(today) else (lambda: today)  # type: ignore[method-assign]
    return app
