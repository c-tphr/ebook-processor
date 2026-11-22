"""Questions made from highlights.

A reader marks a passage and asks for a question about it. A model proposes one (the highlight is shown to it
inside its section, with the reader's note as a steer), or the reader writes their own. The reader accepts,
rewords or discards a proposal. An accepted question is practised like the book's own: it is asked at the end of
its section in the app's reader when that checkpoint is still ahead, and otherwise goes straight to Practice.

The questions live with the highlights, in ``notes.json``, under ``questions``. Each has:

    id          "hq" and eight hex digits; never the form of a book question's id (c0007-p2)
    highlight   the id of its highlight, or None once that highlight was removed and the question kept
    chapter, start, end, text
                where the highlighted words are and what they say, as they were when the question was made
    question, answer
    status      proposed: waiting for the reader to accept it
                held: accepted automatically but failed its check; waiting for the reader, with ``problem``
                accepted
    by          "model" or "you"
    ask         for an accepted question: "checkpoint" (asked at the end of ``section`` in the reader) or
                "practice" (a card from the start)
    section     the id of the section the highlight begins in, or None
    checked     None, or what the independent check said: {"passed", "criteria", "problem"}
"""

from __future__ import annotations

import copy
import secrets
from dataclasses import replace
from pathlib import Path
from string import Template
from typing import Any

from . import forms as qforms
from . import prompts
from .content import render_blocks
from .llm import Backend, BackendError
from .store import now
from .prep.epub import Block, SpineDoc
from .prep.checkpoints import ANSWER_MAX_CHARS, PROMPT_MAX_CHARS, plain_text_problem
from .reading import ReadingBook
from .settings import INSTRUCTIONS_VERSION, Settings
from .steer import Steer, highlight_rules

PROPOSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["question", "answer"],
                  "properties": {"question": {"type": "string"}, "answer": {"type": "string"}}}
OPEN = ("proposed", "held")  # waiting for the reader


class QuestionError(ValueError):
    """A request about a question cannot be carried out; the message says why."""


# --------------------------------------------------------------------------
# The store


def questions(store: dict[str, Any]) -> list[dict[str, Any]]:
    return store.setdefault("questions", [])


def find(store: dict[str, Any], qid: str) -> dict[str, Any]:
    for q in questions(store):
        if q["id"] == qid:
            return q
    raise QuestionError("no such question")


def of_highlight(store: dict[str, Any], hid: str) -> list[dict[str, Any]]:
    return [q for q in questions(store) if q["highlight"] == hid]


def accepted(store: dict[str, Any]) -> list[dict[str, Any]]:
    return [q for q in questions(store) if q["status"] == "accepted"]


def ids(store: dict[str, Any]) -> set[str]:
    """The ids a reading record or a practice card may carry: those of accepted questions."""
    return {q["id"] for q in accepted(store)}


