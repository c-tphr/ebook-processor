"""The model's part in the notes folder: connections, answers from the notes, questions from a note."""

from __future__ import annotations

from pathlib import Path

import pytest

from ebook_processor import assist
from ebook_processor.llm import BackendError, ScriptedBackend
from ebook_processor.settings import from_options
from ebook_processor.vault import Vault
from tests.test_vault import BOOK

SETTINGS = from_options()


@pytest.fixture
def folder(tmp_path: Path) -> Vault:
    vault = Vault(tmp_path / "notes")
    vault.ensure()
    vault.put_book("b-20261118-101500-almanac", "The Almanac", BOOK)
    return vault


def backend(reply) -> ScriptedBackend:
    return ScriptedBackend(lambda role, system, prompt, schema: reply(prompt) if callable(reply) else reply)


def ref_of(prompt: str, title: str) -> str:
    """The reference the message gave a note, as the model would read it off the list."""
    line = next(line for line in prompt.split("\n") if f"  {title}  |" in line or f'"{title}"' in line)
    return line.split()[0].rstrip(":")


def test_connections_name_what_was_shown_and_quote_what_is_there(folder):
    note = folder.create("A duty is kept by rule", "The keeper lights the lamp at sunset whatever the weather. A rule removes the daily decision. See [[Already linked]].")
    folder.create("Already linked", "The lamp and the rule again.")
    other = folder.create("Decide once", "A decision made in advance is cheaper than one made in the moment. The lamp is one such rule.")
    far = folder.create("Markets clear", "Prices move until buyers meet sellers.")
    sent = {}

    def reply(prompt: str) -> dict:
        sent["prompt"] = prompt
        return {"connections": [
            {"to": ref_of(prompt, "Decide once").lower(), "reason": "  The other note states the general principle\nthat this note is a case of. ",
             "mine": "A rule removes the daily decision.", "theirs": "A decision made in advance is cheaper than one made in the moment."},
            {"to": "P1", "reason": "The passage is the rule itself.", "mine": "Something this note never says.", "theirs": ""},
            {"to": ref_of(prompt, "Already linked"), "reason": "Linked before.", "mine": "", "theirs": ""},
            {"to": "N99", "reason": "Made up.", "mine": "", "theirs": ""},
            {"to": ref_of(prompt, "Markets clear"), "reason": "", "mine": "", "theirs": ""},
            {"to": ref_of(prompt, "Decide once"), "reason": "Twice the same.", "mine": "", "theirs": ""}]}

    asked = backend(reply)
    got = assist.connections(asked, folder, note["id"], SETTINGS)
    assert [(c["kind"], c["title"]) for c in got["connections"]] == [("note", "Decide once"), ("passage", "The Almanac")]
    first, second = got["connections"]
    assert first["id"] == other["id"] and first["link"] == "Decide once" and first["reason"] == "The other note states the general principle that this note is a case of."
    assert first["mine"] == "A rule removes the daily decision." and first["theirs"].startswith("A decision made in advance")
    assert second["mine"] == "" and second["theirs"].startswith("The lamp is lit at sunset") and second["link"] == "The Almanac#^h3f9a"
    assert got["sent"] == {"listed": 3, "full": 2, "passages": 1, "total": 3} and got["model"] == "scripted"
    prompt = sent["prompt"]
    assert prompt.startswith("THE NOTE\nTitle: A duty is kept by rule\n\nThe keeper lights the lamp at sunset") and "ALREADY LINKED\n" + ref_of(prompt, "Already linked") in prompt
    assert f"NOTE {ref_of(prompt, 'Decide once')} IN FULL: Decide once\nA decision made in advance" in prompt and "Prices move until buyers" in prompt  # the first line of every note
    assert "IN FULL: Markets clear" not in prompt and "PASSAGE P1 (highlighted in The Almanac)\nThe lamp is lit at sunset" in prompt
    assert "at most 6 connections" in asked.calls[0]["system"] and far["id"] not in prompt and asked.calls[0]["role"] == "draft"
    # nothing else in the folder: no call; a note with no text, or a book: said plainly
    alone = Vault(folder.root.parent / "alone")
    only = alone.create("Only note", "The single note of this folder.")
    silent = backend(lambda prompt: pytest.fail("the model was asked with nothing to connect"))
    assert assist.connections(silent, alone, only["id"], SETTINGS)["connections"] == []
    empty = folder.create("Empty", "")
    for bad in (empty["id"], "b-20261118-101500-almanac"):
        with pytest.raises(assist.AssistError):
            assist.connections(silent, folder, bad, SETTINGS)


