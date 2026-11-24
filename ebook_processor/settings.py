"""The options of one run, and the presets the command line and the web app offer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from .forms import FORMS, FormError, clean_forms

INSTRUCTIONS_VERSION = "2.3"

# Target section length in words, by name. At 240 words per minute "medium" is 3 to 8 minutes of reading.
DENSITIES = {
    "short": (300, 1000),
    "medium": (600, 2000),
    "long": (1500, 4000),
}
CONTENT_MODES = ("prose", "text", "figures")
KINDS = ("auto", "expository", "narrative", "technical")
MAX_QUESTIONS = 8  # the highest limit a checkpoint's questions can be given
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EPHEMERAL = ("none", "attention", "linked")  # see ``pathway``


class SettingsError(ValueError):
    """An option has a value that cannot be used."""


@dataclass(frozen=True)
class Settings:
    min_words: int = 600  # target section length, lower end
    max_words: int = 2000  # ... and upper end
    max_questions: int = 3  # most questions one checkpoint may have; the model chooses how many, up to this
    content: str = "text"  # what Claude sees of tables, code, formulas and figures: prose, text or figures
    kind: str = "auto"  # the kind of book: auto, expository, narrative or technical
    rounds: int = 3  # how often a question may be checked (0: no check at all)
    model: str | None = None  # model for drafting and revising (None: Claude Code's default; see llm.split_model)
    checker_model: str | None = None  # model for checking (None: the same as ``model``)
    effort: str | None = None  # reasoning effort passed to Claude Code and to Codex (None: each tool's default)
    jobs: int = 3  # calls to the models that may run at the same time
    part_words: int = 10000  # about how much text one drafting call reads
    epubcheck: bool = False  # run EPUBCheck on both output books (needs Java and a one-time download)
    title_suffix: str = " (checkpoints)"  # added to the title of the book with visible checkpoint pages
    preview: bool = False  # the book with visible checkpoints also shows a section's questions before the section
    forms: tuple[str, ...] = ("open",)  # the forms a question may take (see ``forms``); the model picks among them
    # Ephemeral questions inside the sections (see ``pathway``): none, to sustain attention generally ("attention"),
    # or to build toward the practice questions explicitly ("linked")
    ephemeral: str = "none"
    # the forms an ephemeral question may take; they matter only in a book with ephemeral questions
    ephemeral_forms: tuple[str, ...] = ("open",)

    def validate(self) -> None:
        if not 50 <= self.min_words < self.max_words <= 20000:
            raise SettingsError(f"section length {self.min_words}-{self.max_words} words: need 50 <= min < max <= 20000")
        if not 1 <= self.max_questions <= MAX_QUESTIONS:
            raise SettingsError(f"the most questions per checkpoint must be 1 to {MAX_QUESTIONS}")
        if self.content not in CONTENT_MODES:
            raise SettingsError(f"content must be one of {', '.join(CONTENT_MODES)}")
        if self.kind not in KINDS:
            raise SettingsError(f"kind must be one of {', '.join(KINDS)}")
        if not 0 <= self.rounds <= 5:
            raise SettingsError("rounds must be 0 to 5")
        if self.effort is not None and self.effort not in EFFORTS:
            raise SettingsError(f"effort must be one of {', '.join(EFFORTS)}")
        for forms, what in ((self.forms, "the forms of question"), (self.ephemeral_forms, "the forms of an ephemeral question")):
            try:
                if clean_forms(forms) != tuple(forms):
                    raise FormError(f"{what} are given in this order, each once: {', '.join(FORMS)}")
            except FormError as exc:
                raise SettingsError(str(exc)) from exc
        if self.ephemeral not in EPHEMERAL:
            raise SettingsError(f"ephemeral questions must be one of {', '.join(EPHEMERAL)}")
        if not 1 <= self.jobs <= 8:
            raise SettingsError("jobs must be 1 to 8")
        if not 2000 <= self.part_words <= 60000:
            raise SettingsError("part size must be 2000 to 60000 words")
        if self.part_words < self.max_words * 2:
            raise SettingsError("part size must be at least twice the longest section")

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "forms": list(self.forms), "ephemeral_forms": list(self.ephemeral_forms)}

    @property
    def open_only(self) -> bool:
        """Whether the practice questions are open questions only."""
        return tuple(self.forms) == ("open",)

    @property
    def all_forms(self) -> tuple[str, ...]:
        """The forms a question of the book may take, in either role: in a book with ephemeral questions, the forms
        of the practice questions and those of the ephemeral questions together."""
        wanted = set(self.forms) | (set(self.ephemeral_forms) if self.pathway else set())
        return tuple(f for f in FORMS if f in wanted)

    @property
    def pathway(self) -> bool:
        """Whether the book is prepared for the graduated pathway: ephemeral questions inside its sections."""
        return self.ephemeral != "none"

    def drafting_key(self) -> dict[str, Any]:
        """The options that decide what the drafter writes. A run can be resumed only while these are unchanged."""
        return {**{k: getattr(self, k) for k in ("min_words", "max_words", "max_questions", "content", "kind", "part_words")},
                "forms": list(self.forms), "ephemeral": self.ephemeral,
                # the forms of the ephemeral questions only where there are any: a run without them resumes whatever they are
                **({"ephemeral_forms": list(self.ephemeral_forms)} if self.pathway else {})}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Settings:
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        for name in ("forms", "ephemeral_forms"):
            if isinstance(values.get(name), list):
                values[name] = tuple(values[name])
        if "ephemeral" in values and "ephemeral_forms" not in values:  # saved before the two roles had forms of their own
            values["ephemeral_forms"] = values.get("forms", cls.forms)
        settings = cls(**values)
        settings.validate()
        return settings


def from_options(density: str | None = None, section_words: str | None = None, **rest: Any) -> Settings:
    """Settings from the user's options. ``section_words`` ("400-1200") wins over ``density``."""
    values = {k: v for k, v in rest.items() if v is not None}
    for name in ("forms", "ephemeral_forms"):
        if name in values:
            try:
                values[name] = clean_forms(values[name])
            except FormError as exc:
                raise SettingsError(str(exc) if name == "forms" else f"ephemeral questions: {exc}") from exc
    if section_words:
        low, sep, high = section_words.partition("-")
        if not sep or not low.strip().isdigit() or not high.strip().isdigit():
            raise SettingsError(f"section length {section_words!r}: write it as MIN-MAX, for example 600-2000")
        values["min_words"], values["max_words"] = int(low), int(high)
    elif density:
        if density not in DENSITIES:
            raise SettingsError(f"density must be one of {', '.join(DENSITIES)}")
        values["min_words"], values["max_words"] = DENSITIES[density]
    settings = Settings(**values)
    if "part_words" not in values and settings.part_words < settings.max_words * 2:
        settings = Settings(**{**values, "part_words": settings.max_words * 3})
    settings.validate()
    return settings
