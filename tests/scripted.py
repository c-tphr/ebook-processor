"""A stand-in for Claude that answers from the task message alone, so that runs can be tested without Claude.

``Script`` reads the task messages exactly as ``ebook_processor.prompts`` writes them. It drafts one section per
``blocks_per_section`` blocks with one question citing the section's first prose block, excludes a document whose
href names a title page, passes every question, and can be told to misbehave in the ways the pipeline must handle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ebook_processor.llm import ScriptedBackend
from ebook_processor.prompts import CRITERIA

GROUP_RE = re.compile(r"^=== (d\d+) (\S+) ===$", re.M)
BLOCK_RE = re.compile(r"^\[(d\d+-p\d+)\] ([a-z-]+)[^\n]*, (\d+) words?$", re.M)
SECTION_RE = re.compile(r"^=== SECTION (s\d+): ", re.M)
QUESTION_RE = re.compile(r"^QUESTION \[([^\]]+)\]$", re.M)
FIX_RE = re.compile(r"^QUESTION TO FIX \[([^\]]+)\]$", re.M)
JUDGE_RE = re.compile(r"^JUDGE THE BREAK of (s\d+): (yes|no)$", re.M)
EXCLUDED_RE = re.compile(r"^\[(\d+)\] (d\d+-p\d+) to (d\d+-p\d+) ", re.M)


def text_groups(prompt: str) -> list[tuple[str, str, list[tuple[str, str, int]]]]:
    """(document prefix, href, [(ref, kind, words)]) for each document in a draft task's TEXT."""
    text = prompt.split("\nTEXT\n", 1)[1]
    marks = list(GROUP_RE.finditer(text))
    out = []
    for n, m in enumerate(marks):
        body = text[m.end() : marks[n + 1].start() if n + 1 < len(marks) else len(text)]
        out.append((m.group(1), m.group(2), [(b.group(1), b.group(2), int(b.group(3))) for b in BLOCK_RE.finditer(body)]))
    return out


