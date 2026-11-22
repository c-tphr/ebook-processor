"""The web reader's server side: the reading model, the saved state, and the routes."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ebook_processor import reading
from ebook_processor.runner import rebuild
from ebook_processor.web import server as web
from ebook_processor.web.server import make_server
from tests.scripted import Script
from tests.test_pipeline import FIXTURE, run

PREPARED = Path(__file__).resolve().parent / "data" / "books" / "fixture.prepared.epub"


def test_the_manifest_places_sections_and_questions_by_word():
    book = reading.open_reading_book(PREPARED)
    m = book.manifest()
    assert m["title"] == "The Lighthouse Keeper's Almanac" and m["words"] == 1434
    assert [c["title"] for c in m["chapters"]][1:4] == ["The Lamp Room", "The Fog Season", "The Keeper's Log"]
    first = m["sections"][0]
    assert (first["chapter"], first["start"], first["checkpoint"]) == (2, 0, "c0001") and first["end"] > 0
    assert first["prompts"][0]["id"] == "c0001-p1" and first["prompts"][0]["answer"]
    for a, b in zip(m["sections"], m["sections"][1:]):  # in reading order, never overlapping inside a chapter
        assert (a["chapter"], a["end"]) <= (b["chapter"], b["start"]) or a["chapter"] < b["chapter"]
    assert {"label": "The Fog Season", "depth": 1, "chapter": 3, "word": 0} in m["toc"]


def test_a_chapter_is_clean_html_with_word_positions():
    book = reading.open_reading_book(PREPARED)
    ch = book.chapter(2)
    html = ch["html"]
    assert len(ch["words"]) == len(ch["flags"]) == 451 and ["IMAGE", 108, 108] in ch["markers"]
    assert '<h1 data-w="0" data-n="3" data-ref="d02-p001" data-kind="heading"' in html
    assert '<span data-anchor="s0001-start"></span>' in html and '<span data-anchor="s0001-end"></span>' in html
    assert 'data-src="OEBPS/images/gannet-rock.png"' in html and "<img src" not in html
    assert '<a data-go="2:fn1" class="noteref">' in html and 'class="note-body"' in html
    for banned in ("<script", "<style", "style=", "class=\"c", "onclick", "<link", "epub:type", "rsvp-"):
        assert banned not in html, banned
    with pytest.raises(reading.ReadingError):
        book.chapter(99)


def test_markup_that_could_run_is_dropped(tmp_path):
    from tests.prep.bookmaker import make_epub, xhtml
    from ebook_processor.prep.epub import load_book
    body = ('<p onclick="x()" style="color:red">Safe <b>text</b> <script>alert(1)</script>here &lt;b&gt; &amp; more.</p>'
            '<p><a href="https://example.org/a?b=1&amp;c=2">out</a> <a href="javascript:alert(1)">bad</a> <img src="http://x/y.png" alt="a"/></p>')
    book = load_book(make_epub(tmp_path / "b.epub", [("text/a.xhtml", xhtml(body))]))
    html = reading.render_chapter(book, book.spine[0])
    assert "onclick" not in html and "style" not in html and "alert(1)" not in html and "javascript" not in html
    assert "here &lt;b&gt; &amp; more." in html and "<img" not in html
    assert 'href="https://example.org/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer"' in html


def test_a_picture_or_a_formula_that_stands_alone_says_where_it_lies(tmp_path):
    """Outside a figure, the picture is itself the block. Without its place in the words, the speed reader stopped
    at it and had nothing to show."""
    from tests.prep.bookmaker import make_epub, xhtml
    from ebook_processor.prep.epub import load_book
    body = ('<p>One two three.</p><img src="../images/pic.png" alt="The light"/><p>Four five.</p>'
            '<math xmlns="http://www.w3.org/1998/Math/MathML" alttext="a = b"><mi>a</mi></math><p>Six.</p>')
    book = load_book(make_epub(tmp_path / "b.epub", [("text/a.xhtml", xhtml(body))], with_image=True))
    kinds = {b.kind: b for b in book.spine[0].blocks}
    assert kinds["image"].element.local == "img" and kinds["math"].element.local == "math"
    html = reading.render_chapter(book, book.spine[0])
    assert f'<img data-w="3" data-n="0" data-ref="{kinds["image"].ref}" data-kind="image" data-src="OEBPS/images/pic.png" alt="The light"/>' in html
    assert f'<span class="math" data-w="{kinds["math"].start}"' in html and 'data-kind="math">a = b</span>' in html


def test_search_and_images():
    book = reading.open_reading_book(PREPARED)
    hits = book.search("  White FLASH ")
    assert hits and hits[0]["chapter"] == 2 and hits[0]["match"].lower() == "white flash"
    assert book.search("x") == [] and book.search("zebra crossing") == []
    data, kind = book.asset("OEBPS/images/gannet-rock.png")
    assert kind == "image/png" and data.startswith(b"\x89PNG")
    for path in ("OEBPS/text/chapter-1.xhtml", "../../etc/passwd", "mimetype"):
        with pytest.raises(reading.ReadingError):
            book.asset(path)


def test_an_ordinary_epub_is_not_a_prepared_book(tmp_path):
    with pytest.raises(reading.ReadingError, match="not a prepared book"):
        reading.open_reading_book(FIXTURE)
    (tmp_path / "x.epub").write_bytes(b"nonsense")
    with pytest.raises(reading.ReadingError):
        reading.open_reading_book(tmp_path / "x.epub")


def test_saved_state_keeps_what_is_well_formed_and_never_replaces_a_first_grade():
    known = {"c0001-p1", "c0001-p2"}
    raw = {
        "overrides": {"wpm": 300, "words_per_update": 9}, "position": {"chapter": 2, "word": 40, "mode": "rsvp"},
        "grades": {"c0001-p1": {"first": "incorrect", "at": "2026-12-01T10:00:00"}, "c9-p9": {"first": "correct", "at": "x"},
                   "c0001-p2": {"first": "maybe", "at": "x"}},
        "x_attempts": [{"id": "c0001-p1", "at": "t", "grade": "incorrect", "text": "my words", "where": "read"},
                       {"id": "c0001-p1", "at": "t", "grade": "great"}, "junk"],
        "x_stats": {"seconds": {"page": 12, "rsvp": -5}, "words": {"page": 100}, "last_read": "2026-12-01T10:00:00"},
        "active_checkpoint": {"id": "c0001", "prompt_index": 1, "resume": {"chapter": 2, "word": 233, "mode": "page"},
                              "reread": {"mode": "scroll", "chapter": 2, "word": 0}},
        "x_previewed": {"c0001-p1": "2026-12-01T09:00:00", "c9-p9": "x", "c0001-p2": 7},
        "x_progress": 0.25, "anything": "else",
    }
    state = reading.clean_state(raw, "ab" * 32, known)
    assert state["overrides"] == {"wpm": 300, "words_per_update": 1} and state["position"]["mode"] == "rsvp"
    assert list(state["grades"]) == ["c0001-p1"] and len(state["x_attempts"]) == 1 and state["x_attempts"][0]["text"] == "my words"
    assert state["x_stats"]["seconds"] == {"page": 12.0, "scroll": 0, "rsvp": 0} and "anything" not in state
    assert state["active_checkpoint"]["reread"] == {"chapter": 2, "word": 0, "mode": "scroll"}
    assert "x_all" not in state["active_checkpoint"]
    assert state["x_previewed"] == {"c0001-p1": "2026-12-01T09:00:00"}  # when a question was shown ahead of its section
    again = reading.clean_state({**raw, "active_checkpoint": {**raw["active_checkpoint"], "x_all": True}}, "ab" * 32, known)
    assert again["active_checkpoint"]["x_all"] is True  # a checkpoint being gone through again asks every question
    later = reading.clean_state({**raw, "grades": {"c0001-p1": {"first": "correct", "at": "2026-12-02T10:00:00"}}}, "ab" * 32, known)
    merged = reading.merge_state(state, later)
    assert merged["grades"]["c0001-p1"] == {"first": "incorrect", "at": "2026-12-01T10:00:00"}  # written once
    s = reading.summary(merged)
    assert s["progress"] == 0.25 and s["answered"] == 1 and s["correct"] == 0 and s["in_checkpoint"]
    assert reading.summary(reading.new_state("ab" * 32)) is None and reading.results(merged) == {"c0001-p1": "missed"}


def test_results_tag_the_cards_and_choose_which_are_exported(tmp_path):
    run(tmp_path)
    out = tmp_path / "out"
    state = reading.new_state("x")
    state["grades"] = {"c0001-p1": {"first": "correct", "at": "t"}, "c0002-p1": {"first": "incorrect", "at": "t"}}
    state["x_attempts"] = [{"id": "c0003-p1", "at": "t", "grade": "skipped", "text": "", "where": "read"}]
    (out / reading.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
    report = rebuild(out, cards="missed")
    doc = json.loads((out / "fixture.questions.json").read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in doc["questions"]}
    assert by_id["c0001-p1"]["result"] == "correct" and "recall::correct" in by_id["c0001-p1"]["tags"]
    assert by_id["c0002-p1"]["result"] == "missed" and by_id["c0004-p1"]["tags"][-1] == "recall::unseen"
    assert by_id["c0003-p1"]["reached"] and not by_id["c0004-p1"]["reached"]
    rows = (out / "fixture.cards.tsv").read_text(encoding="utf-8").strip().split("\n")
    assert len(rows) == 2 and rows[1].startswith("c0002-p1\t") and report["cards"] == "missed"
    assert any("1 of the 8 questions" in n for n in report["notes"])
    rebuild(out, cards="reached")
    assert len((out / "fixture.cards.tsv").read_text(encoding="utf-8").strip().split("\n")) == 4
    rebuild(out)  # the choice is kept until it is changed
    assert len((out / "fixture.cards.tsv").read_text(encoding="utf-8").strip().split("\n")) == 4
    assert len(json.loads((out / "fixture.questions.json").read_text(encoding="utf-8"))["questions"]) == 8


# --------------------------------------------------------------------------
# The routes


@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "data", "127.0.0.1", 0, backend_factory=lambda settings: Script().backend(),
                         claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, body: bytes | None = None, json_body=None):
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(base + path, data=body, method=method, headers={"X-Ebook-Processor": "1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if response.headers.get("Content-Type", "").startswith("application/json") else raw)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield call
    server.shutdown()
    server.server_close()


def test_an_imported_book_can_be_read_and_its_state_is_kept(app):
    status, job = app("POST", "/api/books/import?name=fixture.prepared.epub", PREPARED.read_bytes())
    assert status == 201 and job["kind"] == "imported" and job["can_read"] and job["reading"] is None
    assert job["counts"]["questions"] == 4 and job["title"] == "The Lighthouse Keeper's Almanac"
    jid = job["id"]
    assert b"<title>Reader</title>" in app("GET", f"/read/{jid}")[1]
    assert app("GET", f"/api/jobs/{jid}/book")[1]["words"] == 1434
    assert len(app("GET", f"/api/jobs/{jid}/book/chapter/3")[1]["words"]) == 608
    assert app("GET", f"/api/jobs/{jid}/book/chapter/77")[0] == 404
    assert app("GET", f"/api/jobs/{jid}/book/asset?path=OEBPS/images/gannet-rock.png")[1].startswith(b"\x89PNG")
    assert app("GET", f"/api/jobs/{jid}/book/search?q=fog")[1]["results"]
    state = app("GET", f"/api/jobs/{jid}/reading")[1]
    assert state["position"] is None and state["grades"] == {}
    state.update(position={"chapter": 2, "word": 233, "mode": "page"}, x_progress=0.17,
                 grades={"c0001-p1": {"first": "incorrect", "at": "2026-12-01T10:00:00"}})
    assert app("PUT", f"/api/jobs/{jid}/reading", json_body=state)[0] == 200
    state["grades"] = {"c0001-p1": {"first": "correct", "at": "2026-12-02T10:00:00"}}  # a later Correct changes nothing
    assert app("PUT", f"/api/jobs/{jid}/reading", json_body=state)[1]["grades"]["c0001-p1"]["first"] == "incorrect"
    listed = app("GET", "/api/jobs")[1]["jobs"][0]
    assert listed["reading"]["progress"] == 0.17 and listed["reading"]["answered"] == 1 and listed["reading"]["correct"] == 0
    detail = app("GET", f"/api/jobs/{jid}")[1]
    assert detail["results"] == {"c0001-p1": "missed"} and "report" not in detail
    assert app("GET", f"/api/jobs/{jid}/review")[0] == 409 and app("POST", f"/api/jobs/{jid}/rebuild", json_body={"removed": []})[0] == 409
    assert app("GET", f"/api/jobs/{jid}/files/fixture.prepared.epub")[0] == 200


def test_what_is_not_a_prepared_book_is_refused_and_settings_are_kept(app):
    status, reply = app("POST", "/api/books/import?name=fixture.epub", FIXTURE.read_bytes())
    assert status == 400 and "not a prepared book" in reply["error"] and app("GET", "/api/jobs")[1]["jobs"] == []
    assert app("GET", "/read/20260101-000000-nothing")[0] == 404
    assert app("GET", "/api/settings")[1] == {}
    assert app("PUT", "/api/settings", json_body={"size": 21, "face": "sans", "bad": {"x": 1}})[1] == {"size": 21, "face": "sans"}
    assert app("GET", "/api/settings")[1] == {"size": 21, "face": "sans"}


def test_pages_are_the_ones_the_app_started_with(tmp_path, monkeypatch):
    """Updating the files while the app runs must not put a new page in front of old code."""
    from ebook_processor.web import server as web
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<title>library, first version</title>", encoding="utf-8")
    (static / "reader.html").write_text("<title>reader, first version</title>", encoding="utf-8")
    (static / "base.js").write_text("// base, first version", encoding="utf-8")
    (static / "settings.js").write_text("// settings, first version", encoding="utf-8")
    (static / "forms.js").write_text("// forms, first version", encoding="utf-8")
    (static / "notes.js").write_text("// notes, first version", encoding="utf-8")
    (static / "editor.js").write_text("// editor, first version", encoding="utf-8")
    monkeypatch.setattr(web, "STATIC", static)
    server = web.make_server(tmp_path / "data", "127.0.0.1", 0, claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        (static / "index.html").write_text("<title>library, second version</title>", encoding="utf-8")
        with urllib.request.urlopen(base + "/", timeout=10) as response:
            assert response.read() == b"<title>library, first version</title>"
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"  # no other site frames it
        (static / "settings.js").write_text("// settings, second version", encoding="utf-8")
        with urllib.request.urlopen(base + "/static/settings.js", timeout=10) as response:
            assert response.read() == b"// settings, first version"
            assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
        with pytest.raises(urllib.error.HTTPError):  # only the script is served this way, not the pages or other files
            urllib.request.urlopen(base + "/static/index.html", timeout=10)
    finally:
        server.shutdown()
        server.server_close()


def test_settings_are_merged_key_by_key_so_two_pages_do_not_undo_each_other(app):
    assert app("PUT", "/api/settings", json_body={"size": 21, "nb_side": "right"})[1] == {"size": 21, "nb_side": "right"}
    assert app("PUT", "/api/settings", json_body={"theme": "dark"})[1] == {"size": 21, "nb_side": "right", "theme": "dark"}
    # null puts a default back by removing the key; what is not a plain value, or is too long, is left out
    kept = app("PUT", "/api/settings", json_body={"size": None, "nb_beside": False, "add_model": "x" * 201, "list": [1]})[1]
    assert kept == {"nb_side": "right", "theme": "dark", "nb_beside": False}
    assert app("GET", "/api/settings")[1] == kept
    status, reply = app("PUT", "/api/settings", json_body={f"k{n}": "v" * 200 for n in range(30)})
    assert status == 400 and app("GET", "/api/settings")[1] == kept
    assert app("GET", "/api/practice/settings")[1]["remember_multiplier"] == 1.8


def test_the_place_in_a_book_can_be_set_from_outside_the_reader(app):
    jid = app("POST", "/api/books/import?name=fixture.prepared.epub", PREPARED.read_bytes())[1]["id"]
    state = app("GET", f"/api/jobs/{jid}/reading")[1]
    state.update(position={"chapter": 3, "word": 100, "mode": "scroll"}, x_furthest={"chapter": 3, "word": 100},
                 active_checkpoint={"id": "c0001", "prompt_index": 0, "resume": {"chapter": 2, "word": 233, "mode": "scroll"}},
                 grades={"c0001-p1": {"first": "correct", "at": "2026-12-01T10:00:00"}})
    app("PUT", f"/api/jobs/{jid}/reading", json_body=state)
    status, reply = app("PUT", f"/api/jobs/{jid}/reading/place", json_body={"chapter": 2, "word": 61})
    assert status == 200 and reply["position"] == {"chapter": 2, "word": 61, "mode": "scroll"}  # the view is kept
    after = app("GET", f"/api/jobs/{jid}/reading")[1]
    assert after["x_furthest"] == {"chapter": 3, "word": 100}        # going back does not undo how far you got
    assert "active_checkpoint" not in after and after["grades"] == state["grades"]
    assert 0 < after["x_progress"] < 0.2
    app("PUT", f"/api/jobs/{jid}/reading/place", json_body={"chapter": 3, "word": 400})
    assert app("GET", f"/api/jobs/{jid}/reading")[1]["x_furthest"] == {"chapter": 3, "word": 400}
    for bad in ({"chapter": 77, "word": 0}, {"chapter": 2, "word": 99999}, {"chapter": 2}, {"chapter": "2", "word": 1}):
        assert app("PUT", f"/api/jobs/{jid}/reading/place", json_body=bad)[0] == 400


def test_a_place_can_be_set_in_a_book_not_yet_opened():
    book = reading.open_reading_book(PREPARED)
    state = reading.place(reading.new_state("x"), book.manifest(), 2, 0)
    assert state["position"] == {"chapter": 2, "word": 0, "mode": "page"} and state["x_furthest"] == {"chapter": 2, "word": 0}


def test_entries_of_one_long_name_added_in_one_second_each_get_an_id(tmp_path, monkeypatch):
    """The id is the second and 40 characters of the name; more of the same get a counter, however many."""
    class Clock(web._dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 22, 9, 0, 0)
    monkeypatch.setattr(web._dt, "datetime", Clock)
    jobs = web.Jobs(tmp_path, backend_factory=lambda settings: None)
    name = "a-title-that-is-longer-than-forty-characters-by-far.prepared.epub"
    ids = [jobs.import_prepared(name, PREPARED.read_bytes())["id"] for _ in range(12)]
    assert len(set(ids)) == 12 and all(web.JOB_ID_RE.match(i) for i in ids)
    assert ids[0].endswith("a-title-that-is-longer-than-forty-charac") and ids[1].endswith("-2") and ids[11].endswith("-12")


def test_answers_are_merged_so_that_a_page_cannot_take_away_what_another_saved():
    def attempt(pid, minute, grade="correct", **more):
        return {"id": pid, "at": f"2026-12-01T10:{minute:02d}:00", "grade": grade, "text": "", "where": "read", **more}
    known = {"a", "b", "c"}
    old = reading.new_state("ab" * 32)
    old.update(x_attempts=[attempt("a", 0), attempt("gone", 1), attempt("b", 2, "incorrect")], x_furthest={"chapter": 3, "word": 50})
    old["x_stats"] = {"seconds": {"page": 1800, "scroll": 0, "rsvp": 0}, "words": {"page": 9000, "scroll": 0, "rsvp": 0},
                      "last_read": "2026-12-01T10:30:00"}
    # a page opened earlier knows one answer, an older place and less reading time; it sends one new answer
    new = reading.new_state("ab" * 32)
    new.update(x_attempts=[attempt("a", 0), attempt("c", 5)], x_furthest={"chapter": 2, "word": 400})
    new["x_stats"] = {"seconds": {"page": 60, "scroll": 5, "rsvp": 0}, "words": {"page": 300, "scroll": 0, "rsvp": 0},
                      "last_read": "2026-12-01T09:00:00"}
    merged = reading.merge_state(old, new, known, [1, 2, 3])
    assert [(a["id"], a["grade"]) for a in merged["x_attempts"]] == [("a", "correct"), ("b", "incorrect"), ("c", "correct")]
    assert merged["x_stats"]["seconds"] == {"page": 1800, "scroll": 5, "rsvp": 0} and merged["x_stats"]["words"]["page"] == 9000
    assert merged["x_stats"]["last_read"] == "2026-12-01T10:30:00" and merged["x_furthest"] == {"chapter": 3, "word": 50}
    # a page that sends nothing new keeps everything; the reader overruling the app changes the answer that is held
    again = reading.new_state("ab" * 32)
    again["x_attempts"] = [attempt("b", 2, "correct", overruled=True)]
    merged = reading.merge_state(merged, again, known, [1, 2, 3])
    assert [(a["id"], a["grade"]) for a in merged["x_attempts"]] == [("a", "correct"), ("b", "correct"), ("c", "correct")]
    stale = reading.new_state("ab" * 32)
    stale["x_attempts"] = [attempt("b", 2, "incorrect")]  # a page that still holds the answer as it was
    assert reading.merge_state(merged, stale, known, [1, 2, 3])["x_attempts"][1]["overruled"] is True


def test_a_second_page_of_a_book_does_not_remove_the_answers_of_the_first(app):
    jid = app("POST", "/api/books/import?name=fixture.prepared.epub", PREPARED.read_bytes())[1]["id"]
    early = app("GET", f"/api/jobs/{jid}/reading")[1]   # a tab that is opened and left
    later = app("GET", f"/api/jobs/{jid}/reading")[1]
    later["x_attempts"] = [{"id": "c0001-p1", "at": "2026-12-01T10:00:00", "grade": "correct", "text": "typed", "where": "read"}]
    later["grades"] = {"c0001-p1": {"first": "correct", "at": "2026-12-01T10:00:00"}}
    later["x_stats"]["seconds"]["page"] = 1800
    assert app("PUT", f"/api/jobs/{jid}/reading", json_body=later)[0] == 200
    assert app("PUT", f"/api/jobs/{jid}/reading", json_body=early)[0] == 200
    kept = app("GET", f"/api/jobs/{jid}/reading")[1]
    assert [a["text"] for a in kept["x_attempts"]] == ["typed"] and kept["x_stats"]["seconds"]["page"] == 1800
    assert app("GET", "/api/practice")[1]["counts"]["cards"] == 1
