"""The run: draft the book in parts, check every question in a fresh session, revise, and keep what passes.

Everything a run decides is kept in one file, ``state.json`` in the work folder, and written after every call to
Claude. A run that stops (the usage limit, a closed laptop, Ctrl-C) therefore continues where it was when the same
command is given again, and the outputs can be rebuilt from the state at any time without calling Claude.

Steps:

1. **Draft.** The book is cut into parts of about ``part_words`` words: several short documents together, or a
   stretch of a long one. For each part Claude returns sections, excluded ranges and questions. The reply is
   checked mechanically (every paragraph in exactly one range, limits, citations inside the section) and sent back
   with the list of problems when it fails. Parts of different documents run side by side.
2. **Check.** A separate call, which sees only the section text and the questions, judges each question on five
   criteria and each section's break. One more call reviews what was excluded.
3. **Revise.** Failed questions go back to the drafter with the checker's finding, then to the checker again, up
   to ``rounds`` checks per question. A question that still fails is left out of the outputs and listed in the
   report.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__, prompts
from .content import export_images
from .llm import Backend, Completion
from .prep import checkpoints as cp
from .prep.build import _preflight
from .prep.draft import check_draft
from .prep.epub import Block, Book, EpubError, SpineDoc, load_book
from .prep.extract import extract
from .prep.limits import MAX_LABEL_CHARS
from .prep.validate import exclusion_category
from .settings import INSTRUCTIONS_VERSION, Settings

STATE_FORMAT = "ebook-processor-state"
STATE_VERSION = 1
STATE_FILE = "state.json"
SOURCE_COPY = "source.epub"
DRAFT_ATTEMPTS = 3  # calls for one part before the fallback
KEPT = ("passed", "unchecked", "unverified")  # question statuses that go into the outputs

Progress = Callable[[dict[str, Any]], None]


class PipelineError(Exception):
    """The run cannot go on; the message says why."""


class Cancelled(Exception):
    """The run was stopped on request; its state is saved."""


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, data: Any) -> None:
    """Write through a temporary file, so that a stop in the middle never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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


def plan_units(book: Book, settings: Settings) -> list[Unit]:
    units: list[Unit] = []
    batch: list[SpineDoc] = []
    total = 0
    for sd in book.spine:
        if not sd.is_xhtml or not sd.blocks:
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


def question_problems(q: dict[str, Any], inside: set[str]) -> list[str]:
    """What is wrong with one question, in words the drafter can act on (empty when nothing is)."""
    out = []
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
    raw = raw if isinstance(raw, dict) else {}
    refs = raw.get("supporting_paragraphs")
    seen: list[str] = []
    for r in refs if isinstance(refs, list) else []:
        if isinstance(r, str) and r.strip() not in seen:
            seen.append(r.strip())
    return {"prompt": clean_text(raw.get("prompt")), "answer": clean_text(raw.get("answer")),
            "supporting_paragraphs": seen}


def auto_label(blocks: list[Block]) -> str:
    heading = next((b for b in blocks if b.kind == "heading" and b.words), None)
    words = heading.words if heading else next((b.words for b in blocks if b.words), ["Section"])
    return " ".join(words[:12]) + ("" if heading or len(words) <= 12 else " ...")


def check_part(data: Any, groups: list[tuple[SpineDoc, list[Block]]], may_stop_early: bool,
               settings: Settings) -> tuple[list[dict[str, Any]], list[str], list[tuple[int, int, str]]]:
    """(ranges as stored, structural problems, question problems as (range, question, message)).

    The ranges are complete only when there is no structural problem.
    """
    raw = data.get("ranges") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return [], ["the reply has no ranges"], []
    pos: dict[str, tuple[int, int]] = {}
    for g, (_, blocks) in enumerate(groups):
        for i, b in enumerate(blocks):
            pos[b.ref] = (g, i)
    structural: list[str] = []
    flaws: list[tuple[int, int, str]] = []
    out: list[dict[str, Any]] = []
    expected: tuple[int, int] | None = (0, 0)
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
            questions = [normal_question(q) for q in (r.get("prompts") if isinstance(r.get("prompts"), list) else [])]
            for k, q in enumerate(questions):
                if k >= settings.max_questions:
                    flaws.append((len(out), k, f"{name} has {len(questions)} questions; at most "
                                               f"{settings.max_questions} are allowed"))
                for problem in question_problems(q, inside):
                    flaws.append((len(out), k, f"{name}, question {k + 1}: {problem}"))
            entry.update(kind="section", label=label[:MAX_LABEL_CHARS], prompts=questions)
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
    return out, structural, flaws


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


def new_state(book: Book, settings: Settings) -> dict[str, Any]:
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
        "stage": "draft",  # draft -> check -> done
        "parts": {},
        "excluded": [],
        "sections": [],
        "questions": [],
        "next": {"section": 1, "checkpoint": 1},
        "exclusions_reviewed": False,
        "warnings": [],
        "usage": {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0, "by_role": {},
                  "models": {}},
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


