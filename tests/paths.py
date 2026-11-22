"""Where the test data is. Every test file takes these paths from here, not from another test file."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "tests" / "data"

BOOKS = DATA / "books"
FIXTURE = BOOKS / "fixture.epub"             # the made-up book, as a reader would add it
PREPARED = BOOKS / "fixture.prepared.epub"   # the same book with its checkpoints, as the engine builds it
DRAFT = BOOKS / "fixture.draft.json"
CHECK = BOOKS / "fixture.check.json"
NEGATIVE = BOOKS / "negative"                # books that are wrong, each in one known way

CHECKPOINTS = DATA / "checkpoints"
WORDS = DATA / "words"
