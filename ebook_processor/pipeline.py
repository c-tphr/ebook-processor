"""The run: draft the book in parts, check every question in a fresh session, revise, and keep what passes.

Everything a run decides is kept in one file, ``state.json`` in the work folder, and written after every call to
Claude. A run that stops (the usage limit, a closed laptop, Ctrl-C) therefore continues where it was when the same
command is given again, and the outputs can be rebuilt from the state at any time without calling Claude.

Steps:

1. **Draft.** The book is cut into parts of about ``part_words`` words: several short documents together, or a
   stretch of a long one. For each part Claude returns sections, excluded ranges and questions. The reply is
   checked mechanically (every paragraph in exactly one range, limits, citations inside the section) and sent back
   with the list of problems when it fails. Parts of different documents run side by side.
2. **Check.** A separate call, which sees only the section text and the questions, judges each question on five or six
   criteria and each section's break. One more call reviews what was excluded.
3. **Revise.** Failed questions go back to the drafter with the checker's finding, then to the checker again, up
   to ``rounds`` checks per question. A question that still fails is left out of the outputs and listed in the
   report.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__, prompts
from . import forms as qforms
from . import pathway
from .content import export_images
from .llm import Backend, BackendCancelled, Completion
from .prep import checkpoints as cp
from .prep.build import _preflight
from .prep.draft import check_draft
from .prep.epub import Block, Book, EpubError, SpineDoc, load_book
from .prep.extract import extract
from .prep.limits import MAX_LABEL_CHARS
from .prep.validate import exclusion_category
from .settings import INSTRUCTIONS_VERSION, Settings
from .steer import Steer
from .store import now, write_json  # noqa: F401  kept here too: older code and tests take them from this module

STATE_FORMAT = "ebook-processor-state"
STATE_VERSION = 1
STATE_FILE = "state.json"
SOURCE_COPY = "source.epub"
DRAFT_ATTEMPTS = 3  # calls for one part before the fallback
CONTEXT_WORDS = 3000  # the graduated pathway: how much text from before a part its drafter is shown, at most
# Question statuses that go into the outputs. ``edited``: changed or written by the reader (see ``editing``).
KEPT = ("passed", "unchecked", "unverified", "edited")

Progress = Callable[[dict[str, Any]], None]


class PipelineError(Exception):
    """The run cannot go on; the message says why."""


class Cancelled(Exception):
    """The run was stopped on request; its state is saved."""


def clean_text(value: Any) -> str:
    """A question or answer as stored: spaces collapsed inside each line, no control characters but line feeds."""
    if not isinstance(value, str):
        return ""
    lines = [re.sub(r"[ \t\f\v ]+", " ", line).strip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# The book, indexed


class BookIndex:
    def __init__(self, book: Book):
        self.book = book
        self.by_href = {sd.href: sd for sd in book.spine}
        self.where: dict[str, tuple[SpineDoc, int]] = {}
        for sd in book.spine:
            for i, b in enumerate(sd.blocks):
                self.where[b.ref] = (sd, i)

    def span(self, entry: dict[str, Any]) -> tuple[SpineDoc, int, int]:
        sd, first = self.where[entry["first_paragraph"]]
        _, last = self.where[entry["last_paragraph"]]
        return sd, first, last

    def blocks(self, entry: dict[str, Any]) -> list[Block]:
        sd, first, last = self.span(entry)
        return sd.blocks[first : last + 1]

    def order(self, entry: dict[str, Any]) -> tuple[int, int]:
        sd, first, _ = self.span(entry)
        return sd.index, first


def words_of(blocks: list[Block]) -> int:
    return sum(b.word_count for b in blocks)


@dataclass
class Unit:
    """Work that runs in order. Units run side by side."""

    docs: list[SpineDoc]
    long: bool  # one document drafted in several parts

    @property
    def key(self) -> str:
        return self.docs[0].prefix if len(self.docs) == 1 else f"{self.docs[0].prefix}-{self.docs[-1].prefix}"


def plan_units(book: Book, settings: Settings, only: set[int] | None = None,
               skip: set[str] | None = None) -> list[Unit]:
    """The drafting work. ``only`` keeps it to the documents with these spine indexes (a trial of one chapter);
    ``skip`` leaves out documents, by prefix, whose ranges are already there (carried over from a trial)."""
    units: list[Unit] = []
    batch: list[SpineDoc] = []
    total = 0
    for sd in book.spine:
        if not sd.is_xhtml or not sd.blocks or (only is not None and sd.index not in only):
            continue
        if skip and sd.prefix in skip:
            if batch:  # the documents on either side are not drafted as neighbours
                units.append(Unit(batch, False))
                batch, total = [], 0
            continue
        words = words_of(sd.blocks)
        if words > settings.part_words * 1.4:
            if batch:
                units.append(Unit(batch, False))
                batch, total = [], 0
            units.append(Unit([sd], True))
            continue
        if batch and total + words > settings.part_words:
            units.append(Unit(batch, False))
            batch, total = [], 0
        batch.append(sd)
        total += words
    if batch:
        units.append(Unit(batch, False))
    return units


# --------------------------------------------------------------------------
# Mechanical checks of what Claude returns


def question_problems(q: dict[str, Any], inside: set[str], allowed: tuple[str, ...] | None = None) -> list[str]:
    """What is wrong with one question, in words the drafter can act on (empty when nothing is). ``allowed`` are
    the forms a question may take."""
    out = qforms.problems(q, allowed, cp.plain_text_problem)
    for name, limit in (("prompt", cp.PROMPT_MAX_CHARS), ("answer", cp.ANSWER_MAX_CHARS)):
        label = "question" if name == "prompt" else "answer"
        text = q[name]
        if not text or cp.is_blank(text):
            out.append(f"the {label} is empty")
            continue
        if len(text) > limit:
            out.append(f"the {label} has {len(text)} characters; the limit is {limit}")
        problem = cp.plain_text_problem(text)
        if problem:
            out.append(f"the {label} {problem}; write plain text")
    refs = q["supporting_paragraphs"]
    if not refs:
        out.append("supporting_paragraphs is empty; cite the paragraphs the answer rests on")
    outside = [r for r in refs if r not in inside]
    if outside:
        out.append(f"supporting paragraph{'s' if len(outside) > 1 else ''} {', '.join(outside)} "
                   f"{'are' if len(outside) > 1 else 'is'} not inside the question's section")
    return out


def normal_question(raw: Any) -> dict[str, Any]:
    """A question as a model gave it, as it is kept: its texts tidied, and the members of its form."""
    raw = raw if isinstance(raw, dict) else {}
    refs = raw.get("supporting_paragraphs")
    seen: list[str] = []
    for r in refs if isinstance(refs, list) else []:
        if isinstance(r, str) and r.strip() not in seen:
            seen.append(r.strip())
    prompt = clean_text(raw.get("prompt"))
    return {"prompt": prompt, "answer": clean_text(raw.get("answer")), "supporting_paragraphs": seen,
            **qforms.from_model(raw, prompt, lambda text: clean_text(text).replace("\n", " "))}


def _one_line(text: Any) -> str:
    return clean_text(text).replace("\n", " ")


def auto_label(blocks: list[Block]) -> str:
    heading = next((b for b in blocks if b.kind == "heading" and b.words), None)
    words = heading.words if heading else next((b.words for b in blocks if b.words), ["Section"])
    return " ".join(words[:12]) + ("" if heading or len(words) <= 12 else " ...")


def check_part(data: Any, groups: list[tuple[SpineDoc, list[Block]]], may_stop_early: bool,
               settings: Settings, context: set[str] | None = None) -> tuple[list[dict[str, Any]], list[str], list[tuple[int, Any, str]]]:
    """(ranges as stored, structural problems, question problems as (range, question, message)).

    The ranges are complete only when there is no structural problem.

    In a book prepared for the graduated pathway (``pathway``) a section also has ``checks``, its ephemeral
    questions. A problem with the n-th of them (counted from 0) has ``-1 - n`` where a question's number stands,
    and a problem that is no reason to leave a question out has ``None`` there. ``context`` are the paragraphs the
    drafter was shown from before the part.
    """
    raw = data.get("ranges") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return [], ["the reply has no ranges"], []
    pos: dict[str, tuple[int, int]] = {}
    for g, (_, blocks) in enumerate(groups):
        for i, b in enumerate(blocks):
            pos[b.ref] = (g, i)
    structural: list[str] = []
    flaws: list[tuple[int, Any, str]] = []
    out: list[dict[str, Any]] = []
    expected: tuple[int, int] | None = (0, 0)
    before = set(context or ())  # the pathway: the paragraphs shown that come before the range in hand
    local_keys: dict[str, tuple[int, str]] = {}  # a practice question's key in the reply -> (its range, its key in the run)
    for n, r in enumerate(raw):
        if not isinstance(r, dict):
            structural.append(f"range {n + 1} is not an object")
            break
        first_ref, last_ref = str(r.get("first_paragraph", "")).strip(), str(r.get("last_paragraph", "")).strip()
        name = f"range {n + 1} ({first_ref} to {last_ref})"
        f, last = pos.get(first_ref), pos.get(last_ref)
        if f is None or last is None:
            bad = first_ref if f is None else last_ref
            structural.append(f"{name}: {bad!r} is not a paragraph of the text you were asked to cover")
            break
        if expected is None:
            structural.append(f"{name}: the ranges before it already cover all the text")
            break
        if f != expected:
            want = groups[expected[0]][1][expected[1]].ref
            structural.append(f"{name} must start at {want}: ranges are in reading order, each starting at the "
                              "paragraph after the one before it ends, with no gap and no overlap")
            break
        if last[0] != f[0]:
            structural.append(f"{name} crosses from one document into the next; a range lies inside one document")
            break
        if last[1] < f[1]:
            structural.append(f"{name}: last_paragraph comes before first_paragraph")
            break
        sd, blocks = groups[f[0]]
        span = blocks[f[1] : last[1] + 1]
        entry: dict[str, Any] = {"chapter_href": sd.href, "first_paragraph": first_ref, "last_paragraph": last_ref}
        if r.get("kind") == "excluded":
            reason = clean_text(r.get("reason")).replace("\n", " ")
            if exclusion_category(reason) is None:
                structural.append(f"{name}: the reason of an excluded range must start with 'Front matter:', "
                                  "'Back matter:' or 'Editorial matter:' (text of any other kind gets a section)")
            entry.update(kind="excluded", reason=reason)
        else:
            if span[-1].end - span[0].start == 0:
                structural.append(f"{name} has no words; join it to a neighbouring section")
            for block, side in ((span[0], "start"), (span[-1], "end")):
                if block.placement == "none":
                    structural.append(f"{name}: a section cannot {side} at {block.ref} (a <{block.element.local}> "
                                      "that takes no marker); move the boundary by one paragraph")
            label = clean_text(r.get("label")).replace("\n", " ") or auto_label(span)
            inside = {b.ref for b in span}
            given = r.get("prompts") if isinstance(r.get("prompts"), list) else []
            questions = [normal_question(q) for q in given]
            if settings.pathway:
                for k, q in enumerate(questions):
                    q.update(pathway.extras(given[k], _one_line), key=f"{first_ref}#{k + 1}")
            # over the limit are the last ones, and of the graduated pathway the recall questions before a higher-order one
            over = set(sorted(range(len(questions)), key=lambda k: not questions[k].get("reasoning"))[settings.max_questions:])
            for k, q in enumerate(questions):
                if k in over:
                    flaws.append((len(out), k, f"{name} has {len(questions)} questions; at most "
                                               f"{settings.max_questions} are allowed"))
                more: list[str] = []
                if settings.pathway:
                    local = _one_line(given[k].get("key")) if isinstance(given[k], dict) else ""
                    if local in local_keys:
                        more.append(f"the key {local!r} is used by another question; give each practice question its own")
                    elif local:
                        local_keys[local] = (len(out), q["key"])
                    more += pathway.extras_problems(q, before, cp.plain_text_problem)
                for problem in question_problems(q, inside, settings.forms) + more:
                    flaws.append((len(out), k, f"{name}, question {k + 1}: {problem}"))
            entry.update(kind="section", label=label[:MAX_LABEL_CHARS], prompts=questions)
            if settings.pathway:
                entry["checks"] = [pathway.check_from(c, normal_question(c), settings.ephemeral)
                                   for c in (r.get("checks") if isinstance(r.get("checks"), list) else [])]
                for k, c in enumerate(entry["checks"]):
                    if k >= pathway.MAX_CHECKS:
                        flaws.append((len(out), -1 - k, f"{name} has {len(entry['checks'])} ephemeral questions; at most "
                                                        f"{pathway.MAX_CHECKS} are allowed in a section"))
                    for problem in question_problems(c, inside, settings.ephemeral_forms) + pathway.check_problems(c, span, settings.ephemeral):
                        flaws.append((len(out), -1 - k, f"{name}, ephemeral question {k + 1}: {problem}"))
        before.update(b.ref for b in span)
        out.append(entry)
        if last[1] + 1 < len(blocks):
            expected = (last[0], last[1] + 1)
        elif last[0] + 1 < len(groups):
            expected = (last[0] + 1, 0)
        else:
            expected = None
    else:
        if expected is not None:
            g, i = expected
            if not may_stop_early:
                structural.append(f"{groups[g][1][i].ref} to {groups[-1][1][-1].ref} are in no range; every "
                                  "paragraph must be in a section or an excluded range")
            else:
                blocks = groups[0][1]
                done, total = words_of(blocks[:i]), words_of(blocks)
                if done * 2 < total:
                    structural.append(f"you stopped before {blocks[i].ref}, after {done} of {total} words; go on at "
                                      "least to the middle of the text before you stop")
    if settings.pathway and not structural:
        flaws += _links(out, local_keys, settings, {(r, k) for r, k, _ in flaws})
    return out, structural, flaws


def _links(ranges: list[dict[str, Any]], local_keys: dict[str, tuple[int, str]], settings: Settings,
           flawed: set[tuple[int, Any]]) -> list[tuple[int, Any, str]]:
    """The graduated pathway: give each ephemeral question the run's keys of the practice questions it supports, in
    place of the reply's own, and say what is wrong with the links and with the help a higher-order question has.
    ``flawed`` are the questions that have a problem already: one of those may be left out, so it is no help."""
    flaws: list[tuple[int, Any, str]] = []
    supported: set[str] = set()
    for r, entry in enumerate(ranges):
        name = f"range {r + 1} ({entry['first_paragraph']} to {entry['last_paragraph']})"
        for k, c in enumerate(entry.get("checks") or []):
            resolved = []
            for local in c["supports"]:
                target = local_keys.get(local)
                if target is None:
                    flaws.append((r, -1 - k, f"{name}, ephemeral question {k + 1}: supports names {local!r}, which is the "
                                             "key of no practice question in your reply"))
                elif target[0] < r:
                    flaws.append((r, -1 - k, f"{name}, ephemeral question {k + 1}: supports names {local!r}, a practice "
                                             "question of an earlier section; an ephemeral question supports questions "
                                             "of its own section's checkpoint or of a later one"))
                else:
                    resolved.append(target[1])
            c["supports"] = resolved
            if (r, -1 - k) not in flawed and not any(f[:2] == (r, -1 - k) for f in flaws):
                supported.update(resolved)
    need = ("an ephemeral question that supports it, or a cue (cue_prompt and cue_answer)" if settings.ephemeral == "linked"
            else "a cue (cue_prompt and cue_answer)")
    for r, entry in enumerate(ranges):
        for k, q in enumerate(entry.get("prompts") or []):
            if q.get("reasoning") and not q.get("cue") and q.get("key") not in supported:
                flaws.append((r, None, f"range {r + 1} ({entry['first_paragraph']} to {entry['last_paragraph']}), question "
                                       f"{k + 1}: a higher-order question needs something for \"Help me recall\": {need}"))
    return flaws


def fallback_ranges(groups: list[tuple[SpineDoc, list[Block]]], settings: Settings) -> list[dict[str, Any]]:
    """Sections without questions, cut by length alone: what is used when Claude returns no usable division."""
    out = []
    for sd, blocks in groups:
        start = 0
        while start < len(blocks):
            end, words = start, 0
            while end < len(blocks) and (words < settings.max_words or end == start):
                words += blocks[end].word_count
                end += 1
            # do not leave a tail too short to be a section, and never a section without words
            if end < len(blocks) and words_of(blocks[end:]) < settings.min_words // 2:
                end = len(blocks)
            span = blocks[start:end]
            if span[-1].end - span[0].start == 0 and out and out[-1]["chapter_href"] == sd.href:
                out[-1]["last_paragraph"] = span[-1].ref
            else:
                out.append({"chapter_href": sd.href, "first_paragraph": span[0].ref, "last_paragraph": span[-1].ref,
                            "kind": "section", "label": auto_label(span)[:MAX_LABEL_CHARS], "prompts": []})
            start = end
    return out


# --------------------------------------------------------------------------
# State


def _totals() -> dict[str, Any]:
    """The totals of the calls to the models, before the first call: of a run, or of one role in it."""
    return {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0}


def new_state(book: Book, settings: Settings, steer: Steer | None = None,
              only: set[int] | None = None) -> dict[str, Any]:
    return {
        "format": STATE_FORMAT,
        "version": STATE_VERSION,
        "run_id": secrets.token_hex(8),
        "created": now(),
        "updated": now(),
        "tool_version": __version__,
        "instructions_version": INSTRUCTIONS_VERSION,
        "source": {"file": book.source_name, "sha256": book.sha256, "title": book.title or book.source_name,
                   "language": book.language or "und",
                   "words": sum(len(sd.words) for sd in book.spine)},
        "settings": settings.as_json(),
        "steer": steer.as_json() if steer is not None and not steer.empty else None,
        "only": sorted(only) if only is not None else None,  # a trial: the spine indexes it is kept to
        "carried": [],  # documents (by prefix) whose ranges came from a trial
        "stage": "draft",  # draft -> check -> done
        "parts": {},
        "excluded": [],
        "sections": [],
        "questions": [],
        "ephemeral": [],  # the graduated pathway: the questions asked inside the sections (see ``pathway``)
        "next": {"section": 1, "checkpoint": 1},
        "exclusions_reviewed": False,
        "warnings": [],
        "usage": {**_totals(), "by_role": {}, "models": {}},
    }


def load_state(work: Path) -> dict[str, Any] | None:
    path = work / STATE_FILE
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PipelineError(f"{path} is not readable JSON ({exc}); delete the output folder or choose a new one")
    if state.get("format") != STATE_FORMAT or state.get("version") != STATE_VERSION:
        raise PipelineError(f"{path} was not written by this version of the tool; choose a new output folder")
    return state


def count_call(usage: dict[str, Any], role: str, completion: Completion) -> None:
    """Add one call to a run's totals."""
    per = usage["by_role"].setdefault(role, _totals())
    for target in (usage, per):
        target["calls"] += 1
        target["cost_usd"] = round(target["cost_usd"] + completion.cost_usd, 6)
        target["input_tokens"] += completion.input_tokens
        target["output_tokens"] += completion.output_tokens
        target["seconds"] = round(target["seconds"] + completion.seconds, 1)
    if not completion.priced:  # Codex: the tool reports no price, so the total covers the other calls only
        usage["unpriced_calls"] = usage.get("unpriced_calls", 0) + 1
    if completion.model:
        usage["models"][role] = completion.model


