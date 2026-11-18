"""The book with visible checkpoints, the content shown to Claude, and the web app's API."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from ebook_processor import cli, content, prompts
from ebook_processor.llm import ClaudeCodeBackend, extract_json
from ebook_processor.prep.draft import check_draft
from ebook_processor.prep.epub import load_book, load_book_bytes
from ebook_processor.prep.xmltree import parse
from ebook_processor.settings import Settings, from_options
from ebook_processor.visible import build_visible
from ebook_processor.web.server import make_server
from tests.prep.bookmaker import make_epub, xhtml
from tests.programs import make_program
from tests.scripted import Script
from tests.test_pipeline import FIXTURE, run

BLOCK_RE = re.compile(rb'<div class="ebp-checkpoint".*?</div></div>', re.S)
PREVIEW_RE = re.compile(rb'<div class="ebp-preview" id="ebp-preview-(c\d+)".*?</div>', re.S)


def visible_of(epub: Path, sections: list[tuple[str, str]], suffix: str = " (checkpoints)"):
    """Build the visible book for sections given as (first, last) paragraph references, one question each."""
    book = load_book(epub)
    where = {b.ref: sd for sd in book.spine for b in sd.blocks}
    draft = {
        "format": "rsvp-draft", "version": 1,
        "book": {"title": "T", "language": "en", "source_sha256": book.sha256, "instructions_version": "2.0"},
        "excluded": [],
        "sections": [{"id": f"s{n:04d}", "chapter_href": where[a].href, "first_paragraph": a, "last_paragraph": b,
                      "label": f"Part {n} & more"} for n, (a, b) in enumerate(sections, start=1)],
        "checkpoints": [{"id": f"c{n:04d}", "section": f"s{n:04d}", "prompts": [
            {"id": f"c{n:04d}-p1", "prompt": "Is 1 < 2 & why?", "answer": "Line one.\nLine two.",
             "supporting_paragraphs": [a]}]} for n, (a, b) in enumerate(sections, start=1)],
    }
    data, issues, plan = check_draft(json.dumps(draft).encode(), book)
    assert not issues, issues
    out, notes = build_visible(book, data, plan, suffix)
    return book, load_book_bytes(out, "visible"), out, notes


def test_each_question_and_answer_gets_its_own_page_and_the_text_is_untouched(tmp_path):
    report, _, _ = run(tmp_path)
    original = load_book(FIXTURE)
    visible = load_book(tmp_path / "out" / "fixture.checkpoints.epub")
    assert visible.title == "The Lighthouse Keeper's Almanac (checkpoints)"
    assert "rsvp/checkpoints.json" not in "".join(visible.archive.by_name)  # no firmware data in this edition
    total = 0
    for before, after in zip(original.spine, visible.spine):
        if before.doc is None:
            continue
        blocks = BLOCK_RE.findall(after.doc.data)
        total += len(blocks)
        assert BLOCK_RE.sub(b"", after.doc.data) == before.doc.data  # only whole checkpoint blocks were added
        for block in blocks:
            assert block.count(b"page-break-before: always") == 2  # the question page and the answer page
            assert b"Recall your answer, then turn the page." in block
    assert total == report["counts"]["checkpoints"] == 8
    # every member but the changed chapters and the package document is byte-identical
    changed = {sd.zip_path for sd in visible.spine if sd.doc is not None and BLOCK_RE.search(sd.doc.data)}
    for name in original.archive.names():
        if name not in changed and name != original.opf_path:
            assert original.archive.read(name) == visible.archive.read(name), name


def test_previews_put_a_page_of_questions_before_each_section_and_can_be_switched_by_a_rebuild(tmp_path):
    from ebook_processor.runner import rebuild
    report, _, _ = run(tmp_path, settings=from_options(density="short", preview=True))
    out = tmp_path / "out"
    assert report["settings"]["preview"] is True and "a page before each section" in (out / "fixture.report.md").read_text(encoding="utf-8")
    original, visible = load_book(FIXTURE), load_book(out / "fixture.checkpoints.epub")
    state = json.loads((out / "work" / "state.json").read_text(encoding="utf-8"))
    asked = {q["id"]: q["prompt"] for q in state["questions"]}
    previews = 0
    for before, after in zip(original.spine, visible.spine):
        if before.doc is None:
            continue
        data = after.doc.data
        for found in PREVIEW_RE.finditer(data):
            previews += 1
            page, cid = found.group(0), found.group(1).decode()
            assert page.count(b"page-break-before: always") == 1 and b"page-break-after: always" in page  # a page of its own
            mine = [text for qid, text in asked.items() if qid.startswith(cid + "-")]
            assert mine and all(text.encode() in page for text in mine)  # the questions, and none of the answers
            assert b"ebp-answer" not in page and b"Keep these in mind as you read." in page
            # the preview stands before its section's text and the checkpoint after it
            assert data.index(found.group(0)) < data.index(b'id="ebp-' + cid.encode() + b'"')
        assert BLOCK_RE.sub(b"", PREVIEW_RE.sub(b"", data)) == before.doc.data  # nothing else was added
    assert previews == report["counts"]["checkpoints"] == 8
    # the firmware edition has no previews, and a rebuild takes them out of the visible one again
    assert not any(b"ebp-preview" in sd.doc.data for sd in load_book(out / "fixture.prepared.epub").spine if sd.doc is not None)
    assert rebuild(out, preview=False)["settings"]["preview"] is False
    assert not any(b"ebp-preview" in sd.doc.data for sd in load_book(out / "fixture.checkpoints.epub").spine if sd.doc is not None)
    assert cli.main(["rebuild", str(out), "--preview-questions", "on"]) == 0
    assert any(b"ebp-preview" in sd.doc.data for sd in load_book(out / "fixture.checkpoints.epub").spine if sd.doc is not None)


def test_text_is_escaped_and_multi_line_answers_become_paragraphs(tmp_path):
    epub = make_epub(tmp_path / "b.epub", [("text/a.xhtml", xhtml("<p>One two three.</p><p>Four five six.</p>"))])
    _, visible, _, notes = visible_of(epub, [("d01-p001", "d01-p001"), ("d01-p002", "d01-p002")])
    data = visible.spine[0].doc.data.decode()
    assert "Is 1 &lt; 2 &amp; why?" in data and "Part 1 &amp; more" in data and not notes
    assert data.count('class="ebp-text"') == 4 and "Line one.</p>" in data
    first, second = BLOCK_RE.findall(visible.spine[0].doc.data)
    assert b"page-break-after: always" in first  # text follows: it starts on a new page
    assert b"page-break-after" not in second  # the document ends here: no empty last page
    assert data.index("One two three.") < data.index("ebp-c0001") < data.index("Four five six.") < data.index("ebp-c0002")


def test_a_section_ending_in_a_list_item_or_a_table_gets_a_valid_place(tmp_path):
    body = ("<ul><li>First item here.</li><li>Second item here.</li></ul><p>Middle text.</p>"
            "<table><tr><td>Cell one</td></tr></table><p>Last words.</p>")
    epub = make_epub(tmp_path / "b.epub", [("text/a.xhtml", xhtml(body))])
    _, visible, _, notes = visible_of(epub, [("d01-p001", "d01-p001"), ("d01-p002", "d01-p004"), ("d01-p005", "d01-p005")])
    data = visible.spine[0].doc.data.decode()
    assert not notes
    assert re.search(r"First item here\.<div class=\"ebp-checkpoint\" id=\"ebp-c0001\">.*?</div></div></li>", data, re.S)
    assert re.search(r"</table><div class=\"ebp-checkpoint\" id=\"ebp-c0002\">", data)
    parse(visible.spine[0].doc.data)  # still well formed


def test_the_title_suffix_can_be_switched_off(tmp_path):
    epub = make_epub(tmp_path / "b.epub", [("text/a.xhtml", xhtml("<p>One two three.</p>"))])
    original, visible, _, _ = visible_of(epub, [("d01-p001", "d01-p001")], suffix="")
    assert visible.title == original.title
    assert visible.archive.read(original.opf_path) == original.archive.read(original.opf_path)


# --------------------------------------------------------------------------
# What Claude is shown


def test_content_modes_show_or_hide_tables_code_and_images(tmp_path):
    book = load_book(FIXTURE)
    sd = next(sd for sd in book.spine if any(b.kind == "table" for b in sd.blocks))
    table = next(b for b in sd.blocks if b.kind == "table")
    prose = content.block_body(sd, table, "prose")
    text = content.block_body(sd, table, "text")
    assert prose.startswith("[Table:") and "\n" not in prose
    assert text.startswith(prose) and text.count("\n| ") >= 2
    code_doc = next(sd for sd in book.spine if any(b.kind == "code" for b in sd.blocks))
    code = next(b for b in code_doc.blocks if b.kind == "code")
    assert len(content.block_body(code_doc, code, "text").split("\n")) > 2
    images = content.export_images(book, tmp_path / "images")
    ref, names = next(iter(images.items()))
    assert (tmp_path / "images" / names[0]).read_bytes().startswith(b"\x89PNG")
    doc = next(sd for sd in book.spine if any(b.ref == ref for b in sd.blocks))
    block = next(b for b in doc.blocks if b.ref == ref)
    assert f"[image file: {names[0]}]" in content.block_body(doc, block, "figures", images)
    assert "[image file" not in content.block_body(doc, block, "text", images)


def test_instructions_carry_the_settings():
    system = prompts.drafter_system(from_options(section_words="400-1200", max_questions=2, content="figures",
                                                 kind="narrative"))
    assert "Target 400 to 1200 words" in system and "Never more than 2." in system
    assert "open it with the Read tool" in system and "The reader says this book is narrative." in system
    assert "$" not in system and "$" not in prompts.checker_system(Settings())
    assert "stands_alone" in prompts.checker_system(Settings())


def test_claude_code_is_called_without_tools_unless_figures_are_viewed(tmp_path):
    backend = ClaudeCodeBackend(model="opus", effort="high")
    cmd = backend.command(tmp_path / "instructions.md", {"type": "object"}, None, None)
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--tools") + 1] == "" and "--allowedTools" not in cmd
    assert cmd[cmd.index("--system-prompt-file") + 1] == str(tmp_path / "instructions.md") and "--system-prompt" not in cmd
    assert cmd[cmd.index("--model") + 1] == "opus" and cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--setting-sources") + 1] == "" and "--no-session-persistence" in cmd
    cmd = backend.command(tmp_path / "instructions.md", {"type": "object"}, "sonnet", tmp_path)
    assert cmd[cmd.index("--tools") + 1] == "Read" and cmd[cmd.index("--model") + 1] == "sonnet"


def test_the_instructions_reach_claude_code_as_a_file_so_the_command_line_stays_short(tmp_path):
    """Windows allows a command line of 8,191 characters when the program is a script, which is how npm installs
    Claude Code there. The drafting instructions alone are longer than that."""
    exe = make_program(tmp_path / "bin", "claude", '''import json, sys
args = sys.argv[1:]
text = open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read()
schema = json.loads(args[args.index("--json-schema") + 1])
print(json.dumps({"structured_output": {"instructions": text, "task": sys.stdin.read(), "schema": schema,
                                        "command_line": sum(len(a) + 1 for a in sys.argv)}, "usage": {}}))
''')
    system = prompts.drafter_system(Settings()) + "\nA line with other letters: Brontë, “quoted”, 100 % & more."
    assert len(system) > 8191
    done = ClaudeCodeBackend(executable=exe).complete(role="draft", system=system, prompt="THE TASK", schema=prompts.DRAFT_SCHEMA)
    assert done.data["instructions"] == system and done.data["task"] == "THE TASK"
    assert done.data["schema"] == prompts.DRAFT_SCHEMA  # the schema, with its quotes and braces, arrives whole
    assert done.data["command_line"] < 4000


def test_a_reply_with_text_around_its_json_is_still_read():
    assert extract_json('Here it is:\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}
    assert extract_json('noise {"a": {"b": 2}} more') == {"a": {"b": 2}}
    assert extract_json("no json here") is None


# --------------------------------------------------------------------------
# The web app


@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "data", "127.0.0.1", 0, backend_factory=lambda settings: Script().backend(),
                         claude={"ok": True, "version": "test"}, models=[])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, body: bytes | None = None, json_body=None, token: bool = True):
        headers = {"X-Ebook-Processor": "1"} if token else {}
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                kind = response.headers.get("Content-Type", "")
                return response.status, (json.loads(raw) if kind.startswith("application/json") and path.startswith("/api") and "/files/" not in path else raw)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield call
    server.shutdown()
    server.server_close()


def wait_done(call, job_id: str) -> dict:
    for _ in range(200):
        status, job = call("GET", f"/api/jobs/{job_id}")
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def test_the_web_app_runs_a_book_and_serves_review_rebuild_and_downloads(app):
    status, page = app("GET", "/")
    assert status == 200 and b"ebook-processor" in page
    status, info = app("GET", "/api/info")
    assert info["claude"]["ok"] and info["densities"]["medium"] == [600, 2000]
    settings = json.dumps({"density": "short", "model": "", "effort": "", "epubcheck": False})
    status, job = app("POST", f"/api/jobs?name=fixture.epub&settings={urllib.request.quote(settings)}", FIXTURE.read_bytes())
    assert status == 201 and job["title"] == "The Lighthouse Keeper's Almanac"
    job = wait_done(app, job["id"])
    assert job["status"] == "done" and job["report"]["counts"]["questions"] == 8, job["message"]
    assert {e["stage"] for e in job["events"]} >= {"draft", "check", "build"}
    status, review = app("GET", f"/api/jobs/{job['id']}/review")
    assert len(review["sections"]) == 8 and review["sections"][0]["questions"][0]["can_keep"]
    status, rebuilt = app("POST", f"/api/jobs/{job['id']}/rebuild", json_body={"removed": ["c0001-p1"]})
    assert status == 200 and rebuilt["report"]["counts"]["questions"] == 7
    status, data = app("GET", f"/api/jobs/{job['id']}/files/fixture.checkpoints.epub")
    assert status == 200 and zipfile.is_zipfile(__import__("io").BytesIO(data))
    # question previews in the exported book are switched by a rebuild, and the page is told which way they are
    assert rebuilt["report"]["preview"] is False and b"ebp-preview" not in zipfile.ZipFile(__import__("io").BytesIO(data)).read("OEBPS/text/chapter-1.xhtml")
    status, rebuilt = app("POST", f"/api/jobs/{job['id']}/rebuild", json_body={"preview": True})
    assert status == 200 and rebuilt["report"]["preview"] is True and rebuilt["report"]["counts"]["questions"] == 7
    with zipfile.ZipFile(__import__("io").BytesIO(app("GET", f"/api/jobs/{job['id']}/files/fixture.checkpoints.epub")[1])) as z:
        assert sum(z.read(n).count(b'class="ebp-preview"') for n in z.namelist()) == 7
    assert app("POST", f"/api/jobs/{job['id']}/rebuild", json_body={"preview": "yes"})[0] == 400
    assert app("GET", f"/api/jobs/{job['id']}/files/job.json")[0] == 404  # only the outputs can be downloaded
    assert app("GET", f"/api/jobs/{job['id']}/files/..%2Fjob.json")[0] == 404
    assert app("GET", "/api/jobs")[1]["jobs"][0]["counts"]["questions"] == 7
    assert app("DELETE", f"/api/jobs/{job['id']}")[0] == 200 and app("GET", "/api/jobs")[1]["jobs"] == []


def test_the_web_app_refuses_what_it_should(app):
    assert app("POST", "/api/jobs?name=a.epub", b"not a zip")[0] == 400
    assert app("POST", "/api/jobs?name=a.epub", FIXTURE.read_bytes(), token=False)[0] == 403  # not from the app's page
    bad = urllib.request.quote(json.dumps({"density": "tiny"}))
    assert app("POST", f"/api/jobs?name=a.epub&settings={bad}", FIXTURE.read_bytes())[0] == 400
    assert app("GET", "/api/jobs/20260101-000000-nothing")[0] == 404
    assert app("POST", "/api/jobs/local", json_body={"path": "/nowhere/book.epub"})[0] == 400


# --------------------------------------------------------------------------
# The models Claude Code offers


def fake_claude(tmp_path: Path, lines: list[dict]) -> str:
    """A stand-in for the claude executable that prints ``lines`` as JSON, one per line."""
    payload = "\n".join(json.dumps(line) for line in lines)
    return make_program(tmp_path, "claude", f"import sys\nsys.stdin.read()\nprint({payload!r})\n")


def test_the_models_are_read_from_claude_codes_own_list(tmp_path):
    reply = {"type": "control_response", "response": {"subtype": "success", "request_id": "models", "response": {"models": [
        {"value": "default", "resolvedModel": "claude-x-1", "displayName": "Default (recommended)", "description": "X 1",
         "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
        {"value": "small", "resolvedModel": "claude-small-1", "displayName": "Small 1", "description": "Fast"},
        {"displayName": "no value: skipped"}]}}}
    exe = fake_claude(tmp_path, [{"type": "system", "subtype": "init"}, reply])
    models = ClaudeCodeBackend(executable=exe).list_models()
    assert models == [
        {"value": "default", "name": "Default (recommended)", "description": "X 1", "resolved": "claude-x-1", "efforts": ["low", "high"]},
        {"value": "small", "name": "Small 1", "description": "Fast", "resolved": "claude-small-1", "efforts": []}]
    assert ClaudeCodeBackend(executable=fake_claude(tmp_path, [{"type": "result"}])).list_models() == []
    assert ClaudeCodeBackend(executable=str(tmp_path / "missing")).list_models() == []


def test_the_web_app_passes_the_models_to_its_page(tmp_path):
    models = [{"value": "default", "name": "Default", "description": "X 1", "resolved": "claude-x-1", "efforts": ["low"]}]
    server = make_server(tmp_path / "data", "127.0.0.1", 0, claude={"ok": True, "version": "test"}, models=models)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/info", timeout=10) as response:
            assert json.loads(response.read())["models"] == models
    finally:
        server.shutdown()
        server.server_close()