def kept(q: dict[str, Any]) -> bool:
    return q["status"] in KEPT and not q.get("removed")


# --------------------------------------------------------------------------
# The run


class Run:
    def __init__(self, epub: Path, work: Path, settings: Settings, backend: Backend, progress: Progress | None = None,
                 cancel: threading.Event | None = None):
        self.epub, self.work, self.settings, self.backend = epub, work, settings, backend
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
            write_json(self.work / STATE_FILE, self.state)

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
        completion = self.backend.complete(role=role, system=system, prompt=message, schema=schema,
                                           model=model or self.settings.model, read_dir=self.read_dir)
        with self.lock:
            usage = self.state["usage"]
            per = usage["by_role"].setdefault(role, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0,
                                                     "output_tokens": 0, "seconds": 0.0})
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
        return completion

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
            for future in as_completed(futures):
                exc = future.exception()
                if exc is not None and first is None:
                    first = exc
                    stop.set()
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
            self.state = existing
            self.state["settings"] = self.settings.as_json()
            self.emit("start", f"continuing the run in {self.work}")
        else:
            extract(self.epub, self.work)
            self.state = new_state(self.book, self.settings)
        copy = self.work / SOURCE_COPY  # rebuilding the outputs later needs the book, wherever the original goes
        if not copy.is_file():
            shutil.copyfile(self.epub, copy)
        if self.settings.content == "figures":
            folder = self.work / "images"
            self.images = export_images(self.book, folder)
            self.read_dir = folder if self.images else None
        self.save()

    # ---- draft

    def draft(self) -> None:
        units = plan_units(self.book, self.settings)
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
                ranges = self.draft_part(groups, None, None, unit.key)
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
                    ranges = self.draft_part([(sd, window)], continues, previous, key)
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
                   key: str, feedback: str | None = None) -> list[dict[str, Any]]:
        system = prompts.drafter_system(self.settings)
        ranges: list[dict[str, Any]] = []
        structural: list[str] = []
        flaws: list[tuple[int, int, str]] = []
        for _ in range(DRAFT_ATTEMPTS):
            message = prompts.draft_message(self.book, groups, self.settings, self.images, continues, previous,
                                            feedback)
            reply = self.call("draft", system, message, prompts.DRAFT_SCHEMA).data
            ranges, structural, flaws = check_part(reply, groups, continues is not None, self.settings)
            if not structural and not flaws:
                return ranges
            feedback = "\n".join(f"- {p}" for p in structural + [m for _, _, m in flaws])
            feedback += "\n\nYour earlier reply:\n" + json.dumps(reply, ensure_ascii=False)[:60000]
        if structural:
            self.warn(f"Part {key}: the model returned no usable division after {DRAFT_ATTEMPTS} attempts "
                      f"({structural[0]}). It was cut into sections by length, without questions.")
            return fallback_ranges(groups, self.settings)
        drop = {(r, k) for r, k, _ in flaws}
        for r, entry in enumerate(ranges):
            if entry["kind"] == "section":
                entry["prompts"] = [q for k, q in enumerate(entry["prompts"]) if (r, k) not in drop]
        self.warn(f"Part {key}: {len(drop)} question{'s' if len(drop) != 1 else ''} left out because the drafter did "
                  f"not correct {'them' if len(drop) != 1 else 'it'} ({flaws[0][2]}).")
        return ranges

    def add_ranges(self, ranges: list[dict[str, Any]], reviewed: bool = False) -> None:
        """Give ids to new sections and questions and add them to the state, in reading order."""
        state = self.state
        for entry in sorted(ranges, key=self.index.order):
            base = {k: entry[k] for k in ("chapter_href", "first_paragraph", "last_paragraph")}
            if entry["kind"] == "excluded":
                state["excluded"].append({**base, "reason": entry["reason"], "acceptable": True if reviewed else None,
                                          "note": "", "reviewed": reviewed})
                continue
            sid = f"s{state['next']['section']:04d}"
            state["next"]["section"] += 1
            section = {"id": sid, **base, "label": entry["label"], "checkpoint": None, "judged": False,
                       "natural_boundary": None, "boundary_note": ""}
            if entry["prompts"]:
                section["checkpoint"] = f"c{state['next']['checkpoint']:04d}"
                state["next"]["checkpoint"] += 1
            state["sections"].append(section)
            for n, q in enumerate(entry["prompts"], start=1):
                state["questions"].append({
                    "id": f"{section['checkpoint']}-p{n}", "section": sid, **q, "status": "unchecked", "checks": 0,
                    "criteria": {}, "citations": [], "evidence": "", "problem": "", "suggested_fix": "",
                    "minor_note": "", "history": [], "removed": False})
        state["sections"].sort(key=self.index.order)
        state["excluded"].sort(key=self.index.order)
        position = {s["id"]: n for n, s in enumerate(state["sections"])}
        state["questions"].sort(key=lambda q: (position[q["section"]], int(q["id"].rsplit("-p", 1)[1])))

    def assemble(self) -> None:
        with self.lock:
            self.add_ranges([r for ranges in self.state["parts"].values() for r in ranges])
            self.state["stage"] = "check"
        issues = draft_issues(self.book, self.state)
        if issues:
            raise PipelineError("the assembled draft does not fit the book (a fault of this tool, not of the book):\n"
                                + "\n".join(f"  {i}" for i in issues[:20]))
        self.save()
        self.emit("draft", f"drafted {len(self.state['sections'])} sections and {len(self.state['questions'])} "
                           "questions")

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
                          "others": [q for q in self.state["questions"]
                                     if q["section"] == s["id"] and q not in failed[s["id"]] and kept(q)]}
                         for s in self.state["sections"] if s["id"] in failed]
                count = sum(len(i["failed"]) for i in items)
                self.emit("revise", f"revising {count} question{'s' if count != 1 else ''} the check failed", 0, count)
                self.parallel([(lambda b=b: self.revise_batch(b)) for b in self.batches(items)])
            if not any(q["status"] == "unchecked" for q in self.state["questions"]):
                break

    def questions_by_section(self, want: Callable[[dict[str, Any]], bool]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for q in self.state["questions"]:
            if want(q):
                out.setdefault(q["section"], []).append(q)
        return out

    def check_batch(self, items: list[dict[str, Any]], total: int) -> None:
        system = prompts.checker_system(self.settings)
        wanted = {q["id"]: (q, {b.ref for b in item["blocks"]}) for item in items for q in item["questions"]}
        judge = {item["section"]["id"]: item["section"] for item in items if item["judge_break"]}
        feedback = None
        usable: dict[str, dict[str, Any]] = {}
        sections: dict[str, dict[str, Any]] = {}
        for _ in range(2):
            reply = self.call("check", system, prompts.check_message(self.book, items, self.settings, self.images,
                                                                     feedback), prompts.CHECK_SCHEMA).data
            usable, sections, problems = read_check(reply, wanted, judge)
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
                q["criteria"] = {c: entry[c] for c in prompts.CRITERIA}
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
        system = prompts.drafter_system(self.settings)
        wanted = {q["id"]: (q, {b.ref for b in item["blocks"]}) for item in items for q in item["failed"]}
        feedback = None
        good: dict[str, dict[str, Any]] = {}
        for _ in range(2):
            reply = self.call("revise", system, prompts.revise_message(self.book, items, self.settings, self.images,
                                                                       feedback), prompts.REVISE_SCHEMA).data
            good, problems = read_revisions(reply, wanted)
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
                                     "failed": [c for c, ok in q["criteria"].items() if not ok],
                                     "problem": q["problem"]})
                if entry["action"] == "withdraw":
                    q["status"] = "withdrawn"
                    continue
                q.update(prompt=entry["prompt"], answer=entry["answer"],
                         supporting_paragraphs=entry["supporting_paragraphs"], status="unchecked", criteria={},
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
    a, b = old.drafting_key(), new.drafting_key()
    return ", ".join(f"{k}: {a[k]} then, {b[k]} now" for k in a if a[k] != b[k])


def read_check(reply: Any, wanted: dict[str, tuple[dict[str, Any], set[str]]],
               judge: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
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
        if not all(isinstance(entry.get(c), bool) for c in prompts.CRITERIA):
            problems.append(f"{qid}: each of the five criteria must be true or false")
            continue
        inside = wanted[qid][1]
        citations = [c.strip() for c in entry.get("citations") or [] if isinstance(c, str)]
        outside = [c for c in citations if c not in inside]
        if outside:
            problems.append(f"{qid}: citation {', '.join(outside)} is not a paragraph of the question's own section; "
                            "cite only paragraphs of that section (name others in the evidence)")
            continue
        passed = all(entry[c] for c in prompts.CRITERIA)
        if passed and not citations:
            problems.append(f"{qid} passes all criteria but cites no paragraph; a passing question needs a citation")
            continue
        usable[qid] = {**{c: entry[c] for c in prompts.CRITERIA}, "citations": citations,
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


def read_revisions(reply: Any, wanted: dict[str, tuple[dict[str, Any], set[str]]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
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
        flaws = question_problems(q, wanted[qid][1])
        if flaws:
            problems += [f"{qid}: {f}" for f in flaws]
            continue
        old = wanted[qid][0]
        if q["prompt"] == old["prompt"] and q["answer"] == old["answer"]:
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
                {"id": q["id"], "prompt": q["prompt"], "answer": q["answer"],
                 "supporting_paragraphs": q["supporting_paragraphs"]} for q in questions]})
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
            cancel: threading.Event | None = None) -> tuple[Book, dict[str, Any]]:
    """Run (or continue) the Claude part of a run. Returns the book and the final state."""
    try:
        run = Run(epub, work, settings, backend, progress, cancel)
    except EpubError as exc:
        raise PipelineError(str(exc)) from exc
    return run.book, run.run()
