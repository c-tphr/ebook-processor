"""The graduated pathway through a book (``docs/COMPREHENSION-PLAN.md``; the words are in ``GLOSSARY.md``).

A book prepared with ``Settings.ephemeral`` set to ``attention`` or ``linked`` has questions of two roles.

A **practice question** is what a book's question has always been: asked at the checkpoint at the end of its
section and then a card in Practice. In the pathway it can carry more:

    reasoning            the kind of reasoning a higher-order question asks for (``REASONING``), or "" for recall
    earlier_paragraphs   paragraphs before its section that its answer also draws on (``supporting_paragraphs``
                         stay inside the section, as the format of the book for the e-reader demands)
    accounts_for         for a question with more than one defensible answer: the evidence a well-supported answer
                         accounts for. ``answer`` is then one such answer
    cue                  ``{"prompt", "answer"}``: a short recall prompt for "Help me recall", written for a
                         higher-order question that no ephemeral question supports

An **ephemeral question** is asked inside a section, right after the paragraph ``after``, to help the reader keep
their attention on the text. It is never graded and never a card, unless the reader promotes it. The run keeps
them in ``state["ephemeral"]``, each shaped like a question, with:

    id          "<section id>-e<n>" (s0007-e2): never the form of a practice question's id (c0007-p2)
    after       the paragraph it follows; its ``supporting_paragraphs`` lie at or before it, in its section
    purpose     "attention", or "linked" when it rehearses material for practice questions
    supports    the ids of the practice questions it supports (``linked`` only)
    promoted    the id of the practice question the reader made of it, once they have

This module holds what does not depend on the run: the rules a model's reply is held to, and how the app's reader
is given the pathway. The run itself is in ``pipeline``, the file the reader is given is written by ``outputs``, and
promotion is in ``editing``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from . import forms as qforms

MODES = ("none", "attention", "linked")
MODE_WORDS = {
    "none": "none: questions at the end of each section only",
    "attention": "to help sustain attention generally",
    "linked": "to build toward practice questions explicitly",
}
REASONING = ("explain", "infer", "compare", "synthesize", "apply", "predict", "example", "evaluate")
REASONING_WORDS = {
    "explain": "explain a mechanism", "infer": "infer or interpret", "compare": "compare and distinguish",
    "synthesize": "synthesize across passages", "apply": "apply to a new case", "predict": "predict an outcome",
    "example": "give an example", "evaluate": "evaluate a claim",
}
EXTRAS = ("reasoning", "earlier_paragraphs", "accounts_for", "cue")  # what a practice question may carry

CHECK_PROMPT_MAX = 200  # characters of an ephemeral question, and of a cue
CHECK_ANSWER_MAX = 300  # ... and of its answer
MAX_CHECKS = 8  # ephemeral questions in one section
ACCOUNTS_MAX = 5  # statements of evidence a question may list
ACCOUNT_CHARS = 200
EARLIER_MAX = 8  # paragraphs before its section a question may draw on

FILE = "pathway.json"
FORMAT = "ebook-processor-pathway"
VERSION = 1


def ephemeral(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The ephemeral questions of a run. A run made before there were any has none."""
    return state.get("ephemeral") or []


def is_ephemeral(q: dict[str, Any]) -> bool:
    return "after" in q


def _refs(value: Any) -> list[str]:
    seen: list[str] = []
    for r in value if isinstance(value, list) else []:
        if isinstance(r, str) and r.strip() and r.strip() not in seen:
            seen.append(r.strip())
    return seen


# --------------------------------------------------------------------------
# What a model's reply holds, as it is kept


def extras(raw: Any, clean: Callable[[Any], str]) -> dict[str, Any]:
    """The pathway's members of a practice question as a model gave them. ``clean`` tidies one text to one line."""
    raw = raw if isinstance(raw, dict) else {}
    accounts = [text for text in (clean(a) for a in (raw.get("accounts_for") if isinstance(raw.get("accounts_for"), list) else []))
                if text]
    cue = raw.get("cue") if isinstance(raw.get("cue"), dict) else {"prompt": raw.get("cue_prompt"), "answer": raw.get("cue_answer")}
    prompt, answer = clean(cue.get("prompt")), clean(cue.get("answer"))
    return {"reasoning": raw["reasoning"] if raw.get("reasoning") in REASONING else "",
            "earlier_paragraphs": _refs(raw.get("earlier_paragraphs")), "accounts_for": accounts,
            "cue": {"prompt": prompt, "answer": answer} if prompt or answer else None}


def extras_of(q: dict[str, Any]) -> dict[str, Any]:
    """The pathway's members a question has, to compare two wordings or to carry them over."""
    return {k: copy.deepcopy(q[k]) for k in EXTRAS if k in q}


