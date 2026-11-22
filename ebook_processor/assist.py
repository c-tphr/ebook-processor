"""The model's part in the notes folder. Everything here happens on the reader's request.

    connections   which other notes and passages are really connected to a note, and why
    answer        an answer to a question from the reader's own notes and highlights, with its sources
    questions     a question made from a note, checked against the note, for Practice

Each call has its instructions in ``instructions/``, a schema for the reply, and a check of the reply that is made
here and not by the model: a connection must name something the model was shown, a quoted sentence is kept only
when it is found in the text it is said to come from, and a citation in an answer must be one of the sources that
were sent. What is sent is chosen here too, and ``sent`` says what it is, so that the page can tell the reader.

The questions of notes, and their cards, are kept in the data folder, like those of books:

    <data>/notes-practice/questions.json   {"version": 1, "questions": [{"id": "nq1a2b3c4d", "note": "n7f3a2c91", ...}]}
    <data>/notes-practice/srs.json         the cards (see srs.py)

A question has ``question``, ``answer`` and the members of its form, ``status`` (proposed, held with ``problem``,
or accepted), ``by`` (model or you), ``checked`` and ``made_from``: a mark of the note's text when the question
was made, by which a later change of the note is seen.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path
from string import Template
from typing import Any

from . import forms as qforms
from . import highlight_questions as hq
from . import prompts
from .asking import Unusable, ask_with_retry, with_form
from .llm import Backend, Completion
from .store import load_json, now
from .prep.checkpoints import ANSWER_MAX_CHARS, PROMPT_MAX_CHARS
from .settings import Settings
from .vault import Note, Vault, key, mask_code

DECK = "notes"  # what the notes' cards are called where a book's cards carry the book's id
FOLDER, FILE, VERSION = "notes-practice", "questions.json", 1
LISTED, FULL, PASSAGES, TEXT_MAX = 400, 16, 10, 1800
MOST_CONNECTIONS, ANSWER_WORDS = 6, 180
INSTRUCTIONS = Path(__file__).resolve().parent / "instructions"

CONNECTIONS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["connections"], "properties": {
    "connections": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["to", "reason", "mine", "theirs"],
                                               "properties": {k: {"type": "string"} for k in ("to", "reason", "mine", "theirs")}}}}}
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["covered", "answer", "missing"],
                 "properties": {"covered": {"type": "boolean"}, "answer": {"type": "string"}, "missing": {"type": "string"}}}
CHECKS = ("supported_by_note", "answerable_from_note", "does_not_give_away_answer", "stands_alone")
ONE_RIGHT = "one_right_answer"


class AssistError(ValueError):
    """A request to the model's help cannot be carried out; the message says why."""


def _instructions(name: str, **values: Any) -> str:
    return Template((INSTRUCTIONS / name).read_text(encoding="utf-8")).substitute(**values)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _text(note: Note, limit: int = TEXT_MAX) -> str:
    body = note.body.strip()
    return body if len(body) <= limit else body[:limit].rsplit(" ", 1)[0] + " [...]"


def _name(note: Note) -> str:
    return "(no title yet)" if note.untitled else note.title


def _quoted(said: Any, *texts: str) -> str:
    """A sentence the model says it copied, kept only when it is in one of the texts (spacing apart)."""
    words = _flat(str(said or "")).strip("\"'“”‘’ ")
    return words if len(words) >= 8 and any(key(words) in key(_flat(t)) for t in texts) else ""


# --------------------------------------------------------------------------
# Connections


def connection_material(vault: Vault, note: Note) -> dict[str, Any]:
    """What is sent for "Find connections": every other note by title and first line, the likeliest in full, and
    the likeliest passages. Each has a reference (N1, P1) by which the model names it."""
    linked = {vault.resolve(k.target).id for k in note.links if k.target and vault.resolve(k.target) is not None}
    linked |= {o.id for o, _ in vault.backlinks(note)}
    cited = {f"{vault.resolve(k.target).id}#{k.anchor[1:]}" for k in note.links
             if k.target and k.anchor.startswith("^") and vault.resolve(k.target) is not None}
    related = vault.related(note.title + "\n" + note.body, note.tags, {note.id} | cited, limit=max(FULL, PASSAGES))
    likely = [vault.by_id[r["id"]] for r in related["notes"] if r["id"] in vault.by_id][:FULL]
    others = sorted((n for n in vault.notes.values() if not n.source and n is not note and n.body.strip()), key=lambda n: n.modified, reverse=True)
    listed = (likely + [n for n in others if n not in likely])[:LISTED]
    refs = {f"N{i}": n for i, n in enumerate(listed, 1)}
    passages = {f"P{i}": p for i, p in enumerate(related["passages"][:PASSAGES], 1)}
    return {"note": note, "refs": refs, "full": {r for r, n in refs.items() if n in likely}, "passages": passages,
            "linked": [r for r, n in refs.items() if n.id in linked],
            "sent": {"listed": len(refs), "full": len(likely), "passages": len(passages), "total": len(others)}}


