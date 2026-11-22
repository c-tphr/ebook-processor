"""What every test file may ask for.

The browser tests need Playwright (``pip install -e '.[browser]'`` and ``python -m playwright install``).
Without it they are left out, and the rest of the suite runs as it is.

``web_app`` and ``make_web_app`` start the app for a test (``tests/webapp.py``). ``book`` is the made-up book as
the reader's side of the app opens it. ``the_web`` is a small web site on this computer, for the tests that give
the app an address. Paths to the test data are in ``tests/paths.py``; small helpers in ``tests/helpers.py``."""

import http.server
import importlib.util
import threading

collect_ignore = [] if importlib.util.find_spec("playwright") and importlib.util.find_spec("pytest_playwright") else ["browser"]


import urllib.parse  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from tests.paths import PREPARED  # noqa: E402
from tests.webapp import WebApp, start_web_app  # noqa: E402


@pytest.fixture(autouse=True)
def no_web(monkeypatch):
    """No test fetches a page or a picture from the web: only from this computer (a server the test starts).

    This guards ``sources.fetch``. The one other thing a test could fetch is the release of EPUBCheck, and
    ``tests/prep/prepkit.py`` allows that only where an environment variable says so."""
    from ebook_processor.sources import fetch

    real = fetch.check

    def check(url: str, public_only: bool) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() in ("http", "https") and parts.hostname not in ("127.0.0.1", "localhost"):
            raise fetch.FetchError("the tests do not reach the web", reason="unreachable")
        real(url, public_only)

    monkeypatch.setattr(fetch, "check", check)


@pytest.fixture
def make_web_app(tmp_path):
    """Start an app for this test: ``make_web_app(model=..., today=...)`` gives a ``WebApp`` (``tests/webapp.py``
    says what each argument does). Its library is ``tmp_path / "data"`` unless ``data`` names another folder.
    Every app made here is shut down when the test ends."""
    made: list[WebApp] = []

    def make(data: Path | None = None, **how: Any) -> WebApp:
        made.append(start_web_app(tmp_path / "data" if data is None else data, **how))
        return made[-1]

    yield make
    for app in reversed(made):
        app.close()


@pytest.fixture
def web_app(make_web_app) -> WebApp:
    """A running app with an empty library and the stand-in for the model."""
    return make_web_app()


@pytest.fixture(scope="module")
def book():
    """The made-up book, prepared, as the reader's side of the app opens it. (The browser tests have a fixture of
    this name of their own: the app's book as never opened.)"""
    from ebook_processor import reading

    return reading.open_reading_book(PREPARED)


@pytest.fixture
def the_web(monkeypatch):
    """The address of a web site on this computer that serves the pages of ``tests/sources/makers.py``."""
    from ebook_processor.sources import fetch
    from tests.sources.makers import Web

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Web)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(fetch, "is_private", lambda host: False)  # the pictures of these pages are on this computer
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