def extras_problems(q: dict[str, Any], shown_before: set[str], plain: Callable[[str], str | None]) -> list[str]:
    """What is wrong with the pathway's members of a practice question, in words the drafter can act on.
    ``shown_before`` are the paragraphs the drafter was shown that come before the question's section."""
    out: list[str] = []
    earlier = q.get("earlier_paragraphs") or []
    unknown = [r for r in earlier if r not in shown_before]
    if unknown:
        out.append(f"earlier_paragraphs names {', '.join(unknown)}, which {'are' if len(unknown) > 1 else 'is'} not text "
                   "you were shown before the question's section; cite only earlier paragraphs you were shown, and "
                   "the section's own paragraphs in supporting_paragraphs")
    if len(earlier) > EARLIER_MAX:
        out.append(f"earlier_paragraphs has {len(earlier)} entries; at most {EARLIER_MAX}: cite the paragraphs the answer rests on")
    if earlier and not q.get("reasoning"):
        out.append("a question that draws on earlier paragraphs is a higher-order question: say which reasoning it asks "
                   "for, or ask from this section alone")
    accounts = q.get("accounts_for") or []
    if len(accounts) > ACCOUNTS_MAX:
        out.append(f"accounts_for has {len(accounts)} statements; at most {ACCOUNTS_MAX}")
    if len(accounts) == 1:
        out.append("accounts_for has one statement; give at least two pieces of evidence, or leave it empty")
    for text in accounts:
        if len(text) > ACCOUNT_CHARS:
            out.append(f"a statement of accounts_for has {len(text)} characters; the limit is {ACCOUNT_CHARS}")
        if plain(text):
            out.append(f"a statement of accounts_for {plain(text)}; write plain text")
    if accounts and qforms.form_of(q) != "open":
        out.append("accounts_for belongs to an open question: a question the app grades has one right answer")
    cue = q.get("cue")
    if cue:
        out += _short_problems(cue.get("prompt") or "", cue.get("answer") or "", "the cue's question", "the cue's answer", plain)
        if not q.get("reasoning"):
            out.append("a cue belongs to a higher-order question; leave cue_prompt and cue_answer empty for this one")
    return out


def _short_problems(prompt: str, answer: str, what: str, what_answer: str, plain: Callable[[str], str | None]) -> list[str]:
    out = []
    for label, text, limit in ((what, prompt, CHECK_PROMPT_MAX), (what_answer, answer, CHECK_ANSWER_MAX)):
        if not text.strip():
            out.append(f"{label} is empty")
        elif len(text) > limit:
            out.append(f"{label} has {len(text)} characters; the limit is {limit}: keep it brief")
        elif plain(text):
            out.append(f"{label} {plain(text)}; write plain text")
    return out


def check_from(raw: Any, wording: dict[str, Any], mode: str) -> dict[str, Any]:
    """An ephemeral question as a model gave it. ``wording`` is its text and form as any question's is kept. The
    practice questions it supports are kept only in a book prepared with explicit links."""
    raw = raw if isinstance(raw, dict) else {}
    return {**wording, "after": str(raw.get("after_paragraph") or raw.get("after") or "").strip(),
            "supports": _refs(raw.get("supports")) if mode == "linked" else []}


def brief_problems(c: dict[str, Any]) -> list[str]:
    """An ephemeral question is brief."""
    out = []
    if qforms.form_of(c) in ("open", "choice") and len(c["prompt"]) > CHECK_PROMPT_MAX:
        out.append(f"the question has {len(c['prompt'])} characters; an ephemeral question is brief, at most {CHECK_PROMPT_MAX}")
    if len(c["answer"]) > CHECK_ANSWER_MAX:
        out.append(f"the answer has {len(c['answer'])} characters; an ephemeral question's answer is brief, at most {CHECK_ANSWER_MAX}")
    return out


def check_problems(c: dict[str, Any], blocks: list[Any], mode: str) -> list[str]:
    """What is wrong with an ephemeral question besides what is wrong with any question: its length, its place and
    its links. ``blocks`` are the blocks of its section, in reading order."""
    out = brief_problems(c)
    order = {b.ref: n for n, b in enumerate(blocks)}
    after = order.get(c["after"])
    worded = [n for n, b in enumerate(blocks) if b.words and b.kind != "heading"]
    if after is None:
        out.append(f"after_paragraph {c['after']!r} is not a paragraph of the question's section")
    elif not blocks[after].words or blocks[after].kind == "heading":
        out.append(f"after_paragraph {c['after']} is a heading or has no words; place the question after a paragraph of text")
    elif blocks[after].placeholder is not None:  # the app's reader knows where a paragraph ends, not a table or a picture
        out.append(f"after_paragraph {c['after']} is a table, a listing, a formula or a picture; place the question after a "
                   "paragraph of text")
    elif not worded or after >= worded[-1]:
        out.append(f"after_paragraph {c['after']} is the section's last paragraph, where the checkpoint is; place the "
                   "question at a pause inside the section, or leave it out")
    else:
        late = [r for r in c["supporting_paragraphs"] if r in order and order[r] > after]
        if late:
            out.append(f"supporting paragraph{'s' if len(late) > 1 else ''} {', '.join(late)} come{'' if len(late) > 1 else 's'} "
                       f"after {c['after']}, where the question is asked; the reader has not read that yet. Place the "
                       "question after the last paragraph its answer needs")
    if mode == "linked" and not c["supports"]:
        out.append("supports is empty; in this book every ephemeral question supports at least one practice question "
                   "of its section or a later one")
    return out


