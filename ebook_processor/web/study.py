"""The reader's part of the local web app: a book as the reader shows it, the reader's place and grades, the
highlights and notes of a book, and practice.

This is one of the parts the class ``Jobs`` of ``server.py`` is put together from. Its methods use the fields that
``Jobs.__init__`` makes, and the methods of the other parts, through ``self``. It does not import ``server.py``.
"""

from __future__ import annotations

import datetime as _dt
import json
from http import HTTPStatus
from typing import Any

from .. import assist, notes, pathway, reading, srs
from .. import forms as qforms
from .. import highlight_questions as hq
from ..outputs import stem_of
from ..store import write_json
from .common import ApiError

# The locks, as in every part of ``Jobs``: a book's lock (``self.booked(id)``) is taken first, the library's lock
# (``self.lock``) second and for moments only, never across a call to a model or while a book's files are built. The
# whole rule is in ``Jobs.__init__`` (server.py).


class Study:
    """What ``Jobs`` keeps of the reader's work on a book (see reading.py, notes.py and srs.py)."""

    # ---- reading: the book as the reader shows it, and the reader's place and grades

    def manifest(self, job_id: str) -> dict[str, Any]:
        """The book as the reader needs it, with the questions from highlights that its checkpoints ask, and with
        its graduated pathway when it was prepared with one: the ephemeral questions inside its sections, and
        what the pathway adds to its practice questions (see ``pathway.into_manifest``)."""
        book = self.book(job_id)
        made = pathway.into_manifest(book.manifest(), pathway.load(self.folder(job_id)), book.blocks)
        made = hq.with_questions(made, notes.load(self.folder(job_id)))
        return {**made, "editable": self.editable(job_id)}

    def reading_state(self, job_id: str) -> dict[str, Any]:
        book = self.book(job_id)
        return reading.load_state(self.folder(job_id)) or reading.new_state(book.data["book"]["source_sha256"])

    def save_reading(self, job_id: str, raw: Any) -> dict[str, Any]:
        book = self.book(job_id)
        known = {p["id"] for c in book.data["checkpoints"] for p in c["prompts"]}
        with self.lock:
            known |= hq.ids(notes.load(self.folder(job_id)))  # questions from highlights are graded too
            checks = pathway.ids(pathway.load(self.folder(job_id)))  # ephemeral questions: met, never graded
            old = reading.load_state(self.folder(job_id))
            state = reading.merge_state(old, reading.clean_state(raw, book.data["book"]["source_sha256"], known, checks),
                                        known, [c["index"] for c in book.manifest()["chapters"]])
            write_json(self.folder(job_id) / reading.STATE_FILE, state)
        return state

    def set_place(self, job_id: str, raw: Any) -> dict[str, Any]:
        """Move the reader's place in a book, from outside the reader."""
        book = self.book(job_id)
        if not isinstance(raw, dict) or not isinstance(raw.get("chapter"), int) or not isinstance(raw.get("word"), int) \
                or isinstance(raw["chapter"], bool) or isinstance(raw["word"], bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "a place needs a chapter and a word")
        with self.lock:
            state = reading.load_state(self.folder(job_id)) or reading.new_state(book.data["book"]["source_sha256"])
            try:
                reading.place(state, book.manifest(), raw["chapter"], raw["word"])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            write_json(self.folder(job_id) / reading.STATE_FILE, state)
        return state

    # ---- highlights and notes

    def notes_do(self, job_id: str, change: Any = None) -> dict[str, Any]:
        """The book's highlights and notes, after ``change(store, book)`` when one is given."""
        book = self.book(job_id)
        folder = self.folder(job_id)
        with self.lock:
            store = notes.load(folder)
            if change is not None:
                try:
                    change(store, book)
                except notes.NotesError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                write_json(folder / notes.FILE, store)
        if change is not None:
            self.file_book(job_id, quiet=True)
        return store

    def notebook(self, job_id: str) -> dict[str, Any]:
        """The notebook's entries, and under ``asking`` what is being written for them at the same moment."""
        book = self.book(job_id)
        with self.lock:
            store = self.notes_do(job_id)
            asking = self._writing(job_id)
        made = notes.notebook(book, store, reading.load_state(self.folder(job_id)), self.sources(job_id))
        return {**made, "editable": self.editable(job_id), "asking": asking}

    # ---- practice (spaced repetition)

    def srs_settings(self) -> dict[str, Any]:
        path = self.data / "srs-settings.json"
        try:
            return srs.clean_settings(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)
        except ValueError:
            return srs.clean_settings(None)

    def save_srs_settings(self, raw: Any) -> dict[str, Any]:
        """Keep the practice settings a page sent, and give them back as they are kept."""
        settings = srs.clean_settings(raw)
        with self.lock:
            write_json(self.data / "srs-settings.json", settings)
        return settings

    def cards(self, job_id: str) -> dict[str, Any]:
        """The book's cards, brought up to date with what was answered in the reader."""
        folder = self.folder(job_id)
        with self.lock:
            store = srs.load(folder)
            if srs.take_in(store, reading.load_state(folder), self.srs_settings(), self.today()):
                write_json(folder / srs.FILE, store)
        return store

    def practice(self, only: str | None = None) -> dict[str, Any]:
        """What to practise now, across every book or in one: the counts and the next card."""
        today, settings = self.today(), self.srs_settings()
        with self.lock:
            ids = [j["id"] for j in sorted(self.jobs.values(), key=lambda j: j["created"])
                   if j["status"] == "done" and self.prepared(j["id"]).is_file() and (only is None or j["id"] == only)]
        if only is not None and only not in ids:
            self.get(only)
            raise ApiError(HTTPStatus.CONFLICT, "this book cannot be practised yet")
        total = {"due": 0, "again": 0, "new": 0, "cards": 0, "next_due": None, "next_count": 0}
        stores, lists = {}, {"due": [], "new": [], "again": [], "first": [], "later": []}
        new_done = 0
        decks: list[tuple[str, dict[str, Any], Any, dict[str, Any]]] = []
        for job_id in ids:
            store = self.cards(job_id)
            if not store["cards"]:
                continue
            try:
                manifest = self.manifest(job_id)
            except ApiError:
                continue
            prompts = {p["id"]: (s, p) for s in manifest["sections"] for p in s["prompts"]}
            prompts.update(hq.practice_prompts(manifest, notes.load(self.folder(job_id))))  # cards from highlights
            decks.append((job_id, store, manifest, prompts))
        if only is None:  # the cards made from notes are practised with those of the books
            store = srs.load(self.data / assist.FOLDER)
            if store["cards"]:
                vault = self.vault()
                vault.refresh()
                with vault.lock:
                    decks.append((assist.DECK, store, None, assist.practice_prompts(vault, assist.load(self.data))))
        for job_id, store, manifest, prompts in decks:
            order = {pid: n for n, pid in enumerate(prompts)}
            stores[job_id] = (store, manifest, prompts)
            new_done += srs.new_today(store, today)
            c = srs.counts(store, today, set(prompts))
            for key in ("due", "again", "new", "cards"):
                total[key] += c[key]
            if c["next_due"] and (total["next_due"] is None or c["next_due"] <= total["next_due"]):
                total["next_count"] = c["next_count"] + (total["next_count"] if c["next_due"] == total["next_due"] else 0)
                total["next_due"] = c["next_due"]
            for pid in srs.queue(store, today, order, None):
                card = store["cards"][pid]
                kind = srs.kind_of(card, today)
                key = srs.shuffle_key(pid, today) if kind in ("due", "first") else card["reviews"][-1]["at"] if card["reviews"] else ""
                lists[kind].append((card["due"] or "", key, job_id, pid))
        limit = settings["new_per_day"]
        room = None if limit is None else max(0, limit - new_done)
        fresh = lists["new"] if room is None else lists["new"][:room]
        later = sorted(lists["later"], key=lambda t: t[1])
        later = later if room is None else later[:max(0, room - len(fresh))]
        total["new"] = len(fresh) + len(later)
        line = sorted(lists["due"]) + sorted(lists["again"], key=lambda t: t[1]) + fresh + sorted(lists["first"], key=lambda t: t[1]) + later
        card = None
        if line:
            _, _, job_id, pid = line[0]
            store, manifest, prompts = stores[job_id]
            section, prompt = prompts[pid]
            state = store["cards"][pid]

            def shown(book: str, chapter: str, label: str, go: str | None, **own: Any) -> dict[str, Any]:
                """The card as the page gets it. ``own`` is what differs between a book's card and a note's."""
                return {"job": job_id, "id": pid, "book": book, "chapter": chapter, "section": label,
                        **qforms.as_question(prompt), **own,
                        # several answers are defensible: the evidence a well-supported one accounts for
                        "accounts_for": list(prompt.get("accounts_for") or []),
                        "state": state["state"], "again": state["again"], "interval": round(state["interval"], 1),
                        "first": srs.kind_of(state, today) == "first", "times": len(state["reviews"]), "go": go,
                        "preview": srs.preview(state, today, settings)}
            if job_id == assist.DECK:  # a card from a note: the note is its passage, and where it leads
                note = section
                card = shown("Your notes", "", "A note without a title" if note.untitled else note.title, None, editable=False, note=note.id,
                             passage=[[{"t": " ".join(part.split()), "h": False}] for part in note.body.split("\n\n") if part.strip()][:12])
            else:
                chapter = next((c["title"] for c in manifest["chapters"] if c["index"] == section["chapter"]), "")
                card = shown(manifest["title"], chapter, section["label"], f"{section['chapter']}:{section['start']}",
                             editable=not prompt.get("hq") and self.editable(job_id), passage=self.passage(job_id, pid))
        return {"counts": total, "left": len(line), "card": card, "settings": settings, "today": srs.day(today)}

    def practice_answer(self, job_id: str, pid: str, remembered: bool, text: str) -> None:
        if not isinstance(job_id, str) or not isinstance(pid, str) or not isinstance(remembered, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "an answer needs job, id and remembered")
        folder = self.data / assist.FOLDER if job_id == assist.DECK else self.folder(self.get(job_id)["id"])
        with self.lock:
            store = srs.load(folder) if job_id == assist.DECK else self.cards(job_id)
            card = store["cards"].get(pid)
            if card is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "no such card")
            today = self.today()  # the stamp carries the same local day the schedule is counted in
            at = f"{srs.day(today)}T{_dt.datetime.now().strftime('%H:%M:%S')}"
            srs.answer(card, remembered, today, self.srs_settings(), at, "review", text)
            write_json(folder / srs.FILE, store)

    def passage(self, job_id: str, pid: str) -> list[list[dict[str, Any]]]:
        """The paragraphs that support a question's answer, when the book was processed here. Each paragraph is
        a list of runs ``{"t": text, "h": highlighted?}``, so that what the reader highlighted shows here too."""
        store = notes.load(self.folder(job_id))
        mine = next((q for q in hq.questions(store) if q["id"] == pid), None)
        if mine is not None:  # made from a highlight: the paragraphs the highlight lies in
            refs = [{"ref": b.ref, "text": " ".join(b.words)} for _, b in hq.passage_refs(self.book(job_id), mine)]
        else:
            doc = self._questions_doc(job_id)
            if doc is None:
                return []
            refs = next((q["passage"] for q in doc.get("questions", []) if q["id"] == pid), [])
        book = self.book(job_id)
        words: dict[str, list[str]] | None = None
        out = []
        for entry in refs:
            place = book.blocks.get(entry["ref"])  # None for a picture, a table or a listing
            marks = list(notes.marked(store, place[0], place[1], place[2])) if place else []
            if not marks:  # nothing in it is highlighted: the text as the files hold it, and the book is not read
                out.append([{"t": entry["text"], "h": False}])
                continue
            if words is None:
                words = {b.ref: b.words for sd in book.book.spine for b in sd.blocks}
            mine_words = words.get(entry["ref"]) or []
            lit = [False] * len(mine_words)
            for start, end in marks:
                for i in range(max(0, start - place[1]), min(len(lit), end - place[1])):
                    lit[i] = True
            runs: list[dict[str, Any]] = []
            for word, h in zip(mine_words, lit):
                if runs and runs[-1]["h"] == h:
                    runs[-1]["t"] += " " + word
                else:
                    runs.append({"t": word, "h": h})
            out.append(runs)
        return out

    def _questions_doc(self, job_id: str) -> Any:
        """The book's ``questions.json`` as it is on disk; None when it is not there or cannot be read."""
        path = self.folder(job_id) / f"{stem_of(self.jobs[job_id]['file'])}.questions.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def sources(self, job_id: str) -> dict[str, dict[str, int]]:
        """For each question, where the passage behind its answer begins: ``{"chapter", "word"}``."""
        doc = self._questions_doc(job_id)
        if doc is None:
            return {}
        where = self.book(job_id).blocks
        out = {}
        for q in doc.get("questions", []):
            for entry in q.get("passage") or []:
                if entry.get("ref") in where:
                    out[q["id"]] = {"chapter": where[entry["ref"]][0], "word": where[entry["ref"]][1]}
                    break
        return out
