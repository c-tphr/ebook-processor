"""Markdown, text and HTML files through the app and the command line."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from ebook_processor import cli, runner
from ebook_processor.web import server as web
from tests.scripted import Script
from tests.sources.test_convert import MARKDOWN, PAGE, body
from tests.test_pipeline import FIXTURE

TEXT_BOOK = "The Lamp Book\n\n" + "\n\n".join(f"CHAPTER {r}\n\n{body(2500)}" for r in ("I", "II", "III"))


@pytest.fixture
def app(tmp_path):
    server = web.make_server(tmp_path / "data", "127.0.0.1", 0, backend_factory=lambda settings: Script().backend(),
                             claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, json_body=None, body: bytes | None = None):
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(base + "/api" + path, data=body, method=method, headers={"X-Ebook-Processor": "1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def done(job_id: str) -> dict:
        for _ in range(600):
            job = call("GET", f"/jobs/{job_id}")[1]
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.05)
        raise AssertionError("the run did not end")

    call.done = done
    call.jobs = server.RequestHandlerClass.jobs
    call.data = tmp_path / "data"
    call.folder = tmp_path / web.NOTES_FOLDER
    yield call
    server.shutdown()
    server.server_close()


def test_a_markdown_file_is_looked_at_held_and_processed(app):
    status, seen = app("POST", "/look?name=lamps.md", body=(MARKDOWN + "\n\n" + body(600)).encode())
    assert status == 200 and seen["type"] == "article" and seen["title"] == "On Keeping Lamps" and seen["author"] == "Ione Hartwell"
    assert seen["origin"]["format"] == "markdown" and seen["found"] is None and len(seen["chapters"]) == 1 and seen["seen_before"] is None
    assert seen["words"] == seen["chapters"][0]["words"] > 600 and seen["split"] == "auto"
    status, job = app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}, "plan": {"title": "Keeping Lamps", "author": "I. H."}})
    assert status == 201 and (job["title"], job["file"], job["type"]) == ("Keeping Lamps", "Keeping Lamps.epub", "article")
    assert job["origin"]["url"] == "https://example.org/essays/lamps" and job["origin"]["original"] == "lamps.md"
    job = app.done(job["id"])
    assert job["status"] == "done" and job["can_read"] and job["type"] == "article"
    folder = app.data / job["id"]
    assert (folder / "input" / "Keeping Lamps.epub").is_file() and (folder / "Keeping Lamps.prepared.epub").is_file()
    assert (folder / "input" / "original" / "lamps.md").read_text(encoding="utf-8").startswith("---\ntitle: On Keeping Lamps")
    assert app("GET", f"/jobs/{job['id']}/book")[1]["authors"] == ["I. H."]
    assert app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}})[0] == 410  # a held file is used once
    again = app("POST", "/look?name=other-name.md", body=(MARKDOWN + "\n\n" + body(600)).encode())[1]
    assert again["seen_before"] == job["created"]  # the same file, whatever it is called


def test_a_text_book_shows_its_chapters_and_can_be_one_piece(app):
    seen = app("POST", "/look?name=lamp_book.txt", body=TEXT_BOOK.encode())[1]
    assert seen["found"] == {"rule": "named", "count": 3, "example": "CHAPTER I"} and [c["title"] for c in seen["chapters"]] == ["CHAPTER I", "CHAPTER II", "CHAPTER III"]
    whole = app("POST", "/look/held", json_body={"held": seen["held"], "split": "none"})[1]
    assert whole["found"] is None and len(whole["chapters"]) == 1 and whole["split"] == "none" and whole["held"] == seen["held"]
    assert app("POST", "/look/held", json_body={"held": "nothing", "split": "none"})[0] == 410
    job = app("POST", "/jobs/held", json_body={"held": seen["held"], "settings": {}, "plan": {"split": "none", "type": "book"}})[1]
    assert job["type"] == "book" and len(app.jobs.jobs[job["id"]]["chapters"]) == 1
    assert app.done(job["id"])["status"] == "done"


def test_files_given_whole_and_by_path(app, tmp_path):
    job = app("POST", "/jobs?name=page.html", body=PAGE.encode())[1]
    assert (job["title"], job["type"], job["origin"]["site"]) == ("On Keeping Lamps", "article", "The Shore Review")
    (tmp_path / "essay.md").write_text(MARKDOWN + "\n\n" + body(300), encoding="utf-8")
    seen = app("POST", "/look/local", json_body={"path": str(tmp_path / "essay.md")})[1]
    assert seen["title"] == "On Keeping Lamps" and seen["held"]
    job = app("POST", "/jobs/local", json_body={"path": str(tmp_path / "essay.md"), "settings": {}, "plan": {"split": "none"}})[1]
    assert job["type"] == "article" and app.done(job["id"])["status"] == "done"
    assert app("POST", "/look?name=book.epub", body=FIXTURE.read_bytes())[1]["type"] == "book"
    assert "held" not in app("POST", "/look?name=book.epub", body=FIXTURE.read_bytes())[1]


def test_what_cannot_be_taken_is_said(app):
    assert app("POST", "/look?name=a.txt", body=b"too short")[1]["error"].startswith("This has only 3 words")
    assert app("POST", "/look?name=a.pdf", body=b"%PDF-1.4")[1]["error"] == "PDF files are not supported yet."
    assert app("POST", "/jobs?name=a.xyz", body=b"what")[0] == 400
    assert app("POST", "/look?name=a.epub", body=b"not a zip")[1]["error"].startswith("This file cannot be read as an EPUB")


def test_only_a_few_files_are_held(app):
    tokens = [app("POST", f"/look?name=n{n}.md", body=(f"# Piece {n}\n\n" + body(300)).encode())[1]["held"] for n in range(6)]
    assert len(app.jobs.held) == 4 and tokens[0] not in app.jobs.held and tokens[-1] in app.jobs.held


def test_an_article_has_its_file_in_the_notes_folder_and_can_become_a_book(app):
    job = app("POST", "/jobs?name=lamps.md", body=(MARKDOWN + "\n\n" + body(600)).encode())[1]
    app.done(job["id"])
    made = app("POST", f"/jobs/{job['id']}/notes/highlight", json_body={"chapter": 1, "start": 3, "end": 9, "note": "Why it matters."})[1]["made"]
    home = app("GET", "/vault")[1]
    assert [(b["title"], b["kind"], b["job"]) for b in home["books"]] == [("On Keeping Lamps", "article", job["id"])]
    file = app.folder / "Articles" / "On Keeping Lamps.md"
    text = file.read_text(encoding="utf-8")
    assert "kind: article\n" in text and "url: https://example.org/essays/lamps\n" in text and "published: 2024-03-09\n" in text
    assert f"^{made}\n" in text and not (app.folder / "Books" / "On Keeping Lamps.md").exists()
    # a note cites the article as it would a book
    note = app("POST", "/vault/notes", json_body={"title": "Lamps are promises", "body": f"See [[On Keeping Lamps#^{made}]]."})[1]
    assert note["links"][0]["to"]["kind"] == "article" and note["sources"][0]["job"] == job["id"]
    found = app("GET", "/vault/search?" + urllib.parse.urlencode({"kind": "article"}))[1]
    assert {p["book"] for p in found["passages"]} == {"On Keeping Lamps"} and app("GET", "/vault/search?kind=book")[1]["passages"] == []
    assert app("POST", f"/jobs/{job['id']}/type", json_body={"type": "pamphlet"})[0] == 400
    assert app("POST", f"/jobs/{job['id']}/type", json_body={"type": "book"})[1]["type"] == "book"
    moved = app.folder / "Books" / "On Keeping Lamps.md"
    assert moved.is_file() and not file.exists() and "kind: book\n" in moved.read_text(encoding="utf-8")
    assert app("GET", f"/vault/notes/{note['id']}")[1]["links"][0]["to"]["kind"] == "book"  # the citation holds
    assert [j["type"] for j in app("GET", "/jobs")[1]["jobs"]] == ["book"]


def test_an_epub_can_be_called_an_article_and_old_runs_are_books(app):
    plan = urllib.parse.quote(json.dumps({"type": "article"}))
    job = app("POST", f"/jobs?name=fixture.epub&plan={plan}", body=FIXTURE.read_bytes())[1]
    assert job["type"] == "article" and job["origin"] is None
    app.done(job["id"])
    del app.jobs.jobs[job["id"]]["type"]  # a run made before articles were known
    assert app("GET", f"/jobs/{job['id']}")[1]["type"] == "book" and app("GET", "/jobs")[1]["jobs"][0]["type"] == "book"


# ---- the command line


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
