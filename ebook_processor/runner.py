"""What the command line and the web app both do: run a book, and rebuild its outputs after a review."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Iterable

from . import forms as qforms
from .llm import PROVIDERS, Backend, Backends, ClaudeCodeBackend, CodexBackend, Completion, Connection, service_urls
from .outputs import build_outputs, try_build
from .pipeline import KEPT, SOURCE_COPY, STATE_FILE, Cancelled, PipelineError, Progress, kept, load_state, process
from .prep.epub import EpubError, load_book_bytes
from .prep.limits import MAX_PROMPTS, MAX_PROMPTS_PER_CHECKPOINT
from .settings import Settings
from .steer import Steer
from .store import now, write_json

__all__ = ["Cancelled", "PipelineError", "change_run", "default_out_dir", "run_book", "try_chapter", "rebuild", "review_data", "work_dir",
           "trial_dir"]


def default_out_dir(epub: Path) -> Path:
    from . import sources
    known = epub.name.lower().endswith(".epub") or sources.is_source(epub.name)
    return epub.resolve().parent / f"{epub.stem if known else epub.name}-processed"


def work_dir(out_dir: Path) -> Path:
    return out_dir / "work"


def trial_dir(out_dir: Path) -> Path:
    """Where the trial of one chapter is kept: a run of its own, beside the book's."""
    return out_dir / "trial"


# A call the reader waits for at the screen (a check, a rewrite, a question on a highlight, the model's help in the
# notes folder) is given up much sooner than a call of a run, which may take half an hour and is tried four times.
WAITING = {"timeout": 300.0, "retries": 1, "backoff": (5.0,)}


def default_backend(settings: Settings, progress: Progress | None = None,
                    cancel: threading.Event | None = None, waiting: bool = False) -> Backends:
    """Claude Code and Codex, each with a watch on its connection. When a call is lost to the network the run
    says so, waits, and says when it goes on; a set ``cancel`` gives up calls that are running or waiting. With
    ``waiting`` the calls have the short limits of a request the reader waits for."""
    limits: dict[str, Any] = WAITING if waiting else {}
    def watch(provider: str) -> Connection:
        name = PROVIDERS[provider]

        def changed(up: bool) -> None:
            if progress:
                progress({"time": now(), "stage": "network", "done": None, "total": None,
                          "message": f"The connection to {name} is back. Going on." if up else
                                     f"No connection to {name}. Waiting for the network; the run goes on by itself "
                                     "when it is back."})
        return Connection(service_urls()[provider], name, on_change=changed)

    return Backends(ClaudeCodeBackend(effort=settings.effort, cancel=cancel, link=watch("claude"), **limits),
                    CodexBackend(effort=settings.effort, cancel=cancel, link=watch("codex"), **limits))


def models_for(settings: Settings, roles: Iterable[str]) -> list[str]:
    """The models these roles use: the checker's own for a check, when one is set."""
    return [settings.checker_model or settings.model if role == "check" else settings.model for role in roles]


def ready_backend(settings: Settings, roles: Iterable[str] = ("draft", "check"), progress: Progress | None = None,
                  cancel: threading.Event | None = None, waiting: bool = False) -> Backends:
    """A backend whose tools are there for these roles. Claude Code and Codex both stand ready; only the ones the
    roles' models name are looked for."""
    backend = default_backend(settings, progress, cancel, waiting)
    backend.check_available(models_for(settings, roles))
    return backend


def _ready(epub: Path, settings: Settings, backend: Backend | None, progress: Progress | None,
           cancel: threading.Event | None) -> tuple[Path, Backend]:
    epub = epub.resolve()
    if not epub.is_file():
        raise PipelineError(f"{epub} does not exist")
    settings.validate()
    if backend is None:
        backend = ready_backend(settings, ("draft", "check"), progress, cancel)
    return epub, backend


