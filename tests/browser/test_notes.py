"""Highlights and notes in the reader, the notebook, and the reader beside the notebook."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.browser.conftest import READY, App, Reader

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


def test_selected_text_is_highlighted_and_kept(reader: Reader):
    reader.open("2:0", "scroll")
    chosen = highlight(reader)
    kept = reader.app.notes()["highlights"]
    assert len(kept) == 1 and (kept[0]["start"], kept[0]["end"]) == (chosen["start"], chosen["end"])
    assert " ".join(kept[0]["text"].split()) == " ".join(chosen["text"].split())
    painted = reader.page.evaluate("() => window.Highlight && CSS.highlights ? CSS.highlights.get('hl').size : null")
    assert painted is None or painted >= 1     # drawn in the text, where the browser can
    # the same words are lit in RSVP, which shows the text another way
    reader.page.evaluate("([c, w]) => jump(c, w)", [2, chosen["start"]])
    reader.mode("rsvp")
    assert reader.page.locator(".rv .ctx .lit").count() >= 1


def test_a_note_on_a_paragraph_leaves_a_mark_that_opens_it(reader: Reader):
    reader.open("2:0", "page")
    reader.key("n")                            # nothing selected: a note on the paragraph at the reading place
    reader.page.locator(".ed textarea").fill("A first note.")
    reader.page.locator(".ed button.primary").click()
    reader.page.wait_for_function("() => NB.notes.length === 1")
    assert reader.app.notes()["notes"][0]["text"] == "A first note."
    pin = reader.page.locator(".text .pin")
    expect(pin).to_have_count(1)
    pin.click()
    expect(reader.page.locator(".ed textarea")).to_have_value("A first note.")


def side(page: Page):
    """The reader beside the notebook, once it has drawn."""
    frame = next(f for f in page.frames if "pane=1" in f.url)
    frame.wait_for_function(READY)
    return frame


def test_the_notebook_shows_what_was_marked_and_opens_its_place_beside_itself(reader: Reader, page: Page, book: App):
    reader.open("2:0", "scroll")
    chosen = highlight(reader)
    page.goto(f"{book.url}/#/notebook/{book.id}")
    quote = page.locator(".nb .quote a").first
    expect(quote).to_contain_text(" ".join(chosen["text"].split())[:20])
    quote.click()
    page.locator(".side iframe").wait_for()
    frame = side(page)
    shown = frame.evaluate("() => ({ pane: PANE, mode, chapter: pos.chapter, gates: document.querySelectorAll('.gate').length })")
    assert shown == {"pane": True, "mode": "scroll", "chapter": 2, "gates": 0}   # scroll view, nothing held back
    assert "split" in page.evaluate("() => document.body.className")
    before = book.reading()["position"]
    # the text points back: a click on the highlight brings its entry into view in the notebook
    spot = frame.evaluate("""(w) => { const r = rangesFor(w, w + 1)[0].getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }""", chosen["start"])
    box = page.locator(".side iframe").bounding_box()
    page.mouse.click(box["x"] + spot["x"], box["y"] + spot["y"])
    expect(page.locator(".nb .flash")).to_have_count(1)
    # looking a passage up does not move the place in the book, and the pane can be put away
    frame.locator("#modes button", has_text="Pages").click()
    frame.wait_for_function("() => mode === 'page'")
    assert book.reading()["position"] == before
    frame.locator("#b-close").click()
    expect(page.locator(".side")).to_have_count(0)


def test_in_a_narrow_window_open_goes_to_the_full_reader(reader: Reader, page: Page, book: App):
    reader.open("2:0", "scroll")
    highlight(reader)
    page.set_viewport_size({"width": 900, "height": 800})
    page.goto(f"{book.url}/#/notebook/{book.id}")
    page.locator(".nb .quote a").first.click()
    page.wait_for_url("**/read/**")
    page.wait_for_function(READY)
    assert page.evaluate("() => PANE") is False
