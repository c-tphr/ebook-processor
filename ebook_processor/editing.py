"""Changing a book's questions by hand, after the run that wrote them.

A question the reader has changed, or written, has the status ``edited``: it is the reader's, it is kept as they
wrote it, and no check removes it. The first change to a question keeps what the run wrote in ``original``, so that
it can be put back. A question the reader wrote has ``added`` and belongs to a section like any other.

Everything here works on the state of a finished run (``work/state.json``), which is what the outputs are built
from; ``runner.change_run`` applies a change and builds them again. Three things call a model, each only when the
reader asks: a rewrite to a wish (``rewrite``), an independent check of an edited question (``check``), and the
choice of the paragraphs an added question rests on (``choose_passage``).
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any

from . import forms as qforms
from . import prompts
from .llm import Backend, BackendError
from .pipeline import (BookIndex, clean_text, kept, new_question, next_checkpoint, normal_question, question_problems,
                       sort_questions)
from .store import now
from .prep.epub import Block, Book, SpineDoc
from .settings import Settings
from .steer import INSTRUCTION_MAX, Steer

PASSAGES = ("section", "pick", "model_review", "model")  # how an added question gets its passage
CHECKED = ("status", "checks", "criteria", "citations", "evidence", "problem", "suggested_fix", "minor_note")
SHOWN = 160  # characters of a paragraph in the list to pick from


class EditError(ValueError):
    """A change that cannot be made; the message says why."""


def find(state: dict[str, Any], qid: str) -> dict[str, Any]:
    for q in state["questions"]:
        if q["id"] == qid:
            return q
    raise EditError("no such question")


def section_of(state: dict[str, Any], sid: Any) -> dict[str, Any]:
    for s in state["sections"]:
        if s["id"] == sid:
            return s
    raise EditError("no such section")


def blocks_of(book: Book, section: dict[str, Any]) -> tuple[SpineDoc, list[Block], int, int]:
    sd, first, last = BookIndex(book).span(section)
    return sd, sd.blocks[first : last + 1], first, last


def citable(blocks: list[Block]) -> list[Block]:
    """The blocks of a section that an answer can rest on: its text, not its headings or bare pictures."""
    return [b for b in blocks if b.kind != "heading" and b.words]


def paragraphs(book: Book, state: dict[str, Any], sid: str, cited: list[str] | None = None) -> list[dict[str, Any]]:
    """A section's paragraphs as the reader picks among them: reference, how the paragraph begins, and whether a
    question cites it."""
    sd, blocks, _, _ = blocks_of(book, section_of(state, sid))
    out = []
    for b in citable(blocks):
        text = " ".join(sd.words[b.start : b.end])
        out.append({"ref": b.ref, "text": text if len(text) <= SHOWN else text[: SHOWN - 1].rstrip() + "…",
                    "words": b.word_count, "cited": b.ref in (cited or [])})
    return out


def _wording(raw: dict[str, Any], inside: set[str], supporting: list[str]) -> dict[str, Any]:
    """What the reader wrote, as a question is kept: ``raw`` has ``question``, ``answer`` and, for a question that
    is not open, ``form`` with its members."""
    members = raw.get("form") if isinstance(raw.get("form"), dict) else {}
    if isinstance(members.get("choices"), list):
        members = {**members, "choices": [clean_text(c).replace("\n", " ") for c in members["choices"]]}
    if isinstance(members.get("accept"), list):
        members = {**members, "accept": [a for a in (clean_text(a).replace("\n", " ") for a in members["accept"]) if a]}
    q = {"prompt": clean_text(raw.get("question")), "answer": clean_text(raw.get("answer")),
         "supporting_paragraphs": supporting, **qforms.members(members)}
    wrong = question_problems(q, inside)
    if wrong:
        raise EditError(wrong[0][0].upper() + wrong[0][1:] + ".")
    return q


def _passage(given: Any, inside: list[str], default: list[str]) -> list[str]:
    if given is None:
        return default
    if not isinstance(given, list) or not all(isinstance(r, str) for r in given):
        raise EditError("the passage is a list of paragraphs of the section")
    chosen = [r for r in inside if r in set(given)]  # in reading order, and only paragraphs of the section
    if not chosen:
        raise EditError("Choose at least one paragraph of the section that the answer rests on.")
    return chosen


def view(book: Book, state: dict[str, Any], qid: str) -> dict[str, Any]:
    """A question as the editor shows it."""
    q = find(state, qid)
    section = section_of(state, q["section"])
    return {"id": q["id"], "section": {"id": section["id"], "label": section["label"]},
            **qforms.as_question(q), "status": q["status"],
            "kept": kept(q),
            "edited": q.get("edited"), "added": bool(q.get("added")), "has_original": "original" in q,
            "left_out_because": q["problem"] if q["status"] in ("failed", "withdrawn") else "",
            "verdict": q.get("verdict"), "supporting_paragraphs": q["supporting_paragraphs"],
            "paragraphs": paragraphs(book, state, section["id"], q["supporting_paragraphs"])}


# --------------------------------------------------------------------------
# Changes


def edit(state: dict[str, Any], book: Book, qid: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Change a question's wording, the members of its form, and with ``supporting_paragraphs`` its passage. Its
    form stays what it is. A question that had been left out is kept from now on: it is the reader's."""
    q = find(state, qid)
    members = raw.get("form") if isinstance(raw.get("form"), dict) else {}
    if members.get("form", qforms.form_of(q)) != qforms.form_of(q):
        raise EditError("A question keeps its kind. Take it out and add one of the other kind.")
    _, blocks, _, _ = blocks_of(book, section_of(state, q["section"]))
    inside = [b.ref for b in citable(blocks)]
    made = _wording({"question": raw.get("question", q["prompt"]), "answer": raw.get("answer", q["answer"]),
                     "form": {**qforms.members(q), **members}}, {b.ref for b in blocks},
                    _passage(raw.get("supporting_paragraphs"), inside, q["supporting_paragraphs"]))
    if all(q.get(k) == v for k, v in made.items()):
        return q
    if "original" not in q and not q.get("added"):
        q["original"] = copy.deepcopy({k: q[k] for k in ("prompt", "answer", "supporting_paragraphs", *CHECKED)}
                                      | qforms.members(q))
    for member in qforms.MEMBERS:
        q.pop(member, None)
    q.update(made, status="edited", edited=now(), criteria={}, citations=[], evidence="", problem="", suggested_fix="",
             minor_note="")
    q.pop("verdict", None)
    return q


