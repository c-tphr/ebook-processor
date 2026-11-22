"""Small helpers that more than one test file uses. No test file takes a helper from another test file."""

from __future__ import annotations

import json
from pathlib import Path

from ebook_processor.runner import run_book
from ebook_processor.settings import Settings, from_options
from tests.paths import FIXTURE
from tests.scripted import Script

SHORT = from_options(density="short")


def run(tmp_path: Path, script: Script | None = None, settings: Settings = SHORT, epub: Path = FIXTURE, **kwargs):
    """Run a book into ``tmp_path / "out"`` with the stand-in for the model; gives the report, the backend and the script."""
    script = script or Script()
    backend = script.backend()
    report = run_book(epub, tmp_path / "out", settings, backend=backend, **kwargs)
    return report, backend, script


def state_of(folder: Path) -> dict:
    """The state a run keeps in its output folder."""
    return json.loads((folder / "work" / "state.json").read_text(encoding="utf-8"))


# A book's file in the notes folder, as the app writes one.
BOOK_FILE = """---
id: b-20261118-101500-almanac
kind: book
title: The Almanac
---
> [!info] This file is kept by ebook-processor. Edits made here are overwritten.

About the whole book. ^n5e10

## The Lamp Room

### Lighting

> The lamp is lit at sunset and put out at sunrise. ^h3f9a

A first paragraph on that.

A second one. ^h3f9a-note

A note on a paragraph about wicks. ^n81c2
"""
