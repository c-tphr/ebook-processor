"""Spaced repetition for the questions of the books you have read, after Mochi's default (non-FSRS) scheduler.

What Mochi's documentation, changelog and developer establish, and this module follows:

- A review has two answers, **Remembered** and **Forgot**. Remembered multiplies the card's interval by one
  fixed multiplier; Forgot multiplies it by another, smaller one. The interval is not reset.
- A forgotten card goes to the **re-review** queue, shown after the due cards. Remembered there keeps the
  shortened interval; Forgot there drops the interval to 1 day.
- A **new** card joins the schedule when it is first remembered, and is due for its first review the same day.
  Forgot leaves it new and sends it to the back.
- Remembering an overdue card counts the time actually waited, not only the scheduled interval.
- Due dates are varied slightly (on by default; can be switched off).
- There is an optional longest interval and an optional limit on new cards per day; neither is on by default.
  There is no limit on reviews per day.
- Due cards are shown most overdue first, cards due the same day in random order.

What Mochi does not publish, so the values here are this tool's and all are settings: the Remembered multiplier
(1.8 here), the Forgot multiplier (0.5, the default two Mochi users report), the interval a card starts with
(1 day), how much due dates are varied (about 5% of the interval), and the exact formula for overdue cards
(here: the longer of the interval and the days actually waited, times the multiplier). Meeting a card before it
is due moves nothing, which Mochi does not document either way.

How cards come to exist here: a question becomes a card when you meet it at its checkpoint in the reader, and
your answer there is the card's first answer. Correct: it joins the schedule. Incorrect or skipped: it stays new.
Questions of sections you have not read are never shown.

One file per book, ``srs.json`` beside ``reading.json``:

    {"version": 1, "taken": {"<question id>@<time of the answer>": "<its grade when it was taken in>"},
     "cards": {"c0007-p1": {"state": "learned", "interval": 3.24, "due": "2026-12-04", "last": "2026-12-01",
                            "again": false, "reviews": [{"at": "...", "remembered": true, "kind": "review",
                                                         "interval": 3.24, "due": "2026-12-04"}]}}}
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import random
from pathlib import Path
from typing import Any

FILE = "srs.json"
VERSION = 1
RESET_INTERVAL = 1.0  # days, after a second miss in re-review (Mochi's changelog, v1.14.7)
FUZZ_SHARE = 0.05  # of the interval, either way
HARD_CAP = 3650.0  # days; a guard, not a setting
DEFAULTS: dict[str, Any] = {
    "remember_multiplier": 1.8,
    "forget_multiplier": 0.5,
    "first_interval": 1.0,  # days
    "max_interval": None,  # days; None: no longest interval
    "new_per_day": None,  # None: no limit
    "fuzz": True,  # vary due dates slightly
    "same_day_first_review": True,  # a newly learned card is due the day it is learned
}
LIMITS = {"remember_multiplier": (1.1, 5.0), "forget_multiplier": (0.0, 1.0), "first_interval": (1.0, 30.0),
          "max_interval": (7.0, HARD_CAP), "new_per_day": (0, 500)}
OPTIONAL = ("max_interval", "new_per_day")
_random = random.Random()


def clean_settings(raw: Any) -> dict[str, Any]:
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key, (low, high) in LIMITS.items():
        value = raw.get(key)
        if key in OPTIONAL and value is None and key in raw:
            out[key] = None
        elif key == "new_per_day":
            if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
                out[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
            out[key] = float(value)
    for key in ("fuzz", "same_day_first_review"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    return out


def day(date: _dt.date) -> str:
    return date.isoformat()


def due_date(today: _dt.date, interval: float, settings: dict[str, Any] | None = None,
             rng: random.Random | None = None) -> str:
    """The day a card with this interval comes back: whole days, at least one, varied slightly when asked."""
    days = max(1, round(interval))
    spread = round(interval * FUZZ_SHARE) if settings and settings.get("fuzz") else 0
    if spread:
        days = max(1, days + (rng or _random).randint(-spread, spread))
    return day(today + _dt.timedelta(days=days))


def _log(card: dict[str, Any], at: str, remembered: bool, kind: str, text: str = "") -> None:
    entry = {"at": at, "remembered": remembered, "kind": kind, "interval": round(card["interval"], 2), "due": card["due"]}
    if text:
        entry["text"] = text[:4000]
    card["reviews"].append(entry)


def new_card() -> dict[str, Any]:
    return {"state": "new", "interval": 0.0, "due": None, "last": None, "again": False, "reviews": []}


def is_due(card: dict[str, Any], today: _dt.date) -> bool:
    return card["state"] == "learned" and (card["again"] or (card["due"] or "") <= day(today))


def waited(card: dict[str, Any], today: _dt.date) -> int:
    """Days since the card was last scheduled."""
    try:
        return max(0, (today - _dt.date.fromisoformat(card.get("last") or "")).days)
    except ValueError:
        return 0


def answer(card: dict[str, Any], remembered: bool, today: _dt.date, settings: dict[str, Any], at: str,
           kind: str = "review", text: str = "", rng: random.Random | None = None) -> dict[str, Any]:
    """Apply one answer to a card, in place, and return it."""
    first = settings["first_interval"]
    longest = min(HARD_CAP, settings["max_interval"] or HARD_CAP)
    if card["state"] == "new":
        if remembered:  # the card joins the schedule, due for its first review the same day
            due = day(today) if settings["same_day_first_review"] else due_date(today, first)
            card.update(state="learned", interval=first, due=due, last=day(today), again=False)
        _log(card, at, remembered, "new" if kind == "review" else kind, text)  # Forgot: it stays new
        return card
    if card["again"]:  # re-review of a card forgotten earlier
        if remembered:
            card.update(again=False, due=due_date(today, card["interval"]), last=day(today))
        else:  # forgotten twice: down to one day
            card.update(interval=RESET_INTERVAL, due=day(today), last=day(today))
        _log(card, at, remembered, "rereview" if kind == "review" else kind, text)
        return card
    if not is_due(card, today):  # met early (rereading a section): nothing moves
        _log(card, at, remembered, "early", text)
        return card
    if remembered:  # an overdue card is credited with the time actually waited
        base = max(card["interval"], float(waited(card, today)), first)
        card["interval"] = min(longest, base * settings["remember_multiplier"])
        card.update(due=due_date(today, card["interval"], settings, rng), last=day(today))
    else:
        card["interval"] = max(RESET_INTERVAL, card["interval"] * settings["forget_multiplier"])
        card.update(again=True, due=day(today), last=day(today))
    _log(card, at, remembered, kind, text)
    return card


def preview(card: dict[str, Any], today: _dt.date, settings: dict[str, Any]) -> dict[str, str]:
    """What each answer would do, in words, for the buttons (without the slight variation of due dates)."""
    def words(c: dict[str, Any]) -> str:
        if c["state"] == "new":
            return "later"
        days = (_dt.date.fromisoformat(c["due"]) - today).days
        if c["again"] or days <= 0:
            return "again today"
        return "tomorrow" if days == 1 else f"in {days} days" if days < 60 else f"in {round(days / 30)} months"
    out = {}
    for name, remembered in (("remembered", True), ("forgot", False)):
        copy = {**card, "reviews": []}
        out[name] = words(answer(copy, remembered, today, {**settings, "fuzz": False}, ""))
    return out


# --------------------------------------------------------------------------
# One book's cards


def load(folder: Path) -> dict[str, Any]:
    path = folder / FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == VERSION and isinstance(data.get("cards"), dict):
                return data
        except ValueError:
            pass
    return {"version": VERSION, "taken": {}, "cards": {}}


def _taken(store: dict[str, Any], reading_state: dict[str, Any] | None) -> dict[str, str]:
    """Which answers of ``reading.json`` the cards have taken in, by question and time.

    A file written before this was kept holds a count instead, ``seen``: the first so many answers were taken in.
    That count went wrong whenever the list of answers became shorter (a question left the book, or a second page
    of the book saved an older list): the answers given after that were counted as taken in and never were. They
    are found here, once: an answer that was graded, whose question has no card and was not started over."""
    if isinstance(store.get("taken"), dict):
        return store["taken"]
    state = reading_state or {}
    attempts = [a for a in state.get("x_attempts") or [] if isinstance(a, dict)]
    restarted = state.get("x_restarted") or {}
    taken = store["taken"] = {}
    for a in attempts[:store.pop("seen", 0) if isinstance(store.get("seen"), int) else 0]:
        lost = a.get("grade") in ("correct", "incorrect") and a.get("id") not in store["cards"] and a.get("id") not in restarted
        if not lost:
            taken[_key(a)] = str(a.get("grade"))
    return taken


def _key(attempt: dict[str, Any]) -> str:
    return f"{attempt.get('id')}@{attempt.get('at')}"


def take_in(store: dict[str, Any], reading_state: dict[str, Any] | None, settings: dict[str, Any],
            today: _dt.date) -> bool:
    """Bring the book's cards up to date with what happened in the reader. Returns whether anything changed.

    Each attempt of ``reading.json`` is taken in once, in order, and is remembered by its question and its time,
    so that the list may lose entries or get them in another order. The first attempt at a question makes its
    card and is its first answer; later ones (a retry after a reread, the checkpoint met again) are answers like
    any other, which move a learned card only when it is due.

    The reader may overrule the app on a typed answer after it was taken in as a miss. The card is then put right
    as long as that miss is the last thing that happened to it.
    """
    had_no_record = not isinstance(store.get("taken"), dict)
    taken = _taken(store, reading_state)
    changed = had_no_record
    for a in (reading_state or {}).get("x_attempts") or []:
        pid, grade = a.get("id"), a.get("grade")
        if not isinstance(pid, str) or grade not in ("correct", "incorrect", "skipped"):
            continue
        at, text = str(a.get("at", "")), str(a.get("text") or "")
        try:
            when = _dt.date.fromisoformat(at[:10])
        except ValueError:
            when = today
        was = taken.get(_key(a))
        if was == grade:
            continue
        taken[_key(a)] = grade
        changed = True
        card = store["cards"].get(pid)
        if was is None:
            if card is None:
                card = store["cards"][pid] = new_card()
            if grade != "skipped":
                answer(card, grade == "correct", when, settings, at, "checkpoint", text)
        elif a.get("overruled") and grade == "correct" and card is not None and card["state"] == "new" \
                and card["reviews"] and card["reviews"][-1].get("at") == at and not card["reviews"][-1].get("remembered"):
            card["reviews"].pop()
            answer(card, True, when, settings, at, "checkpoint", text)
    return changed


def new_today(store: dict[str, Any], today: _dt.date) -> int:
    """New cards that joined the schedule today in practice (for the daily limit)."""
    return sum(1 for c in store["cards"].values()
               for r in c["reviews"] if r["kind"] == "new" and r["remembered"] and r["at"][:10] == day(today))


def counts(store: dict[str, Any], today: _dt.date, known: set[str] | None = None) -> dict[str, Any]:
    cards = [c for pid, c in store["cards"].items() if known is None or pid in known]
    learned = [c for c in cards if c["state"] == "learned"]
    upcoming = sorted(c["due"] for c in learned if not c["again"] and c["due"] and c["due"] > day(today))
    return {"due": sum(1 for c in learned if not c["again"] and (c["due"] or "") <= day(today)),
            "again": sum(1 for c in learned if c["again"]), "new": sum(1 for c in cards if c["state"] == "new"),
            "cards": len(cards), "next_due": upcoming[0] if upcoming else None,
            "next_count": upcoming.count(upcoming[0]) if upcoming else 0}


def _shuffle(pid: str, today: _dt.date) -> str:
    """A key that orders cards due the same day at random, the same way all day."""
    return hashlib.sha256(f"{day(today)}:{pid}".encode()).hexdigest()


def kind_of(card: dict[str, Any], today: _dt.date) -> str | None:
    """Where a card stands today: ``due``, ``again`` (re-review), ``new``, ``later`` (a new card already forgotten
    today, sent to the back), ``first`` (learned today and waiting for its same-day first review), or ``None``
    when it is not to be shown."""
    if card["state"] == "new":
        return "later" if card["reviews"] and card["reviews"][-1]["at"][:10] == day(today) else "new"
    if card["again"]:
        return "again"
    if (card["due"] or "") > day(today):
        return None
    return "first" if card.get("last") == day(today) else "due"


def queue(store: dict[str, Any], today: _dt.date, order: dict[str, int], new_left: int | None) -> list[str]:
    """Question ids to practise now: due cards (most overdue first, the same day's in random order), then the
    re-review queue (forgotten longest ago first), then new cards up to the day's limit, then the cards learned
    today, for their same-day first review, and last the new cards already forgotten today. ``order`` gives
    reading order and drops cards whose question is no longer in the book."""
    groups: dict[str, list[str]] = {"due": [], "again": [], "new": [], "first": [], "later": []}
    cards = {pid: c for pid, c in store["cards"].items() if pid in order}
    for pid, c in cards.items():
        kind = kind_of(c, today)
        if kind:
            groups[kind].append(pid)
    last_at = lambda pid: cards[pid]["reviews"][-1]["at"] if cards[pid]["reviews"] else ""  # noqa: E731
    groups["due"].sort(key=lambda pid: (cards[pid]["due"], _shuffle(pid, today)))
    groups["again"].sort(key=lambda pid: (last_at(pid), order[pid]))
    groups["new"].sort(key=lambda pid: (len(cards[pid]["reviews"]), last_at(pid), order[pid]))
    groups["first"].sort(key=lambda pid: _shuffle(pid, today))
    groups["later"].sort(key=lambda pid: (last_at(pid), order[pid]))
    if new_left is not None:
        groups["new"] = groups["new"][:max(0, new_left)]
        groups["later"] = groups["later"][:max(0, new_left - len(groups["new"]))]
    return groups["due"] + groups["again"] + groups["new"] + groups["first"] + groups["later"]
