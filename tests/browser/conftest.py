"""The app in a real browser: Chromium, Firefox or WebKit (``--browser firefox``).

One app serves every test of a session. It holds one book, the made-up fixture, processed through the app's own
upload with a stand-in for the model that writes two questions for each section. Before each test the book is
put back to "never opened": no place, no grades, no highlights, no cards, default settings.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

from tests.browser.helpers import App, Reader, start_app


@pytest.fixture(scope="session")
def app(tmp_path_factory) -> Any:
    it = start_app(tmp_path_factory.mktemp("app"), density="short")
    yield it
    it.web.close()


@pytest.fixture
def book(app: App) -> App:
    """The book as never opened, and the app's settings as they come."""
    return app.never_opened()


@pytest.fixture(scope="module")
def shelf(tmp_path_factory):
    """An app of its own, whose one book has every form: the first question of a section is open, multiple
    choice, true or false, fill in the blank, in turn; its second is open."""
    it = start_app(tmp_path_factory.mktemp("forms"), density="short", forms="all")
    yield it
    it.web.close()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "viewport": {"width": 1400, "height": 900}}


@pytest.fixture
def page(page: Page) -> Page:
    """A page that fails its test on any script error, which a passing assertion could otherwise hide."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    yield page
    assert not errors, f"script errors on the page: {errors}"


@pytest.fixture
def reader(page: Page, book: App) -> Reader:
    return Reader(page, book)
