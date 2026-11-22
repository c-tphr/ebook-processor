"""The forms a question can take.

- ``open``: a question the reader answers in their head, then grades themselves against the reference answer.
- ``choice``: multiple choice, four choices of which one is right.
- ``truefalse``: a statement that is true or false.
- ``cloze``: a statement with a gap; the reader types the missing words.

The last three are graded by the app. Whatever its form, a question is a record with ``prompt`` and ``answer``:
for an open question the answer is the reference answer, for the others it is the explanation shown after the
reader has answered. A question that is not open also has ``form`` and that form's own members:

    choice      "choices": four texts, "correct": the index of the right one
    truefalse   "truth": true or false           (``prompt`` is the statement)
    cloze       "accept": the answers that count, the first being the one shown
                                                 (``prompt`` is the statement, with ``GAP`` where the words go)

Some records name the question's text ``question`` and not ``prompt``: a highlight's and a note's question,
``questions.json``, the review page and the editor. ``as_prompt`` and ``as_question`` turn one into the other.

``as_text`` gives any question as a plain question and answer, for the places that can show nothing else: a
firmware that knows only open questions, a text file of cards. In the firmware-format book each prompt carries
that text, and its form in one extra member, ``x_form`` (``to_x`` and ``from_x``).
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Callable

FORMS = ("open", "choice", "truefalse", "cloze")
NAMES = {"open": "open question", "choice": "multiple choice", "truefalse": "true or false", "cloze": "fill in the blank"}
GRADED = FORMS[1:]  # the forms the app grades
MEMBERS = ("form", "choices", "correct", "truth", "accept")

GAP = "____"
TF_PREFIX = "True or false: "
CHOICES = 4
CHOICE_MAX = 150  # characters of one choice
STATEMENT_MAX = 280  # a true-or-false statement: with ``TF_PREFIX`` it still fits the firmware's question
BLANK_MAX = 60  # the missing words
ALSO_ACCEPT = 4  # other wordings of the missing words that count
EXPLANATION_MAX = 400  # with the right answer before it, it still fits the firmware's answer

_GAPS = re.compile(r"_{3,}")


class FormError(ValueError):
    """Forms that cannot be used."""


def clean_forms(value: Any) -> tuple[str, ...]:
    """The allowed forms from a list or a comma-separated text, in the fixed order. ``all`` is every form."""
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value.strip()) if v]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise FormError("the forms of question are a list of: " + ", ".join(FORMS))
    wanted = set(FORMS) if "all" in value else set(value)
    unknown = sorted(wanted - set(FORMS))
    if unknown:
        raise FormError(f"unknown form of question: {', '.join(unknown)} (the forms are {', '.join(FORMS)})")
    if not wanted:
        raise FormError("allow at least one form of question")
    return tuple(f for f in FORMS if f in wanted)


def form_of(q: dict[str, Any]) -> str:
    return q.get("form") if q.get("form") in FORMS else "open"


def members(q: dict[str, Any]) -> dict[str, Any]:
    """The form and its own members, as they are kept beside ``prompt`` and ``answer``. Empty for an open question."""
    form = form_of(q)
    if form == "choice":
        return {"form": form, "choices": list(q.get("choices") or []), "correct": q.get("correct")}
    if form == "truefalse":
        return {"form": form, "truth": q.get("truth")}
    if form == "cloze":
        return {"form": form, "accept": list(q.get("accept") or [])}
    return {}


def as_prompt(q: dict[str, Any]) -> dict[str, Any]:
    """The wording of a record that names its text ``question``, as a record with ``prompt`` holds it: ``prompt``,
    ``answer`` and the members of its form."""
    return {"prompt": q["question"], "answer": q["answer"], **members(q)}


def as_question(q: dict[str, Any]) -> dict[str, Any]:
    """The other way: the wording of a record with ``prompt``, as ``question``, ``answer`` and the members of its
    form."""
    return {"question": q["prompt"], "answer": q["answer"], **members(q)}


def _order(seed: str, count: int) -> list[int]:
    """An order for the choices that depends only on the question's words, so that it is the same every time the
    outputs are built. The model tends to put the right choice in the same place; this takes that away."""
    order = list(range(count))
    random.Random(int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")).shuffle(order)
    return order


def from_model(raw: dict[str, Any], prompt: str, clean: Callable[[Any], str]) -> dict[str, Any]:
    """A model's reply for one question, as the members to keep. Nothing is refused here: ``problems`` says what
    is wrong, in words the model can act on."""
    form = raw.get("form") if raw.get("form") in FORMS else "open"
    if form == "choice":
        wrong = raw.get("wrong_choices")
        texts = [clean(raw.get("correct_choice"))] + [clean(w) for w in (wrong if isinstance(wrong, list) else [])]
        order = _order(prompt, len(texts))
        return {"form": form, "choices": [texts[i] for i in order], "correct": order.index(0)}
    if form == "truefalse":
        return {"form": form, "truth": raw.get("truth") if isinstance(raw.get("truth"), bool) else None}
    if form == "cloze":
        also = raw.get("also_accept")
        accept: list[str] = []
        for text in [raw.get("blank")] + (also if isinstance(also, list) else []):
            text = clean(text)
            if text and text.casefold() not in [a.casefold() for a in accept]:
                accept.append(text)
        if not clean(raw.get("blank")):
            accept = []
        return {"form": form, "accept": accept}
    return {}


def with_choices(correct: str, wrong: list[str], prompt: str) -> dict[str, Any]:
    """The members of a multiple-choice question from its right and its wrong choices."""
    return from_model({"form": "choice", "correct_choice": correct, "wrong_choices": wrong}, prompt, lambda t: str(t or "").strip())


def problems(q: dict[str, Any], allowed: tuple[str, ...] | None = None,
             plain_text_problem: Callable[[str], str | None] | None = None) -> list[str]:
    """What is wrong with a question's form and its members (not with its prompt and answer as texts)."""
    form = form_of(q)
    out: list[str] = []
    if allowed is not None and form not in allowed:
        return [f"its form is {NAMES[form]}, which is not allowed here; the allowed forms are "
                + ", ".join(NAMES[f] for f in allowed)]
    prompt, answer = str(q.get("prompt") or ""), str(q.get("answer") or "")
    texts: list[tuple[str, str, int]] = []
    if form == "choice":
        choices = q.get("choices") or []
        if len(choices) != CHOICES or not all(isinstance(c, str) and c.strip() for c in choices):
            out.append(f"a multiple-choice question needs correct_choice and exactly {CHOICES - 1} wrong_choices, none empty")
        elif len({c.casefold() for c in choices}) != CHOICES:
            out.append("two of the choices are the same")
        if not isinstance(q.get("correct"), int) or not 0 <= q["correct"] < max(1, len(choices)):
            out.append("which choice is correct is not given")
        texts = [("a choice", c, CHOICE_MAX) for c in choices if isinstance(c, str)]
    elif form == "truefalse":
        if not isinstance(q.get("truth"), bool):
            out.append("a true-or-false question needs truth: true or false")
        if prompt.rstrip().endswith("?"):
            out.append("the prompt of a true-or-false question is a statement, not a question")
        if len(prompt) > STATEMENT_MAX:
            out.append(f"the statement has {len(prompt)} characters; the limit for a true-or-false statement is {STATEMENT_MAX}")
    elif form == "cloze":
        accept = q.get("accept") or []
        if _GAPS.findall(prompt) != [GAP]:
            out.append(f"the prompt of a fill-in-the-blank question has the missing words replaced by {GAP} (four "
                       "underscores), exactly once")
        elif len(prompt.replace(GAP, " ").split()) < 4:
            out.append("the statement around the blank is too short to point to one answer")
        if not accept or not all(isinstance(a, str) and a.strip() for a in accept):
            out.append("a fill-in-the-blank question needs blank: the missing words")
        else:
            if len(accept) > 1 + ALSO_ACCEPT:
                out.append(f"also_accept has more than {ALSO_ACCEPT} entries")
            shown = accept[0].casefold()
            if len(shown) > 3 and shown in prompt.casefold():
                out.append("the statement contains the missing words, which gives the answer away")
        texts = [("the missing words" if n == 0 else "an accepted answer", a, BLANK_MAX) for n, a in enumerate(accept)
                 if isinstance(a, str)]
    if form != "open" and len(answer) > EXPLANATION_MAX:
        out.append(f"the explanation has {len(answer)} characters; for a question that is not open the limit is {EXPLANATION_MAX}")
    for label, text, limit in texts:
        if len(text) > limit:
            out.append(f"{label} has {len(text)} characters; the limit is {limit}")
        problem = plain_text_problem(text) if plain_text_problem else None
        if problem:
            out.append(f"{label} {problem}; write plain text")
    return out


