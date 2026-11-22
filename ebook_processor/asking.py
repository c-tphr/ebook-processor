"""Asking a model for one question, and for the check of one: what the questions of a book, of a highlight and of
a note do in the same way.

``ask_with_retry`` sends a task and hands the reply to the caller. A reply that breaks a plain rule goes back to the
model once, with what was wrong with it. ``check_one`` is the call that checks one question against its section, by
the rules the run checks its own questions by; what becomes of the verdict is the caller's business.

Two checks are not here, because they differ on purpose. The run (``pipeline.read_check``) judges many questions
in one call and is stricter about a reply. A note's question (``assist.check``) has its own task and criteria.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from . import forms as qforms
from . import prompts
from .llm import Backend, BackendError, Completion
from .prep.epub import Book
from .settings import Settings
from .steer import Steer


class Unusable(Exception):
    """A reply that cannot be used. ``problems`` says what is wrong with it, in words the model can act on."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def ask_with_retry(backend: Backend, *, role: str, system: str, message: str, schema: dict[str, Any], model: str | None,
                   read: Callable[[Completion], Any], feedback: Callable[[list[str]], str],
                   failed: Callable[[list[str]], str]) -> Any:
    """Ask a model, and return what ``read(reply)`` makes of its reply. ``read`` raises ``Unusable`` for a reply
    that breaks a plain rule. The task is then sent once more, with ``feedback(problems)`` after it. When the
    second reply cannot be used either, ``BackendError`` is raised with the message ``failed(problems)``."""
    said, problems = "", []
    for _ in range(2):
        done = backend.complete(role=role, system=system, prompt=message + said, schema=schema, model=model)
        try:
            return read(done)
        except Unusable as exc:
            problems = exc.problems
            said = "\n\n" + feedback(problems)
    raise BackendError(failed(problems))


def with_form(settings: Settings, q: dict[str, Any]) -> Settings:
    """The settings with the question's own form among the allowed ones. A form that was allowed when the question
    was written may not be allowed now, and a check must know the forms."""
    form = qforms.form_of(q)
    return settings if form in settings.forms else replace(settings, forms=qforms.clean_forms([*settings.forms, form]))


def check_one(backend: Backend, book: Book, item: dict[str, Any], settings: Settings,
              steer: Steer | None = None) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    """Have one question checked against its section. ``item`` is the section as ``prompts.check_message`` takes
    it, with the one question under ``questions`` (a record with ``id`` and ``prompt``). Returns the checker's
    entry for the question, or None when its reply has none, and each criterion as true or false: anything that is
    not ``true`` counts as false."""
    q = item["questions"][0]
    settings = with_form(settings, q)
    done = backend.complete(role="check", system=prompts.checker_system(settings, steer),
                            prompt=prompts.check_message(book, [item], settings, None), schema=prompts.check_schema(settings),
                            model=settings.checker_model or settings.model)
    entries = done.data.get("prompts") if isinstance(done.data, dict) else None
    entry = next((p for p in entries or [] if isinstance(p, dict) and p.get("id") == q["id"]), None)
    return entry, ({c: entry.get(c) is True for c in prompts.criteria(settings)} if entry is not None else {})
