"""Spaced repetition for the questions of the books you have read, after Mochi's default algorithm.

The algorithm (https://mochi.cards/docs/reviewing and its FAQ):

- A review has two answers, **Remembered** and **Forgot**.
- Remembered multiplies the card's interval by a fixed multiplier (Mochi's default: 1.8).
- Forgot multiplies it by a fixed, smaller multiplier. The interval is not reset. The card goes to the
  **re-review** queue and is shown again the same day.
- Remembered in re-review puts the card back on its (shortened) schedule. Forgot again in re-review resets the
  interval to the first interval.
- A **new** card joins the schedule when it is first remembered; Forgot postpones it.
- New cards per day can be limited.

Mochi's docs give the remembering multiplier. The forgetting multiplier (0.5 here), a new card's first interval
(1 day) and the longest interval (365 days) are this tool's choices; all four are settings.

How cards come to exist here: a question becomes a card when you meet it at its checkpoint in the reader. Marked
Correct there, it is scheduled for the first interval; marked Incorrect, it goes to re-review, due the same day;
skipped, it is a new card. Questions of sections you have not read are never shown.

One file per book, ``srs.json`` beside ``reading.json``:

    {"version": 1, "seen": <attempts of reading.json already taken in>,
     "cards": {"c0007-p1": {"state": "learned", "interval": 3.24, "due": "2026-12-04", "again": false,
                            "reviews": [{"at": "...", "remembered": true, "kind": "review", "interval": 3.24}]}}}
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

FILE = "srs.json"
VERSION = 1
DEFAULTS: dict[str, Any] = {
    "remember_multiplier": 1.8,  # Mochi's documented default
    "forget_multiplier": 0.5,
    "first_interval": 1.0,  # days
    "max_interval": 365.0,  # days
    "new_per_day": None,  # no limit
}
LIMITS = {"remember_multiplier": (1.1, 5.0), "forget_multiplier": (0.0, 1.0), "first_interval": (1.0, 30.0),
          "max_interval": (7.0, 3650.0), "new_per_day": (0, 500)}


def clean_settings(raw: Any) -> dict[str, Any]:
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key, (low, high) in LIMITS.items():
        value = raw.get(key)
        if key == "new_per_day":
            if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
                out[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
            out[key] = float(value)
    return out


def day(date: _dt.date) -> str:
    return date.isoformat()


def due_date(today: _dt.date, interval: float) -> str:
    return day(today + _dt.timedelta(days=max(1, round(interval))))


def _log(card: dict[str, Any], at: str, remembered: bool, kind: str, text: str = "") -> None:
    entry = {"at": at, "remembered": remembered, "kind": kind, "interval": round(card["interval"], 2)}
    if text:
        entry["text"] = text[:4000]
    card["reviews"].append(entry)


def new_card() -> dict[str, Any]:
    return {"state": "new", "interval": 0.0, "due": None, "again": False, "reviews": []}


def is_due(card: dict[str, Any], today: _dt.date) -> bool:
    return card["state"] == "learned" and (card["again"] or (card["due"] or "") <= day(today))


def answer(card: dict[str, Any], remembered: bool, today: _dt.date, settings: dict[str, Any], at: str,
           kind: str = "review", text: str = "") -> dict[str, Any]:
    """Apply one answer to a card, in place, and return it."""
    first, longest = settings["first_interval"], settings["max_interval"]
    if card["state"] == "new":
        if remembered:  # the card joins the schedule
            card.update(state="learned", interval=first, due=due_date(today, first), again=False)
        _log(card, at, remembered, "new" if kind == "review" else kind, text)  # Forgot: postponed, still new
        return card
    if card["again"]:  # re-review of a card forgotten earlier
        if remembered:
            card.update(again=False, due=due_date(today, card["interval"]))
        else:
            card.update(interval=first, due=day(today))  # forgotten twice: start again
        _log(card, at, remembered, "rereview" if kind == "review" else kind, text)
        return card
    if not is_due(card, today):  # met early (rereading a section): nothing moves
        _log(card, at, remembered, "early", text)
        return card
    if remembered:
        card["interval"] = min(longest, max(card["interval"], first) * settings["remember_multiplier"])
        card["due"] = due_date(today, card["interval"])
    else:
        card["interval"] = max(first, card["interval"] * settings["forget_multiplier"])
        card.update(again=True, due=day(today))
    _log(card, at, remembered, kind, text)
    return card


def preview(card: dict[str, Any], today: _dt.date, settings: dict[str, Any]) -> dict[str, str]:
    """What each answer would do, in words, for the buttons."""
    def words(c: dict[str, Any]) -> str:
        if c["state"] == "new":
            return "later"
        if c["again"]:
            return "again today"
        days = (_dt.date.fromisoformat(c["due"]) - today).days
        return "tomorrow" if days == 1 else f"in {days} days" if days < 60 else f"in {round(days / 30)} months"
    out = {}
    for name, remembered in (("remembered", True), ("forgot", False)):
        copy = {**card, "reviews": []}
        out[name] = words(answer(copy, remembered, today, settings, ""))
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
    return {"version": VERSION, "seen": 0, "cards": {}}


def take_in(store: dict[str, Any], reading_state: dict[str, Any] | None, settings: dict[str, Any],
            today: _dt.date) -> bool:
    """Bring the book's cards up to date with what happened in the reader. Returns whether anything changed.

    Each attempt of ``reading.json`` is taken in once, in order. The first attempt at a question makes its card;
    later ones (the checkpoint met again on a reread) count as reviews, which move a card only when it is due.
    """
    attempts = (reading_state or {}).get("x_attempts") or []
    fresh = attempts[store["seen"]:]
    for a in fresh:
        pid, grade = a.get("id"), a.get("grade")
        if not isinstance(pid, str) or grade not in ("correct", "incorrect", "skipped"):
            continue
        try:
            when = _dt.date.fromisoformat(str(a.get("at", ""))[:10])
        except ValueError:
            when = today
        card = store["cards"].get(pid)
        if card is None:
            card = store["cards"][pid] = new_card()
            if grade == "correct":
                card.update(state="learned", interval=settings["first_interval"],
                            due=due_date(when, settings["first_interval"]))
            elif grade == "incorrect":  # as a card forgotten once: re-review, due the same day
                card.update(state="learned", interval=settings["first_interval"], due=day(when), again=True)
            if grade != "skipped":
                _log(card, str(a.get("at", "")), grade == "correct", "checkpoint", str(a.get("text") or ""))
        elif grade != "skipped":
            answer(card, grade == "correct", when, settings, str(a.get("at", "")), "checkpoint", str(a.get("text") or ""))
    store["seen"] = len(attempts)
    return bool(fresh)


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


def queue(store: dict[str, Any], today: _dt.date, order: dict[str, int], new_left: int | None) -> list[str]:
    """Question ids to practise now: due cards (longest overdue first), then new ones up to the day's limit, then
    the re-review queue with the card forgotten longest ago first. ``order`` gives reading order and drops cards
    whose question is no longer in the book."""
    cards = {pid: c for pid, c in store["cards"].items() if pid in order}
    due = sorted((pid for pid, c in cards.items() if c["state"] == "learned" and not c["again"] and (c["due"] or "") <= day(today)),
                 key=lambda pid: (cards[pid]["due"], order[pid]))
    fresh = sorted((pid for pid, c in cards.items() if c["state"] == "new"),
                   key=lambda pid: (len(cards[pid]["reviews"]), cards[pid]["reviews"][-1]["at"] if cards[pid]["reviews"] else "", order[pid]))
    if new_left is not None:
        fresh = fresh[:max(0, new_left)]
    again = sorted((pid for pid, c in cards.items() if c["state"] == "learned" and c["again"]),
                   key=lambda pid: (cards[pid]["reviews"][-1]["at"] if cards[pid]["reviews"] else "", order[pid]))
    return due + fresh + again
