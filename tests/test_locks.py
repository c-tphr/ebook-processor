"""The two kinds of lock of the app, and their order.

The library's lock is held for moments. A book's own lock guards its run and its built files. A model is asked
with no lock held. A book's lock is taken before the library's, never after it.
"""

from __future__ import annotations

import threading
import time

import pytest

from ebook_processor.web import server as web
from tests.scripted import Script
from tests.test_pipeline import FIXTURE


@pytest.fixture
def lib(tmp_path):
    """A library with one processed book, and a model whose answers can be held back."""
    real = Script().backend()
    gate = {"entered": threading.Event(), "release": threading.Event(), "hold": False}

    class Held:
        def complete(self, **kwargs):
            if gate["hold"]:
                gate["entered"].set()
                assert gate["release"].wait(20), "the test never let the model answer"
            return real.complete(**kwargs)

    jobs = web.Jobs(tmp_path / "data", backend_factory=lambda settings: Held())
    job = jobs.create("fixture.epub", FIXTURE.read_bytes(), web.from_options(density="short"))
    for _ in range(400):
        if jobs.get(job["id"])["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert jobs.get(job["id"])["status"] == "done"
    ids = [p["id"] for s in jobs.manifest(job["id"])["sections"] for p in s["prompts"]]
    return jobs, job["id"], ids, gate


def in_thread(call) -> tuple[threading.Thread, dict]:
    got: dict = {}

    def run() -> None:
        try:
            got["value"] = call()
        except Exception as exc:  # noqa: BLE001  the test looks at it
            got["error"] = exc
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, got


def test_the_app_goes_on_while_a_model_checks_a_question(lib):
    """'Save and check' held the one lock for as long as the model took: every other request waited."""
    jobs, jid, ids, gate = lib
    gate["hold"] = True
    check, got = in_thread(lambda: jobs.check_question(jid, ids[0]))
    assert gate["entered"].wait(10)
    began = time.monotonic()
    assert jobs.listing() and jobs.reading_state(jid) is not None and jobs.practice() is not None
    assert jobs.edit_question(jid, ids[1], {"answer": "Another answer, saved meanwhile."})["status"] == "edited"  # the same book
    assert time.monotonic() - began < 8 and check.is_alive()
    gate["release"].set()
    check.join(20)
    assert "error" not in got and got["value"]["verdict"]["passed"] is True
    assert jobs.book_question(jid, ids[1])["answer"] == "Another answer, saved meanwhile."


def test_a_verdict_is_not_kept_for_a_question_changed_while_it_was_checked(lib):
    jobs, jid, ids, gate = lib
    gate["hold"] = True
    check, got = in_thread(lambda: jobs.check_question(jid, ids[0]))
    assert gate["entered"].wait(10)
    gate["hold"] = False
    jobs.edit_question(jid, ids[0], {"answer": "Rewritten while the check ran."})
    gate["release"].set()
    check.join(20)
    assert isinstance(got.get("error"), web.ApiError) and "changed while it was being checked" in str(got["error"])
    shown = jobs.book_question(jid, ids[0])
    assert shown["answer"] == "Rewritten while the check ran." and not shown.get("verdict")


def test_a_delete_and_a_save_of_one_book_at_the_same_time_both_finish(lib):
    """Two paths that take the two locks: with the locks in two orders they would wait for each other for ever."""
    jobs, jid, ids, _ = lib
    section = jobs.manifest(jid)["sections"][0]["id"]
    for round_ in range(3):
        mine = jobs.add_question(jid, {"section": section, "question": f"What is point {round_}?", "answer": "A point of the text."})["id"]
        threads = [in_thread(lambda: jobs.delete_question(jid, mine)),
                   in_thread(lambda: jobs.edit_question(jid, ids[1], {"answer": f"Answer number {round_}."})),
                   in_thread(lambda: jobs.restart_question(jid, ids[2])),
                   in_thread(lambda: jobs.rebuild(jid, None))]
        for thread, got in threads:
            thread.join(30)
            assert not thread.is_alive() and "error" not in got, got
    assert jobs.book_question(jid, ids[1])["answer"] == "Answer number 2."


def test_a_books_lock_is_never_asked_for_with_the_librarys_lock_held(lib):
    jobs, jid, _, _ = lib
    with jobs.lock:
        with pytest.raises(RuntimeError, match="a book's lock comes first"):
            with jobs.booked(jid):
                pass
    with jobs.booked(jid), jobs.lock, jobs.booked(jid):   # held already: taking it again changes no order
        assert jobs.lock.held()
    assert not jobs.lock.held()


def test_downloads_asked_for_together_build_the_files_once(lib, monkeypatch):
    jobs, jid, _, _ = lib
    section = next(s for s in jobs.manifest(jid)["sections"] if s["prompts"])
    made: dict = {}
    jobs.notes_do(jid, lambda store, book: made.update(web.notes.add_highlight(store, book, section["chapter"], section["start"] + 3, section["start"] + 9)))
    jobs.write_question(jid, made["id"], "What is asked from the highlight?", "This.")
    assert jobs.get(jid)["exports_stale"]
    builds: list = []
    real = web.rebuild

    def counted(*args, **kwargs):
        builds.append(1)
        time.sleep(0.3)
        return real(*args, **kwargs)
    monkeypatch.setattr(web, "rebuild", counted)
    threads = [in_thread(lambda: jobs.fresh_exports(jid)) for _ in range(3)]
    for thread, got in threads:
        thread.join(30)
        assert not thread.is_alive() and "error" not in got, got
    assert len(builds) == 1 and "exports_stale" not in jobs.get(jid)


def test_a_question_accepted_while_the_files_are_built_leaves_them_marked_as_old(lib, monkeypatch):
    jobs, jid, _, _ = lib
    real = web.rebuild

    def changed_meanwhile(*args, **kwargs):
        with jobs.lock:
            jobs._exports_stale(jid)   # what accepting a question from a highlight does, after the build read them
        return real(*args, **kwargs)
    monkeypatch.setattr(web, "rebuild", changed_meanwhile)
    jobs.rebuild(jid, None)
    assert jobs.get(jid)["exports_stale"]
    monkeypatch.setattr(web, "rebuild", real)
    jobs.fresh_exports(jid)
    assert "exports_stale" not in jobs.get(jid)