def add_usage(usage: dict[str, Any], other: dict[str, Any]) -> None:
    """Add the totals of another run (a trial whose chapter is taken over) to a run's totals."""
    def add(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key in ("calls", "input_tokens", "output_tokens"):
            target[key] = target.get(key, 0) + source.get(key, 0)
        target["cost_usd"] = round(target.get("cost_usd", 0.0) + source.get("cost_usd", 0.0), 6)
        target["seconds"] = round(target.get("seconds", 0.0) + source.get("seconds", 0.0), 1)

    add(usage, other)
    for role, per in other.get("by_role", {}).items():
        add(usage["by_role"].setdefault(role, _totals()), per)
    if other.get("unpriced_calls"):
        usage["unpriced_calls"] = usage.get("unpriced_calls", 0) + other["unpriced_calls"]
    for role, model in other.get("models", {}).items():
        usage["models"].setdefault(role, model)


def steer_key(state: dict[str, Any]) -> str:
    return Steer.from_json(state.get("steer")).key()


def carried_ranges(trial: dict[str, Any], book: Book) -> dict[str, list[dict[str, Any]]]:
    """What a finished trial holds, as ranges a new run can take over: document prefix -> its ranges in reading
    order, each question and section with everything the trial found out about it."""
    prefix = {sd.href: sd.prefix for sd in book.spine}
    out: dict[str, list[dict[str, Any]]] = {}
    by_section: dict[str, list[dict[str, Any]]] = {}
    for q in trial["questions"]:
        by_section.setdefault(q["section"], []).append(q)
    found = ("status", "checks", "criteria", "citations", "evidence", "problem", "suggested_fix", "minor_note", "history")

    def wording(q: dict[str, Any]) -> dict[str, Any]:
        return {"prompt": q["prompt"], "answer": q["answer"], "supporting_paragraphs": q["supporting_paragraphs"],
                **qforms.members(q), "carried": {k: copy.deepcopy(q[k]) for k in found}}

    checks: dict[str, list[dict[str, Any]]] = {}
    for c in pathway.ephemeral(trial):
        checks.setdefault(c["section"], []).append(c)
    for s in trial["sections"]:
        base = {k: s[k] for k in ("chapter_href", "first_paragraph", "last_paragraph")}
        entry = {
            **base, "kind": "section", "label": s["label"],
            "carried": {k: s[k] for k in ("judged", "natural_boundary", "boundary_note")},
            # a question's id in the trial is its key here: the ephemeral questions that support it name it so
            "prompts": [{**wording(q), **pathway.extras_of(q), **({"key": q["id"]} if "reasoning" in q else {})}
                        for q in by_section.get(s["id"], [])]}
        if "ephemeral" in trial:
            entry["checks"] = [{**wording(c), "after": c["after"], "supports": list(c["supports"])} for c in checks.get(s["id"], [])]
        out.setdefault(prefix[s["chapter_href"]], []).append(entry)
    for e in trial["excluded"]:
        base = {k: e[k] for k in ("chapter_href", "first_paragraph", "last_paragraph")}
        out.setdefault(prefix[e["chapter_href"]], []).append({
            **base, "kind": "excluded", "reason": e["reason"],
            "carried": {k: e[k] for k in ("acceptable", "note", "reviewed", "redrafted") if k in e}})
    return out


def kept(q: dict[str, Any]) -> bool:
    return q["status"] in KEPT and not q.get("removed")


def next_checkpoint(state: dict[str, Any]) -> str:
    """A new checkpoint id, for a section that gets its first question."""
    made = f"c{state['next']['checkpoint']:04d}"
    state["next"]["checkpoint"] += 1
    return made


def new_question(qid: str, section_id: str, wording: dict[str, Any], **more: Any) -> dict[str, Any]:
    """A question as the state keeps it from its first moment: its id, its section, its wording, and the members
    a check fills in later. ``more`` sets members to something else, or adds some."""
    return {"id": qid, "section": section_id, **wording, "status": "unchecked", "checks": 0,
            "criteria": {}, "citations": [], "evidence": "", "problem": "", "suggested_fix": "",
            "minor_note": "", "history": [], "removed": False, **more}


def sort_questions(state: dict[str, Any]) -> None:
    """Put the questions in reading order: by their section, then by their number at its checkpoint."""
    position = {s["id"]: n for n, s in enumerate(state["sections"])}
    state["questions"].sort(key=lambda q: (position[q["section"]], int(q["id"].rsplit("-p", 1)[1])))


# --------------------------------------------------------------------------
# The run


class Run:
    def __init__(self, epub: Path, work: Path, settings: Settings, backend: Backend, progress: Progress | None = None,
                 cancel: threading.Event | None = None, steer: Steer | None = None, only: set[int] | None = None,
                 carry: dict[str, Any] | None = None, spent: list[Completion] | None = None):
        """``steer`` is what the reader wants from the book. ``only`` keeps the run to some documents (a trial of
        one chapter). ``carry`` is the finished state of such a trial, whose chapter a new run takes over instead
        of writing it again. ``spent`` are calls made for this run before it started (the reading of the steer)."""
        self.epub, self.work, self.settings, self.backend = epub, work, settings, backend
        self.steer = steer if steer is not None and not steer.empty else None
        self.only, self.carry, self.spent = only, carry, spent or []
        self.progress = progress or (lambda event: None)
        self.cancel = cancel or threading.Event()
        self.lock = threading.RLock()
        self.book = load_book(epub)
        self.index = BookIndex(self.book)
        self.images: dict[str, list[str]] | None = None
        self.read_dir: Path | None = None
        self.state: dict[str, Any] = {}

    # ---- plumbing

    def save(self) -> None:
        with self.lock:
            self.state["updated"] = now()
            write_json(self.work / STATE_FILE, self.state, sync=True)  # on the disk: a power cut must not cost the run

    def warn(self, message: str) -> None:
        with self.lock:
            if message not in self.state["warnings"]:
                self.state["warnings"].append(message)
        self.emit("warning", message)

    def emit(self, stage: str, message: str, done: int | None = None, total: int | None = None) -> None:
        self.progress({"time": now(), "stage": stage, "message": message, "done": done, "total": total})

    def call(self, role: str, system: str, message: str, schema: dict[str, Any]) -> Completion:
        if self.cancel.is_set():
            raise Cancelled()
        model = self.settings.checker_model if role in ("check", "exclusions") else None
        try:
            completion = self.backend.complete(role=role, system=system, prompt=message, schema=schema,
                                               model=model or self.settings.model, read_dir=self.read_dir)
        except BackendCancelled:  # stopped while the call was running or waiting for the network
            raise Cancelled() from None
        with self.lock:
            count_call(self.state["usage"], role, completion)
        return completion

    def every(self) -> list[dict[str, Any]]:
        """Every question of the run: the practice questions, then the ephemeral ones."""
        return self.state["questions"] + pathway.ephemeral(self.state)

    def practice(self) -> dict[str, dict[str, Any]] | None:
        """The practice questions by id, for the tasks that show an ephemeral question with those it supports."""
        return {q["id"]: q for q in self.state["questions"]} if self.settings.pathway else None

    def inside(self, item: dict[str, Any], q: dict[str, Any], citing: bool = True) -> set[str]:
        """The paragraphs a question may rest on: its section's, for an ephemeral question only up to where it is
        asked. With ``citing``, a check's citations: also the earlier paragraphs a practice question draws on."""
        refs = [b.ref for b in item["blocks"]]
        if pathway.is_ephemeral(q):
            return set(refs[: refs.index(q["after"]) + 1] if q["after"] in refs else refs)
        return set(refs) | set(q.get("earlier_paragraphs") or [] if citing else [])

    def context_before(self, sd: SpineDoc, start: int) -> list[tuple[SpineDoc, list[Block]]] | None:
        """The graduated pathway: the text just before the block ``start`` of a document, back through the
        documents before it, up to a number of words. A higher-order question may draw on it."""
        if not self.settings.pathway:
            return None
        budget = min(CONTEXT_WORDS, self.settings.part_words // 3)
        out: list[tuple[SpineDoc, list[Block]]] = []
        for doc in reversed([d for d in self.book.spine if d.is_xhtml and d.blocks and d.index <= sd.index]):
            take: list[Block] = []
            for block in reversed(doc.blocks[:start] if doc is sd else doc.blocks):
                if budget <= 0:
                    break
                take.append(block)
                budget -= block.word_count
            if take:
                out.append((doc, take[::-1]))
            if budget <= 0:
                break
        return out[::-1] or None

    def parallel(self, jobs: list[Callable[[], None]]) -> None:
        """Run the jobs side by side; the first failure stops the rest and is raised."""
        if not jobs:
            return
        stop = threading.Event()
        first: BaseException | None = None

        def guarded(job: Callable[[], None]) -> None:
            if stop.is_set() or self.cancel.is_set():
                return
            job()

        with ThreadPoolExecutor(max_workers=self.settings.jobs) as pool:
            futures = [pool.submit(guarded, job) for job in jobs]
            try:
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc is not None and first is None:
                        first = exc
                        stop.set()
            except BaseException:  # Ctrl-C: give up the calls that are running, or leaving the pool waits for them
                self.cancel.set()
                raise
        if first is not None:
            raise first
        if self.cancel.is_set():
            raise Cancelled()

    # ---- start

    def prepare(self) -> None:
        issues = _preflight(self.book)
        if issues:
            raise PipelineError(f"{self.epub.name} already holds checkpoints (it is a prepared book). Give the "
                                "original EPUB instead.")
        if not any(sd.blocks for sd in self.book.spine):
            raise PipelineError(f"{self.epub.name} has no text to prepare")
        existing = load_state(self.work)
        if existing is not None:
            if existing["source"]["sha256"] != self.book.sha256:
                raise PipelineError(f"{self.work} holds a run of another book ({existing['source']['file']}); "
                                    "choose a new output folder")
            old = Settings.from_json(existing["settings"])
            if old.drafting_key() != self.settings.drafting_key():
                raise PipelineError(
                    f"{self.work} holds a run made with other options ({_differences(old, self.settings)}). Use "
                    "the same options to continue it, or choose a new output folder to start again.")
            if steer_key(existing) != (self.steer.key() if self.steer else ""):
                raise PipelineError(
                    f"{self.work} holds a run made with {'another' if existing.get('steer') else 'no'} statement of "
                    "what you want from the book. Give the same one to continue it, or choose a new output folder "
                    "to start again.")
            if existing.get("only") != (sorted(self.only) if self.only is not None else None):
                raise PipelineError(f"{self.work} holds a run of {'a part of' if existing.get('only') else 'the whole'} "
                                    "book; choose a new output folder")
            self.state = existing
            self.state["settings"] = self.settings.as_json()
            self.emit("start", f"continuing the run in {self.work}")
        else:
            extract(self.epub, self.work)
            self.state = new_state(self.book, self.settings, self.steer, self.only)
            for completion in self.spent:
                count_call(self.state["usage"], "reading", completion)
            self.take_over()
        copy = self.work / SOURCE_COPY  # rebuilding the outputs later needs the book, wherever the original goes
        if not copy.is_file():
            shutil.copyfile(self.epub, copy)
        if self.settings.content == "figures":
            folder = self.work / "images"
            self.images = export_images(self.book, folder)
            self.read_dir = folder if self.images else None
        self.save()

    def take_over(self) -> None:
        """Start from the chapter a trial has already written and checked, when it was made the same way."""
        trial = self.carry
        if not trial or trial.get("stage") != "done" or trial["source"]["sha256"] != self.book.sha256 \
                or Settings.from_json(trial["settings"]).drafting_key() != self.settings.drafting_key() \
                or steer_key(trial) != (self.steer.key() if self.steer else ""):
            return
        for prefix, ranges in carried_ranges(trial, self.book).items():
            self.state["parts"][prefix] = sorted(ranges, key=self.index.order)
            self.state["carried"].append(prefix)
        add_usage(self.state["usage"], trial["usage"])
        self.emit("start", "taking over the chapter you tried: " + ", ".join(self.state["carried"]))

    # ---- draft

    def draft(self) -> None:
        units = plan_units(self.book, self.settings, self.only, set(self.state.get("carried") or []))
        total = sum(words_of(sd.blocks) for u in units for sd in u.docs)
        self.drafted_words = 0
        self.emit("draft", f"drafting {total:,} words in {len(units)} part{'s' if len(units) != 1 else ''}"
                           f"{' or more' if any(u.long for u in units) else ''}", 0, total)

        def advance(blocks: list[Block], label: str) -> None:
            with self.lock:
                self.drafted_words += words_of(blocks)
                done = self.drafted_words
            self.emit("draft", f"drafted {label}", done, total)

        def whole(unit: Unit) -> None:
            groups = [(sd, sd.blocks) for sd in unit.docs]
            if unit.key not in self.state["parts"]:
                ranges = self.draft_part(groups, None, None, unit.key, context=self.context_before(unit.docs[0], 0))
                with self.lock:
                    self.state["parts"][unit.key] = ranges
                self.save()
            advance([b for _, bs in groups for b in bs], unit.key)

        def long(unit: Unit) -> None:
            sd = unit.docs[0]
            start, previous = 0, None
            while start < len(sd.blocks):
                end, words = start, 0
                while end < len(sd.blocks) and words < self.settings.part_words:
                    words += sd.blocks[end].word_count
                    end += 1
                if words_of(sd.blocks[end:]) < self.settings.part_words * 0.4:
                    end = len(sd.blocks)
                window = sd.blocks[start:end]
                key = window[0].ref
                if key not in self.state["parts"]:
                    continues = sd.blocks[-1] if end < len(sd.blocks) else None
                    ranges = self.draft_part([(sd, window)], continues, previous, key, context=self.context_before(sd, start))
                    with self.lock:
                        self.state["parts"][key] = ranges
                    self.save()
                ranges = self.state["parts"][key]
                stop = self.index.where[ranges[-1]["last_paragraph"]][1] + 1
                advance(sd.blocks[start:stop], f"{window[0].ref} to {ranges[-1]['last_paragraph']}")
                previous = ranges[-1].get("label") or ranges[-1].get("reason")
                start = stop

        self.parallel([(lambda u=u: long(u) if u.long else whole(u)) for u in units])
        self.assemble()

    def draft_part(self, groups: list[tuple[SpineDoc, list[Block]]], continues: Block | None, previous: str | None,
                   key: str, feedback: str | None = None,
                   context: list[tuple[SpineDoc, list[Block]]] | None = None) -> list[dict[str, Any]]:
        system = prompts.drafter_system(self.settings, self.steer)
        ranges: list[dict[str, Any]] = []
        structural: list[str] = []
        flaws: list[tuple[int, Any, str]] = []
        shown = {b.ref for _, blocks in context or [] for b in blocks}
        for _ in range(DRAFT_ATTEMPTS):
            message = prompts.draft_message(self.book, groups, self.settings, self.images, continues, previous,
                                            feedback, self.steer, context)
            reply = self.call("draft", system, message, prompts.draft_schema(self.settings)).data
            ranges, structural, flaws = check_part(reply, groups, continues is not None, self.settings, shown)
            # a problem that is no reason to leave a question out is no reason to draft the part again either
            if not structural and all(k is None for _, k, _ in flaws):
                return ranges
            feedback = "\n".join(f"- {p}" for p in structural + [m for _, _, m in flaws])
            feedback += "\n\nYour earlier reply:\n" + json.dumps(reply, ensure_ascii=False)[:60000]
        if structural:
            self.warn(f"Part {key}: the model returned no usable division after {DRAFT_ATTEMPTS} attempts "
                      f"({structural[0]}). It was cut into sections by length, without questions.")
            return fallback_ranges(groups, self.settings)
        drop = {(r, k) for r, k, _ in flaws if k is not None}
        if not drop:  # what is left is no reason to leave a question out
            return ranges
        flaws = [f for f in flaws if f[1] is not None]
        gone: set[str] = set()
        for r, entry in enumerate(ranges):
            if entry["kind"] == "section":
                gone.update(q["key"] for k, q in enumerate(entry["prompts"]) if (r, k) in drop and q.get("key"))
                entry["prompts"] = [q for k, q in enumerate(entry["prompts"]) if (r, k) not in drop]
                if "checks" in entry:
                    entry["checks"] = [c for k, c in enumerate(entry["checks"]) if (r, -1 - k) not in drop]
        for entry in ranges:  # an ephemeral question whose practice questions are all gone has nothing to build toward
            for c in entry.get("checks") or []:
                c["supports"] = [k for k in c["supports"] if k not in gone]
            if self.settings.ephemeral == "linked" and "checks" in entry:
                entry["checks"] = [c for c in entry["checks"] if c["supports"]]
        self.warn(f"Part {key}: {len(drop)} question{'s' if len(drop) != 1 else ''} left out because the drafter did "
                  f"not correct {'them' if len(drop) != 1 else 'it'} ({flaws[0][2]}).")
        return ranges

    def add_ranges(self, ranges: list[dict[str, Any]], reviewed: bool = False) -> None:
        """Give ids to new sections and questions and add them to the state, in reading order."""
        state = self.state
        keys: dict[str, str] = {}  # the pathway: a practice question's key -> its id
        checks: list[dict[str, Any]] = []
        for entry in sorted(ranges, key=self.index.order):
            base = {k: entry[k] for k in ("chapter_href", "first_paragraph", "last_paragraph")}
            carried = entry.get("carried") or {}  # what a trial already found out
            if entry["kind"] == "excluded":
                state["excluded"].append({**base, "reason": entry["reason"], "acceptable": True if reviewed else None,
                                          "note": "", "reviewed": reviewed, **carried})
                continue
            sid = f"s{state['next']['section']:04d}"
            state["next"]["section"] += 1
            section = {"id": sid, **base, "label": entry["label"], "checkpoint": None, "judged": False,
                       "natural_boundary": None, "boundary_note": "", **carried}
            if entry["prompts"]:
                section["checkpoint"] = next_checkpoint(state)
            state["sections"].append(section)
            # a checkpoint opens with its higher-order questions
            for n, q in enumerate(sorted(entry["prompts"], key=lambda q: not q.get("reasoning")), start=1):
                found = q.get("carried") or {}
                qid = f"{section['checkpoint']}-p{n}"
                if q.get("key"):
                    keys[q["key"]] = qid
                state["questions"].append(new_question(qid, sid, {k: v for k, v in q.items() if k not in ("carried", "key")},
                                                       **copy.deepcopy(found)))
            for n, c in enumerate(entry.get("checks") or [], start=1):
                found = c.get("carried") or {}
                checks.append(new_question(f"{sid}-e{n}", sid, {k: v for k, v in c.items() if k != "carried"},
                                           promoted=None, **copy.deepcopy(found)))
        for c in checks:
            c["supports"] = [keys[k] for k in c["supports"] if k in keys]
            c["purpose"] = "linked" if c["supports"] else "attention"
        state["sections"].sort(key=self.index.order)
        state["excluded"].sort(key=self.index.order)
        sort_questions(state)
        if checks or "ephemeral" in state:
            position = {s["id"]: n for n, s in enumerate(state["sections"])}
            state["ephemeral"] = sorted(pathway.ephemeral(state) + checks, key=lambda c: (
                position[c["section"]], self.index.where[c["after"]][1], int(c["id"].rsplit("-e", 1)[1])))

    def assemble(self) -> None:
        with self.lock:
            self.add_ranges([r for ranges in self.state["parts"].values() for r in ranges])
            self.state["stage"] = "check"
        issues = [] if self.only is not None else draft_issues(self.book, self.state)
        if issues:
            raise PipelineError("the assembled draft does not fit the book (a fault of this tool, not of the book):\n"
                                + "\n".join(f"  {i}" for i in issues[:20]))
        self.save()
        inside = len(pathway.ephemeral(self.state))
        self.emit("draft", f"drafted {len(self.state['sections'])} sections and {len(self.state['questions'])} "
                           "questions" + (f", with {inside} ephemeral question{'s' if inside != 1 else ''} inside the sections"
                                          if self.settings.pathway else ""))

    # ---- check and revise

    def section_item(self, section: dict[str, Any]) -> dict[str, Any]:
        sd, first, last = self.index.span(section)
        return {"doc": sd, "section": section, "blocks": sd.blocks[first : last + 1], "first": first, "last": last}

    def batches(self, items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        out: list[list[dict[str, Any]]] = []
        size = 0
        for item in items:
            words = words_of(item["blocks"])
            if not out or size + words > self.settings.part_words:
                out.append([])
                size = 0
            out[-1].append(item)
            size += words
        return out

    def check_loop(self) -> None:
        if self.settings.rounds == 0:
            self.emit("check", "checking is switched off; every question is kept unchecked")
            return
        round_number = 0
        while True:
            round_number += 1
            jobs: list[Callable[[], None]] = []
            if not self.state["exclusions_reviewed"] and self.state["excluded"]:
                jobs.append(self.review_exclusions)
            by_section = self.questions_by_section(lambda q: q["status"] == "unchecked")
            items = []
            for section in self.state["sections"]:
                questions = by_section.get(section["id"], [])
                if questions or not section["judged"]:
                    items.append({**self.section_item(section), "questions": questions,
                                  "judge_break": not section["judged"]})
            total = sum(len(i["questions"]) for i in items)
            self.checked = 0
            if items:
                self.emit("check", f"check {round_number}: {total} question{'s' if total != 1 else ''} in "
                                   f"{len(items)} section{'s' if len(items) != 1 else ''}", 0, total)
            jobs += [(lambda b=b: self.check_batch(b, total)) for b in self.batches(items)]
            self.parallel(jobs)
            if not self.state["exclusions_reviewed"]:
                with self.lock:
                    self.state["exclusions_reviewed"] = True
                self.save()
            self.redraft_rejected_exclusions()
            failed = self.questions_by_section(lambda q: q["status"] == "failed" and q["checks"] < self.settings.rounds)
            if failed:
                items = [{**self.section_item(s), "failed": failed[s["id"]],
                          "others": [q for q in self.every()
                                     if q["section"] == s["id"] and q not in failed[s["id"]] and kept(q)]}
                         for s in self.state["sections"] if s["id"] in failed]
                count = sum(len(i["failed"]) for i in items)
                self.emit("revise", f"revising {count} question{'s' if count != 1 else ''} the check failed", 0, count)
                self.parallel([(lambda b=b: self.revise_batch(b)) for b in self.batches(items)])
            if not any(q["status"] == "unchecked" for q in self.every()):
                break

    def questions_by_section(self, want: Callable[[dict[str, Any]], bool]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for q in self.every():
            if want(q):
                out.setdefault(q["section"], []).append(q)
        return out

    def check_batch(self, items: list[dict[str, Any]], total: int) -> None:
        system = prompts.checker_system(self.settings, self.steer)
        wanted = {q["id"]: (q, self.inside(item, q)) for item in items for q in item["questions"]}
        judge = {item["section"]["id"]: item["section"] for item in items if item["judge_break"]}
        feedback = None
        usable: dict[str, dict[str, Any]] = {}
        sections: dict[str, dict[str, Any]] = {}
        for _ in range(2):
            reply = self.call("check", system, prompts.check_message(self.book, items, self.settings, self.images,
                                                                     feedback, self.practice()), prompts.check_schema(self.settings)).data
            usable, sections, problems = read_check(reply, wanted, judge, prompts.criteria(self.settings))
            if not problems:
                break
            feedback = "\n".join(f"- {p}" for p in problems)
        with self.lock:
            for qid, (q, _) in wanted.items():
                entry = usable.get(qid)
                q["checks"] += 1
                if entry is None:
                    q["status"] = "unverified"
                    q["problem"] = "The checker's result for this question could not be used, so it is unchecked."
                    continue
                q["criteria"] = {c: entry[c] for c in prompts.criteria(self.settings)}
                q.update(citations=entry["citations"], evidence=entry["evidence"], problem=entry["problem"],
                         suggested_fix=entry["suggested_fix"], minor_note=entry["minor_note"])
                q["status"] = "passed" if all(q["criteria"].values()) else "failed"
            for sid, section in judge.items():
                entry = sections.get(sid)
                section["judged"] = True
                if entry is not None:
                    section["natural_boundary"] = entry["natural_boundary"]
                    section["boundary_note"] = entry["evidence"]
            self.checked += len(wanted)
            done = self.checked
        self.save()
        self.emit("check", f"checked {done} of {total} questions", done, total)

    def revise_batch(self, items: list[dict[str, Any]]) -> None:
        system = prompts.drafter_system(self.settings, self.steer)
        wanted = {q["id"]: (q, self.inside(item, q, citing=False)) for item in items for q in item["failed"]}
        feedback = None
        good: dict[str, dict[str, Any]] = {}
        for _ in range(2):
            reply = self.call("revise", system, prompts.revise_message(self.book, items, self.settings, self.images,
                                                                       feedback, self.practice()), prompts.revise_schema(self.settings)).data
            good, problems = read_revisions(reply, wanted, self.settings.forms, self.settings.pathway, self.settings.ephemeral_forms)
            if not problems:
                break
            feedback = "\n".join(f"- {p}" for p in problems)
        with self.lock:
            for qid, (q, _) in wanted.items():
                entry = good.get(qid)
                if entry is None:  # no usable revision: the question stays failed, for good
                    q["checks"] = self.settings.rounds
                    continue
                q["history"].append({"check": q["checks"], "prompt": q["prompt"], "answer": q["answer"],
                                     **qforms.members(q),
                                     "failed": [c for c, ok in q["criteria"].items() if not ok],
                                     "problem": q["problem"]})
                if entry["action"] == "withdraw":
                    q["status"] = "withdrawn"
                    continue
                for member in qforms.MEMBERS:  # the revision may be of another form
                    q.pop(member, None)
                q.update({k: v for k, v in entry.items() if k != "action"}, status="unchecked", criteria={},
                         citations=[], evidence="", problem="", suggested_fix="", minor_note="")
        self.save()

    def review_exclusions(self) -> None:
        pending = [e for e in self.state["excluded"] if not e["reviewed"]]
        if not pending:
            return
        ranges = []
        for e in pending:
            sd, first, last = self.index.span(e)
            ranges.append((sd, sd.blocks[first : last + 1], e["reason"]))
        reply = self.call("exclusions", prompts.EXCLUSION_SYSTEM, prompts.exclusion_message(self.book, ranges),
                          prompts.EXCLUSION_SCHEMA).data
        entries = reply.get("exclusions") if isinstance(reply, dict) else None
        with self.lock:
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or not isinstance(entry.get("index"), int):
                    continue
                if 0 <= entry["index"] < len(pending) and isinstance(entry.get("acceptable"), bool):
                    pending[entry["index"]].update(acceptable=entry["acceptable"],
                                                   note=clean_text(entry.get("evidence")))
            for e in pending:
                e["reviewed"] = True
        self.save()

    def redraft_rejected_exclusions(self) -> None:
        rejected = [e for e in self.state["excluded"] if e["acceptable"] is False and not e.get("redrafted")]
        for e in rejected:
            sd, first, last = self.index.span(e)
            blocks = sd.blocks[first : last + 1]
            self.emit("draft", f"the check found body text in the excluded range {e['first_paragraph']} to "
                               f"{e['last_paragraph']}; dividing it into sections")
            feedback = (f"- An earlier draft excluded all of this text with the reason \"{e['reason']}\". A reviewer "
                        f"judged that wrong: {e['note'] or 'it holds text that belongs in sections'} Divide it "
                        "again. Exclude only what truly is front, back or editorial matter.")
            ranges = self.draft_part([(sd, blocks)], None, None, f"{e['first_paragraph']} (excluded before)", feedback)
            with self.lock:
                if all(r["kind"] == "excluded" for r in ranges):
                    e["redrafted"] = True  # the drafter stands by it: keep the exclusion, and say so in the report
                    self.warn(f"{e['first_paragraph']} to {e['last_paragraph']} stays excluded ({e['reason']}), "
                              f"although the check doubted it: {e['note']}")
                else:
                    self.state["excluded"].remove(e)
                    self.add_ranges(ranges, reviewed=True)
            self.save()

    # ---- whole run

    def run(self) -> dict[str, Any]:
        self.prepare()
        if self.state["stage"] == "draft":
            self.draft()
        if self.state["stage"] == "check":
            self.check_loop()
            with self.lock:
                self.state["stage"] = "done"
            self.save()
        return self.state


def _differences(old: Settings, new: Settings) -> str:
    a, b = old.drafting_key(), new.drafting_key()  # a key holds the forms of the ephemeral questions only where there are any
    return ", ".join(f"{k}: {a.get(k, 'not used')} then, {b.get(k, 'not used')} now" for k in dict.fromkeys([*a, *b]) if a.get(k) != b.get(k))


def read_check(reply: Any, wanted: dict[str, tuple[dict[str, Any], set[str]]], judge: dict[str, dict[str, Any]],
               criteria: tuple[str, ...] = prompts.CRITERIA) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """(usable question verdicts, section verdicts, problems to send back)."""
    problems: list[str] = []
    usable: dict[str, dict[str, Any]] = {}
    entries = reply.get("prompts") if isinstance(reply, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("id") not in wanted:
            continue
        qid = entry["id"]
        if qid in usable:
            problems.append(f"{qid} has more than one entry")
            continue
        if not all(isinstance(entry.get(c), bool) for c in criteria):
            problems.append(f"{qid}: each of the {'five' if len(criteria) == 5 else len(criteria)} criteria must be true or false")
            continue
        inside = wanted[qid][1]
        citations = [c.strip() for c in entry.get("citations") or [] if isinstance(c, str)]
        outside = [c for c in citations if c not in inside]
        if outside:
            problems.append(f"{qid}: citation {', '.join(outside)} is not a paragraph of the question's own section; "
                            "cite only paragraphs of that section (name others in the evidence)")
            continue
        passed = all(entry[c] for c in criteria)
        if passed and not citations:
            problems.append(f"{qid} passes all criteria but cites no paragraph; a passing question needs a citation")
            continue
        usable[qid] = {**{c: entry[c] for c in criteria}, "citations": citations,
                       "evidence": clean_text(entry.get("evidence")),
                       "problem": "" if passed else clean_text(entry.get("problem")) or "The checker gave no reason.",
                       "suggested_fix": "" if passed else clean_text(entry.get("suggested_fix")),
                       "minor_note": clean_text(entry.get("minor_note")) if passed else ""}
    for qid in wanted:
        if qid not in usable and not any(p.startswith(qid) for p in problems):
            problems.append(f"{qid} has no entry; judge every question listed")
    sections: dict[str, dict[str, Any]] = {}
    entries = reply.get("sections") if isinstance(reply, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("id") in judge and isinstance(entry.get("natural_boundary"), bool):
            sections[entry["id"]] = {"natural_boundary": entry["natural_boundary"],
                                     "evidence": clean_text(entry.get("evidence"))}
    for sid in judge:
        if sid not in sections:
            problems.append(f"section {sid} has no entry in 'sections'; judge its break")
    return usable, sections, problems


def read_revisions(reply: Any, wanted: dict[str, tuple[dict[str, Any], set[str]]],
                   allowed: tuple[str, ...] | None = None, with_pathway: bool = False,
                   ephemeral_allowed: tuple[str, ...] | None = None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """(usable revisions, problems to send back). ``with_pathway``: the book is prepared for the graduated pathway,
    so a practice question's revision carries the pathway's members, and an ephemeral question's stays brief.
    ``allowed`` are the forms a practice question may take, ``ephemeral_allowed`` those of an ephemeral question
    (the same as ``allowed`` when not given)."""
    problems: list[str] = []
    good: dict[str, dict[str, Any]] = {}
    entries = reply.get("revisions") if isinstance(reply, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("id") not in wanted or entry["id"] in good:
            continue
        qid = entry["id"]
        if entry.get("action") == "withdraw":
            good[qid] = {"action": "withdraw"}
            continue
        q = normal_question(entry)
        old = wanted[qid][0]
        ephemeral = with_pathway and pathway.is_ephemeral(old)
        flaws = question_problems(q, wanted[qid][1], (ephemeral_allowed or allowed) if ephemeral else allowed)
        if ephemeral:
            flaws += pathway.brief_problems(q)
        elif with_pathway:  # of the earlier paragraphs, a revision can keep those the question had: it was shown no others
            q.update(pathway.extras(entry, _one_line))
            flaws += pathway.extras_problems(q, set(old.get("earlier_paragraphs") or []), cp.plain_text_problem)
        if flaws:
            problems += [f"{qid}: {f}" for f in flaws]
            continue
        if q["prompt"] == old["prompt"] and q["answer"] == old["answer"] and qforms.members(q) == qforms.members(old) \
                and (pathway.is_ephemeral(old) or pathway.extras_of(q) == pathway.extras_of(old)):
            problems.append(f"{qid}: the revision is identical to the question that failed; change what the "
                            "finding flags, or withdraw the question")
            continue
        good[qid] = {"action": "revise", **q}
    for qid in wanted:
        if qid not in good and not any(p.startswith(qid) for p in problems):
            problems.append(f"{qid} has no entry; revise or withdraw every question listed")
    return good, problems


# --------------------------------------------------------------------------
# From the state to a draft the engine can build


def draft_prompt(q: dict[str, Any]) -> dict[str, Any]:
    """A question as the engine that builds the firmware-format book takes it: as a plain question and answer,
    which is all that a firmware that knows only open questions shows, with its form beside them in ``x_form``."""
    prompt, answer = qforms.as_text(q)
    out = {"id": q["id"], "prompt": prompt, "answer": answer, "supporting_paragraphs": q["supporting_paragraphs"]}
    x = qforms.to_x(q)
    if x is not None:
        out["x_form"] = x
    return out


def draft_document(state: dict[str, Any]) -> dict[str, Any]:
    """The draft (``prep/schema/draft.schema.json``) holding the kept questions."""
    by_section: dict[str, list[dict[str, Any]]] = {}
    for q in state["questions"]:
        if kept(q):
            by_section.setdefault(q["section"], []).append(q)
    checkpoints = []
    for section in state["sections"]:
        questions = by_section.get(section["id"])
        if questions:
            checkpoints.append({"id": section["checkpoint"], "section": section["id"], "prompts": [
                draft_prompt(q) for q in questions]})
    source = state["source"]
    return {
        "format": "rsvp-draft",
        "version": 1,
        "book": {"title": source["title"][:300], "language": source["language"][:35],
                 "source_sha256": source["sha256"], "instructions_version": state["instructions_version"]},
        "excluded": [{k: e[k] for k in ("chapter_href", "first_paragraph", "last_paragraph", "reason")}
                     for e in state["excluded"]],
        "sections": [{k: s[k] for k in ("id", "chapter_href", "first_paragraph", "last_paragraph", "label")}
                     for s in state["sections"]],
        "checkpoints": checkpoints,
        "x_generator": {"tool": "ebook-processor", "version": state["tool_version"], "run_id": state["run_id"]},
    }


def draft_bytes(state: dict[str, Any]) -> bytes:
    return (json.dumps(draft_document(state), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def draft_issues(book: Book, state: dict[str, Any]) -> list[str]:
    _, issues, _ = check_draft(draft_bytes(state), book)
    return [str(i) for i in issues]


def process(epub: Path, work: Path, settings: Settings, backend: Backend, progress: Progress | None = None,
            cancel: threading.Event | None = None, steer: Steer | None = None, only: set[int] | None = None,
            carry: dict[str, Any] | None = None, spent: list[Completion] | None = None) -> tuple[Book, dict[str, Any]]:
    """Run (or continue) the models' part of a run. Returns the book and the final state."""
    try:
        run = Run(epub, work, settings, backend, progress, cancel, steer, only, carry, spent)
    except EpubError as exc:
        raise PipelineError(str(exc)) from exc
    return run.book, run.run()