def test_an_answer_comes_from_the_notes_and_names_its_sources(folder):
    rule = folder.create("A duty is kept by rule", "The keeper lights the lamp whatever the weather, because a rule removes the daily decision.")
    folder.create("Markets clear", "Prices move until buyers meet sellers.")

    def reply(prompt: str) -> dict:
        n, p = ref_of(prompt, "A duty is kept by rule"), "P1"
        return {"covered": True, "answer": f"Your note says a rule removes the daily decision [{n.lower()}] . The passage gives the rule: lit at sunset [{p}][N77].", "missing": ""}

    asked = backend(reply)
    got = assist.answer(asked, folder, "  Why does the keeper light the lamp at sunset\nwhatever the weather? ", SETTINGS)
    assert got["covered"] and got["answer"] == "Your note says a rule removes the daily decision [N1]. The passage gives the rule: lit at sunset [P1]."
    assert [(s["ref"], s["kind"], s["title"]) for s in got["sources"]] == [("N1", "note", "A duty is kept by rule"), ("P1", "passage", "The Almanac")]
    assert got["sources"][0]["id"] == rule["id"] and got["sources"][1]["block"] == "h3f9a" and got["closest"] == [] and got["sent"] == {"notes": 1, "passages": 1}
    prompt = asked.calls[0]["prompt"]
    assert prompt.startswith("QUESTION\nWhy does the keeper light the lamp at sunset whatever the weather?\n\nSOURCES\n\nN1: a note of the reader's, \"A duty is kept by rule\"")
    assert "Prices move" not in prompt and "P1: a passage highlighted in The Almanac\nThe lamp is lit at sunset" in prompt
    assert "Do not answer from your own knowledge" in asked.calls[0]["system"]
    kept = assist.answer_markdown("Why the lamp?", got, "2026-11-19")
    assert kept == ("**Question:** Why the lamp?\n\nYour note says a rule removes the daily decision [[A duty is kept by rule]]. The passage gives the rule: "
                    "lit at sunset [[The Almanac#^h3f9a]].\n\n*Answered by a model from my notes and highlights on 2026-11-19. Not my own words.*\n")
    # the notes do not hold it: said so, with what comes closest; an answer that names no source is not shown
    gap = assist.answer(backend({"covered": False, "answer": "", "missing": "Your notes say nothing about the fuel."}), folder, "What fuel did the lamp burn at sunset?", SETTINGS)
    assert not gap["covered"] and gap["answer"] == "" and gap["sources"] == [] and gap["missing"] == "Your notes say nothing about the fuel."
    assert [s["kind"] for s in gap["closest"]] == ["passage"]
    bare = assist.answer(backend({"covered": True, "answer": "Whale oil, as everyone knows.", "missing": ""}), folder, "What fuel did the lamp burn at sunset?", SETTINGS)
    assert not bare["covered"] and bare["answer"] == "" and bare["missing"].startswith("The model gave an answer without naming a source")
    # nothing related at all: no call is made
    silent = backend(lambda prompt: pytest.fail("the model was asked with no source"))
    nothing = assist.answer(silent, folder, "Who invented the bicycle?", SETTINGS)
    assert not nothing["covered"] and nothing["model"] is None and "Nothing in your notes" in nothing["missing"]
    for bad in ("", "a?", "x" * 601, None):
        with pytest.raises(assist.AssistError):
            assist.answer(silent, folder, bad, SETTINGS)


