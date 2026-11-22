"""What the parts of the local web app share: the error a request ends with, a few constants and small helpers.

``server.py`` and the parts of its class ``Jobs`` (``library.py``, ``runs.py``, ``study.py``, ``questions.py`` and
``notes_folder.py``) import from here. This module imports none of them.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .. import sources
from .. import steer as steering
from ..prep.limits import MAX_PROMPTS
from ..settings import Settings
from ..steer import SteerError

JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
EVENTS_KEPT = 300
SETTINGS_FILE = "reader-settings.json"  # what the settings window keeps, for every page of the app
LONG_SETTINGS = ("steer_about", "steer_style")  # the reader's own texts: longer than any other setting
WAITING = ("asking", "tried")  # a run that waits for its reader: to confirm a reading, or after a trial chapter
NOTES_FOLDER = "ebook-processor-notes"  # the notes folder's name where none was chosen: beside the data folder
CITED = ("id", "title", "block", "text", "found", "job", "go", "in_library")  # what a page is told of a cited passage


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "book"


def _plain_name(name: str) -> str:
    """A file name that is safe to write, with the ending it came with."""
    return re.sub(r"[^\w .,()'&+-]", "_", Path(name.replace("\\", "/")).name).strip(" .") or "original"


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w .,()'&+-]", "_", name).strip(" .") or "book.epub"
    return name if name.lower().endswith(".epub") else name + ".epub"


def _text(value: Any, limit: int = steering.TEXT_MAX) -> str:
    try:
        return steering.clean(value, limit)
    except SteerError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc


def suggested_chapter(chapters: list[dict[str, Any]]) -> int | None:
    """The chapter to try a steer on: the first that is long enough to be a real one."""
    real = next((c for c in chapters if c["words"] >= 1500), None) or max(chapters, key=lambda c: c["words"], default=None)
    return real["index"] if real else None


def _looked(book: Any, title: str) -> dict[str, Any]:
    chapters = steering.chapter_list(book)
    return {"title": title, "words": sum(c["words"] for c in chapters), "chapters": chapters,
            "suggested": suggested_chapter(chapters)}


def _is_epub(name: str, data: bytes) -> bool:
    """An EPUB by its name, or by its bytes when the name says nothing this app knows."""
    return name.lower().endswith(".epub") or (not sources.is_source(name) and data[:2] == b"PK" and b"mimetypeapplication/epub+zip" in data[:100])


def _split_how(value: Any) -> str:
    return "none" if value == "none" else "auto"


def _whole(value: Any) -> bool | None:
    """How much of a page is taken: all of it, only its article, or as the app guesses."""
    return value if isinstance(value, bool) else None


def estimate(words: Any, settings: Settings, steered: bool) -> dict[str, Any]:
    if not isinstance(words, list) or len(words) > 5000 or not all(isinstance(w, int) and not isinstance(w, bool) and 0 <= w < 10**8 for w in words):
        raise ApiError(HTTPStatus.BAD_REQUEST, "'words' is the list of the chapters' lengths")
    out = steering.estimate(words, settings, steered)
    out["max_questions"] = settings.max_questions
    out["steered"] = steered
    out["firmware_limit"] = MAX_PROMPTS if out["questions"][1] > MAX_PROMPTS else None
    return out
