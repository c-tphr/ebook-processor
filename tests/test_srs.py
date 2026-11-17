"""Spaced repetition: the scheduler after Mochi's default algorithm, and the practice routes."""

from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.error
import urllib.request

import pytest

from ebook_processor import srs
from ebook_processor.web import server as web
from tests.test_reading import PREPARED

D = dt.date(2026, 12, 1)
S = srs.clean_settings(None)


def learned(interval: float, due: dt.date = D, again: bool = False) -> dict:
    return {"state": "learned", "interval": interval, "due": srs.day(due), "again": again, "reviews": []}


def test_remembered_multiplies_the_interval():
    card = srs.answer(learned(10), True, D, S, "t")
    assert card["interval"] == 18 and card["due"] == "2026-12-19" and not card["again"]
    assert card["reviews"] == [{"at": "t", "remembered": True, "kind": "review", "interval": 18}]
    assert srs.answer(learned(300), True, D, S, "t")["interval"] == 365  # never past the longest interval


def test_forgot_shortens_without_resetting_and_brings_the_card_back_today():
    card = srs.answer(learned(10), False, D, S, "t")
    assert card["interval"] == 5 and card["again"] and card["due"] == "2026-12-01"
    assert srs.is_due(card, D)
    again = srs.answer(dict(card), True, D, S, "t2")  # remembered in re-review: back on the shortened schedule
    assert not again["again"] and again["interval"] == 5 and again["due"] == "2026-12-06"
    assert again["reviews"][-1]["kind"] == "rereview"
    twice = srs.answer(dict(card, reviews=[]), False, D, S, "t2")  # forgotten again: starts over
    assert twice["again"] and twice["interval"] == 1 and twice["due"] == "2026-12-01"
    assert srs.answer(learned(1.2), False, D, S, "t")["interval"] == 1  # never below the first interval


def test_a_new_card_joins_when_remembered_and_is_postponed_when_forgotten():
    card = srs.answer(srs.new_card(), False, D, S, "t")
    assert card["state"] == "new" and card["due"] is None and card["reviews"][-1]["kind"] == "new"
    card = srs.answer(card, True, D, S, "t2")
    assert card["state"] == "learned" and card["interval"] == 1 and card["due"] == "2026-12-02"


def test_an_early_answer_is_logged_and_moves_nothing():
    card = srs.answer(learned(10, D + dt.timedelta(days=4)), False, D, S, "t")
    assert card["interval"] == 10 and card["due"] == "2026-12-05" and not card["again"]
    assert card["reviews"][-1]["kind"] == "early"


def test_the_buttons_say_when_the_card_comes_back():
    assert srs.preview(learned(10), D, S) == {"remembered": "in 18 days", "forgot": "again today"}
    assert srs.preview(srs.new_card(), D, S) == {"remembered": "tomorrow", "forgot": "later"}
    assert srs.preview(learned(200), D, S)["remembered"] == "in 12 months"
    card = learned(10)
    srs.preview(card, D, S)
    assert card["interval"] == 10 and card["reviews"] == []  # a preview changes nothing


def test_settings_keep_only_usable_values():
    s = srs.clean_settings({"remember_multiplier": 2.5, "forget_multiplier": 7, "first_interval": "3", "new_per_day": 15,
                            "max_interval": True, "other": 1})
    assert s == {**srs.DEFAULTS, "remember_multiplier": 2.5, "new_per_day": 15}
    assert srs.answer(learned(10), True, D, s, "t")["interval"] == 25


def test_checkpoint_answers_become_cards_once_each():
    reading_state = {"x_attempts": [
        {"id": "a", "at": "2026-11-30T10:00:00", "grade": "correct", "text": "mine"},
        {"id": "b", "at": "2026-11-30T10:01:00", "grade": "incorrect"},
        {"id": "c", "at": "2026-11-30T10:02:00", "grade": "skipped"},
        {"id": "b", "at": "2026-11-30T10:09:00", "grade": "correct"},  # the retry after a reread
    ]}
    store = srs.load(PREPARED.parent / "nowhere")
    assert srs.take_in(store, reading_state, S, D)
    a, b, c = store["cards"]["a"], store["cards"]["b"], store["cards"]["c"]
    assert (a["state"], a["due"], a["reviews"][0]["text"]) == ("learned", "2026-12-01", "mine")
    assert (b["again"], b["due"], [r["kind"] for r in b["reviews"]]) == (False, "2026-12-01", ["checkpoint", "checkpoint"])
    assert c["state"] == "new" and c["reviews"] == []
    assert not srs.take_in(store, reading_state, S, D) and len(a["reviews"]) == 1  # nothing is taken in twice
    assert srs.counts(store, D) == {"due": 2, "again": 0, "new": 1, "cards": 3, "next_due": None, "next_count": 0}
    assert srs.counts(store, D - dt.timedelta(days=1))["next_due"] == "2026-12-01"
    assert srs.queue(store, D, {"a": 0, "b": 1, "c": 2}, None) == ["a", "b", "c"]
    assert srs.queue(store, D, {"a": 0, "b": 1, "c": 2}, 0) == ["a", "b"]  # the day's new cards are used up
    assert srs.queue(store, D, {"b": 0}, None) == ["b"]  # a question no longer in the book is left out
    srs.answer(a, False, D, S, "2026-12-01T09:00:00")
    assert srs.queue(store, D, {"a": 0, "b": 1, "c": 2}, None) == ["b", "c", "a"]  # the forgotten card comes last


