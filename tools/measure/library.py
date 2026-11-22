#!/usr/bin/env python3
"""Measure the app with a large library and a large notes folder. No model is called and the web is not used.

    python3 tools/measure/library.py                 # 50 books, 10,000 notes
    python3 tools/measure/library.py --books 200 --notes 3000 --keep

It processes the 53,000-word test book once with the tests' stand-in model, copies the result to make the library,
writes a notes folder of made-up notes, and prints how long the pages' requests take, how much memory the app
holds and how many EPUBs were read. Everything is written to a temporary folder, which is removed unless --keep.

The numbers this printed when it was written (Linux, 4 cores, Python 3.13) are in docs/reviews/PLAN.md, section 9.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def timed(label: str, call) -> None:
    began = time.perf_counter()
    call()
    print(f"  {label:<46} {1000 * (time.perf_counter() - began):8.0f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--books", type=int, default=50)
    parser.add_argument("--notes", type=int, default=10000)
    parser.add_argument("--keep", action="store_true", help="leave the temporary folder in place and print where it is")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ebook-processor-measure-"))
    os.environ["EBOOK_PROCESSOR_HOME"] = str(root / "home")

    from ebook_processor import editing, reading
    from ebook_processor.runner import change_run, rebuild, run_book
    from ebook_processor.settings import from_options
    from ebook_processor.vault import Vault
    from ebook_processor.web import server as web
    from tests.scripted import Script

    memory = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # noqa: E731
    try:
        print("One book of 53,000 words (tests/data/books/paine-essays.epub)")
        data = root / "data"
        first = data / "20260101-000000-book-000"
        (first / "input").mkdir(parents=True)
        shutil.copy(REPO / "tests/data/books/paine-essays.epub", first / "input" / "paine-essays.epub")
        timed("a run with the stand-in model", lambda: run_book(first / "input" / "paine-essays.epub", first, from_options(), backend=Script().backend()))
        timed("rebuild its files", lambda: rebuild(first))
        qid = json.loads((first / "work" / "state.json").read_text(encoding="utf-8"))["questions"][5]["id"]
        timed("open the question editor", lambda: change_run(first, lambda book, state: editing.view(book, state, qid), build=False))
        timed("read the whole EPUB", lambda: reading.open_reading_book(next(first.glob("*.prepared.epub"))))
        job = {"id": first.name, "file": "paine-essays.epub", "title": "Essays", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
               "status": "done", "message": "", "stage": None, "done": None, "total": None, "counts": None, "words": 52786,
               "settings": from_options().as_json(), "events": []}
        (first / "job.json").write_text(json.dumps(job), encoding="utf-8")
        # the reader's work on the book: a highlight in every section, and an answer to a third of the questions
        one = web.Jobs(data, backend_factory=lambda settings: None)
        sections = one.manifest(first.name)["sections"]
        for s in sections:
            one.notes_do(first.name, lambda store, book, s=s: web.notes.add_highlight(store, book, s["chapter"], s["start"] + 1, s["start"] + 7))
        asked = [p["id"] for s in sections for p in s["prompts"]][::3]
        state = one.reading_state(first.name)
        state["x_attempts"] = [{"id": pid, "at": f"2026-01-02T10:{n % 60:02d}:{n // 60:02d}", "grade": "correct", "text": "", "where": "read"} for n, pid in enumerate(asked)]
        state["grades"] = {a["id"]: {"first": "correct", "at": a["at"]} for a in state["x_attempts"]}
        state["position"] = {"chapter": sections[-1]["chapter"], "word": sections[-1]["end"], "mode": "page"}
        state["x_furthest"] = {"chapter": sections[-1]["chapter"], "word": sections[-1]["end"]}
        one.save_reading(first.name, state)
        print(f"  with {len(sections)} highlights and {len(asked)} answered questions")
        (first / reading.OUTLINE_FILE).unlink(missing_ok=True)
        for n in range(1, args.books):
            name = f"20260101-000000-book-{n:03d}"
            shutil.copytree(first, data / name)
            (data / name / "job.json").write_text(json.dumps({**job, "id": name, "title": f"Essays {n}"}), encoding="utf-8")

        opened: list[int] = []
        real = reading.open_reading_book
        reading.open_reading_book = lambda path: (opened.append(1), real(path))[1]
        for label in ("the first start (the outlines are made)", "every later start"):
            print(f"\nA library of {args.books} books, {label}")
            opened.clear()
            jobs = web.Jobs(data, backend_factory=lambda settings: None)
            jobs.ui_settings({"notes_folder": str(root / "notes-of-the-library")})
            timed("Practice", jobs.practice)
            timed("the library", jobs.listing)
            timed("the library, again", jobs.listing)
            timed("the Notes front page", jobs.vault_home)
            timed("the Notes front page, again", jobs.vault_home)
            timed("the Process view", jobs.process_view)
            print(f"  EPUBs read: {len(opened)}; the most memory held so far: {memory():.0f} MB")

        print(f"\nA notes folder of {args.notes} notes")
        folder = root / "notes"
        for n in range(args.notes):
            path = folder / f"f{n % 40:02d}" / f"Note {n:05d}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"---\nkind: idea\n---\nNote {n} about lamps. See [[Note {(n * 7) % args.notes:05d}]] and [[Note {(n + 1) % args.notes:05d}]].\n"
                            + "Words of the note. " * 20, encoding="utf-8")
        vault = Vault(folder)
        timed("read every note for the first time", lambda: vault.refresh(force=True))
        timed("list the folder, nothing changed", lambda: vault.refresh(force=True))
        timed("search for a common word", lambda: vault.search("lamps"))
        timed("the graph's data", vault.graph)
        timed("the graph's data, again", vault.graph)
        print(f"  the most memory held so far: {memory():.0f} MB")
        return 0
    finally:
        if args.keep:
            print(f"\nKept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
