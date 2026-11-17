"""From a run's state to the files the user gets. No call to Claude happens here, so it can be repeated at will.

In the output folder, for ``Book.epub``:

    Book.prepared.epub     the firmware format: invisible markers and rsvp/checkpoints.json, text untouched
    Book.checkpoints.epub  visible question and answer pages, for any reading app
    Book.questions.json    the questions as structured data
    Book.anki.apkg         an Anki deck
    Book.cards.tsv         the same cards as tab-separated text, for other apps
    Book.report.md/.json   what was made, what was left out and why
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__, exports, reading
from .pipeline import KEPT, draft_bytes, kept, now, write_json
from .prep import epubcheck as epubcheck_mod
from .prep.build import BuildError, build_bytes
from .prep.draft import check_draft
from .prep.epub import Book
from .prep.validate import NOT_RUN, PASS, validate_bytes
from .visible import build_visible

OUTPUT_SUFFIXES = (".prepared.epub", ".checkpoints.epub", ".questions.json", ".anki.apkg", ".cards.tsv",
                   ".report.md", ".report.json")
CRITERIA_WORDS = {
    "supported_by_section": "the answer says something its section does not",
    "accepts_paraphrase": "the answer is too vague, thin or detailed to grade against",
    "answerable_from_section": "the question cannot be answered from its section alone",
    "does_not_give_away_answer": "the question gives its answer away",
    "stands_alone": "the question cannot be understood without the page",
}


def stem_of(source_file: str) -> str:
    return source_file[:-5] if source_file.lower().endswith(".epub") else source_file


def _no_epubcheck(path: Path) -> epubcheck_mod.Report:
    raise epubcheck_mod.EpubcheckUnavailable("not asked for; run with the EPUBCheck option to include it")


def _epubcheck_line(data: bytes, name: str, folder: Path) -> str:
    """EPUBCheck on the book with visible checkpoints, as one line for the report."""
    path = folder / name
    try:
        report = epubcheck_mod.run_epubcheck(path)
    except (epubcheck_mod.EpubcheckUnavailable, RuntimeError, OSError) as exc:
        return f"EPUBCheck could not be run ({exc})."
    if report.errors:
        return (f"EPUBCheck {report.version} reports {len(report.errors)} error(s): "
                + "; ".join(f"{m.id} {m.message}" for m in report.errors[:5]))
    return f"EPUBCheck {report.version}: no errors, {len(report.warnings)} warning(s)."


def build_outputs(book: Book, original: bytes, state: dict[str, Any], out_dir: Path,
                  epubcheck: bool = False) -> dict[str, Any]:
    """Write every output file and return the report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    stem = stem_of(state["source"]["file"])
    files: dict[str, str | None] = {}
    notes: list[str] = []

    draft_raw = draft_bytes(state)
    work.mkdir(parents=True, exist_ok=True)
    (work / "draft.json").write_bytes(draft_raw)
    draft, issues, plan = check_draft(draft_raw, book)
    if issues:  # cannot happen after a completed run; a hand-edited state can do it
        raise ValueError("the saved run no longer fits the book:\n" + "\n".join(f"  {i}" for i in issues[:20]))

    # 1. the firmware format
    checks: list[dict[str, Any]] = []
    prepared_ok = False
    name = f"{stem}.prepared.epub"
    try:
        prepared = build_bytes(book, draft_raw).data
    except BuildError as exc:
        (out_dir / name).unlink(missing_ok=True)
        files["prepared"] = None
        notes.append("The firmware-format book could not be built: " + "; ".join(str(i) for i in exc.issues[:5])
                     + ". A book this size exceeds what the firmware's checkpoint file can hold; a longer section "
                       "length gives fewer sections.")
    else:
        (out_dir / name).write_bytes(prepared)
        files["prepared"] = name
        outcome = validate_bytes(original, state["source"]["file"], prepared, name, draft_raw, "draft.json",
                                 epubcheck=None if epubcheck else _no_epubcheck, created=now())
        write_json(work / "validation.json", outcome.result)
        checks = outcome.result["checks"]
        prepared_ok = all(c["status"] == PASS or (c["name"] == "epubcheck" and c["status"] == NOT_RUN and not epubcheck)
                          for c in checks)

    # 2. visible checkpoints
    name = f"{stem}.checkpoints.epub"
    visible, visible_notes = build_visible(book, draft, plan, state["settings"].get("title_suffix", " (checkpoints)"),
                                           preview=bool(state["settings"].get("preview")))
    (out_dir / name).write_bytes(visible)
    files["visible"] = name
    notes += visible_notes
    if epubcheck:
        notes.append(f"{name}: " + _epubcheck_line(visible, name, out_dir))

    # 3. the questions on their own
    read = reading.load_state(out_dir)
    met = {a["id"] for a in (read or {}).get("x_attempts", [])}
    doc = exports.questions_document(book, state, reading.results(read), met)
    cards = state.get("cards", "all")
    if cards != "all":
        chosen = len(exports.selected_cards(doc, cards))
        notes.append(f"The Anki deck and the text file hold {chosen} of the {len(doc['questions'])} questions: "
                     + ("those you missed on the first try." if cards == "missed" else "those you have met while reading."))
    write_json(out_dir / f"{stem}.questions.json", doc)
    files["questions"] = f"{stem}.questions.json"
    (out_dir / f"{stem}.cards.tsv").write_text(exports.tsv_text(doc, cards), encoding="utf-8", newline="")
    files["tsv"] = f"{stem}.cards.tsv"
    try:
        exports.write_anki(doc, out_dir / f"{stem}.anki.apkg", cards)
        files["anki"] = f"{stem}.anki.apkg"
    except ImportError:
        files["anki"] = None
        notes.append("The Anki deck was not written: the Python package 'genanki' is not installed "
                     "(pip install genanki).")

    files["report"] = f"{stem}.report.md"
    files["report_json"] = f"{stem}.report.json"
    report = make_report(state, doc, files, checks, prepared_ok, notes)
    write_json(out_dir / f"{stem}.report.json", report)
    (out_dir / f"{stem}.report.md").write_text(report_markdown(report), encoding="utf-8", newline="\n")
    return report