def key(q: dict[str, Any]) -> str:
    """The right answer in words. Empty for an open question, whose answer is its reference answer."""
    form = form_of(q)
    if form == "choice":
        return q["choices"][q["correct"]]
    if form == "truefalse":
        return "True" if q["truth"] else "False"
    if form == "cloze":
        return q["accept"][0]
    return ""


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text[-1:] in ".!?" else text + "."


def as_text(q: dict[str, Any], prompt: str | None = None, answer: str | None = None) -> tuple[str, str]:
    """Any question as a plain question and answer that a reader can grade themselves against."""
    prompt = q["prompt"] if prompt is None else prompt
    answer = q["answer"] if answer is None else answer
    form = form_of(q)
    if form == "open":
        return prompt, answer
    shown = TF_PREFIX + prompt if form == "truefalse" else prompt
    if form == "truefalse" and re.match(r"(?i)(true|false)\b", answer):  # an explanation that opens with the verdict
        return shown, answer
    return shown, f"{_sentence(key(q))} {answer}".strip()


def model_lines(q: dict[str, Any]) -> list[str]:
    """A question's form and members as a model is shown them, when it checks or revises the question."""
    form = form_of(q)
    if form == "choice":
        wrong = [c for n, c in enumerate(q["choices"]) if n != q["correct"]]
        return ["Form: multiple choice", f"Correct choice: {q['choices'][q['correct']]}"] + [f"Wrong choice: {c}" for c in wrong]
    if form == "truefalse":
        return ["Form: true or false (the question is the statement)", f"The statement is: {'true' if q['truth'] else 'false'}"]
    if form == "cloze":
        return ["Form: fill in the blank", f"Missing words: {q['accept'][0]}"] + [f"Also accepted: {a}" for a in q["accept"][1:]]
    return []


# --------------------------------------------------------------------------
# In the firmware-format book


def to_x(q: dict[str, Any]) -> dict[str, Any] | None:
    """The ``x_form`` member of a prompt in the firmware-format book. None for an open question."""
    form = form_of(q)
    if form == "open":
        return None
    out = members(q)
    if form == "truefalse":
        out["statement"] = q["prompt"]
    out["explanation"] = q["answer"]
    return out


def from_x(entry: dict[str, Any]) -> dict[str, Any]:
    """A prompt of a firmware-format book as a question record: ``id``, ``prompt``, ``answer`` and its form's
    members. An ``x_form`` that is not as ``to_x`` writes it is passed over, and the prompt is an open question."""
    q = {"id": entry["id"], "prompt": entry["prompt"], "answer": entry["answer"]}
    x = entry.get("x_form")
    if not isinstance(x, dict) or x.get("form") not in GRADED or not isinstance(x.get("explanation"), str):
        return q
    made = {**q, **members(x), "answer": x["explanation"]}
    if x["form"] == "truefalse":
        if not isinstance(x.get("statement"), str):
            return q
        made["prompt"] = x["statement"]
    return q if problems(made) else made
