"""What the command line and the web app both do: run a book, and rebuild its outputs after a review."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Iterable

from . import forms as qforms
from .llm import PROVIDERS, Backend, Backends, ClaudeCodeBackend, CodexBackend, Completion, Connection, service_urls
from .outputs import build_outputs
from .pipeline import SOURCE_COPY, STATE_FILE, Cancelled, PipelineError, Progress, kept, load_state, now, process, write_json
from .prep.epub import EpubError, load_book_bytes
from .settings import Settings
from .steer import Steer

__all__ = ["Cancelled", "PipelineError", "default_out_dir", "run_book", "try_chapter", "rebuild", "review_data", "work_dir",
           "trial_dir"]


def default_out_dir(epub: Path) -> Path:
    return epub.resolve().parent / f"{epub.name[:-5] if epub.name.lower().endswith('.epub') else epub.name}-processed"


def work_dir(out_dir: Path) -> Path:
    return out_dir / "work"


def trial_dir(out_dir: Path) -> Path:
    """Where the trial of one chapter is kept: a run of its own, beside the book's."""
    return out_dir / "trial"


def default_backend(settings: Settings, progress: Progress | None = None,
                    cancel: threading.Event | None = None) -> Backends:
    """Claude Code and Codex, each with a watch on its connection. When a call is lost to the network the run
    says so, waits, and says when it goes on; a set ``cancel`` gives up calls that are running or waiting."""
    def watch(provider: str) -> Connection:
        name = PROVIDERS[provider]

        def changed(up: bool) -> None:
            if progress:
                progress({"time": now(), "stage": "network", "done": None, "total": None,
                          "message": f"The connection to {name} is back. Going on." if up else
                                     f"No connection to {name}. Waiting for the network; the run goes on by itself "
                                     "when it is back."})
        return Connection(service_urls()[provider], name, on_change=changed)

    return Backends(ClaudeCodeBackend(effort=settings.effort, cancel=cancel, link=watch("claude")),
                    CodexBackend(effort=settings.effort, cancel=cancel, link=watch("codex")))


def _ready(epub: Path, settings: Settings, backend: Backend | None, progress: Progress | None,
           cancel: threading.Event | None) -> tuple[Path, Backend]:
    epub = epub.resolve()
    if not epub.is_file():
        raise PipelineError(f"{epub} does not exist")
    settings.validate()
    if backend is None:
        # Claude Code and Codex both stand ready; only the ones the two model settings name are looked for.
        backend = default_backend(settings, progress, cancel)
        backend.check_available([settings.model, settings.checker_model or settings.model])
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


def review_data(out_dir: Path) -> dict[str, Any]:
    """Every section with its questions and the checker's verdicts, for the review page."""
    from .exports import chapter_titles

    book, _, state = _open_run(out_dir)
    titles = chapter_titles(book)
    where = {b.ref: b for sd in book.spine for b in sd.blocks}
    by_section: dict[str, list[dict[str, Any]]] = {}
    for q in state["questions"]:
        by_section.setdefault(q["section"], []).append({
            "id": q["id"], "question": q["prompt"], "answer": q["answer"], **qforms.members(q), "status": q["status"],
            "kept": kept(q), "removed": bool(q.get("removed")), "can_keep": q["status"] in ("passed", "unchecked", "unverified"),
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