def make_report(state: dict[str, Any], doc: dict[str, Any], files: dict[str, str | None],
                checks: list[dict[str, Any]], prepared_ok: bool, notes: list[str]) -> dict[str, Any]:
    questions = state["questions"]
    sections = state["sections"]
    kept_questions = [q for q in questions if kept(q)]
    counts = {
        "sections": len(sections),
        "checkpoints": len({q["section"] for q in kept_questions}),
        "questions": len(kept_questions),
        "passed": sum(q["status"] == "passed" for q in kept_questions),
        "unchecked": sum(q["status"] in ("unchecked", "unverified") for q in kept_questions),
        "revised": sum(bool(q["history"]) for q in kept_questions),
        "left_out": len(questions) - len(kept_questions),
        "failed": sum(q["status"] == "failed" for q in questions),
        "withdrawn": sum(q["status"] == "withdrawn" for q in questions),
        "removed": sum(bool(q.get("removed")) and q["status"] in KEPT for q in questions),
        "words_in_sections": sum(s["word_count"] for s in doc["sections"]),
        "words": state["source"]["words"],
    }
    by_id = {s["id"]: s for s in sections}
    return {
        "format": "ebook-processor-report",
        "version": 1,
        "generated": now(),
        "tool_version": __version__,
        "complete": state["stage"] == "done",
        "book": doc["book"],
        "run_id": state["run_id"],
        "settings": state["settings"],
        "instructions_version": state["instructions_version"],
        "counts": counts,
        "files": files,
        "firmware_format_ok": prepared_ok,
        "cards": state.get("cards", "all"),
        "structure_checks": [{"name": c["name"], "title": c["title"], "status": c["status"],
                              "failures": c["failures"]} for c in checks],
        "left_out": doc["left_out"],
        "minor_notes": [{"id": q["id"], "section": q["section"], "question": q["prompt"], "note": q["minor_note"]}
                        for q in kept_questions if q["minor_note"]],
        "unchecked": [{"id": q["id"], "section": q["section"], "question": q["prompt"], "why": q["problem"]}
                      for q in kept_questions if q["status"] != "passed"],
        "doubted_breaks": [{"id": s["id"], "label": s["label"], "first_paragraph": s["first_paragraph"],
                            "last_paragraph": s["last_paragraph"], "note": s["boundary_note"]}
                           for s in sections if s["natural_boundary"] is False],
        "sections_without_questions": [s["id"] for s in sections
                                       if not any(q["section"] == s["id"] for q in kept_questions)],
        "excluded": [{"first_paragraph": e["first_paragraph"], "last_paragraph": e["last_paragraph"],
                      "chapter_href": e["chapter_href"], "reason": e["reason"], "acceptable": e["acceptable"],
                      "note": e["note"]} for e in state["excluded"]],
        "notes": notes,
        "warnings": state["warnings"],
        "usage": state["usage"],
        "section_labels": {sid: s["label"] for sid, s in by_id.items()},
    }


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _price(usage: dict[str, Any]) -> str:
    """The price part of the report's usage line. Claude Code reports what a call would cost at list prices;
    Codex reports nothing, so its calls are left out of the sum, and a run made with Codex alone has no price."""
    unpriced = usage.get("unpriced_calls", 0)
    if usage["calls"] and unpriced >= usage["calls"]:
        return ""
    return f"list-price equivalent ${usage['cost_usd']:.2f}" \
        + (" for the Claude Code calls, as Codex reports no price" if unpriced else "") + "; "