def restore(state: dict[str, Any], qid: str) -> dict[str, Any]:
    """Put back what the run wrote, with what its check found."""
    q = find(state, qid)
    if "original" not in q:
        raise EditError("this question is as the run wrote it")
    for member in qforms.MEMBERS:
        q.pop(member, None)
    q.update(q.pop("original"))
    q.pop("edited", None), q.pop("verdict", None)
    return q


def add(state: dict[str, Any], book: Book, sid: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """A question of the reader's own for a section, of any form. Without ``supporting_paragraphs`` its passage is
    the whole section."""
    section = section_of(state, sid)
    _, blocks, _, _ = blocks_of(book, section)
    inside = [b.ref for b in citable(blocks)]
    if not inside:
        raise EditError("This section has no text a question could rest on.")
    members = raw.get("form") if isinstance(raw.get("form"), dict) else {}
    if members and members.get("form") not in qforms.GRADED:
        raise EditError("the kinds of question are: " + ", ".join(qforms.NAMES.values()))
    made = _wording(raw, {b.ref for b in blocks}, _passage(raw.get("supporting_paragraphs"), inside, inside))
    if section["checkpoint"] is None:  # a section that had no questions gets its checkpoint now
        section["checkpoint"] = next_checkpoint(state)
    taken = [int(x["id"].rsplit("-p", 1)[1]) for x in state["questions"] if x["section"] == section["id"]]
    q = new_question(f"{section['checkpoint']}-p{max(taken, default=0) + 1}", section["id"], made, status="edited",
                     added=now(), edited=now())
    state["questions"].append(q)
    sort_questions(state)
    return q


def delete(state: dict[str, Any], qid: str) -> dict[str, Any]:
    """Remove a question the reader added. (One the run wrote is taken out instead, and can be put back.)"""
    q = find(state, qid)
    if not q.get("added"):
        raise EditError("only a question you added can be deleted; take this one out instead")
    state["questions"].remove(q)
    return q


# --------------------------------------------------------------------------
# With a model, on request


def _item(book: Book, state: dict[str, Any], q: dict[str, Any]) -> dict[str, Any]:
    section = section_of(state, q["section"])
    sd, blocks, first, last = blocks_of(book, section)
    return {"doc": sd, "section": section, "blocks": blocks, "first": first, "last": last}


def _with_form(settings: Settings, q: dict[str, Any]) -> Settings:
    """The run's settings with the question's own form among the allowed ones."""
    form = qforms.form_of(q)
    return settings if form in settings.forms else replace(settings, forms=qforms.clean_forms([*settings.forms, form]))


def check(backend: Backend, book: Book, state: dict[str, Any], qid: str, settings: Settings,
          steer: Steer | None = None) -> dict[str, Any]:
    """The independent check of one question, as the run's own questions were checked. The verdict is kept with
    the question and changes nothing else about it: ``{"passed", "at", "criteria", "problem", "minor_note"}``."""
    q = find(state, qid)
    settings = _with_form(settings, q)
    item = {**_item(book, state, q), "judge_break": False, "questions": [q]}
    done = backend.complete(role="check", system=prompts.checker_system(settings, steer),
                            prompt=prompts.check_message(book, [item], settings, None), schema=prompts.check_schema(settings),
                            model=settings.checker_model or settings.model)
    entries = done.data.get("prompts") if isinstance(done.data, dict) else None
    entry = next((p for p in entries or [] if isinstance(p, dict) and p.get("id") == qid), None)
    if entry is None:
        raise BackendError("the check gave no verdict for this question")
    criteria = {c: entry.get(c) is True for c in prompts.criteria(settings)}
    passed = all(criteria.values())
    q["verdict"] = {"passed": passed, "at": now(), "criteria": criteria,
                    "problem": "" if passed else clean_text(entry.get("problem")) or "The check gave no reason.",
                    "suggested_fix": "" if passed else clean_text(entry.get("suggested_fix")),
                    "minor_note": clean_text(entry.get("minor_note")) if passed else ""}
    q["checks"] = q.get("checks", 0) + 1
    return q["verdict"]


def wording_of(q: dict[str, Any]) -> str:
    """A question as it stands, without what a check adds to it: to see later whether it was changed meanwhile."""
    return json.dumps({k: v for k, v in q.items() if k not in ("verdict", "checks")}, sort_keys=True, ensure_ascii=False)


def keep_verdict(state: dict[str, Any], qid: str, verdict: dict[str, Any], was: str) -> dict[str, Any]:
    """Keep a verdict that was worked out on a copy of the run (the model is asked with no lock held). A question
    that was changed in the meantime is not given a verdict on its earlier wording."""
    q = find(state, qid)
    if wording_of(q) != was:
        raise EditError("This question was changed while it was being checked. Check it again.")
    q["verdict"] = verdict
    q["checks"] = q.get("checks", 0) + 1
    return verdict


def rewrite(backend: Backend, book: Book, state: dict[str, Any], qid: str, wish: Any, settings: Settings,
            steer: Steer | None = None, draft: dict[str, Any] | None = None) -> dict[str, Any]:
    """Have the model rewrite a question as the reader asks, in the same form. Nothing is kept: the result is for
    the reader to take or change: ``{"question", "answer", "form", "supporting_paragraphs"}``. ``draft`` is the
    wording in the reader's form when it differs from what is saved."""
    q = find(state, qid)
    wish = clean_text(wish).replace("\n", " ")
    if not wish:
        raise EditError("Say what should be different.")
    if len(wish) > INSTRUCTION_MAX:
        raise EditError(f"the wish can hold {INSTRUCTION_MAX} characters")
    item = _item(book, state, q)
    inside = {b.ref for b in item["blocks"]}
    current = q
    if draft:
        current = {**{k: v for k, v in q.items() if k not in qforms.MEMBERS},
                   **_wording({**draft, "form": {**qforms.members(q), **(draft.get("form") or {})}}, inside, q["supporting_paragraphs"])}
    only = replace(settings, forms=(qforms.form_of(q),))  # the form stays what it is
    others = [x for x in state["questions"] if x["section"] == q["section"] and x["id"] != qid and kept(x)]
    message = prompts.rewrite_message(book, {**item, "question": {**current, "id": qid}, "others": others}, only, wish)
    feedback = ""
    for _ in range(2):
        done = backend.complete(role="revise", system=prompts.drafter_system(only, steer), prompt=message + feedback,
                                schema=prompts.revise_schema(only), model=settings.model)
        entries = done.data.get("revisions") if isinstance(done.data, dict) else None
        entry = next((e for e in entries or [] if isinstance(e, dict) and e.get("id") == qid), None)
        if entry is None or entry.get("action") == "withdraw":
            raise BackendError("the model could not rewrite the question that way")
        made = normal_question(entry)
        wrong = question_problems(made, inside, only.forms)
        if not wrong:
            return {"question": made["prompt"], "answer": made["answer"], "form": qforms.members(made),
                    "supporting_paragraphs": made["supporting_paragraphs"]}
        feedback = "\n\nPROBLEMS WITH YOUR EARLIER REPLY (fix exactly these and reply with the complete object again)\n" \
            + "\n".join(f"- {w}" for w in wrong)
    raise BackendError(f"the model's rewrite could not be used: {wrong[0]}")


def choose_passage(backend: Backend, book: Book, state: dict[str, Any], sid: Any, raw: dict[str, Any],
                   settings: Settings) -> list[str]:
    """Have the model say which paragraphs of a section the reader's question rests on. Returns their references,
    in reading order; all of the section's when the model names none of them."""
    section = section_of(state, sid)
    sd, blocks, first, last = blocks_of(book, section)
    inside = [b.ref for b in citable(blocks)]
    made = _wording(raw, {b.ref for b in blocks}, inside)
    item = {"doc": sd, "section": section, "blocks": blocks, "first": first, "last": last, "question": made}
    done = backend.complete(role="passage", system=prompts.PASSAGE_SYSTEM, prompt=prompts.passage_message(book, item, settings),
                            schema=prompts.PASSAGE_SCHEMA, model=settings.model)
    named = done.data.get("supporting_paragraphs") if isinstance(done.data, dict) else None
    chosen = [r for r in inside if r in {str(x).strip() for x in (named if isinstance(named, list) else [])}]
    return chosen or inside
