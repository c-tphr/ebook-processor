"""The worked chapter of ``docs/COMPREHENSION-PLAN.md``, as a book the tests can prepare both ways.

A messenger takes a letter to the council. Early on, the reader learns the road he takes and who knows the letter
was forged. Later, two councillors read its delivery differently. The checkpoint asks the reader to explain their
different reactions.

``book()`` is the chapter as an EPUB, made by the app's own converter. ``script()`` is a stand-in for the model
(``tests/scripted.py``) that writes for it what the plan's acceptance examples describe. It goes by the instructions
it is given, so the same stand-in serves a book prepared for general attention and one prepared with explicit links.
"""

from __future__ import annotations

import re
from typing import Any

from ebook_processor import sources
from tests.scripted import EMPTY_FORM, EMPTY_PATHWAY, LINKED, Script, forms_in

NAME = "The Forged Letter.md"
TEXT = """# The Forged Letter

## The Council Road

Tomas left the steward's house before the bells, with the letter sewn into the lining of his coat. Two roads lead from Harrow Gate to the council at Venn. The ridge road is the quick one, a day and a half over the high ground, and it is the road every courier is told to take. The river road follows the Arl through the low meadows and takes almost three days.

At the fork he stopped. A carter coming down from the hills told him the bridge at Stone Ford had gone in the spring flood and would not carry a horse. So Tomas turned his mare onto the river road, the slow one, and resigned himself to three days of mud. He did not think the delay mattered. He had been told only that the letter must arrive with its seal unbroken.

Tomas did not know what he carried. The letter claimed to come from the old duke, and granted the steward the wardship of the Arl valley. The duke had never seen it. The steward, Maren, had ordered it written two nights before, and the scribe Ilse had copied the duke's hand from an old charter until the two could not be told apart. Only those two knew the letter was forged: Maren, who wanted the valley, and Ilse, who wanted her debts paid.

Ilse had asked for one thing in return for her silence, and Maren had promised it. Then Maren pressed the duke's seal into the wax herself, with a ring that had been reported lost a year ago. The wax was still soft when she gave the letter to Tomas. She watched him ride out and told the gatekeeper that the courier had gone by the ridge.

On the river road Tomas slept in a mill, crossed the Arl twice by ferry and lost half a day to a lame mare. Nobody stopped him and nobody asked his business. He reached Venn on the evening of the third day, washed the mud from his boots at the trough, and asked for the council's clerk.

The council read the letter the next morning. Councillor Varga held it to the window, turned it over and smiled. The seal was whole and the ribbon had not been cut. A letter that had spent three days on the open road, she said, and had come through with its seal unbroken, was a letter nobody had dared to touch. To her the slow road proved the letter honest, and she moved that the wardship be granted at once.

Councillor Oduya asked to see the wax. It was bright, he said, far brighter than a seal that had lain in a duke's writing desk ought to be. And why would a grant of such weight come by the river road, with one rider, when the duke's own couriers always took the ridge? Somebody had wanted this letter to arrive late, or to arrive unseen. He would not vote until a rider had gone to the duke and come back.

Varga laughed at him, and the vote was put off until the afternoon. Tomas, waiting in the yard for his answer, heard his own road discussed through the open window, and for the first time wondered what was in the letter.

## The Afternoon Vote

The council met again when the sun was past the tower. Oduya had sent his own rider north by the ridge road an hour earlier, without telling anyone. Varga had spent the same hour among the benches, and she had the votes she needed.

The clerk read the letter aloud a second time. Nobody spoke against it but Oduya, and the wardship of the Arl valley was granted to the steward by nine votes to one. Tomas was given a sealed answer and a fresh horse, and told to take the ridge road home.
"""

_BLOCK = re.compile(r"^\[(d\d+-p\d+)\] ([a-z-]+)[^\n]*\n(.*)$", re.M)


def book() -> bytes:
    """The chapter and the short one after it, as an EPUB."""
    return sources.convert(NAME, TEXT.encode("utf-8"), web=False).epub


def _paragraph(blocks: list[tuple[str, str, str]], words: str) -> str:
    return next(ref for ref, kind, text in blocks if kind == "paragraph" and words in text)


def script() -> Script:
    """A stand-in for the model that writes the plan's examples for this book."""
    made = Script()
    made.draft_hook = lambda reply, prompt: worked(prompt, LINKED in made.system, forms_in(made.schema, "ranges", "checks"))
    return made


