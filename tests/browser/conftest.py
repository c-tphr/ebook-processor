"""The app in a real browser: Chromium, Firefox or WebKit (``--browser firefox``).

One app serves every test of a session. It holds one book, the made-up fixture, processed through the app's own
upload with a stand-in for the model that writes two questions for each section. Before each test the book is
put back to "never opened": no place, no grades, no highlights, no cards, default settings.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from ebook_processor.web.server import make_server
from tests.scripted import Script
from tests.paths import FIXTURE

HEADERS = {"X-Ebook-Processor": "1"}
READY = "() => { try { return !!view && !!book; } catch (e) { return false; } }"   # the reader has drawn its view


def two_questions(reply: dict[str, Any], prompt: str) -> dict[str, Any]:
    """A second question for every section, so that a checkpoint can be answered in part."""
    for r in reply["ranges"]:
        if r["kind"] == "section":
            first = r["prompts"][0]
            r["prompts"].append({"prompt": "And in a word: " + first["prompt"], "answer": "Briefly: " + first["answer"],
                                 "supporting_paragraphs": first["supporting_paragraphs"]})
    return reply


@dataclass
class App:
    url: str
    data: Path
    id: str = ""

    @property
    def folder(self) -> Path:
        return self.data / self.id

    def call(self, method: str, path: str, body: bytes | None = None, json_body: Any = None) -> Any:
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(self.url + "/api" + path, data=body, method=method, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def reading(self) -> dict[str, Any]:
        return self.call("GET", f"/jobs/{self.id}/reading")

    def notes(self) -> dict[str, Any]:
        return self.call("GET", f"/jobs/{self.id}/notes")


@pytest.fixture(scope="session")
def app(tmp_path_factory) -> Any:
    data = tmp_path_factory.mktemp("app")
    server = make_server(data, "127.0.0.1", 0, backend_factory=lambda settings: Script(draft_hook=two_questions).backend(),
                         claude={"ok": True, "version": "0.0.0 (test)"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    it = App(f"http://127.0.0.1:{server.server_address[1]}", data)
    settings = urllib.parse.quote(json.dumps({"density": "short"}))
    it.id = it.call("POST", f"/jobs?name=fixture.epub&settings={settings}", FIXTURE.read_bytes())["id"]
    for _ in range(600):
        job = it.call("GET", f"/jobs/{it.id}")
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job["status"] == "done", job.get("message")
    yield it
    server.shutdown()
    server.server_close()


@pytest.fixture
def book(app: App) -> App:
    """The book as never opened, and the app's settings as they come."""
    for name in ("reading.json", "notes.json", "srs.json"):
        (app.folder / name).unlink(missing_ok=True)
    for name in ("reader-settings.json", "srs-settings.json"):
        (app.data / name).unlink(missing_ok=True)
    return app


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


class Reader:
    """The reader page, driven as a person would, with a look at its state for the assertions."""

    def __init__(self, page: Page, app: App):
        self.page, self.app = page, app

    def open(self, at: str | None = "2:0", mode: str | None = None, query: str = "") -> "Reader":
        self.page.goto(f"{self.app.url}/read/{self.app.id}" + (f"?go={at}" if at else "") + query)
        self.page.wait_for_function(READY)
        if mode:
            self.mode(mode)
        return self

    def mode(self, mode: str) -> None:
        self.page.locator("#modes button", has_text={"page": "Page", "scroll": "Scroll", "rsvp": "RSVP"}[mode]).click()
        self.page.wait_for_function("(m) => mode === m && !!view", arg=mode)

    def state(self) -> dict[str, Any]:
        return self.page.evaluate("""() => ({
            mode, chapter: pos.chapter, word: pos.word, playing: !!(view && view.playing),
            checkpoint: cp ? { section: cp.section.id, index: cp.index, all: !!cp.all } : null,
            grades: Object.fromEntries(Object.entries(S.grades).map(([k, v]) => [k, v.first])),
            label: view && view.label ? view.label() : "", focus: document.activeElement.id || document.activeElement.tagName,
            drawer: document.querySelector("#drawer").classList.contains("on"), settings: AppSettings.isOpen })""")

    def sections(self, chapter: int) -> list[dict[str, Any]]:
        return self.page.evaluate("(c) => sectionsIn(c).map((s) => ({ id: s.id, start: s.start, end: s.end, prompts: s.prompts.map((p) => p.id) }))", chapter)

    def key(self, key: str) -> None:
        self.page.keyboard.press(key)
        self.page.wait_for_timeout(120)

    def read_to_checkpoint(self, limit: int = 40) -> dict[str, Any]:
        """Turn pages until a checkpoint opens."""
        for _ in range(limit):
            if self.state()["checkpoint"]:
                return self.state()["checkpoint"]
            self.key("ArrowRight")
        raise AssertionError(f"no checkpoint opened: {self.state()}")

    def answer(self, grade: str) -> None:
        """Answer the question on show: Correct, Incorrect (and go on), or skip."""
        box = self.page.locator(".cp")
        if grade == "skip":
            box.get_by_role("button", name="Skip question").click()
        else:
            box.get_by_role("button", name="Reveal answer").click()
            box.get_by_role("button", name=grade, exact=False).first.click()
            if grade == "Incorrect":
                box.get_by_role("button", name="Continue").click()
        self.page.wait_for_timeout(150)

    def dismiss_offer(self) -> None:
        self.page.evaluate("() => document.querySelector('.offer')?.remove()")

    def saved(self) -> dict[str, Any]:
        """What the app has stored, once the page's pending save has gone out."""
        self.page.evaluate("() => save(true)")
        self.page.wait_for_timeout(250)
        return self.app.reading()


@pytest.fixture
def reader(page: Page, book: App) -> Reader:
    return Reader(page, book)
