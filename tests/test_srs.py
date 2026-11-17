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
S = {**srs.clean_settings(None), "fuzz": False}


def learned(interval: float, due: dt.date = D, again: bool = False, waited: int | None = None) -> dict:
    last = due - dt.timedelta(days=round(interval) if waited is None else waited)
    return {"state": "learned", "interval": interval, "due": srs.day(due), "last": srs.day(last), "again": again, "reviews": []}


def test_remembered_multiplies_the_interval():
    card = srs.answer(learned(10), True, D, S, "t")
    assert card["interval"] == 18 and card["due"] == "2026-12-19" and card["last"] == "2026-12-01" and not card["again"]
    assert card["reviews"] == [{"at": "t", "remembered": True, "kind": "review", "interval": 18, "due": "2026-12-19"}]
    assert srs.answer(learned(300), True, D, S, "t")["interval"] == 540  # no longest interval unless one is set
    assert srs.answer(learned(300), True, D, {**S, "max_interval": 365.0}, "t")["interval"] == 365


def test_an_overdue_card_is_credited_with_the_time_actually_waited():
    late = srs.answer(learned(10, D - dt.timedelta(days=15)), True, D, S, "t")  # due 15 days ago: waited 25
    assert late["interval"] == 45 and late["due"] == "2027-01-15"


def test_forgot_shortens_without_resetting_and_sends_the_card_to_re_review():
    card = srs.answer(learned(10), False, D, S, "t")
    assert card["interval"] == 5 and card["again"] and card["due"] == "2026-12-01" and srs.kind_of(card, D) == "again"
    again = srs.answer(dict(card), True, D, S, "t2")  # remembered in re-review: keeps the shortened interval
    assert not again["again"] and again["interval"] == 5 and again["due"] == "2026-12-06"
    assert again["reviews"][-1]["kind"] == "rereview"
    twice = srs.answer(dict(card, reviews=[]), False, D, {**S, "first_interval": 3.0}, "t2")
    assert twice["again"] and twice["interval"] == 1 and twice["due"] == "2026-12-01"  # one day, whatever the settings
    assert srs.answer(learned(1.2), False, D, S, "t")["interval"] == 1
    assert srs.answer(learned(10), False, D, {**S, "forget_multiplier": 0.0}, "t")["interval"] == 1


def test_a_new_card_joins_when_remembered_due_the_same_day_and_stays_new_when_forgotten():
    card = srs.answer(srs.new_card(), False, D, S, "t")
    assert card["state"] == "new" and card["due"] is None and card["reviews"][-1]["kind"] == "new"
    card = srs.answer(card, True, D, S, "t2")
    assert card["state"] == "learned" and card["interval"] == 1 and card["due"] == "2026-12-01" and srs.kind_of(card, D) == "first"
    first = srs.answer(dict(card), True, D, S, "t3")  # the same-day first review
    assert first["interval"] == 1.8 and first["due"] == "2026-12-03"
    next_day = srs.answer(srs.new_card(), True, D, {**S, "same_day_first_review": False}, "t")
    assert next_day["due"] == "2026-12-02"


def test_an_early_answer_is_logged_and_moves_nothing():
    card = srs.answer(learned(10, D + dt.timedelta(days=4)), False, D, S, "t")
    assert card["interval"] == 10 and card["due"] == "2026-12-05" and not card["again"]
    assert card["reviews"][-1]["kind"] == "early" and srs.kind_of(card, D) is None


def test_due_dates_vary_slightly_when_asked():
    import random
    days = set()
    for seed in range(40):
        card = srs.answer(learned(100), True, D, {**S, "fuzz": True}, "t", rng=random.Random(seed))
        assert card["interval"] == 180  # the interval itself is exact; only the day moves
        days.add((dt.date.fromisoformat(card["due"]) - D).days)
    assert min(days) >= 171 and max(days) <= 189 and len(days) > 5
    short = {srs.answer(learned(4), True, D, {**S, "fuzz": True}, "t", rng=random.Random(n))["due"] for n in range(20)}
    assert short == {"2026-12-08"}  # a short interval is not varied


def test_the_buttons_say_when_the_card_comes_back():
    assert srs.preview(learned(10), D, S) == {"remembered": "in 18 days", "forgot": "again today"}
    assert srs.preview(srs.new_card(), D, S) == {"remembered": "again today", "forgot": "later"}
    assert srs.preview(learned(200), D, S)["remembered"] == "in 12 months"
    card = learned(10)
    srs.preview(card, D, {**S, "fuzz": True})
    assert card["interval"] == 10 and card["reviews"] == []  # a preview changes nothing


def test_settings_keep_only_usable_values():
    s = srs.clean_settings({"remember_multiplier": 2.5, "forget_multiplier": 7, "first_interval": "3", "new_per_day": 15,
                            "max_interval": 90, "fuzz": False, "same_day_first_review": "no", "other": 1})
    assert s == {**srs.DEFAULTS, "remember_multiplier": 2.5, "new_per_day": 15, "max_interval": 90.0, "fuzz": False}
    assert srs.clean_settings({**s, "max_interval": None, "new_per_day": None})["max_interval"] is None
    assert srs.DEFAULTS["max_interval"] is None and srs.DEFAULTS["new_per_day"] is None and srs.DEFAULTS["fuzz"] is True


