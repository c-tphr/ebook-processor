"""The EPUB engine: reading, paragraph numbering, the draft checks, the prepared-EPUB build and its validator.

Ported from tools/prep of c-tphr/eink-learner-aid (MIT). The prepared-EPUB format it writes is the one that
repository's firmware reads, so the names starting with "rsvp" are kept.

Where this copy differs from the port. Each edit is small and leaves the format and the engine's output as they
were. Paths in the docstrings below are those of the other repository: its ``tools/prep`` is this package, its
``fixtures/`` is ``tests/data/`` here, its ``tests/`` is ``tests/prep/`` here, and ``docs/PLAN.md`` and
``docs/spikes/`` are its own documents.

- ``epub.py``, ``load_book_bytes``: a chapter nested thousands of levels deep is refused with an ``EpubError``.
  It ended in a ``RecursionError`` before.
- ``words.py``, ``is_separator_only``: one regular expression in place of ``not tokenize(text)``, with the same
  answer for every text. ``epub.py`` asks it in the four places that only want to know whether a text holds a
  word (``_has_own_text``, ``placeholder_for``, ``_segment``). Opening a book takes about half the time.
- ``epubzip.py``, ``Archive``: an entry may unpack to 64 MB and an archive to 1 GB, by the sizes the archive
  states, and ``read`` unpacks no more than the stated size. ``epub.py``, ``_parse_member``: a document of the
  book may be 16 MB. A small file could otherwise unpack to gigabytes and hold the app for minutes.
"""

from .checkpoints import (
    CheckpointFileError,
    Issue,
    check_bytes,
    check_file,
    load_checkpoints,
    validate_checkpoints,
    verdict,
)

__all__ = [
    "CheckpointFileError",
    "Issue",
    "check_bytes",
    "check_file",
    "load_checkpoints",
    "validate_checkpoints",
    "verdict",
]