def test_a_question_is_made_from_a_note_and_checked_against_it(folder, tmp_path):
    made = folder.create("A duty is kept by rule", "The keeper lights the lamp whatever the weather. A rule removes the daily decision, as [[Decide once|deciding once]] does. ^b1\n\nSource: [[The Almanac#^h3f9a]]\n")
    note = folder.get(made["id"])
    replies = iter([{"question": "According to the note, why?", "answer": "Because."},     # refers to the note: sent back once
                    {"question": "Why does a fixed rule make a daily duty easier to keep?", "answer": "It removes the decision that would otherwise be made each day."}])
    asked = backend(lambda prompt: next(replies))
    wording = assist.propose(backend(lambda prompt: {"question": "Why does a fixed rule make a daily duty easier to keep?",
                                                     "answer": "It removes the decision that would otherwise be made each day."}),
                             folder, note, ["An earlier question?"], SETTINGS, "Ask why.")
    assert wording["question"].startswith("Why does a fixed rule") and wording["model"] == "scripted"
    seen = backend(lambda prompt: {"question": "Why does a fixed rule help?", "answer": "It removes the daily decision."})
    assist.propose(seen, folder, note, ["An earlier question?"], SETTINGS, "Ask why.")
    prompt = seen.calls[0]["prompt"]
    assert prompt.startswith("THE NOTE\nTitle: A duty is kept by rule\n\nThe keeper lights the lamp whatever the weather. A rule removes the daily decision, as deciding once does.\n\nSource: The Almanac")
    assert "^b1" not in prompt and "[[" not in prompt and "THE READER ASKS OF THIS QUESTION\nAsk why." in prompt and "ALREADY ASKED\n- An earlier question?" in prompt
    assert "must **stand alone**" in seen.calls[0]["system"] and "$" not in seen.calls[0]["system"]
    with pytest.raises(BackendError):
        assist.propose(backend({"question": "", "answer": ""}), folder, note, [], SETTINGS)
    with pytest.raises(assist.AssistError):
        assist.propose(seen, folder, folder.get(folder.create("Thin", "Too short.")["id"]), [], SETTINGS)
    # the store, and the mark by which a later change of the note is seen
    store = assist.load(tmp_path / "data")
    q = assist.add(store, note, {k: wording[k] for k in ("question", "answer")}, "model", wording["model"])
    assert q["id"].startswith("nq") and q["status"] == "proposed" and assist.of_note(store, note)[0]["stale"] is False
    ok = assist.check(backend({**{c: True for c in assist.CHECKS}, "problem": ""}), folder, note, q, SETTINGS)
    assert ok == {"passed": True, "criteria": {c: True for c in assist.CHECKS}, "problem": ""}
    judge = backend({**{c: True for c in assist.CHECKS}, "stands_alone": False, "problem": "It says 'the note'."})
    bad = assist.check(judge, folder, note, q, SETTINGS)
    assert not bad["passed"] and bad["criteria"]["stands_alone"] is False and bad["problem"] == "It says 'the note'."
    assert "ITS REFERENCE ANSWER\nIt removes the decision" in judge.calls[0]["prompt"] and judge.calls[0]["role"] == "check" and "one_right_answer" not in judge.calls[0]["system"]
    folder.save(note.id, note.body.replace("whatever the weather", "in any weather"), None, note.token)
    changed = folder.get(note.id)
    assert assist.of_note(store, changed)[0]["stale"] is True
    again = assist.reword(store, changed, q["id"], "Why does a rule make a duty easier to keep?", None)
    assert again["by"] == "you" and again["answer"] == wording["answer"] and assist.of_note(store, changed)[0]["stale"] is False
    with pytest.raises(assist.AssistError):
        assist.find(store, "nq00000000")
    q["status"] = "accepted"
    prompts = assist.practice_prompts(folder, store)
    assert list(prompts) == [q["id"]] and prompts[q["id"]][0].id == note.id and prompts[q["id"]][1]["prompt"].startswith("Why does a rule")
    folder.delete(note.id)
    assert assist.practice_prompts(folder, store) == {}                          # the note is in the trash: its card rests


def test_a_question_of_a_note_can_take_a_form_the_reader_ticked(folder):
    note = folder.get(folder.create("A duty is kept by rule", "The keeper lights the lamp whatever the weather. A rule removes the daily decision.")["id"])
    forms = from_options(forms="open,truefalse")
    asked = backend({"question": "A fixed rule adds a decision to each day.", "form": "truefalse", "truth": False,
                     "answer": "A rule removes the daily decision.", "choices": [], "correct": 0, "accept": []})
    wording = assist.propose(asked, folder, note, [], forms)
    assert wording["form"] == "truefalse" and wording["truth"] is False and "## The form of the question" in asked.calls[0]["system"]
    judge = backend({**{c: True for c in assist.CHECKS}, "one_right_answer": True, "problem": ""})
    assert assist.check(judge, folder, note, wording, SETTINGS)["criteria"]["one_right_answer"] is True and "one_right_answer" in judge.calls[0]["system"]
