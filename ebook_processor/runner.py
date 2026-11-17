"""What the command line and the web app both do: run a book, and rebuild its outputs after a review."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Iterable

from .llm import Backend, Backends, ClaudeCodeBackend, CodexBackend
from .outputs import build_outputs
from .pipeline import SOURCE_COPY, STATE_FILE, Cancelled, PipelineError, Progress, kept, load_state, process, write_json
from .prep.epub import EpubError, load_book_bytes
from .settings import Settings

__all__ = ["Cancelled", "PipelineError", "default_out_dir", "run_book", "rebuild", "review_data", "work_dir"]


def default_out_dir(epub: Path) -> Path:
    return epub.resolve().parent / f"{epub.name[:-5] if epub.name.lower().endswith('.epub') else epub.name}-processed"


def work_dir(out_dir: Path) -> Path:
    return out_dir / "work"


def run_book(epub: Path, out_dir: Path, settings: Settings, backend: Backend | None = None,
             progress: Progress | None = None, cancel: threading.Event | None = None) -> dict[str, Any]:
    """Process ``epub`` into ``out_dir`` (continuing an unfinished run there) and return the report."""
    epub = epub.resolve()
    if not epub.is_file():
        raise PipelineError(f"{epub} does not exist")
    settings.validate()
    work = work_dir(out_dir)
    if backend is None:
        # Claude Code and Codex both stand ready; only the ones the two model settings name are looked for.
        backend = Backends(ClaudeCodeBackend(effort=settings.effort), CodexBackend(effort=settings.effort))
        backend.check_available([settings.model, settings.checker_model or settings.model])
    book, state = process(epub, work, settings, backend, progress, cancel)
    original = (work / SOURCE_COPY).read_bytes()
    if progress:
        progress({"stage": "build", "message": "building the books, the deck and the report", "done": None,
                  "total": None, "time": state["updated"]})
    return build_outputs(book, original, state, out_dir, settings.epubcheck)


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
            "id": q["id"], "question": q["prompt"], "answer": q["answer"], "status": q["status"],
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
