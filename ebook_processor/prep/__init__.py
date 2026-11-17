"""The EPUB engine: reading, paragraph numbering, the draft checks, the prepared-EPUB build and its validator.

Ported from tools/prep of c-tphr/eink-learner-aid (MIT). The prepared-EPUB format it writes is the one that
repository's firmware reads, so the names starting with "rsvp" are kept.
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