def test_a_checkpoint_answer_is_the_cards_first_answer():
    reading_state = {"x_attempts": [
        {"id": "a", "at": "2026-11-30T10:00:00", "grade": "correct", "text": "mine"},
        {"id": "b", "at": "2026-11-30T10:01:00", "grade": "incorrect"},
        {"id": "c", "at": "2026-11-30T10:02:00", "grade": "skipped"},
        {"id": "d", "at": "2026-11-30T10:03:00", "grade": "incorrect"},
        {"id": "b", "at": "2026-11-30T10:09:00", "grade": "correct"},  # the retry after a reread
    ]}
    store = srs.load(PREPARED.parent / "nowhere")
    assert srs.take_in(store, reading_state, S, D)
    a, b, c, d = (store["cards"][k] for k in "abcd")
    assert (a["state"], a["due"], a["reviews"][0]["text"]) == ("learned", "2026-11-30", "mine")  # due the day it was learned
    assert (b["state"], b["due"], [r["remembered"] for r in b["reviews"]]) == ("learned", "2026-11-30", [False, True])
    assert c["state"] == "new" and c["reviews"] == []
    assert d["state"] == "new" and len(d["reviews"]) == 1  # missed: it stays new until remembered once
    assert not srs.take_in(store, reading_state, S, D) and len(a["reviews"]) == 1  # nothing is taken in twice
    assert srs.counts(store, D) == {"due": 2, "again": 0, "new": 2, "cards": 4, "next_due": None, "next_count": 0}
    order = {k: n for n, k in enumerate("abcd")}
    line = srs.queue(store, D, order, None)
    assert set(line[:2]) == {"a", "b"} and line[2:] == ["c", "d"]  # due first; then new, the untouched one before the missed
    assert srs.queue(store, D, order, 1)[2:] == ["c"] and srs.queue(store, D, {"b": 0}, None) == ["b"]
    assert srs.queue(store, D, order, None) == line  # the day's order does not change between calls
    srs.answer(a, False, D, S, "2026-12-01T09:00:00")
    assert srs.queue(store, D, order, None) == ["b", "a", "c", "d"]  # due, then the re-review, then new
    srs.answer(c, True, D, S, "2026-12-01T09:05:00")
    assert srs.queue(store, D, order, None) == ["b", "a", "d", "c"]  # learned today: its first review comes last


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
    call("PUT", "/api/practice/settings", json_body={"fuzz": False})
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
    assert (listed["due"], listed["again"], listed["new"], listed["cards"]) == (1, 0, 2, 3)
    now = call("GET", f"/api/practice?job={jid}")[1]
    card = now["card"]
    assert now["left"] == 3 and card["id"] == "c0002-p2" and card["state"] == "new"  # new cards before today's first reviews
    assert card["book"] == "The Lighthouse Keeper's Almanac" and card["question"] and card["answer"] and card["go"].startswith("3:")
    assert card["preview"] == {"remembered": "again today", "forgot": "later"}
    after = call("POST", "/api/practice/answer", json_body={"job": jid, "id": "c0002-p2", "remembered": True, "text": "x", "only": jid})[1]
    assert after["card"]["first"] and after["card"]["preview"] == {"remembered": "in 2 days", "forgot": "again today"}
    first_reviews = set()
    while after["card"] and after["card"]["first"]:  # the same-day first reviews of the two learned cards
        first_reviews.add(after["card"]["id"])
        after = call("POST", "/api/practice/answer", json_body={"job": jid, "id": after["card"]["id"], "remembered": True})[1]
    assert first_reviews == {"c0001-p1", "c0002-p2"}
    assert after["card"]["id"] == "c0002-p1" and after["card"]["state"] == "new"  # missed at its checkpoint today: new, and last
    after = call("POST", "/api/practice/answer", json_body={"job": jid, "id": "c0002-p1", "remembered": False})[1]
    assert after["card"]["id"] == "c0002-p1" and after["card"]["state"] == "new" and after["left"] == 1  # still new
    clock["today"] = D + dt.timedelta(days=1)
    assert call("GET", "/api/practice")[1]["counts"]["due"] == 0
    clock["today"] = D + dt.timedelta(days=2)
    due = call("GET", "/api/practice")[1]
    assert due["counts"]["due"] == 2 and due["card"]["id"] in first_reviews and not due["card"]["first"]
    forgot = call("POST", "/api/practice/answer", json_body={"job": jid, "id": due["card"]["id"], "remembered": False})[1]
    assert forgot["counts"]["again"] == 1 and forgot["counts"]["due"] == 1 and forgot["card"]["id"] != due["card"]["id"]
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
    saved = call("PUT", "/api/practice/settings", json_body={"new_per_day": 1, "remember_multiplier": 3, "max_interval": None, "junk": 1})[1]
    assert saved["new_per_day"] == 1 and saved["remember_multiplier"] == 3.0 and saved["max_interval"] is None and "junk" not in saved
    limited = call("GET", "/api/practice")[1]
    assert limited["counts"]["new"] == 1 and limited["left"] == 1 and limited["settings"]["new_per_day"] == 1
    after = call("POST", "/api/practice/answer", json_body={"job": jid, "id": limited["card"]["id"], "remembered": True})[1]
    assert after["counts"]["new"] == 0  # the day's one new card is used
    assert after["card"]["id"] == limited["card"]["id"] and after["card"]["first"]  # and it waits for its same-day first review