def _table(header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines + [""]


def report_markdown(r: dict[str, Any]) -> str:
    c = r["counts"]
    title = r["book"]["title"]
    lines = [f"# {title}: checkpoint report", ""]
    if not r["complete"]:
        lines += ["**This run is not finished.** The files below hold what was ready when it stopped. Give the same "
                  "command again to continue.", ""]
    summary = (f"{c['questions']} questions at {c['checkpoints']} checkpoints, in {c['sections']} sections "
               f"({c['words_in_sections']:,} of the book's {c['words']:,} words).")
    if r["settings"]["rounds"] == 0:
        summary += " The questions were not checked (checking was switched off)."
    else:
        summary += f" {c['passed']} passed the independent check"
        summary += f", {c['revised']} of them after a revision." if c["revised"] else "."
        if c["unchecked"]:
            summary += f" {c['unchecked']} could not be checked and are kept unchecked."
    if c["left_out"]:
        parts = [f"{c[k]} {word}" for k, word in (("failed", "failed the check"), ("withdrawn", "withdrawn by the drafter"),
                                                   ("removed", "removed by you")) if c[k]]
        summary += f" {c['left_out']} left out ({', '.join(parts)})."
    lines += [summary, ""]

    f = r["files"]
    lines += ["## Files", ""]
    rows = [
        (f["prepared"] or "(not built)", "For the Picco firmware and readers built for this format: invisible "
                                           "markers and a checkpoint data file; the text is untouched."
         + ("" if r["firmware_format_ok"] or not f["prepared"] else " **Its structure checks did not all pass; see below.**")),
        (f["visible"], "For any other reading app: each question on its own page, its answer on the next."),
        (f["questions"], "The questions as structured data."),
        (f["anki"] or "(not written)", "Anki deck: one card per question, with the supporting passage on the back."),
        (f["tsv"], "The same cards as tab-separated text, for other spaced-repetition apps."),
    ]
    lines += _table(("File", "What it is"), rows)

    if r["left_out"]:
        lines += ["## Questions left out", ""]
        rows = []
        for q in r["left_out"]:
            why = q["reason"]
            if q["failed_criteria"]:
                why += " " + "; ".join(CRITERIA_WORDS.get(k, k) for k in q["failed_criteria"]).capitalize() + "."
            if q["problem"] and q["status"] != "removed":
                why += " Checker: " + q["problem"]
            rows.append((q["id"], q["section_label"], q["question"], why))
        lines += _table(("Id", "Section", "Question", "Why"), rows)
    if r["unchecked"] and r["settings"]["rounds"] > 0:
        lines += ["## Kept without a check", ""]
        lines += _table(("Id", "Question", "Why"), [(q["id"], q["question"], q["why"]) for q in r["unchecked"]])
    if r["minor_notes"]:
        lines += ["## Small remarks on questions that passed", "",
                  "The checker passed these and noted something minor. Nothing needs doing.", ""]
        lines += _table(("Id", "Question", "Remark"), [(q["id"], q["question"], q["note"]) for q in r["minor_notes"]])
    if r["doubted_breaks"]:
        lines += ["## Section breaks the checker doubted", "",
                  "These sections were kept as drafted. A doubted break does not affect the questions.", ""]
        lines += _table(("Section", "Paragraphs", "Checker's remark"),
                        [(f"{s['id']} {s['label']}", f"{s['first_paragraph']} to {s['last_paragraph']}", s["note"])
                         for s in r["doubted_breaks"]])
    if r["excluded"]:
        lines += ["## Text in no section", ""]
        lines += _table(("Paragraphs", "Reason", "Review"),
                        [(f"{e['first_paragraph']} to {e['last_paragraph']}", e["reason"],
                          "not reviewed" if e["acceptable"] is None else
                          ("accepted" if e["acceptable"] else "doubted: " + e["note"])) for e in r["excluded"]])
    if r["structure_checks"]:
        lines += ["## Structure checks of the firmware-format book", ""]
        names = {"pass": "Passed", "fail": "Failed", "incomplete": "Incomplete", "not-run": "Not run"}
        lines += _table(("Check", "Result"), [(ch["title"], names.get(ch["status"], ch["status"])
                                                + ("" if not ch["failures"] or ch["status"] == "not-run" else ": "
                                                   + "; ".join(x["message"] for x in ch["failures"][:3])))
                                               for ch in r["structure_checks"]])
    if r["notes"] or r["warnings"]:
        lines += ["## Notes", ""] + [f"- {n}" for n in r["notes"] + r["warnings"]] + [""]

    s = r["settings"]
    u = r["usage"]
    lines += ["## How it was made", ""]
    models = ", ".join(f"{role}: {model}" for role, model in sorted(u["models"].items())) or "not recorded"
    lines += _table(("", ""), [
        ("Source", f"{r['book']['source_file']} (SHA-256 {r['book']['source_sha256']})"),
        ("Section length", f"{s['min_words']} to {s['max_words']} words; at most {s['max_questions']} questions each"),
        ("Tables, code, formulas, figures", {"prose": "not shown to the model", "text": "shown as text; figures by caption only",
                                             "figures": "shown as text; figures viewed"}[s["content"]]),
        ("Kind of book", s["kind"]),
        ("Checks per question", "none" if s["rounds"] == 0 else f"up to {s['rounds']}"),
        ("Question previews", "a page before each section, in the book with visible checkpoints" if s.get("preview") else "none"),
        ("Instructions version", r["instructions_version"]),
        ("Models", models),
        ("Calls to the models", f"{u['calls']} ({u['input_tokens']:,} tokens in, {u['output_tokens']:,} out; "
                                f"{_price(u)}{u['seconds'] / 60:.0f} minutes of model time)"),
        ("Tool", f"ebook-processor {r['tool_version']}, run {r['run_id']}, {r['generated']}"),
    ])
    return "\n".join(lines).rstrip() + "\n"


def load_report(out_dir: Path, source_file: str) -> dict[str, Any] | None:
    path = out_dir / f"{stem_of(source_file)}.report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
