"""The command line (``ebook_processor/cli.py``): what it takes, what it prints and what it refuses.

The commands of ``update`` are in ``test_update.py`` and ``models`` with Codex in ``test_codex.py``, beside the
stand-in programs they need. No test calls a model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebook_processor import cli, runner
from ebook_processor.runner import try_chapter
from ebook_processor.steer import Steer
from tests.helpers import run
from tests.paths import FIXTURE
from tests.scripted import Script
from tests.sources.makers import CLUTTER, MARKDOWN, PAGES, TEXT_BOOK, body


# --------------------------------------------------------------------------
# Errors of use, and rebuild


def test_the_command_line_reports_usage_and_input_errors(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["process", str(FIXTURE), "--section-words", "nonsense"])
    assert exc.value.code == 2
    assert cli.main(["rebuild", str(tmp_path / "nothing")]) == 1
    assert "holds no run" in capsys.readouterr().err


def test_the_command_line_rebuilds(tmp_path, capsys):
    run(tmp_path)
    assert cli.main(["rebuild", str(tmp_path / "out"), "--remove", "c0001-p1"]) == 0
    assert "7 questions at 7 checkpoints" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The options of a run


def test_the_command_line_takes_the_forms(capsys):
    args = cli.make_parser().parse_args(["process", "Book.epub", "--forms", "open,cloze"])
    assert cli.settings_from(args).forms == ("open", "cloze")
    with pytest.raises(SystemExit):
        cli.main(["estimate", str(FIXTURE), "--forms", "essay"])
    assert "unknown form of question: essay" in capsys.readouterr().err


def test_the_command_line_estimates_and_lists_the_chapters(capsys):
    assert cli.main(["estimate", str(FIXTURE), "--density", "short", "--max-questions", "8"]) == 0
    out = capsys.readouterr().out
    assert "about 3 to 4 sections" in out and "(at most 8 at a checkpoint)" in out and "    3      608 words  The Fog Season" in out


def test_the_command_line_shows_the_reading_and_tries_a_chapter(tmp_path, monkeypatch, capsys):
    script = Script()
    monkeypatch.setattr(cli, "ready_backend", lambda *a, **k: type("B", (), {
        "check_available": lambda self, models=None: None,
        "complete": lambda self, **kw: script.backend().complete(**kw)})())
    seen = {}

    def fake_try(epub, out_dir, settings, chapter, **kwargs):
        seen.update(chapter=chapter, steer=kwargs["steer"], spent=len(kwargs["spent"]))
        return try_chapter(epub, out_dir, settings, chapter, backend=script.backend(), steer=kwargs["steer"], spent=kwargs["spent"])

    monkeypatch.setattr(cli, "try_chapter", fake_try)
    purpose = tmp_path / "why.txt"
    purpose.write_text("How the lamp is kept burning.\n", encoding="utf-8")
    args = [str(FIXTURE), "-o", str(tmp_path / "out"), "--density", "short", "--purpose", f"@{purpose}", "--try-chapter", "3"]
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    assert "How your wishes were read" in captured.err and "The reader wants: How the lamp is kept burning." in captured.err
    assert "d02 The Lamp Room: It opens on the reader's subject." in captured.err
    assert "3 questions in 3 sections." in captured.out and "  Q  What does the passage starting at d03-p002" in captured.out
    assert seen["chapter"] == 3 and seen["spent"] == 1 and seen["steer"].reading["focus"].startswith("The reader wants")
    # the same words again: the reading that was confirmed stands, and no model is asked
    assert cli.main(args) == 0 and script.readings == 1 and seen["spent"] == 0
    assert cli.main([str(FIXTURE), "-o", str(tmp_path / "out"), "--try-chapter", "42"]) == 1
    # --no-reading: the words as they are
    assert cli.main([str(FIXTURE), "-o", str(tmp_path / "raw"), "--density", "short", "--purpose", "The fog.", "--no-reading", "--try-chapter", "2"]) == 0
    assert seen["steer"] == Steer(purpose="The fog.") and script.readings == 1


# --------------------------------------------------------------------------
# Files that are no EPUB, and web addresses


def test_the_command_line_estimates_and_processes_a_file(tmp_path: Path, capsys, monkeypatch) -> None:
    essay = tmp_path / "my_essay.md"
    essay.write_text(MARKDOWN + "\n\n" + body(600), encoding="utf-8")
    assert cli.main(["estimate", str(essay)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("On Keeping Lamps: ") and "On Keeping Lamps" in out.splitlines()[-1]
    book = tmp_path / "book.txt"
    book.write_text(TEXT_BOOK, encoding="utf-8")
    assert cli.main(["estimate", str(book)]) == 0 and "CHAPTER III" in capsys.readouterr().out
    assert cli.main(["estimate", str(book), "--one-piece"]) == 0 and "CHAPTER III" not in capsys.readouterr().out
    assert cli.main(["estimate", str(tmp_path / "none.md")]) == 1
    (tmp_path / "tiny.txt").write_text("too short")
    assert cli.main(["estimate", str(tmp_path / "tiny.txt")]) == 1 and "too short to make questions" in capsys.readouterr().err
    assert runner.default_out_dir(essay) == tmp_path / "my_essay-processed"

    def stand_in(*args, **kwargs):
        backend = Script().backend()
        backend.check_available = lambda models=None: {}
        return backend

    monkeypatch.setattr(runner, "default_backend", stand_in)
    assert cli.main([str(essay)]) == 0
    out_dir = tmp_path / "my_essay-processed"
    assert (out_dir / "On Keeping Lamps.epub").is_file() and (out_dir / "On Keeping Lamps.prepared.epub").is_file()
    made = (out_dir / "On Keeping Lamps.epub").stat().st_mtime_ns
    assert cli.main([str(essay)]) == 0 and (out_dir / "On Keeping Lamps.epub").stat().st_mtime_ns == made  # carried on, not made again


def test_the_command_line_takes_an_address(tmp_path: Path, the_web, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    def stand_in(*args, **kwargs):
        backend = Script().backend()
        backend.check_available = lambda models=None: {}
        return backend

    monkeypatch.setattr(runner, "default_backend", stand_in)
    assert cli.main(["estimate", the_web + "/essay"]) == 0 and capsys.readouterr().out.startswith("On Keeping Lamps: ")
    assert cli.main(["estimate", the_web + "/forbidden"]) == 1 and "refused to give the page" in capsys.readouterr().err
    assert cli.main([the_web + "/essay"]) == 0
    out_dir = tmp_path / "On Keeping Lamps-processed"
    assert (out_dir / "On Keeping Lamps.prepared.epub").is_file() and (out_dir / "source" / "page.html").read_bytes() == CLUTTER.encode()
    said = json.loads((out_dir / "source" / "page.json").read_text(encoding="utf-8"))
    assert said["url"] == the_web + "/essay" and said["asked"] == the_web + "/essay"
    made = (out_dir / "On Keeping Lamps.epub").read_bytes()
    # the site changes; the run's own copy is what a second run reads
    monkeypatch.setitem(PAGES, "/essay", ("text/html", CLUTTER.replace("promise", "CHANGED").encode()))
    assert cli.main([the_web + "/essay", "-o", str(out_dir)]) == 0 and (out_dir / "On Keeping Lamps.epub").read_bytes() == made
    assert cli.main([the_web + "/essay", "-o", str(tmp_path / "whole"), "--whole-page"]) == 0
    assert (tmp_path / "whole" / "On Keeping Lamps.epub").stat().st_size != len(made)