# --------------------------------------------------------------------------
# The routes


@pytest.fixture
def app(tmp_path, monkeypatch):
    clock = {"today": D}
    monkeypatch.setattr(web.Jobs, "today", lambda self: clock["today"])
    server = web.make_server(tmp_path / "data", "127.0.0.1", 0, claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, body: bytes | None = None, json_body=None):
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(base + path, data=body, method=method, headers={"X-Ebook-Processor": "1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield call, clock
    server.shutdown()
    server.server_close()


def test_practice_follows_the_reader_and_the_calendar(app):
    call, clock = app
    jid = call("POST", "/api/books/import?name=fixture.prepared.epub", PREPARED.read_bytes())[1]["id"]
    empty = call("GET", "/api/practice")[1]
    assert empty["card"] is None and empty["counts"]["cards"] == 0
    state = call("GET", f"/api/jobs/{jid}/reading")[1]
    state.update(position={"chapter": 2, "word": 233, "mode": "page"}, x_attempts=[
        {"id": "c0001-p1", "at": "2026-12-01T10:00:00", "grade": "correct", "text": "", "where": "read"},
        {"id": "c0002-p1", "at": "2026-12-01T10:05:00", "grade": "incorrect", "text": "", "where": "read"},
        {"id": "c0002-p2", "at": "2026-12-01T10:06:00", "grade": "skipped", "text": "", "where": "read"}],
        grades={"c0001-p1": {"first": "correct", "at": "2026-12-01T10:00:00"}, "c0002-p1": {"first": "incorrect", "at": "2026-12-01T10:05:00"}})
    call("PUT", f"/api/jobs/{jid}/reading", json_body=state)
    listed = call("GET", "/api/jobs")[1]["jobs"][0]["practice"]
    assert (listed["due"], listed["again"], listed["new"], listed["cards"]) == (0, 1, 1, 3)
    now = call("GET", f"/api/practice?job={jid}")[1]
    card = now["card"]
    assert now["left"] == 2 and card["id"] == "c0002-p2" and card["state"] == "new"  # new cards before the re-review queue
    assert card["book"] == "The Lighthouse Keeper's Almanac" and card["question"] and card["answer"] and card["go"].startswith("3:")
    assert card["preview"] == {"remembered": "tomorrow", "forgot": "later"}
    after = call("POST", "/api/practice/answer", json_body={"job": jid, "id": "c0002-p2", "remembered": True, "text": "x", "only": jid})[1]
    assert after["card"]["id"] == "c0002-p1" and after["card"]["again"] and after["card"]["preview"]["forgot"] == "again today"
    done = call("POST", "/api/practice/answer", json_body={"job": jid, "id": "c0002-p1", "remembered": True})[1]
    assert done["card"] is None and done["counts"]["next_due"] == "2026-12-02" and done["counts"]["next_count"] == 3
    clock["today"] = D + dt.timedelta(days=1)
    tomorrow = call("GET", "/api/practice")[1]
    assert tomorrow["counts"]["due"] == 3 and tomorrow["card"]["id"] == "c0001-p1"
    again = call("POST", "/api/practice/answer", json_body={"job": jid, "id": "c0001-p1", "remembered": True})[1]
    assert again["counts"]["due"] == 2
    clock["today"] = D + dt.timedelta(days=2)
    assert call("GET", "/api/practice")[1]["counts"]["due"] == 2  # c0001-p1 now waits 1.8 days, rounded to 2: due on the 4th
    assert call("POST", "/api/practice/answer", json_body={"job": jid, "id": "nope", "remembered": True})[0] == 404
    assert call("POST", "/api/practice/answer", json_body={"job": jid, "id": "c0001-p1"})[0] == 400


def test_practice_settings_and_the_daily_limit(app):
    call, _ = app
    jid = call("POST", "/api/books/import?name=fixture.prepared.epub", PREPARED.read_bytes())[1]["id"]
    state = call("GET", f"/api/jobs/{jid}/reading")[1]
    state.update(position={"chapter": 2, "word": 1, "mode": "page"}, x_attempts=[
        {"id": pid, "at": "2026-12-01T10:00:00", "grade": "skipped", "text": "", "where": "read"} for pid in ("c0001-p1", "c0002-p1", "c0002-p2")])
    call("PUT", f"/api/jobs/{jid}/reading", json_body=state)
    assert call("GET", "/api/practice")[1]["counts"]["new"] == 3
    saved = call("PUT", "/api/practice/settings", json_body={"new_per_day": 1, "remember_multiplier": 3, "junk": 1})[1]
    assert saved["new_per_day"] == 1 and saved["remember_multiplier"] == 3.0 and "junk" not in saved
    limited = call("GET", "/api/practice")[1]
    assert limited["counts"]["new"] == 1 and limited["left"] == 1 and limited["settings"]["new_per_day"] == 1
    after = call("POST", "/api/practice/answer", json_body={"job": jid, "id": limited["card"]["id"], "remembered": True})[1]
    assert after["card"] is None and after["counts"]["new"] == 0  # the day's one new card is used
