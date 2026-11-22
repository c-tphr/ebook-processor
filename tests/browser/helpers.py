"""What the browser tests share that is no fixture: the app and the reader page as a test drives them, and a few
steps several files take. The fixtures are in ``conftest.py``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from tests.scripted import Script
from tests.webapp import WebApp, start_web_app

READY = "() => { try { return !!view && !!book; } catch (e) { return false; } }"   # the reader has drawn its view
ASKED = "() => cp ? (cp.review ? cp.queue[cp.index].p : cp.section.prompts[cp.index]) : null"   # the question on show


def two_questions(reply: dict[str, Any], prompt: str) -> dict[str, Any]:
    """A second question for every section, so that a checkpoint can be answered in part."""
    for r in reply["ranges"]:
        if r["kind"] == "section":
            first = r["prompts"][0]
            r["prompts"].append({"prompt": "And in a word: " + first["prompt"], "answer": "Briefly: " + first["answer"],
                                 "supporting_paragraphs": first["supporting_paragraphs"]})
    return reply


class App:
    """A running app (``tests/webapp.py``) as these tests call it: paths without "/api", answers as JSON, a refusal
    as a failure. ``id`` is the book the test works with."""

    def __init__(self, web: WebApp, id: str = ""):
        self.web, self.id = web, id
        self.url, self.data = web.url, web.data

    @property
    def folder(self) -> Path:
        return self.data / self.id

    def call(self, method: str, path: str, body: bytes | None = None, json_body: Any = None) -> Any:
        reply = self.web.call(method, "/api" + path, body=body, json=json_body)
        assert reply.status < 400, f"{method} {path} was refused ({reply.status}): {reply.text}"
        return reply.json()

    def wait(self, job_id: str) -> dict[str, Any]:
        return self.web.wait(job_id)

    def reading(self) -> dict[str, Any]:
        return self.call("GET", f"/jobs/{self.id}/reading")

    def notes(self) -> dict[str, Any]:
        return self.call("GET", f"/jobs/{self.id}/notes")

    def never_opened(self) -> "App":
        """Put the book back to "never opened", and the app's settings to what they are at first."""
        for name in ("reading.json", "notes.json", "srs.json"):
            (self.folder / name).unlink(missing_ok=True)
        for name in ("reader-settings.json", "srs-settings.json"):
            (self.data / name).unlink(missing_ok=True)
        return self


def start_app(data: Path, **settings: Any) -> App:
    """Start an app whose library holds the made-up book, processed with these settings by a stand-in for the model
    that writes two questions for each section. The caller closes it (``app.web.close()``)."""
    web = start_web_app(data, model=lambda s: Script(draft_hook=two_questions).backend(),
                        claude={"ok": True, "version": "0.0.0 (test)"})
    try:
        return App(web, web.process_fixture(**settings)["id"])
    except BaseException:
        web.close()
        raise


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


SELECT = """([from, to]) => {
    // select words [from, to) of the first paragraph on show, as dragging over them would, and let go of the mouse
    const block = [...view_root.querySelectorAll("[data-kind=paragraph]")].find((b) => wordMap(b).length > to);
    const map = wordMap(block), range = document.createRange();
    range.setStart(map[from].node, map[from].a); range.setEnd(map[to - 1].endNode, map[to - 1].b);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    block.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    return { start: +block.dataset.w + from, end: +block.dataset.w + to, text: range.toString() };
}"""


def highlight(reader: Reader, first: int = 2, last: int = 7) -> dict:
    chosen = reader.page.evaluate(SELECT, [first, last])
    reader.page.locator(".tip button", has_text="Highlight").click()
    reader.page.wait_for_function("() => NB.highlights.length > 0")
    return chosen


def next_checkpoint(reader: Reader) -> dict:
    reader.dismiss_offer()
    reader.read_to_checkpoint()
    return reader.page.evaluate(ASKED)