def connections_message(material: dict[str, Any]) -> str:
    note, refs = material["note"], material["refs"]
    parts = ["THE NOTE", f"Title: {_name(note)}", "", _text(note, 6000), "", "THE READER'S OTHER NOTES (reference, title, first line)"]
    parts += [f"{r}  {_name(n)}  |  {_flat(n.excerpt)[:140]}" for r, n in refs.items()] or ["(none)"]
    if material["linked"]:
        parts += ["", "ALREADY LINKED", ", ".join(material["linked"])]
    for r in sorted(material["full"], key=lambda r: int(r[1:])):
        parts += ["", f"NOTE {r} IN FULL: {_name(refs[r])}", _text(refs[r])]
    for r, p in material["passages"].items():
        parts += ["", f"PASSAGE {r} (highlighted in {p['book']})", p["text"]]
    parts += ["", "Which of these are really connected to THE NOTE? Reply with the JSON object only."]
    return "\n".join(parts)


def connections(backend: Backend, vault: Vault, note_id: str, settings: Settings) -> dict[str, Any]:
    """The connections a model finds for a note, each checked against what it was shown:
    ``{"connections": [{"kind", "id", "title", "link", "reason", "mine", "theirs"}], "sent", "model"}``."""
    with vault.lock:
        note = vault.get(note_id)
        if note.source:
            raise AssistError("Connections are found for a note of your own.")
        if not note.body.strip():
            raise AssistError("Write the note first: there is nothing yet to connect.")
        material = connection_material(vault, note)
        message = connections_message(material)
    if not material["refs"] and not material["passages"]:
        return {"connections": [], "sent": material["sent"], "model": None}
    done = backend.complete(role="draft", system=_instructions("connections.md", most=MOST_CONNECTIONS), prompt=message,
                            schema=CONNECTIONS_SCHEMA, model=settings.model)
    found, seen = [], set(material["linked"])
    for c in (done.data.get("connections") if isinstance(done.data, dict) else None) or []:
        ref = str(c.get("to") or "").strip().upper() if isinstance(c, dict) else ""
        reason = _flat(str(c.get("reason") or "")) if isinstance(c, dict) else ""
        if ref in seen or not reason or len(found) >= MOST_CONNECTIONS:
            continue
        seen.add(ref)
        mine = _quoted(c.get("mine"), note.body, note.title)
        if ref in material["refs"]:
            other = material["refs"][ref]
            found.append({"kind": "note", "id": other.id, "title": other.title, "untitled": other.untitled, "reason": reason[:600], "mine": mine,
                          "link": vault.link_to(other),
                          "theirs": _quoted(c.get("theirs"), other.body, other.title)})
        elif ref in material["passages"]:
            p = material["passages"][ref]
            found.append({"kind": "passage", "id": p["book_id"], "block": p["block"], "title": p["book"], "reason": reason[:600], "mine": mine,
                          "link": f"{p['book']}#^{p['block']}", "theirs": _quoted(c.get("theirs"), p["text"]) or p["text"]})
    return {"connections": found, "sent": material["sent"], "model": done.model}


# --------------------------------------------------------------------------
# Answers from the reader's own notes


def answer_material(vault: Vault, question: str) -> dict[str, Any]:
    """The notes and passages that share most with a question: what an answer may be made from."""
    related = vault.related(question, limit=max(FULL, PASSAGES))
    notes = {f"N{i}": vault.by_id[r["id"]] for i, r in enumerate([r for r in related["notes"] if r["id"] in vault.by_id][:FULL], 1)}
    passages = {f"P{i}": p for i, p in enumerate(related["passages"][:PASSAGES], 1)}
    return {"notes": notes, "passages": passages, "sent": {"notes": len(notes), "passages": len(passages)}}


def _source(vault: Vault, ref: str, material: dict[str, Any]) -> dict[str, Any]:
    if ref in material["notes"]:
        n = material["notes"][ref]
        return {"ref": ref, "kind": "note", "id": n.id, "title": n.title, "untitled": n.untitled, "text": n.excerpt,
                "link": vault.link_to(n)}
    p = material["passages"][ref]
    return {"ref": ref, "kind": "passage", "id": p["book_id"], "block": p["block"], "title": p["book"], "text": p["text"],
            "link": f"{p['book']}#^{p['block']}"}