@dataclass
class Script:
    blocks_per_section: int = 4
    exclude: Callable[[str], bool] = lambda href: "title" in href
    fail: dict[str, int] = field(default_factory=dict)  # question id -> how many checks it fails
    withdraw: set[str] = field(default_factory=set)  # question ids the reviser withdraws
    reject_exclusions: bool = False
    stop_early: bool = False  # when allowed to stop early, cover only about two thirds
    draft_hook: Callable[[dict[str, Any], str], dict[str, Any]] | None = None
    checks_seen: dict[str, int] = field(default_factory=dict)
    drafts: int = 0
    questions: int = 0
    readings: int = 0

    def backend(self) -> ScriptedBackend:
        return ScriptedBackend(self.reply)

    def reply(self, role: str, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if schema.get("required") == ["question", "answer"]:
            return self.question(prompt)
        return getattr(self, role)(prompt)

    def question(self, prompt: str) -> dict[str, Any]:
        """A question for a highlight: about its first words, and each one for the same highlight different."""
        marked = prompt.split("\nHIGHLIGHT (", 1)[1].split("\n", 2)[1]
        again = prompt.count("\n- ") if "\nALREADY ASKED\n" in prompt else 0
        note = " (steered by the note)" if "\nTHE READER'S NOTE\n" in prompt else ""
        if "\nTHE READER ASKS OF THIS QUESTION\n" in prompt:
            note += f" (as asked: {prompt.split(chr(10) + 'THE READER ASKS OF THIS QUESTION' + chr(10), 1)[1].splitlines()[0]})"
        self.questions += 1
        return {"question": f"What does the book say about \"{' '.join(marked.split()[:4])}\"{note}, take {again + 1}?",
                "answer": f"That {' '.join(marked.split()[:12])}."}

    def reading(self, prompt: str) -> dict[str, Any]:
        """A reading of the reader's wishes: their purpose restated, the second document as the one it bears on,
        one question back the first time, and the reader's feedback worked in when there is any."""
        self.readings += 1
        words = prompt.split("\nTHE READER'S WORDS\n", 1)[1].split("\nTHE BOOK'S DOCUMENTS", 1)[0]
        fenced = re.findall(r"~~~\n(.*?)\n~~~", words, re.S)
        again = "\nYOUR EARLIER READING" in prompt
        said = words.split("WHAT THE READER SAYS ABOUT YOUR EARLIER READING\n~~~\n", 1)[1].split("\n~~~", 1)[0] if "WHAT THE READER SAYS" in words else ""
        told = "; ".join(line.strip() for line in words.split("THE READER'S ANSWERS TO YOUR QUESTIONS\n", 1)[1].split("\n\n", 1)[0].splitlines()
                         if line.startswith("  ")) if "THE READER'S ANSWERS" in words else ""
        docs = re.findall(r"^(d\d+)[ ,]", prompt.split("THE BOOK'S DOCUMENTS, IN READING ORDER\n", 1)[1], re.M)
        focus = f"The reader wants: {fenced[0] if fenced else 'nothing'}" + (f" Changed as asked: {said}." if said else "") \
            + (f" Answered: {told}." if told else "")
        return {"focus": focus, "style": "Short answers." if "How I like questions:\n~~~" in words else "",
                "chapters": [{"doc": docs[1], "why": "It opens on the reader's subject."}] if len(docs) > 1 else [],
                "cannot": "Multiple choice cannot be written; recall questions are written instead." if "multiple choice" in words.lower() else "",
                "gaps": "", "questions": [] if again else ["Do you mean the lamp or the fog?"]}

    def draft(self, prompt: str) -> dict[str, Any]:
        self.drafts += 1
        may_stop = "so you may stop" in prompt
        redraft = "An earlier draft excluded all of this text" in prompt
        ranges = []
        for prefix, href, blocks in text_groups(prompt):
            if self.exclude(href) and not redraft:
                ranges.append({"kind": "excluded", "first_paragraph": blocks[0][0], "last_paragraph": blocks[-1][0],
                               "label": "", "reason": "Front matter: title page.", "prompts": []})
                continue
            if may_stop and self.stop_early:
                blocks = blocks[: max(1, len(blocks) * 2 // 3)]
            for start in range(0, len(blocks), self.blocks_per_section):
                span = blocks[start : start + self.blocks_per_section]
                if len(blocks) - (start + len(span)) == 1:  # do not leave a one-block tail (it may have no words)
                    span = blocks[start:]
                support = next((ref for ref, kind, words in span if kind == "paragraph" and words), span[0][0])
                ranges.append({
                    "kind": "section", "first_paragraph": span[0][0], "last_paragraph": span[-1][0],
                    "label": f"Section at {span[0][0]}", "reason": "",
                    "prompts": [{"prompt": f"What does the passage starting at {support} of {href} say?",
                                 "answer": f"What {support} says.", "supporting_paragraphs": [support]}]})
                if len(span) > self.blocks_per_section:
                    break
        reply = {"ranges": ranges}
        return self.draft_hook(reply, prompt) if self.draft_hook else reply

    def check(self, prompt: str) -> dict[str, Any]:
        out: dict[str, Any] = {"prompts": [], "sections": []}
        marks = list(SECTION_RE.finditer(prompt))
        for n, m in enumerate(marks):
            body = prompt[m.end() : marks[n + 1].start() if n + 1 < len(marks) else len(prompt)]
            first = BLOCK_RE.search(body)
            cite = next((b.group(1) for b in BLOCK_RE.finditer(body) if b.group(2) == "paragraph"), first.group(1))
            for q in QUESTION_RE.finditer(body):
                qid = q.group(1)
                seen = self.checks_seen[qid] = self.checks_seen.get(qid, 0) + 1
                ok = seen > self.fail.get(qid, 0)
                out["prompts"].append({
                    "id": qid, **{c: True for c in CRITERIA}, "supported_by_section": ok, "citations": [cite],
                    "evidence": f"{cite} states it.", "problem": "" if ok else "The answer adds a detail.",
                    "suggested_fix": "" if ok else "Remove the detail.", "minor_note": ""})
            judge = JUDGE_RE.search(body)
            if judge and judge.group(2) == "yes":
                out["sections"].append({"id": m.group(1), "natural_boundary": True, "evidence": "Ends at a heading."})
        return out

    def revise(self, prompt: str) -> dict[str, Any]:
        revisions = []
        marks = list(SECTION_RE.finditer(prompt))
        for n, m in enumerate(marks):
            body = prompt[m.end() : marks[n + 1].start() if n + 1 < len(marks) else len(prompt)]
            cite = next(b.group(1) for b in BLOCK_RE.finditer(body) if b.group(2) == "paragraph")
            for q in FIX_RE.finditer(body):
                qid = q.group(1)
                if qid in self.withdraw:
                    revisions.append({"id": qid, "action": "withdraw", "prompt": "", "answer": "",
                                      "supporting_paragraphs": []})
                else:
                    n_checks = self.checks_seen.get(qid, 0)
                    revisions.append({"id": qid, "action": "revise", "prompt": f"What does {cite} say (try {n_checks})?",
                                      "answer": f"What {cite} says, revised after check {n_checks}.",
                                      "supporting_paragraphs": [cite]})
        return {"revisions": revisions}

    def exclusions(self, prompt: str) -> dict[str, Any]:
        return {"exclusions": [{"index": int(m.group(1)), "acceptable": not self.reject_exclusions,
                                "evidence": "It is a title page." if not self.reject_exclusions
                                else "It holds the author's own text."} for m in EXCLUDED_RE.finditer(prompt)]}