# --------------------------------------------------------------------------
# What a model is shown of a question's role


def model_lines(q: dict[str, Any], questions: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """A question's role and the pathway's members, as a checker or a reviser is shown them. ``questions`` are the
    run's practice questions by id, for the ones an ephemeral question supports."""
    if is_ephemeral(q):
        lines = [f"Role: ephemeral question, asked inside the section right after [{q['after']}]. The reader has read the "
                 "section only up to there."]
        for qid in q.get("supports") or []:
            target = (questions or {}).get(qid)
            if target is not None:
                lines.append(f"It supports the practice question [{qid}]: {target['prompt']} / Answer: {target['answer']}")
        return lines
    if not any(q.get(k) for k in EXTRAS):
        return ["Role: practice question (recall)"] if "reasoning" in q else []
    lines = ["Role: practice question" + (f", higher-order ({REASONING_WORDS[q['reasoning']]})" if q.get("reasoning") else " (recall)")]
    if q.get("earlier_paragraphs"):
        lines.append("It also draws on these earlier paragraphs, given under EARLIER PARAGRAPHS: " + ", ".join(q["earlier_paragraphs"]))
    return lines


def after_lines(q: dict[str, Any]) -> list[str]:
    """What follows a practice question's answer when a model is shown it: its evidence and its cue."""
    lines = [f"Evidence a good answer accounts for: {text}" for text in q.get("accounts_for") or []]
    cue = q.get("cue")
    if cue:
        lines.append(f"Recall cue: {cue['prompt']} / Answer: {cue['answer']}")
    return lines


# --------------------------------------------------------------------------
# For the app's reader


def load(folder: Path) -> dict[str, Any] | None:
    """The pathway of a book as ``outputs`` wrote it, or None for a book that has none."""
    try:
        doc = json.loads((folder / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("version") != VERSION:
        return None
    return doc if isinstance(doc.get("questions"), dict) and isinstance(doc.get("ephemeral"), list) else None


def ids(doc: dict[str, Any] | None) -> set[str]:
    """The ids of a book's ephemeral questions: what a reading record may say was met inside a section."""
    return {c["id"] for c in (doc or {}).get("ephemeral", []) if isinstance(c, dict) and isinstance(c.get("id"), str)}


def find(doc: dict[str, Any] | None, eid: str) -> dict[str, Any] | None:
    return next((c for c in (doc or {}).get("ephemeral", []) if c.get("id") == eid), None)


def into_manifest(manifest: dict[str, Any], doc: dict[str, Any] | None, blocks: dict[str, list[int]]) -> dict[str, Any]:
    """The book's manifest with its pathway: every section gets ``checks``, its ephemeral questions in reading
    order with the word each follows (``at``) and the word its passage begins at (``from``), and a practice
    question gets its pathway members. ``help`` names the ephemeral questions "Help me recall" brings back.
    A question the reader promoted carries ``promoted``: it is a card in Practice and is not asked at the
    checkpoint. ``blocks`` says where each paragraph lies (``reading.Outline.blocks``)."""
    if not doc:
        return manifest
    extra = doc["questions"]
    by_section: dict[str, list[dict[str, Any]]] = {}
    for c in doc["ephemeral"]:
        place = blocks.get(c.get("after"))
        if not place:
            continue
        begins = min([blocks[r][1] for r in c.get("supporting_paragraphs") or [] if r in blocks and blocks[r][0] == place[0]] + [place[1]])
        by_section.setdefault(c["section"], []).append({
            "id": c["id"], "prompt": c["prompt"], "answer": c["answer"], **qforms.members(c),
            "purpose": c.get("purpose") or "attention", "supports": list(c.get("supports") or []),
            "promoted": c.get("promoted") or None, "chapter": place[0], "at": place[2], "from": begins})
    out = copy.copy(manifest)
    out["ephemeral"] = doc.get("mode") or "none"
    out["sections"] = []
    for s in manifest["sections"]:
        prompts = []
        for p in s["prompts"]:
            e = extra.get(p["id"])
            if e:
                p = {**p, **{k: e[k] for k in ("reasoning", "accounts_for", "cue", "help") if e.get(k)},
                     **({"promoted": True} if e.get("promoted_from") else {}),
                     **({"earlier": [{"ref": r, "chapter": blocks[r][0], "word": blocks[r][1]} for r in e["earlier"] if r in blocks]}
                        if e.get("earlier") else {})}
            prompts.append(p)
        checks = sorted((c for c in by_section.get(s["id"], []) if c["chapter"] == s["chapter"] and s["start"] < c["at"] < s["end"]),
                        key=lambda c: c["at"])
        out["sections"].append({**s, "prompts": prompts, "checks": checks})
    return out