def run_book(epub: Path, out_dir: Path, settings: Settings, backend: Backend | None = None,
             progress: Progress | None = None, cancel: threading.Event | None = None, steer: Steer | None = None,
             spent: list[Completion] | None = None) -> dict[str, Any]:
    """Process ``epub`` into ``out_dir`` (continuing an unfinished run there) and return the report. A chapter
    tried before with the same options and the same steer is taken over as it is."""
    epub, backend = _ready(epub, settings, backend, progress, cancel)
    work = work_dir(out_dir)
    try:
        trial = load_state(work_dir(trial_dir(out_dir)))
    except PipelineError:
        trial = None
    book, state = process(epub, work, settings, backend, progress, cancel, steer, carry=trial, spent=spent)
    original = (work / SOURCE_COPY).read_bytes()
    if progress:
        progress({"stage": "build", "message": "building the books, the deck and the report", "done": None,
                  "total": None, "time": state["updated"]})
    return build_outputs(book, original, state, out_dir, settings.epubcheck)


def try_chapter(epub: Path, out_dir: Path, settings: Settings, chapter: int, backend: Backend | None = None,
                progress: Progress | None = None, cancel: threading.Event | None = None, steer: Steer | None = None,
                spent: list[Completion] | None = None) -> dict[str, Any]:
    """Write and check the questions of one document of the book (``chapter`` is its spine index), and nothing
    else: no books are built. Returns what ``review_data`` gives for it. A trial that was made of another chapter,
    or in another way, is replaced."""
    import shutil

    epub, backend = _ready(epub, settings, backend, progress, cancel)
    folder = trial_dir(out_dir)
    work = work_dir(folder)
    try:
        old = load_state(work)
    except PipelineError:
        old = None
    if old is not None:
        from .pipeline import steer_key
        same = old.get("only") == [chapter] and steer_key(old) == (steer.key() if steer and not steer.empty else "") \
            and Settings.from_json(old["settings"]).drafting_key() == settings.drafting_key()
        if not same:
            shutil.rmtree(folder, ignore_errors=True)
    process(epub, work, settings, backend, progress, cancel, steer, only={chapter}, spent=spent)
    return review_data(folder)


def _open_run(out_dir: Path) -> tuple[Any, bytes, dict[str, Any]]:
    work = work_dir(out_dir)
    state = load_state(work)
    if state is None:
        raise PipelineError(f"{out_dir} holds no run (there is no {work / STATE_FILE})")
    copy = work / SOURCE_COPY
    if not copy.is_file():
        raise PipelineError(f"{copy} is missing: the run in {out_dir} did not finish; run the book again to "
                            "continue it")
    original = copy.read_bytes()
    if hashlib.sha256(original).hexdigest() != state["source"]["sha256"]:
        raise PipelineError(f"{copy} is not the book this run was made from")
    try:
        book = load_book_bytes(original, state["source"]["file"])
    except EpubError as exc:
        raise PipelineError(str(exc)) from exc
    return book, original, state


def rebuild(out_dir: Path, remove: Iterable[str] = (), restore: Iterable[str] = (),
            removed: Iterable[str] | None = None, epubcheck: bool | None = None,
            cards: str | None = None, preview: bool | None = None) -> dict[str, Any]:
    """Build the outputs again from the saved run, without calling a model.

    ``preview`` switches the question previews of the book with visible checkpoints on or off.

    ``remove`` and ``restore`` change which questions the user has taken out; ``removed``, when given, is the
    complete list instead. Only questions that passed (or were kept unchecked) can be restored: a question the
    check failed stays out.
    """
    book, original, state = _open_run(out_dir)
    by_id = {q["id"]: q for q in state["questions"]}
    wanted = set(removed) if removed is not None else None
    unknown = [i for i in [*remove, *restore, *(wanted or ())] if i not in by_id]
    if unknown:
        raise PipelineError(f"no such question: {', '.join(sorted(set(unknown)))}")
    for q in state["questions"]:
        if wanted is not None:
            q["removed"] = q["id"] in wanted
        if q["id"] in set(remove):
            q["removed"] = True
        if q["id"] in set(restore):
            q["removed"] = False
    if cards is not None:
        from .exports import CARD_CHOICES
        if cards not in CARD_CHOICES:
            raise PipelineError(f"cards must be one of {', '.join(CARD_CHOICES)}")
        state["cards"] = cards
    if preview is not None:
        state["settings"]["preview"] = bool(preview)
    write_json(work_dir(out_dir) / STATE_FILE, state)
    return build_outputs(book, original, state, out_dir,
                         state["settings"].get("epubcheck", False) if epubcheck is None else epubcheck)