def answer(backend: Backend, vault: Vault, question: Any, settings: Settings) -> dict[str, Any]:
    """An answer from the reader's notes and highlights: ``{"covered", "answer", "missing", "sources", "closest",
    "sent", "model"}``. ``answer`` names its sources as ``[N1]``; ``sources`` are those it names. When the folder
    holds nothing related, or the model's answer names no source, the question counts as not covered."""
    asked = _flat(str(question or "")) if isinstance(question, str) else ""
    if len(asked) < 3:
        raise AssistError("Ask a question.")
    if len(asked) > 600:
        raise AssistError("A question can have 600 characters.")
    with vault.lock:
        vault.refresh()
        material = answer_material(vault, asked)
        refs = [*material["notes"], *material["passages"]]
        closest = [_source(vault, r, material) for r in refs[:6]]
        parts = ["QUESTION", asked, "", "SOURCES"]
        for r, n in material["notes"].items():
            parts += ["", f"{r}: a note of the reader's, \"{_name(n)}\"", _text(n)]
        for r, p in material["passages"].items():
            parts += ["", f"{r}: a passage highlighted in {p['book']}", p["text"]]
        parts += ["", "Answer the question from these sources only. Reply with the JSON object only."]
    if not refs:
        return {"covered": False, "answer": "", "missing": "Nothing in your notes or highlights shares its words with this question.",
                "sources": [], "closest": [], "sent": material["sent"], "model": None}
    done = backend.complete(role="draft", system=_instructions("answer.md", words=ANSWER_WORDS), prompt="\n".join(parts),
                            schema=ANSWER_SCHEMA, model=settings.model)
    data = done.data if isinstance(done.data, dict) else {}
    text = str(data.get("answer") or "").strip()
    named: list[str] = []

    def keep(m: re.Match[str]) -> str:  # a source that was not sent is not a source
        ref = m.group(1).upper()
        if ref not in refs:
            return ""
        if ref not in named:
            named.append(ref)
        return f"[{ref}]"

    text = re.sub(r"[ \t]+([.,;])", r"\1", re.sub(r"\[([NnPp]\d+)\]", keep, text))
    covered = data.get("covered") is True and bool(text) and bool(named)
    missing = _flat(str(data.get("missing") or ""))[:600]
    if data.get("covered") is True and text and not named:
        missing = "The model gave an answer without naming a source for it, so it is not shown. " + missing
    with vault.lock:
        return {"covered": covered, "answer": text if covered else "", "missing": missing,
                "sources": [_source(vault, r, material) for r in named] if covered else [], "closest": [] if covered else closest,
                "sent": material["sent"], "model": done.model}


def answer_markdown(question: str, got: dict[str, Any], day: str) -> str:
    """An answer as the text of a quick note: its citations as links, and a line that says who wrote it."""
    links = {s["ref"]: f"[[{s['link']}]]" for s in got["sources"]}
    body = re.sub(r"\[([NP]\d+)\]", lambda m: links.get(m.group(1), ""), got["answer"])
    return f"**Question:** {_flat(question)}\n\n{body}\n\n*Answered by a model from my notes and highlights on {day}. Not my own words.*\n"


# --------------------------------------------------------------------------
# Questions made from a note


def load(data: Path) -> dict[str, Any]:
    return load_json(data / FOLDER / FILE, VERSION, lambda: {"version": VERSION, "questions": []},
                     lambda d: isinstance(d.get("questions"), list))


def mark_of(note: Note) -> str:
    """A mark of what a note says, by which a later change is seen. Spacing does not count."""
    return hashlib.sha1(_flat(note.body).encode("utf-8")).hexdigest()[:16]


def find(store: dict[str, Any], qid: str) -> dict[str, Any]:
    q = next((q for q in store["questions"] if q["id"] == qid), None)
    if q is None:
        raise AssistError("no such question")
    return q


def of_note(store: dict[str, Any], note: Note) -> list[dict[str, Any]]:
    """A note's questions as its page shows them: ``stale`` when the note changed since the question was made."""
    return [{**q, "stale": q.get("made_from") != mark_of(note)} for q in store["questions"] if q["note"] == note.id]


def add(store: dict[str, Any], note: Note, wording: dict[str, Any], by: str, model: str | None = None,
        status: str = "proposed") -> dict[str, Any]:
    q = {"id": "nq" + secrets.token_hex(4), "note": note.id, **wording, "status": status, "by": by, "model": model,
         "problem": "", "checked": None, "made_from": mark_of(note), "created": now()}
    store["questions"].append(q)
    return q


