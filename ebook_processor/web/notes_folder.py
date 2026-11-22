"""The notes folder's part of the local web app: the folder and each book's file in it, the notes as the pages show
them, processing what was marked, and the model's help with notes.

This is one of the parts the class ``Jobs`` of ``server.py`` is put together from. Its methods use the fields that
``Jobs.__init__`` makes, and the methods of the other parts, through ``self``. It does not import ``server.py``.
"""

from __future__ import annotations

import datetime as _dt
import re
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .. import assist, bookfile, notes, process, reading, srs
from .. import highlight_questions as hq
from .. import steer as steering
from ..llm import BackendError
from ..outputs import stem_of
from ..settings import Settings
from ..store import write_json
from ..vault import SOURCES as SOURCE_KINDS
from ..vault import Conflict, Missing, Vault, VaultError, clean_title, compose
from .common import CITED, NOTES_FOLDER, ApiError, _text, home_path

# The locks, as in every part of ``Jobs``: a book's lock (``self.booked(id)``) is taken first, the library's lock
# (``self.lock``) second and for moments only, never across a call to a model or while a book's files are built. The
# whole rule is in ``Jobs.__init__`` (server.py).


class NotesFolder:
    """What ``Jobs`` does with the notes folder (see vault.py, bookfile.py, process.py and assist.py)."""

    # ---- the folder, and each book's file in it (see vault.py and bookfile.py)

    @staticmethod
    def _folder_setting(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 400:
            raise ApiError(HTTPStatus.BAD_REQUEST, "The notes folder is a path on this computer.")
        path = home_path(value.strip())
        if not path.is_absolute():
            raise ApiError(HTTPStatus.BAD_REQUEST, "Give the notes folder as a full path, such as "
                           + str(Path.home() / NOTES_FOLDER))
        if path.exists() and not path.is_dir():
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{path} is a file, not a folder.")
        return str(path)

    def notes_folder(self) -> Path:
        stored = self.ui_settings().get("notes_folder")
        return Path(stored).expanduser() if isinstance(stored, str) and stored.strip() else self.notes_default

    def vault(self, make: bool = False) -> Vault:
        """The notes folder in use. ``make`` creates it when it is not there, and gives every book its file."""
        path = self.notes_folder()
        with self.lock:
            if self._vault is None or self._vault.root != path:
                self._vault, self.filed = Vault(path), {}
            vault = self._vault
        if make:
            try:
                vault.ensure()
            except VaultError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            self.file_books()
        return vault

    def folder_state(self) -> dict[str, Any]:
        path = self.notes_folder()
        return {"folder": str(path), "default": str(self.notes_default), "exists": path.is_dir(),
                "chosen": path != self.notes_default}

    def file_book(self, job_id: str, quiet: bool = False) -> None:
        """Write the book's file in the notes folder, when there is a notes folder. ``quiet``: a failure here
        must not undo what the reader just did in the book."""
        try:
            vault = self.vault()
            if not vault.exists or not self._readable(job_id):
                return
            book = self.book(job_id)
            made_from = self._made_from(job_id)
            store = notes.load(self.folder(job_id))
            job = self.jobs[job_id]
            kind = self.type_of(job)
            vault.put_book(bookfile.book_id(job_id), job.get("title") or stem_of(job["file"]),
                           bookfile.text(job_id, book, store, kind, job.get("origin")), kind)
            self.filed[job_id] = made_from
        except (ApiError, VaultError, OSError) as exc:
            if not quiet:
                raise exc if isinstance(exc, ApiError) else ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def _made_from(self, job_id: str) -> tuple[int, int]:
        """What a book's file in the notes folder is made from: when the prepared book was written, and when what
        is marked in it last changed."""
        marks = self.folder(job_id) / notes.FILE
        return (self.prepared(job_id).stat().st_mtime_ns, marks.stat().st_mtime_ns if marks.is_file() else 0)

    def file_books(self) -> None:
        """Every book of the library has its file, made again when the book or what is marked in it changed."""
        for job_id in [j for j in list(self.jobs) if self._readable(j)]:
            try:
                made_from = self._made_from(job_id)
            except OSError:
                continue
            note = self.vault().book(bookfile.book_id(job_id))
            if note is not None and self.filed.get(job_id) is None and note.stamp[0] >= max(made_from):
                self.filed[job_id] = made_from  # written after the book and its marks last changed: it is current
            if self.filed.get(job_id) != made_from or note is None:
                self.file_book(job_id, quiet=True)

    def _book_left(self, job_id: str) -> None:
        """The book is being removed from the library: its file stays, and says so."""
        try:
            vault = self.vault()
            note = vault.book(bookfile.book_id(job_id)) if vault.exists else None
            if note is not None:
                # in the folder it is in: an article's file is not moved to the books
                vault.put_book(note.id, note.title, bookfile.mark_removed(compose(note.props, note.body)), note.kind)
        except (VaultError, OSError):
            pass

    # ---- a note and the passages it cites, as a page is shown them

    def _cited(self, to: dict[str, Any]) -> dict[str, Any]:
        """What a link into a book's file leads to, with the way to open the reader there."""
        if to.get("kind") not in SOURCE_KINDS or bookfile.job_of(to.get("id") or "") is None:
            return to
        return self._openable(dict(to), to["id"], to.get("block"))

    def _openable(self, row: dict[str, Any], book_id: str, block: str | None, in_library: bool = True) -> dict[str, Any]:
        """Say on a row where its passage of a book's file can be opened. ``in_library``: whether the book is in
        the library and can be read (left out when asked). Then ``job``, the book's entry, and ``go``, the place of
        the block in the reader. ``book_id`` is the id of the book's file, ``block`` the id of the entry in it."""
        job_id = bookfile.job_of(book_id)
        readable = self._readable(job_id or "")
        if in_library:
            row["in_library"] = readable
        if readable:
            row["job"] = job_id
            if block:
                row["go"] = self._place(job_id, block)
        return row

    def _place(self, job_id: str, block: str) -> dict[str, int] | None:
        try:
            sections = self.book(job_id).manifest()["sections"]
        except ApiError:
            sections = None
        return bookfile.place(notes.load(self.folder(job_id)), block, sections)

    def _note(self, detail: dict[str, Any]) -> dict[str, Any]:
        """A note as its page shows it: with the passages it cites, and where each can be read."""
        for link in detail["links"]:
            link["to"] = self._cited(link["to"])
        seen: set[tuple[str, str]] = set()
        detail["sources"] = []
        for link in detail["links"]:
            to = link["to"]
            if to.get("kind") in SOURCE_KINDS and to.get("block") and (to["id"], to["block"]) not in seen:
                seen.add((to["id"], to["block"]))
                detail["sources"].append({k: to.get(k) for k in CITED})
        held = {p["key"]: p.get("value") for p in detail["props"]}
        where = next((k["to"] for k in detail["links"] if not k["anchor"] and k["to"].get("kind") in SOURCE_KINDS
                      and held.get("book") == f"[[{k['target']}]]"), None)
        if where is not None:  # a quick note written in the reader: the book, and the place it was written at
            at = re.fullmatch(r"(\d+):(\d+)", str(held.get("place") or ""))
            detail["written"] = {"title": where["title"], "job": where.get("job"), "in_library": where.get("in_library"),
                                 "go": {"chapter": int(at.group(1)), "word": int(at.group(2))} if at and where.get("job") else None}
        vault = self.vault()
        with vault.lock:  # what a project holds, and which project a note belongs to or was gathered for
            mine = vault.by_id.get(detail["id"])
            if mine is not None and detail["kind"] == "project":
                detail["project"] = {"status": "finished" if held.get("status") == "finished" else "active",
                                     "notes": [vault.brief(n) for n in vault.project_notes(mine)]}
            elif mine is not None and detail["kind"] not in SOURCE_KINDS:
                named = next((k["to"] for k in detail["links"] if held.get("project") == f"[[{k['target']}]]" and k["to"].get("kind") == "project"), None)
                if detail["kind"] == "project-note" and named is not None:
                    detail["of"] = {"id": named["id"], "title": named["title"]}
                detail["gathered_for"] = [{"id": o.id, "title": o.title} for o in {o.id: o for o, _ in vault.backlinks(mine)}.values()
                                          if o.kind == "project"]
        if detail["kind"] in SOURCE_KINDS:
            detail["in_library"] = self._readable(bookfile.job_of(detail["id"]) or "")
            detail["job"] = bookfile.job_of(detail["id"]) if detail["in_library"] else None
        return detail

    def _vaulted(self, call: Any) -> Any:
        try:
            return call()
        except Missing as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
        except Conflict:
            raise
        except VaultError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    # ---- the notes

    def vault_home(self) -> dict[str, Any]:
        """The front page of Notes. Asking for it is what makes the folder, the first time."""
        vault = self.vault(make=True)
        rows = vault.listing()
        books = [{**r, "job": bookfile.job_of(r["id"]), "in_library": self._readable(bookfile.job_of(r["id"]) or ""),
                  "entries": len(vault.get(r["id"]).blocks)} for r in rows if r["kind"] in SOURCE_KINDS]
        aside = {r["id"] for r in rows if r["kind"] == "quick" and vault.prop_of(r["id"], "aside") is True}
        return {"folder": str(vault.root), "inbox": [r for r in rows if r["inbox"] and r["id"] not in aside],
                "waiting": self.process_view()["counts"]["waiting"], "revisit": vault.revisit(self.today())["today"],
                "recent": [r for r in rows if not r["inbox"] and r["kind"] in ("idea", "quick")][:40],
                "indexes": [r for r in rows if r["kind"] == "index" and not r["inbox"]], "projects": vault.projects(),
                "notes": sum(1 for r in rows if r["kind"] not in SOURCE_KINDS), "tags": vault.tags(),
                "books": sorted(books, key=lambda b: b["title"].casefold()), "trash": len(vault.trash())}

    def vault_search(self, query: str, kind: str | None, tag: str | None, book: str | None) -> dict[str, Any]:
        found = self.vault().search(query[:200], kind or None, tag or None, book or None)
        for p in found["passages"]:
            self._openable(p, p["book_id"], p["block"])
        return found

    def vault_note(self, note_id: str) -> dict[str, Any]:
        vault = self.vault()

        def read() -> dict[str, Any]:
            with vault.lock:
                return vault.detail(vault.get(note_id))
        return self._note(self._vaulted(read))

    def vault_peek(self, target: str, anchor: str, inside: str | None, full: bool = False) -> dict[str, Any]:
        vault = self.vault()
        vault.refresh()
        with vault.lock:
            found = vault.peek(target, anchor, vault.by_id.get(inside or ""), full)
        return self._cited(found)  # asks about the book, outside the folder's lock

    def vault_create(self, r: dict[str, Any]) -> dict[str, Any]:
        """A new note. ``source`` names a highlight or note of a book that it starts from; ``where`` the place in
        a book at which it was written."""
        vault = self.vault(make=True)
        body = r.get("body") if isinstance(r.get("body"), str) else ""
        props: dict[str, Any] = {}
        if isinstance(r.get("tags"), (list, str)):
            props["tags"] = r["tags"]
        source, where = r.get("source"), r.get("where")
        if isinstance(source, dict):
            job_id, block = str(source.get("job") or ""), str(source.get("block") or "")
            self.get(job_id)
            self.file_book(job_id)
            book = vault.book(bookfile.book_id(job_id))
            if book is None or block not in book.blocks:
                raise ApiError(HTTPStatus.BAD_REQUEST, "that highlight or note is not in the book's file")
            link = f"[[{book.title}#^{block}]]"
            props["sources"] = [link]
            body = (body.rstrip() + "\n\n" if body.strip() else "\n\n") + f"Source: {link}\n"
        elif isinstance(r.get("sources"), list):  # links the page was given by ``cite``
            if not all(isinstance(x, str) and re.fullmatch(r"\[\[[^\[\]\n]{1,300}\]\]", x) for x in r["sources"]) or len(r["sources"]) > 20:
                raise ApiError(HTTPStatus.BAD_REQUEST, "sources are links to passages")
            props["sources"] = r["sources"]
        if isinstance(where, dict):
            job_id = str(where.get("job") or "")
            self.get(job_id)
            self.file_book(job_id)
            book = vault.book(bookfile.book_id(job_id))
            if book is not None:
                props["book"] = f"[[{book.title}]]"
                if isinstance(where.get("chapter"), int) and isinstance(where.get("word"), int):
                    props["place"] = f"{where['chapter']}:{where['word']}"
        kind = r.get("kind") if isinstance(r.get("kind"), str) else "idea"
        if r.get("by") == "model":  # an answer kept from "Ask": the file says that its words are not the reader's
            props["by"] = "model"
        if kind == "project":  # a project has a name, and its file the two sections the app works on
            if not isinstance(r.get("title"), str) or not r["title"].strip():
                raise ApiError(HTTPStatus.BAD_REQUEST, "A project needs a name.")
            props["status"] = "active"
            body = body if body.strip() else "\n\n## Gathered\n\n## Outline\n"
        if kind == "project-note":
            with vault.lock:
                of = vault.by_id.get(str(r.get("project") or ""))
                if of is None or of.kind != "project":
                    raise ApiError(HTTPStatus.BAD_REQUEST, "A project note belongs to a project.")
                props["project"] = f"[[{of.title}]]"
        return self._note(self._vaulted(lambda: vault.create(r.get("title") or "", body, kind, props)))

    def vault_gather(self, project_id: str, r: dict[str, Any]) -> dict[str, Any]:
        vault = self.vault()
        got = self._vaulted(lambda: vault.gather(project_id, str(r.get("note") or "")))
        return {**self._note(got), "added": got["added"]}

    def vault_cite(self, r: dict[str, Any]) -> dict[str, Any]:
        """The link that cites a highlight or a note of a book, with the words it leads to. Makes the notes
        folder and the book's file when they are not there yet."""
        job_id, block = str(r.get("job") or ""), str(r.get("block") or "")
        self.get(job_id)
        vault = self.vault(make=True)
        self.file_book(job_id)
        book = vault.book(bookfile.book_id(job_id))
        if book is None or block not in book.blocks:
            raise ApiError(HTTPStatus.BAD_REQUEST, "that highlight or note is not in the book's file")
        to = self._cited(vault.peek(book.title, "^" + block))
        return {"link": f"[[{book.title}#^{block}]]", **{k: to.get(k) for k in CITED}}

    def vault_save(self, note_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """Save a note's text and properties, then give it the title that was sent, if it is a new one."""
        vault = self.vault()

        def work() -> dict[str, Any]:
            with vault.lock:
                note = vault.get(note_id)
                if note.source and bookfile.job_of(note.id):
                    raise VaultError("A book's file is kept by the app. Change the highlight or the note in the book.")
                title = r.get("title")
                if isinstance(title, str) and title.strip():
                    title = clean_title(title)  # refused before anything is written
                    taken = vault.taken(title, but=note)
                    if taken is not None:
                        raise VaultError(f"There is already a note called {taken.title}.")
                out = vault.save(note_id, r.get("body"), r.get("props"), r.get("token"), force=r.get("force") is True)
                if isinstance(title, str) and title.strip() and (title != out["title"] or out["inbox"]):
                    out = vault.rename(out["id"], title)
                return out
        return self._note(self._vaulted(work))

    def vault_delete(self, note_id: str) -> dict[str, Any]:
        vault = self.vault()

        def work() -> dict[str, Any]:
            note = vault.get(note_id)
            if note.source and self._readable(bookfile.job_of(note.id) or ""):
                raise VaultError("This book is in your library, so its file is kept. Remove the book first.")
            return vault.delete(note_id)
        return self._vaulted(work)

    def vault_restore(self, name: Any) -> dict[str, Any]:
        """Put a note back from the trash, and give it as its page shows it."""
        vault = self.vault()
        return self._note(self._vaulted(lambda: vault.restore(name)))

    def vault_neighbourhood(self, note_id: str) -> dict[str, Any]:
        """A note and what is joined to it, for the graph."""
        vault = self.vault()
        return self._vaulted(lambda: vault.neighbourhood(note_id))

    def vault_export(self, note_id: str) -> tuple[str, str]:
        """A project's outline as one Markdown text, and the project's title, which names the download."""
        vault = self.vault()
        text = self._vaulted(lambda: vault.export(note_id))
        return text, self._vaulted(lambda: vault.get(note_id)).title

    def vault_token(self, note_id: str) -> str:
        """The mark of a note's file as it is on disk now. A page sends it back with a save (see ``vault_save``)."""
        vault = self.vault()
        return self._vaulted(lambda: vault.get(note_id)).token

    def vault_conflict(self, conflict: Conflict) -> dict[str, Any]:
        """What a page is told when the note it saves has changed on disk: why, and the note as it now is."""
        return {"error": str(conflict), "current": self._note(conflict.current)}

    def vault_related(self, r: dict[str, Any]) -> dict[str, Any]:
        text = r.get("text") if isinstance(r.get("text"), str) else ""
        tags = [t for t in r.get("tags") or [] if isinstance(t, str)][:50] if isinstance(r.get("tags"), list) else []
        but = {b for b in r.get("but") or [] if isinstance(b, str)} if isinstance(r.get("but"), list) else set()
        found = self.vault().related(text[:20000], tags, but)
        for p in found["passages"]:
            self._openable(p, p["book_id"], p["block"])
        return found

    def timeline(self, before: str | None = None, most: int = 30) -> dict[str, Any]:
        """What was written and marked, day by day, the newest day first: notes on the day they were made (quick
        notes apart), notes on the day of their last change when that is another day, and for each book what
        was highlighted and noted in it. ``before`` gives the days earlier than a day. Days are those of this
        computer's clock."""
        if before is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", before):
            raise ApiError(HTTPStatus.BAD_REQUEST, "'before' is a day, as 2026-11-19")

        def day(stamp: str) -> str | None:
            try:
                at = _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
            except (TypeError, ValueError):
                return None
            return at.astimezone().date().isoformat()

        days: dict[str, dict[str, Any]] = {}
        on = lambda d: days.setdefault(d, {"day": d, "written": [], "edited": [], "quick": [], "books": {}})  # noqa: E731
        vault = self.vault()
        vault.refresh()
        with vault.lock:
            for note in vault.notes.values():
                if note.source:
                    continue
                made, changed = day(note.created) or day(note.modified), day(note.modified)
                if made:
                    on(made)["quick" if note.kind == "quick" else "written"].append(vault.brief(note))
                if changed and changed != made:
                    on(changed)["edited"].append(vault.brief(note))
        clip = lambda text: text if len(text) <= 200 else text[:200].rsplit(" ", 1)[0] + " …"  # noqa: E731
        for job_id in [j for j in list(self.jobs) if self._readable(j)]:
            marked = notes.load(self.folder(job_id))
            rows = [(h.get("created"), "highlights", h) for h in marked["highlights"]] + [(n.get("created"), "notes", n) for n in marked["notes"]]
            for stamp, what, row in rows:
                d = day(stamp or "")
                if d:
                    book = on(d)["books"].setdefault(job_id, {"job": job_id, "title": self.jobs[job_id].get("title") or "", "highlights": [], "notes": []})
                    book[what].append({"id": row["id"], "text": clip(row["text"])})
        ordered = [d for d in sorted(days.values(), key=lambda d: d["day"], reverse=True) if before is None or d["day"] < before]
        for d in ordered[:most]:
            # A day has no natural limit: in a folder that was copied or restored, every note carries one date. A
            # day shows so many of each kind, with the number there are; of a book's marks, the three the page shows.
            d["books"] = sorted(d["books"].values(), key=lambda b: b["title"].casefold())
            d["counts"] = {}
            for name in ("written", "edited", "quick"):
                d[name].sort(key=lambda n: n["modified"], reverse=True)
                d["counts"][name] = len(d[name])
                d[name] = d[name][:self.DAY_SHOWN]
            for b in d["books"]:
                b["counts"] = {"highlights": len(b["highlights"]), "notes": len(b["notes"])}
                b["highlights"], b["notes"] = b["highlights"][:3], b["notes"][:3]
        return {"days": ordered[:most], "more": len(ordered) > most}

    DAY_SHOWN = 200

    # ---- processing: making something of what was marked (see process.py)

    def process_view(self, only: str | None = None) -> dict[str, Any]:
        """Everything that can be processed, with what became of each item: quick notes, then each book's
        highlights and notes in reading order. ``only`` narrows to one book."""
        if only is not None:
            self.get(only)
        vault = self.vault(make=True)
        books = []
        for job_id in [j for j in list(self.jobs) if self._readable(j) and only in (None, j)]:
            folder = self.folder(job_id)
            marked = notes.load(folder)
            # a book with nothing marked has nothing to process, and need not be opened to know it
            rows = process.items(self.book(job_id), marked) if marked["highlights"] or marked["notes"] else []
            with self.lock:
                state = process.load(folder)
                if process.tidy(state, {r["id"] for r in rows}):
                    write_json(folder / process.FILE, state)
            with vault.lock:
                note = vault.book(bookfile.book_id(job_id))
                cited = vault.citing(note) if note is not None else {}
                for r in rows:
                    r["notes"] = [{"id": n.id, "title": n.title, "untitled": n.untitled} for n in cited.get(r["id"], [])]
                    r["state"] = "done" if r["notes"] else "aside" if r["id"] in state["aside"] else "waiting"
                    r["job"] = job_id
            read = reading.summary(reading.load_state(folder))
            books.append({"job": job_id, "id": bookfile.book_id(job_id), "title": note.title if note is not None else self.jobs[job_id].get("title"),
                          "last_read": (read or {}).get("last_read") or "", "items": rows})
        books.sort(key=lambda b: b["last_read"], reverse=True)
        quick = []
        if only is None:
            with vault.lock:
                for brief in vault.listing(kind="quick"):
                    note = vault.get(brief["id"])
                    held = {p.key: p.value for p in note.props if p.key}
                    quick.append({"id": note.id, "kind": "quick", "text": "", "note": note.body.strip()[:1200], "created": note.created or note.modified,
                                  "title": "" if note.untitled else note.title, "state": "aside" if held.get("aside") is True else "waiting", "notes": []})
            quick.sort(key=lambda q: q["created"])
        every = quick + [r for b in books for r in b["items"]]
        return {"quick": quick, "books": books,
                "counts": {k: sum(1 for r in every if r["state"] == k) for k in ("waiting", "done", "aside")}}

    def process_waiting(self, job_id: str) -> int:
        """How many items of a book wait to be processed. Looking does not make the notes folder."""
        marked = notes.load(self.folder(job_id))
        if not marked["highlights"] and not marked["notes"]:
            return 0
        try:
            rows = process.items(self.book(job_id), marked)
        except ApiError:
            return 0
        aside = process.load(self.folder(job_id))["aside"]
        vault = self.vault()
        with vault.lock:
            note = vault.book(bookfile.book_id(job_id)) if vault.exists else None
            cited = vault.citing(note) if note is not None else {}
        return sum(1 for r in rows if r["id"] not in cited and r["id"] not in aside)

    def process_aside(self, r: dict[str, Any]) -> dict[str, Any]:
        """Set items aside, or bring them back: ``{job, ids, aside}`` for a book's items, ``{quick, aside}``
        for a quick note."""
        aside = r.get("aside") is not False
        if isinstance(r.get("quick"), str):
            vault = self.vault()
            self._vaulted(lambda: vault.set_props(r["quick"], {"aside": True if aside else None}))
            return {"ok": True}
        job_id, ids = str(r.get("job") or ""), r.get("ids")
        self.get(job_id)
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 5000:
            raise ApiError(HTTPStatus.BAD_REQUEST, "name the items of the book")
        known = {i["id"] for i in process.items(self.book(job_id), notes.load(self.folder(job_id)))}
        with self.lock:
            state = process.load(self.folder(job_id))
            process.set_aside(state, [i for i in ids if i in known], aside)
            write_json(self.folder(job_id) / process.FILE, state)
        return {"ok": True}

    def process_add(self, r: dict[str, Any]) -> dict[str, Any]:
        """Add an item to a note that exists: a book's item as a source, a quick note by its words (the quick
        note then goes to the trash, from which it can be put back). Gives the note as it now is."""
        vault = self.vault(make=True)
        target = str(r.get("note") or "")
        if isinstance(r.get("quick"), str):
            def merge() -> dict[str, Any]:
                with vault.lock:
                    quick = vault.get(r["quick"])
                    if quick.id == vault.get(target).id:
                        raise VaultError("A note cannot be added to itself.")
                    out = vault.add_to(target, words=quick.body)
                    vault.delete(quick.id)
                    return out
            return self._note(self._vaulted(merge))
        link = self.vault_cite(r)["link"]
        return self._note(self._vaulted(lambda: vault.add_to(target, source=link)))

    # ---- the model's help in the notes folder (see assist.py)

    def _helper(self, roles: list[str]) -> tuple[Settings, Any]:
        """The settings and a backend for a call the reader asked for: the models of the Add a book tab."""
        settings = self.question_settings()
        try:
            return settings, self._backend(settings, roles)
        except BackendError as exc:
            raise ApiError(HTTPStatus.BAD_GATEWAY, f"The model could not be asked: {exc}") from exc

    def _helped(self, call: Any) -> Any:
        try:
            return self._asked(lambda: self._vaulted(call))
        except (assist.AssistError, hq.QuestionError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def assist_sent(self, note_id: str) -> dict[str, Any]:
        """What "Find connections" would send for a note, and to which model, before anything is sent."""
        vault = self.vault()

        def count() -> dict[str, Any]:
            with vault.lock:
                return assist.connection_material(vault, vault.get(note_id))["sent"]
        settings = self.question_settings()
        return {"sent": self._vaulted(count), "model": settings.model or "the default model"}

    def assist_connections(self, note_id: str) -> dict[str, Any]:
        settings, backend = self._helper(["draft"])
        vault = self.vault()
        got = self._helped(lambda: assist.connections(backend, vault, note_id, settings))
        for c in got["connections"]:
            if c["kind"] == "passage":
                self._openable(c, c["id"], c["block"], in_library=False)
        return got

    def assist_answer(self, r: dict[str, Any]) -> dict[str, Any]:
        settings, backend = self._helper(["draft"])
        vault = self.vault()
        got = self._helped(lambda: assist.answer(backend, vault, r.get("question"), settings))
        for s in got["sources"] + got["closest"]:
            if s["kind"] == "passage":
                self._openable(s, s["id"], s["block"], in_library=False)
        if got["covered"]:  # the text of the quick note that "Keep" would make
            got["keep"] = assist.answer_markdown(str(r.get("question")), got, srs.day(self.today()))
        return got

    def notes_practice(self) -> dict[str, Any] | None:
        """The counts of the cards made from notes, for the library's Practice button; None when there are none."""
        store = srs.load(self.data / assist.FOLDER)
        if not store["cards"]:
            return None
        vault = self.vault()
        vault.refresh()
        with vault.lock:
            known = set(assist.practice_prompts(vault, assist.load(self.data)))
        return srs.counts(store, self.today(), known)

    def _note_questions(self, change: Any = None) -> dict[str, Any]:
        with self.lock:
            store = assist.load(self.data)
            if change is not None:
                change(store)
                write_json(self.data / assist.FOLDER / assist.FILE, store)
        return store

    def note_questions(self, note_id: str) -> dict[str, Any]:
        vault = self.vault()
        store = self._note_questions()
        cards = srs.load(self.data / assist.FOLDER)["cards"]

        def rows() -> list[dict[str, Any]]:
            with vault.lock:
                return assist.of_note(store, vault.get(note_id))
        return {"questions": [{**q, "card": {"state": cards[q["id"]]["state"], "times": len(cards[q["id"]]["reviews"])} if q["id"] in cards else None}
                              for q in self._vaulted(rows)], "forms": list(self.question_settings().forms)}

    def _card_for(self, qid: str) -> None:
        with self.lock:
            cards = srs.load(self.data / assist.FOLDER)
            if qid not in cards["cards"]:
                cards["cards"][qid] = srs.new_card()
                write_json(self.data / assist.FOLDER / srs.FILE, cards)

    def note_question_propose(self, note_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """Ask the model for a question about a note, and check it. One that fails its check is held, with why."""
        settings, backend = self._helper(["draft", "check"])
        vault = self.vault()
        instruction = _text(r.get("instruction"), steering.INSTRUCTION_MAX)

        def work() -> None:
            note = vault.get(note_id)
            asked = [q["question"] for q in self._note_questions()["questions"] if q["note"] == note.id]
            wording = assist.propose(backend, vault, note, asked, settings, instruction)
            model = wording.pop("model")
            verdict = assist.check(backend, vault, note, wording, settings)

            def keep(store: dict[str, Any]) -> None:
                q = assist.add(store, note, wording, "model", model, "proposed" if verdict["passed"] else "held")
                q.update(checked=verdict, problem=verdict["problem"])
            self._note_questions(keep)
        self._helped(work)
        return self.note_questions(note_id)

    def note_question_write(self, note_id: str, r: dict[str, Any]) -> dict[str, Any]:
        """A question the reader wrote themselves: accepted as it is, and a card from now on."""
        vault = self.vault()
        made: dict[str, Any] = {}

        def work() -> None:
            note = vault.get(note_id)
            wording = hq.formed(r.get("question"), r.get("answer"), r.get("form"))
            self._note_questions(lambda store: made.update(assist.add(store, note, wording, "you", None, "accepted")))
        self._helped(work)
        self._card_for(made["id"])
        return self.note_questions(note_id)

    def note_question_change(self, qid: str, r: dict[str, Any]) -> dict[str, Any]:
        """Accept a question, reword it, or have it checked again against the note as it now is."""
        vault = self.vault()
        found: dict[str, Any] = {}

        def work() -> None:
            q = assist.find(self._note_questions(), qid)
            note = vault.get(q["note"])
            found["note"] = note.id
            if r.get("check") is True:
                settings, backend = self._helper(["check"])
                verdict = assist.check(backend, vault, note, q, settings)

                def checked(store: dict[str, Any]) -> None:
                    mine = assist.find(store, qid)
                    mine.update(checked=verdict, problem=verdict["problem"], made_from=assist.mark_of(note))
                    if mine["status"] == "held" and verdict["passed"]:
                        mine["status"] = "proposed"
                self._note_questions(checked)
                return
            if any(k in r for k in ("question", "answer", "form")):
                self._note_questions(lambda store: assist.reword(store, note, qid, r.get("question"), r.get("answer"), r.get("form")))
            if r.get("accept") is True:
                self._note_questions(lambda store: assist.find(store, qid).update(status="accepted"))
                self._card_for(qid)
        self._helped(work)
        return self.note_questions(found["note"])

    def note_question_delete(self, qid: str) -> dict[str, Any]:
        found: dict[str, Any] = {}

        def drop(store: dict[str, Any]) -> None:
            found["note"] = assist.find(store, qid)["note"]
            store["questions"] = [q for q in store["questions"] if q["id"] != qid]
        self._helped(lambda: self._note_questions(drop))
        with self.lock:
            cards = srs.load(self.data / assist.FOLDER)
            if cards["cards"].pop(qid, None) is not None:
                write_json(self.data / assist.FOLDER / srs.FILE, cards)
        try:
            return self.note_questions(found["note"])
        except ApiError:
            return {"questions": [], "forms": list(self.question_settings().forms)}