def open_run(out_dir: Path) -> tuple[Any, dict[str, Any]]:
    """A finished run as it is saved: the book and the state. For a look that keeps nothing."""
    book, _, state = _open_run(out_dir)
    if state["stage"] != "done":
        raise PipelineError(f"the run in {out_dir} is not finished")
    return book, state


def _fits(book: Any, state: dict[str, Any]) -> bytes:
    """Refuse a state from which the book for the e-reader cannot be made, with the reason in plain words. Returns
    that book, made from the state."""
    in_section: dict[str, int] = {}
    for q in state["questions"]:
        if kept(q):
            in_section[q["section"]] = in_section.get(q["section"], 0) + 1
    most = max(in_section.values(), default=0)
    if most > MAX_PROMPTS_PER_CHECKPOINT:
        raise PipelineError(f"A section can have {MAX_PROMPTS_PER_CHECKPOINT} questions, the most the e-reader's file "
                            f"holds for one checkpoint, and this one would have {most}. Take a question out of it first.")
    if sum(in_section.values()) > MAX_PROMPTS:
        raise PipelineError(f"A book can have {MAX_PROMPTS:,} questions, the most the e-reader's file holds, and this "
                            f"one would have {sum(in_section.values()):,}. Take a question out first.")
    problems, prepared = try_build(book, state)
    if problems or prepared is None:
        raise PipelineError("This change cannot be kept, because the book for the e-reader could not be made with it: "
                            + "; ".join(p[:200] for p in problems[:3]))
    return prepared


def change_run(out_dir: Path, change: Any, build: bool = True) -> Any:
    """Apply ``change(book, state)`` to a finished run and build its outputs again, so that every file and the
    reader hold the questions as they now are. Returns what ``change`` returns. Nothing is written when it raises,
    or when ``change`` only looked."""
    book, original, state = _open_run(out_dir)
    if state["stage"] != "done":
        raise PipelineError(f"the run in {out_dir} is not finished")
    before = json.dumps(state, sort_keys=True)
    result = change(book, state)
    # before anything is written: a change the e-reader's format cannot hold is refused, not half kept
    prepared = _fits(book, state) if build else None
    if json.dumps(state, sort_keys=True) != before:  # a look at the run writes nothing: it could undo a change made meanwhile
        write_json(work_dir(out_dir) / STATE_FILE, state)
    if build:
        build_outputs(book, original, state, out_dir, state["settings"].get("epubcheck", False), prepared)
    return result


def review_data(out_dir: Path) -> dict[str, Any]:
    """Every section with its questions and the checker's verdicts, for the review page."""
    from .exports import chapter_titles

    book, _, state = _open_run(out_dir)
    titles = chapter_titles(book)
    where = {b.ref: b for sd in book.spine for b in sd.blocks}
    by_section: dict[str, list[dict[str, Any]]] = {}
    for q in state["questions"]:
        by_section.setdefault(q["section"], []).append({
            "id": q["id"], **qforms.as_question(q), "status": q["status"],
            "kept": kept(q), "removed": bool(q.get("removed")), "can_keep": q["status"] in KEPT,
            "edited": q["status"] == "edited" and not q.get("added"), "added": bool(q.get("added")), "verdict": q.get("verdict"),
            "failed_criteria": [c for c, ok in q["criteria"].items() if not ok], "problem": q["problem"],
            "minor_note": q["minor_note"], "evidence": q["evidence"], "revisions": len(q["history"]),
            "supporting_paragraphs": q["supporting_paragraphs"]})
    return {
        "book": state["source"], "stage": state["stage"],
        "sections": [{"id": s["id"], "label": s["label"], "chapter": titles.get(s["chapter_href"], s["chapter_href"]),
                      "first_paragraph": s["first_paragraph"], "last_paragraph": s["last_paragraph"],
                      "words": where[s["last_paragraph"]].end - where[s["first_paragraph"]].start,
                      "natural_boundary": s["natural_boundary"], "boundary_note": s["boundary_note"],
                      "questions": by_section.get(s["id"], [])} for s in state["sections"]],
    }