def _clean(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuestionError(f"a question needs {what}")
    text = " ".join(value.split())
    if len(text) > limit:
        raise QuestionError(f"{what} can hold {limit} characters; this one has {len(text)}")
    return text


def section_at(manifest: dict[str, Any], chapter: int, word: int) -> dict[str, Any] | None:
    return next((s for s in manifest["sections"] if s["chapter"] == chapter and s["start"] <= word < s["end"]), None)


def formed(question: Any, answer: Any, form: Any = None) -> dict[str, Any]:
    """The wording of a question as it is kept: ``question``, ``answer`` and, for a question that is not open, the
    members of its form (``forms.members``). Raises ``QuestionError`` when something about it cannot be used."""
    out = {"question": _clean(question, "a question", PROMPT_MAX_CHARS), "answer": _clean(answer, "an answer", ANSWER_MAX_CHARS)}
    extra = form if isinstance(form, dict) else {}
    if isinstance(extra.get("choices"), list):
        extra = {**extra, "choices": [" ".join(str(c).split()) for c in extra["choices"]]}
    if isinstance(extra.get("accept"), list):
        extra = {**extra, "accept": [" ".join(str(a).split()) for a in extra["accept"] if str(a).strip()]}
    out.update(qforms.members(extra))
    wrong = qforms.problems({"prompt": out["question"], "answer": out["answer"], **out}, None, plain_text_problem)
    if wrong:
        raise QuestionError(wrong[0][0].upper() + wrong[0][1:] + ".")
    return out


def add(store: dict[str, Any], manifest: dict[str, Any], highlight: dict[str, Any], question: Any, answer: Any,
        by: str, model: str | None = None, form: Any = None) -> dict[str, Any]:
    """A new question for a highlight, waiting for the reader (``proposed``). ``form`` holds the members of a
    question that is not open."""
    section = section_at(manifest, highlight["chapter"], highlight["start"])
    q = {"id": "hq" + secrets.token_hex(4), "highlight": highlight["id"], "chapter": highlight["chapter"],
         "start": highlight["start"], "end": highlight["end"], "text": highlight["text"],
         "section": section["id"] if section else None,
         **formed(question, answer, form),
         "status": "proposed", "by": by, "model": model, "ask": None, "checked": None, "problem": "",
         "created": now(), "accepted": None}
    questions(store).append(q)
    return q


def passed(reading_state: dict[str, Any] | None, manifest: dict[str, Any], section: dict[str, Any]) -> bool:
    """Whether the reader has been beyond the end of a section, so that its checkpoint is behind them."""
    far = (reading_state or {}).get("x_furthest")
    order = [c["index"] for c in manifest["chapters"]]
    if not far or far.get("chapter") not in order or section["chapter"] not in order:
        return False
    return (order.index(far["chapter"]), far.get("word", 0)) >= (order.index(section["chapter"]), section["end"])


def accept(store: dict[str, Any], manifest: dict[str, Any], reading_state: dict[str, Any] | None, qid: str) -> dict[str, Any]:
    """Accept a question. It is asked at its section's checkpoint when that section has a checkpoint the reader
    has not passed; otherwise it is a practice card from the start."""
    q = find(store, qid)
    if q["status"] == "accepted":
        return q
    section = next((s for s in manifest["sections"] if s["id"] == q["section"]), None)
    ahead = section is not None and bool(section["prompts"]) and not passed(reading_state, manifest, section)
    q.update(status="accepted", ask="checkpoint" if ahead else "practice", accepted=now(), problem="")
    return q


def reword(store: dict[str, Any], qid: str, question: Any = None, answer: Any = None, form: Any = None) -> dict[str, Any]:
    """Change a question's wording, and with ``form`` the members of its form (its choices and which is right,
    whether its statement is true, the answers that count). The form itself stays what it is."""
    q = find(store, qid)
    if form is not None and (not isinstance(form, dict) or form.get("form", qforms.form_of(q)) != qforms.form_of(q)):
        raise QuestionError("a question keeps its form; discard it and ask for another to change that")
    made = formed(q["question"] if question is None else question, q["answer"] if answer is None else answer,
                  {**qforms.members(q), **(form or {})})
    changed = any(q.get(k) != v for k, v in made.items())
    q.update(made)
    if changed:
        q["by"] = "you"  # the wording is the reader's now, and what a check said about the old one no longer holds
        q["checked"] = None
    return q


def discard(store: dict[str, Any], qid: str) -> dict[str, Any]:
    q = find(store, qid)
    questions(store).remove(q)
    return q


def highlight_removed(store: dict[str, Any], hid: str, keep: bool) -> list[dict[str, Any]]:
    """What becomes of a removed highlight's questions. Proposals go with it. Accepted ones are kept, without
    their highlight, when ``keep`` is set. Returns the questions that were removed."""
    gone = [q for q in of_highlight(store, hid) if not (keep and q["status"] == "accepted")]
    store["questions"] = [q for q in questions(store) if q not in gone]
    for q in of_highlight(store, hid):
        q["highlight"] = None
    return gone


def highlights_joined(store: dict[str, Any], old: list[str], new: str) -> None:
    """Highlights that touched became one: their questions belong to it."""
    for q in questions(store):
        if q["highlight"] in old:
            q["highlight"] = new


def with_questions(manifest: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    """The book's manifest with the accepted questions that are asked at checkpoints added to their sections'
    prompts, after the book's own. Each carries ``"hq": True``."""
    extra: dict[str, list[dict[str, Any]]] = {}
    for q in accepted(store):
        if q["ask"] == "checkpoint" and q["section"]:
            extra.setdefault(q["section"], []).append({"id": q["id"], "prompt": q["question"], "answer": q["answer"],
                                                       **qforms.members(q), "hq": True})
    if not extra:
        return manifest
    out = copy.copy(manifest)
    out["sections"] = [{**s, "prompts": s["prompts"] + extra[s["id"]]} if s["id"] in extra else s for s in manifest["sections"]]
    return out


def practice_prompts(manifest: dict[str, Any], store: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """For Practice: every accepted question that is a card from the start, as (where it is, prompt)."""
    titles = {c["index"]: c["title"] for c in manifest["chapters"]}
    labels = {s["id"]: s["label"] for s in manifest["sections"]}
    return {q["id"]: ({"chapter": q["chapter"], "start": q["start"], "label": labels.get(q["section"]) or titles.get(q["chapter"], "")},
                      {"id": q["id"], "prompt": q["question"], "answer": q["answer"], **qforms.members(q), "hq": True})
            for q in accepted(store) if q["ask"] == "practice"}


# --------------------------------------------------------------------------
# The text around a highlight


def around(book: ReadingBook, manifest: dict[str, Any], chapter: int, start: int, end: int) -> dict[str, Any]:
    """The section a highlight begins in, as blocks of its document: ``doc``, ``blocks``, ``first`` and ``last``
    (indexes into the document's blocks), ``section`` (id and label), and ``marked``, the blocks the highlight
    touches. A highlight outside every section gets the blocks around it instead."""
    sd = next((d for d in book.book.spine if d.index == chapter and d.doc is not None), None)
    if sd is None or not sd.blocks:
        raise QuestionError("the highlight is not in the book's text")
    touched = [n for n, b in enumerate(sd.blocks) if b.start < end and start < b.end]
    if not touched:
        touched = [max((n for n, b in enumerate(sd.blocks) if b.start <= start), default=0)]
    section = section_at(manifest, chapter, start)
    if section:
        inside = [n for n, b in enumerate(sd.blocks) if section["start"] <= b.start < section["end"]]
        first, last = (inside[0], inside[-1]) if inside else (touched[0], touched[-1])
        first, last = min(first, touched[0]), max(last, touched[-1])
        label = {"id": section["id"], "label": section["label"]}
    else:
        first, last = max(0, touched[0] - 3), min(len(sd.blocks) - 1, touched[-1] + 3)
        label = {"id": "s0000", "label": sd.title or ""}
    return {"doc": sd, "blocks": sd.blocks[first:last + 1], "first": first, "last": last, "section": label,
            "marked": [sd.blocks[n] for n in touched]}


def passage_refs(book: ReadingBook, q: dict[str, Any]) -> list[tuple[SpineDoc, Block]]:
    """The blocks a question's highlight lies in: its supporting passage."""
    sd = next((d for d in book.book.spine if d.index == q["chapter"] and d.doc is not None), None)
    if sd is None:
        return []
    return [(sd, b) for b in sd.blocks if b.start < q["end"] and q["start"] < b.end]


# --------------------------------------------------------------------------
# The model


def system(settings: Settings, steer: Steer | None = None) -> str:
    text = (Path(__file__).resolve().parent / "instructions" / "highlight.md").read_text(encoding="utf-8")
    form_rules, form_reply = prompts.highlight_forms(settings)
    return Template(text).substitute(version=INSTRUCTIONS_VERSION, prompt_chars=PROMPT_MAX_CHARS, answer_chars=ANSWER_MAX_CHARS,
                                     reader_rules=highlight_rules(steer), form_rules=form_rules, form_reply=form_reply)


def propose_schema(settings: Settings) -> dict[str, Any]:
    if settings.open_only:
        return PROPOSE_SCHEMA
    draft = prompts.draft_schema(settings)["properties"]["ranges"]["items"]["properties"]["prompts"]["items"]["properties"]
    members = {k: v for k, v in draft.items() if k not in ("prompt", "supporting_paragraphs")}
    return {"type": "object", "additionalProperties": False, "required": ["question", *members],
            "properties": {"question": {"type": "string"}, **members}}


def propose_message(book: ReadingBook, place: dict[str, Any], highlight: dict[str, Any], asked: list[str],
                    settings: Settings, instruction: str = "") -> str:
    sd: SpineDoc = place["doc"]
    parts = [f"BOOK\nTitle: {book.book.title or '(no title)'}\nLanguage: {book.book.language or 'unknown'}", "",
             f"SECTION \"{place['section']['label']}\" ({sd.href})", "",
             render_blocks(sd, place["blocks"], settings.content if settings.content != "figures" else "text"), "",
             f"HIGHLIGHT (in {', '.join(b.ref for b in place['marked'])})", highlight["text"]]
    if highlight.get("note"):
        parts += ["", "THE READER'S NOTE", highlight["note"]]
    if instruction:
        parts += ["", "THE READER ASKS OF THIS QUESTION", instruction]
    if asked:
        parts += ["", "ALREADY ASKED"] + [f"- {a}" for a in asked]
    parts += ["", "Write one question about the highlight, with its reference answer. Reply with the JSON object only."]
    return "\n".join(parts)


def propose(backend: Backend, book: ReadingBook, manifest: dict[str, Any], store: dict[str, Any],
            highlight: dict[str, Any], settings: Settings, steer: Steer | None = None,
            instruction: str = "") -> dict[str, Any]:
    """Ask the model for a question about a highlight: ``{"question", "answer", "model"}``. ``steer`` is what the
    reader wants from the book; ``instruction`` is their wish for this one question. Raises ``BackendError`` when
    the model cannot be reached or does not answer usably."""
    place = around(book, manifest, highlight["chapter"], highlight["start"], highlight["end"])
    section = next((s for s in manifest["sections"] if s["id"] == place["section"]["id"]), None)
    asked = [q["question"] for q in of_highlight(store, highlight["id"])] + [p["prompt"] for p in (section or {}).get("prompts", [])]
    message = propose_message(book, place, highlight, asked, settings, instruction)
    feedback = ""
    for _ in range(2):  # a reply that breaks a plain rule is sent back once, with what was wrong
        done = backend.complete(role="draft", system=system(settings, steer), prompt=message + feedback,
                                schema=propose_schema(settings), model=settings.model)
        data = done.data if isinstance(done.data, dict) else {}
        try:
            question = _clean(data.get("question"), "a question", PROMPT_MAX_CHARS)
            form = {} if settings.open_only else qforms.from_model(data, question, lambda t: " ".join(str(t or "").split()))
            if qforms.form_of(form) not in settings.forms:
                raise QuestionError(f"the form {qforms.NAMES[qforms.form_of(form)]} is not allowed; the allowed forms are "
                                    + ", ".join(qforms.NAMES[f] for f in settings.forms))
            return {**formed(question, data.get("answer"), form), "form": form, "model": done.model}
        except QuestionError as exc:
            feedback = f"\n\nPROBLEM WITH YOUR EARLIER REPLY (fix it and reply with the complete object again)\n{exc}"
    raise BackendError(f"the model's question could not be used: {feedback.splitlines()[-1]}")


def check(backend: Backend, book: ReadingBook, manifest: dict[str, Any], q: dict[str, Any], settings: Settings,
          steer: Steer | None = None) -> dict[str, Any]:
    """The independent check of one question, by the rules the book's own questions are checked by:
    ``{"passed", "criteria", "problem"}``. A reply that cannot be used counts as not passed."""
    place = around(book, manifest, q["chapter"], q["start"], q["end"])
    item = {"doc": place["doc"], "section": place["section"], "blocks": place["blocks"], "first": place["first"],
            "last": place["last"], "judge_break": False,
            "questions": [{"id": q["id"], "prompt": q["question"], "answer": q["answer"], **qforms.members(q)}]}
    if qforms.form_of(q) not in settings.forms:  # a form allowed when it was written: the check must know the forms
        settings = replace(settings, forms=qforms.clean_forms([*settings.forms, qforms.form_of(q)]))
    done = backend.complete(role="check", system=prompts.checker_system(settings, steer),
                            prompt=prompts.check_message(book.book, [item], settings, None), schema=prompts.check_schema(settings),
                            model=settings.checker_model or settings.model)
    verdict = next((p for p in done.data.get("prompts") or [] if isinstance(p, dict) and p.get("id") == q["id"]), None)
    if verdict is None:
        return {"passed": False, "criteria": {}, "problem": "The check gave no verdict for this question."}
    criteria = {c: verdict.get(c) is True for c in prompts.criteria(settings)}
    return {"passed": all(criteria.values()), "criteria": criteria,
            "problem": "" if all(criteria.values()) else str(verdict.get("problem") or "It failed the check.")[:600]}