def reword(store: dict[str, Any], note: Note, qid: str, question: Any, answer: Any, form: Any = None) -> dict[str, Any]:
    q = find(store, qid)
    kept = {k: q[k] for k in ("id", "note", "status", "by", "model", "created")}
    wording = hq.formed(question if question is not None else q["question"], answer if answer is not None else q["answer"],
                        form if form is not None else qforms.members(q))
    q.clear()
    q.update({**kept, **wording, "by": "you", "problem": "", "checked": None, "made_from": mark_of(note)})
    if q["status"] == "held":
        q["status"] = "proposed"
    return q


def system(settings: Settings) -> str:
    form_rules, form_reply = prompts.highlight_forms(settings)
    return _instructions("note_question.md", prompt_chars=PROMPT_MAX_CHARS, answer_chars=ANSWER_MAX_CHARS,
                         form_rules=form_rules.replace("the highlight", "the note"), form_reply=form_reply)


def plain_note(vault: Vault, note: Note) -> str:
    """The note as the model reads it: links as the words they show, no block ids."""
    lines, masked = note.body.split("\n"), mask_code(note.body).split("\n")
    return "\n".join(vault.words(line) if masked[i] == line else line for i, line in enumerate(lines)).strip()


def propose(backend: Backend, vault: Vault, note: Note, asked: list[str], settings: Settings, instruction: str = "") -> dict[str, Any]:
    """Ask the model for a question about a note: its wording, as ``hq.formed`` gives it, and ``model``."""
    text = plain_note(vault, note)
    if len(_flat(text)) < 20:
        raise AssistError("The note is too short to ask about. Write the idea out first.")
    parts = ["THE NOTE", f"Title: {_name(note)}", "", text[:8000]]
    if instruction:
        parts += ["", "THE READER ASKS OF THIS QUESTION", instruction]
    if asked:
        parts += ["", "ALREADY ASKED"] + [f"- {a}" for a in asked]
    parts += ["", "Write one question about the idea of this note, with its reference answer. Reply with the JSON object only."]

    def read(done: Completion) -> dict[str, Any]:
        data = done.data if isinstance(done.data, dict) else {}
        try:
            question = hq.clean(data.get("question"), "a question", PROMPT_MAX_CHARS)
            form = {} if settings.open_only else qforms.from_model(data, question, _flat)
            if qforms.form_of(form) not in settings.forms:
                raise hq.QuestionError(f"the form {qforms.NAMES[qforms.form_of(form)]} is not allowed; the allowed forms are "
                                       + ", ".join(qforms.NAMES[f] for f in settings.forms))
            return {**hq.formed(question, data.get("answer"), form), "model": done.model}
        except hq.QuestionError as exc:
            raise Unusable([str(exc)]) from exc

    return ask_with_retry(backend, role="draft", system=system(settings), message="\n".join(parts), schema=hq.propose_schema(settings),
                          model=settings.model, read=read, feedback=lambda wrong: prompts.problem_block(wrong[0]),
                          failed=hq.could_not_be_used)


def check(backend: Backend, vault: Vault, note: Note, q: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The independent check of a note's question against the note: ``{"passed", "criteria", "problem"}``."""
    graded = qforms.form_of(q) != "open"
    points = CHECKS + ((ONE_RIGHT,) if graded else ())
    one_right = ("- `one_right_answer`: by the note, exactly one answer is right: the one given as right is right, and no other "
                 "choice or wording a careful reader could give is also right.\n") if graded else ""
    asked, reference = qforms.as_text(q, q["question"], q["answer"])
    schema = {"type": "object", "additionalProperties": False, "required": [*points, "problem"],
              "properties": {**{p: {"type": "boolean"} for p in points}, "problem": {"type": "string"}}}
    message = "\n".join(["THE NOTE", f"Title: {_name(note)}", "", plain_note(vault, note)[:8000], "", "THE QUESTION", asked, "",
                         "ITS REFERENCE ANSWER", reference, "", *qforms.model_lines(q), "", "Judge the question. Reply with the JSON object only."])
    settings = with_form(settings, q)
    done = backend.complete(role="check", system=_instructions("note_check.md", one_right=one_right), prompt=message, schema=schema,
                            model=settings.checker_model or settings.model)
    data = done.data if isinstance(done.data, dict) else {}
    criteria = {p: data.get(p) is True for p in points}
    return {"passed": all(criteria.values()), "criteria": criteria,
            "problem": "" if all(criteria.values()) else _flat(str(data.get("problem") or "It failed the check."))[:600]}


def practice_prompts(vault: Vault, store: dict[str, Any]) -> dict[str, tuple[Note, dict[str, Any]]]:
    """For Practice: every accepted question whose note is still in the folder, as (the note, the prompt)."""
    out = {}
    for q in store["questions"]:
        note = vault.by_id.get(q["note"])
        if q["status"] == "accepted" and note is not None:
            out[q["id"]] = (note, {"id": q["id"], **qforms.as_prompt(q)})
    return out
