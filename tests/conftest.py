"""The browser tests need Playwright (``pip install -e '.[browser]'`` and ``python -m playwright install``).
Without it they are left out, and the rest of the suite runs as it is."""

import importlib.util

collect_ignore = [] if importlib.util.find_spec("playwright") and importlib.util.find_spec("pytest_playwright") else ["browser"]


import urllib.parse  # noqa: E402

import pytest  # noqa: E402


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