# The ephemeral questions as multiple choice, for a book whose ephemeral questions may not be open: the right choice,
# the wrong ones, and the explanation shown after the reader has answered.
CHOICES = {
    "Which road does Tomas take to the council, and why?": (
        "The slow river road, because the bridge at Stone Ford was down",
        ["The quick ridge road, because every courier is told to take it", "The ridge road, because the river had flooded",
         "The river road, because Maren told him to avoid the ridge"],
        "A carter told him the bridge at Stone Ford had gone, so he turned onto the river road."),
    "Who knows that the letter Tomas carries was forged?": (
        "Only the steward Maren and the scribe Ilse",
        ["Tomas, the courier who carries it", "Councillor Oduya, who later doubts it", "The old duke, whose hand was copied"],
        "Maren ordered the letter written and Ilse copied the duke's hand; nobody else knew."),
    "What did Maren tell the gatekeeper about the courier's road?": (
        "That he had gone by the ridge road",
        ["That he had gone by the river road", "That he had not left yet", "That he had gone straight to the duke"],
        "She watched Tomas ride out and told the gatekeeper he had gone by the ridge."),
}


def worked(prompt: str, linked: bool, check_forms: list[str] | None = None) -> dict[str, Any]:
    """What a careful model writes for this book in the graduated pathway: the plan's acceptance examples. The
    chapter of the letter is one section and the short chapter after it another. ``linked``: the reader asked for
    ephemeral questions that build toward the practice questions explicitly. ``check_forms`` are the forms the reply
    schema allows for an ephemeral question: where open questions are not among them, they are multiple choice."""
    blocks = [(m.group(1), m.group(2), m.group(3)) for m in _BLOCK.finditer(prompt.split("\nTEXT\n", 1)[1])]
    refs = [ref for ref, _, _ in blocks]
    fork, forged = _paragraph(blocks, "At the fork he stopped"), _paragraph(blocks, "Only those two knew")
    seal = _paragraph(blocks, "into the wax herself")
    varga, oduya = _paragraph(blocks, "Councillor Varga held it"), _paragraph(blocks, "Councillor Oduya asked")
    vote = _paragraph(blocks, "by nine votes to one")
    second = next(ref for ref, kind, text in blocks if kind == "heading" and "Afternoon Vote" in text)

    def check(prompt: str, answer: str, after: str, supports: list[str]) -> dict[str, Any]:
        made = {"prompt": prompt, "answer": answer, "supporting_paragraphs": [after], "after_paragraph": after, "supports": supports}
        if not check_forms:
            return made
        if "open" in check_forms:
            return {**EMPTY_FORM, "form": "open", **made}
        assert "choice" in check_forms, "the worked chapter has its ephemeral questions as open questions or as multiple choice"
        right, wrong, why = CHOICES[prompt]
        return {**made, **EMPTY_FORM, "form": "choice", "answer": why, "correct_choice": right, "wrong_choices": wrong}

    forgery = check("Who knows that the letter Tomas carries was forged?",
                    "Only the steward Maren, who ordered it, and the scribe Ilse, who wrote it.", forged, ["reactions"] if linked else [])
    road = check("Which road does Tomas take to the council, and why?",
                 "The slow river road: the bridge on the ridge road was washed away.", fork, [])
    gate = check("What did Maren tell the gatekeeper about the courier's road?", "That he had gone by the ridge.", seal, [])
    reactions = {
        **EMPTY_PATHWAY, "key": "reactions", "reasoning": "explain",
        "prompt": "Why do Councillors Varga and Oduya react so differently to the way the steward's letter was delivered?",
        "answer": "Both look at the same facts. Varga takes the unbroken seal after three days on the slow road as proof that "
                  "nobody tampered with the letter. Oduya takes the fresh wax and the slow road with a single rider as signs "
                  "that someone wanted the letter to arrive late or unseen.",
        "supporting_paragraphs": [varga, oduya],
        "accounts_for": ["The letter came by the slow river road, with one rider, and took three days.",
                         "Its seal arrived unbroken.", "The wax of the seal was unusually bright and fresh."],
        # with links, "Help me recall" brings back the ephemeral question that supports it; without, its own cue
        **({} if linked else {"cue_prompt": "Who knows that the steward's letter was forged?",
                              "cue_answer": "Only the steward Maren and the scribe Ilse."})}
    result = {**EMPTY_PATHWAY, "key": "vote", "prompt": "How did the council vote on the wardship of the Arl valley?",
              "answer": "It granted the wardship to the steward by nine votes to one, Oduya alone voting against.",
              "supporting_paragraphs": [vote]}
    return {"ranges": [
        {"kind": "section", "first_paragraph": refs[0], "last_paragraph": refs[refs.index(second) - 1], "label": "The Council Road",
         "reason": "", "prompts": [reactions], "checks": [forgery] if linked else [road, forgery, gate]},
        {"kind": "section", "first_paragraph": second, "last_paragraph": refs[-1], "label": "The Afternoon Vote", "reason": "",
         "prompts": [result], "checks": []}]}
