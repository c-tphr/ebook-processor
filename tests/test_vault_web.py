"""The notes folder over HTTP: the routes, the books' files, and where the folder is."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from ebook_processor import bookfile
from ebook_processor.web import server as web
from tests.test_reading import PREPARED


@pytest.fixture
def app(tmp_path):
    server = web.make_server(tmp_path / "data", "127.0.0.1", 0, claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, json_body=None, body: bytes | None = None, csrf: bool = True):
        if json_body is not None:
            body = json.dumps(json_body).encode()
        request = urllib.request.Request(base + path, data=body, method=method, headers={"X-Ebook-Processor": "1"} if csrf else {})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    call.port = server.server_address[1]
    call.folder = tmp_path / web.NOTES_FOLDER
    call.jobs = server.RequestHandlerClass.jobs
    call.book = lambda: call("POST", "/api/books/import?name=fixture.prepared.epub", body=PREPARED.read_bytes())[1]["id"]
    yield call
    server.shutdown()
    server.server_close()


def q(**values: str) -> str:
    return urllib.parse.urlencode(values)


def test_the_folder_is_made_when_notes_is_first_opened_and_every_book_gets_its_file(app):
    jid = app.book()
    hid = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9, "note": "Why it matters."})[1]["made"]
    state = app("GET", "/api/vault/folder")[1]
    assert state == {"folder": str(app.folder), "default": str(app.folder), "exists": False, "chosen": False}
    assert not app.folder.exists()  # marking a book does not make the folder
    assert app("GET", "/api/vault/search?q=supply")[1]["passages"] == [] and app("GET", "/api/vault/complete?q=a")[1] == {"notes": []}
    home = app("GET", "/api/vault")[1]
    assert home["folder"] == str(app.folder) and home["inbox"] == [] and home["recent"] == [] and home["notes"] == 0 and home["trash"] == 0
    assert [(b["title"], b["job"], b["in_library"], b["entries"]) for b in home["books"]] == [("The Lighthouse Keeper's Almanac", jid, True, 2)]
    file = app.folder / "Books" / "The Lighthouse Keeper's Almanac.md"
    assert (app.folder / "Inbox").is_dir() and f"^{hid}\n\nWhy it matters. ^{hid}-note\n" in file.read_text(encoding="utf-8")
    # from now on the file follows the book
    app("PUT", f"/api/jobs/{jid}/notes/target", json_body={"target": {"on": "book"}, "text": "On the whole book"})
    assert "On the whole book ^n" in file.read_text(encoding="utf-8")
    app("DELETE", f"/api/jobs/{jid}/notes/item/{hid}")
    assert hid not in file.read_text(encoding="utf-8")
    stamp = file.stat().st_mtime_ns
    app("GET", "/api/vault")
    assert file.stat().st_mtime_ns == stamp  # nothing changed: not written again


def test_notes_are_created_read_saved_renamed_and_trashed(app):
    made = app("POST", "/api/vault/notes", json_body={"title": "Prices carry information", "body": "No one holds it all. #pricing"})
    assert made[0] == 201 and made[1]["kind"] == "idea" and made[1]["sources"] == [] and made[1]["tags"] == ["pricing"]
    nid, token = made[1]["id"], made[1]["token"]
    quick = app("POST", "/api/vault/notes", json_body={"body": "look at [[Prices carry information]] again", "kind": "quick"})[1]
    assert quick["inbox"] and quick["untitled"] and quick["links"][0]["to"]["id"] == nid
    home = app("GET", "/api/vault")[1]
    assert [n["id"] for n in home["inbox"]] == [quick["id"]] and [n["id"] for n in home["recent"]] == [nid] and home["notes"] == 2
    assert home["tags"] == [{"tag": "pricing", "count": 1}]
    note = app("GET", f"/api/vault/notes/{nid}")[1]
    assert note["body"] == "No one holds it all. #pricing" and [b["id"] for b in note["backlinks"]] == [quick["id"]]
    assert app("GET", f"/api/vault/notes/{nid}/token")[1] == {"token": token}
    saved = app("PUT", f"/api/vault/notes/{nid}", json_body={"body": "Rewritten.", "token": token,
                                                              "props": note["props"] + [{"key": "tags", "value": ["markets"]}]})[1]
    assert saved["body"] == "Rewritten." and saved["tags"] == ["markets"] and saved["token"] != token
    # written over in another program: the app does not write over that, and says what is there now
    path = app.folder / "Prices carry information.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nFrom Obsidian.", encoding="utf-8")
    clash = app("PUT", f"/api/vault/notes/{nid}", json_body={"body": "From the app.", "token": saved["token"]})
    assert clash[0] == 409 and clash[1]["current"]["body"] == "Rewritten.\nFrom Obsidian." and "outside the app" in clash[1]["error"]
    assert app("GET", f"/api/vault/notes/{nid}/token")[1]["token"] == clash[1]["current"]["token"] != saved["token"]
    kept = app("PUT", f"/api/vault/notes/{nid}", json_body={"body": "From the app.", "token": saved["token"], "force": True})[1]
    assert kept["body"] == "From the app."
    # a quick note becomes an idea note in place: a title, a kind, and it leaves the inbox
    idea = app("PUT", f"/api/vault/notes/{quick['id']}", json_body={
        "title": "Look again at prices", "token": quick["token"],
        "props": [p if p["key"] != "kind" else {"key": "kind", "value": "idea"} for p in quick["props"]]})[1]
    assert idea["id"] == quick["id"] and idea["kind"] == "idea" and not idea["inbox"] and idea["title"] == "Look again at prices"
    assert (app.folder / "Look again at prices.md").is_file() and not list((app.folder / "Inbox").iterdir())
    renamed = app("PUT", f"/api/vault/notes/{nid}", json_body={"title": "Prices are signals", "token": kept["token"]})[1]
    assert renamed["title"] == "Prices are signals" and renamed["relinked"] == 1
    assert "[[Prices are signals]]" in app("GET", f"/api/vault/notes/{quick['id']}")[1]["body"]
    taken = app("PUT", f"/api/vault/notes/{nid}", json_body={"title": "look again at PRICES", "body": "not saved", "token": renamed["token"]})
    assert taken[0] == 400 and "already a note" in taken[1]["error"] and app("GET", f"/api/vault/notes/{nid}")[1]["body"] == "From the app."
    assert app("PUT", f"/api/vault/notes/{nid}", json_body={"title": "a/b", "token": renamed["token"]})[0] == 400
    assert app("GET", "/api/vault/complete?q=pri")[1]["notes"][0]["title"] == "Prices are signals"
    assert app("GET", "/api/vault/anchors?" + q(target="prices are signals"))[1] == {"link": "Prices are signals", "headings": [], "blocks": []}
    assert app("GET", "/api/vault/peek?" + q(target="Prices are signals"))[1]["text"] == "From the app."
    assert app("GET", "/api/vault/peek?" + q(target="No such note"))[1] == {"found": False}
    assert app("DELETE", f"/api/vault/notes/{nid}")[1]["trash"] == "Prices are signals.md"
    assert app("GET", f"/api/vault/notes/{nid}")[0] == 404 and app("GET", "/api/vault")[1]["trash"] == 1
    assert [n["title"] for n in app("GET", "/api/vault/trash")[1]["notes"]] == ["Prices are signals"]
    assert app("POST", "/api/vault/trash/restore", json_body={"name": "Prices are signals.md"})[1]["id"] == nid
    assert app("POST", "/api/vault/trash/restore", json_body={"name": "../x.md"})[0] in (400, 404)
    assert app("POST", "/api/vault/notes", json_body={"title": "x"}, csrf=False)[0] == 403
    assert app("POST", "/api/vault/notes", json_body={"title": "Prices are signals"})[0] == 400
    assert app("POST", "/api/vault/notes", json_body={"kind": "book"})[0] == 400
    assert app("GET", "/api/vault/notes/n00000000")[0] == 404 and app("DELETE", "/api/vault/notes/n00000000")[0] == 404


def test_an_idea_note_cites_a_highlight_and_a_quick_note_remembers_its_place(app):
    jid = app.book()
    made = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9})[1]
    hid, words = made["made"], made["highlights"][0]["text"]
    note = app("POST", "/api/vault/notes", json_body={"source": {"job": jid, "block": hid}})[1]  # this also makes the folder
    link = f"[[The Lighthouse Keeper's Almanac#^{hid}]]"
    assert note["inbox"] and note["untitled"] and note["kind"] == "idea" and note["body"] == f"\n\nSource: {link}\n"
    assert {"key": "sources", "value": [link]} in note["props"]
    assert note["sources"] == [{"id": bookfile.book_id(jid), "title": "The Lighthouse Keeper's Almanac", "block": hid, "text": words,
                                "found": True, "job": jid, "go": {"chapter": 2, "word": 3}, "in_library": True}]
    titled = app("PUT", f"/api/vault/notes/{note['id']}", json_body={"title": "Boats come when they can", "body": "In my words.\n\nSource: " + link + "\n",
                                                                     "token": note["token"]})[1]
    assert titled["path"] == "Boats come when they can.md" and len(titled["sources"]) == 1
    book = app("GET", f"/api/vault/notes/{bookfile.book_id(jid)}")[1]
    assert book["kind"] == "book" and book["in_library"] and book["job"] == jid and [b["id"] for b in book["backlinks"]] == [note["id"]]
    assert app("PUT", f"/api/vault/notes/{book['id']}", json_body={"body": "mine", "token": book["token"]})[0] == 400
    assert app("DELETE", f"/api/vault/notes/{book['id']}")[0] == 400
    assert app("POST", "/api/vault/notes", json_body={"source": {"job": jid, "block": "h0000"}})[0] == 400
    assert app("POST", "/api/vault/notes", json_body={"source": {"job": "20200101-000000-none", "block": hid}})[0] == 404
    quick = app("POST", "/api/vault/notes", json_body={"body": "a thought while reading", "kind": "quick",
                                                       "where": {"job": jid, "chapter": 2, "word": 61}})[1]
    assert {"key": "book", "value": "[[The Lighthouse Keeper's Almanac]]"} in quick["props"] and {"key": "place", "value": "2:61"} in quick["props"]
    assert quick["links"][0]["to"]["job"] == jid and quick["body"] == "a thought while reading"
    assert quick["written"] == {"title": "The Lighthouse Keeper's Almanac", "job": jid, "in_library": True, "go": {"chapter": 2, "word": 61}}
    assert "written" not in note and quick["sources"] == []
    peek = app("GET", "/api/vault/peek?" + q(target="the lighthouse keeper's almanac", anchor="^" + hid))[1]
    assert peek["text"] == words and peek["go"] == {"chapter": 2, "word": 3} and peek["job"] == jid
    found = app("GET", "/api/vault/search?q=supply+boat")[1]
    assert [(p["block"], p["job"], p["go"]) for p in found["passages"]] == [(hid, jid, {"chapter": 2, "word": 3})] and found["notes"] == []
    assert [n["id"] for n in app("GET", "/api/vault/search?" + q(q="", book=bookfile.book_id(jid)))[1]["notes"]] == [quick["id"], note["id"]]
    assert [n["id"] for n in app("GET", "/api/vault/search?" + q(q="words", kind="idea"))[1]["notes"]] == [note["id"]]
    # the book leaves the library: its file stays and says so, links still lead to the words, the reader is not offered
    assert app("DELETE", f"/api/jobs/{jid}")[0] == 200
    file = app.folder / "Books" / "The Lighthouse Keeper's Almanac.md"
    assert bookfile.REMOVED in file.read_text(encoding="utf-8") and f"^{hid}" in file.read_text(encoding="utf-8")
    after = app("GET", f"/api/vault/notes/{note['id']}")[1]
    assert after["sources"][0]["text"] == words and after["sources"][0]["in_library"] is False and after["sources"][0]["job"] is None
    home = app("GET", "/api/vault")[1]
    assert [(b["in_library"], b["entries"]) for b in home["books"]] == [(False, 1)]
    assert file.read_text(encoding="utf-8").count(bookfile.REMOVED) == 1
    assert app("DELETE", f"/api/vault/notes/{bookfile.book_id(jid)}")[0] == 200 and not file.exists()


def test_the_folder_can_be_pointed_elsewhere(app, tmp_path):
    app("POST", "/api/vault/notes", json_body={"title": "In the first folder"})
    other = tmp_path / "elsewhere" / "My notes"
    assert app("PUT", "/api/settings", json_body={"notes_folder": "relative/path"})[0] == 400
    blocked = tmp_path / "a file"
    blocked.write_text("x")
    assert app("PUT", "/api/settings", json_body={"notes_folder": str(blocked)})[0] == 400
    assert app("PUT", "/api/settings", json_body={"notes_folder": str(other)})[1]["notes_folder"] == str(other)
    assert app("GET", "/api/vault/folder")[1] == {"folder": str(other), "default": str(app.folder), "exists": False, "chosen": True}
    assert app("GET", "/api/vault")[1]["recent"] == [] and (other / "Inbox").is_dir()  # the app does not move files
    assert (app.folder / "In the first folder.md").is_file()
    app("PUT", "/api/settings", json_body={"notes_folder": None})
    assert [n["title"] for n in app("GET", "/api/vault")[1]["recent"]] == ["In the first folder"]
    assert app("PUT", "/api/settings", json_body={"notes_folder": str(blocked / "under a file")})[0] == 200
    assert app("GET", "/api/vault")[0] == 400 and "cannot be made" in app("GET", "/api/vault")[1]["error"]


def test_serve_remembers_the_folder_it_is_started_with(tmp_path, monkeypatch, capsys):
    from ebook_processor import update

    def no_serving(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(web.ThreadingHTTPServer, "serve_forever", no_serving)
    monkeypatch.setattr(web, "claude_status", lambda: {"ok": False, "error": "not here"})
    monkeypatch.setattr(web, "codex_status", lambda: {"ok": False, "installed": False, "error": "not here"})
    monkeypatch.setattr(update, "write_record", lambda host, port, data: {"token": "t"})
    monkeypatch.setattr(update, "remove_record", lambda port, pid: None)
    web.serve(tmp_path / "data", "127.0.0.1", 0, False, tmp_path / "chosen notes")
    assert f"Notes are kept in {tmp_path / 'chosen notes'}" in capsys.readouterr().out
    assert web.Jobs(tmp_path / "data").notes_folder() == tmp_path / "chosen notes"
    assert not (tmp_path / "chosen notes").exists()


def test_processing_what_was_marked_ends_in_a_note_a_source_or_aside(app):
    jid = app.book()
    first = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9, "note": "Why it matters."})[1]["made"]
    second = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 70, "end": 74})[1]["made"]
    third = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 3, "start": 5, "end": 9})[1]["made"]
    app("PUT", f"/api/jobs/{jid}/notes/target", json_body={"target": {"on": "section", "section": "s0001"}, "text": "My summary."})
    quick = app("POST", "/api/vault/notes", json_body={"body": "a thought to keep", "kind": "quick"})[1]
    view = app("GET", "/api/process")[1]
    assert view["counts"] == {"waiting": 5, "done": 0, "aside": 0} and [q["id"] for q in view["quick"]] == [quick["id"]]
    rows = view["books"][0]["items"]
    assert [(r["kind"], r["state"]) for r in rows] == [("section-note", "waiting"), ("highlight", "waiting"), ("highlight", "waiting"), ("highlight", "waiting")]
    assert rows[1]["id"] == first and rows[1]["note"] == rows[1]["words"] == "Why it matters." and rows[1]["text"].startswith("The supply boat")
    assert rows[1]["section"] == "s0001" and rows[1]["go"] == {"chapter": 2, "word": 3} and rows[0]["words"] == "My summary." and rows[0]["text"] == ""
    assert rows[3]["chapter"] == 3 and app("GET", "/api/vault")[1]["waiting"] == 5
    # an idea note that cites an item processes it, however the citation was made
    note = app("POST", "/api/vault/notes", json_body={"title": "Boats come when they can", "body": "x", "source": {"job": jid, "block": first}})[1]
    by_id = lambda: {r["id"]: r for r in app("GET", f"/api/process?job={jid}")[1]["books"][0]["items"]}  # noqa: E731
    assert by_id()[first]["state"] == "done" and by_id()[first]["notes"] == [{"id": note["id"], "title": "Boats come when they can", "untitled": False}]
    # added to a note that exists: a source line and the property, once
    added = app("POST", "/api/process/add", json_body={"note": note["id"], "job": jid, "block": second})[1]
    link = f"[[The Lighthouse Keeper's Almanac#^{second}]]"
    assert added["body"].endswith(f"\n\nSource: {link}\n") and len(added["sources"]) == 2 and by_id()[second]["state"] == "done"
    assert app("POST", "/api/process/add", json_body={"note": note["id"], "job": jid, "block": second})[1]["body"] == added["body"]
    assert app("POST", "/api/process/add", json_body={"note": note["id"], "job": jid, "block": "h0000"})[0] == 400
    assert app("POST", "/api/process/add", json_body={"note": "n00000000", "job": jid, "block": third})[0] == 404
    # set aside and brought back; an item that is gone from the book is forgotten
    assert app("POST", "/api/process/aside", json_body={"job": jid, "ids": [third, "h0000"]})[1] == {"ok": True}
    assert by_id()[third]["state"] == "aside" and app("GET", "/api/process")[1]["counts"] == {"waiting": 2, "done": 2, "aside": 1}
    assert json.loads((app.jobs.folder(jid) / "process.json").read_text())["aside"].keys() == {third}
    app("POST", "/api/process/aside", json_body={"job": jid, "ids": [third], "aside": False})
    assert by_id()[third]["state"] == "waiting"
    app("POST", "/api/process/aside", json_body={"job": jid, "ids": [third]})
    app("DELETE", f"/api/jobs/{jid}/notes/item/{third}")
    assert third not in by_id() and json.loads((app.jobs.folder(jid) / "process.json").read_text())["aside"] == {}
    # the citing note goes: its items wait again
    app("DELETE", f"/api/vault/notes/{note['id']}")
    assert by_id()[first]["state"] == by_id()[second]["state"] == "waiting"
    # a quick note: set aside (it leaves the inbox's list), brought back, then its words added to a note
    app("POST", "/api/process/aside", json_body={"quick": quick["id"]})
    assert app("GET", "/api/process")[1]["quick"][0]["state"] == "aside" and app("GET", "/api/vault")[1]["inbox"] == []
    app("POST", "/api/process/aside", json_body={"quick": quick["id"], "aside": False})
    assert [n["id"] for n in app("GET", "/api/vault")[1]["inbox"]] == [quick["id"]]
    keep = app("POST", "/api/vault/notes", json_body={"title": "Kept", "body": "Mine.\n"})[1]
    merged = app("POST", "/api/process/add", json_body={"note": keep["id"], "quick": quick["id"]})[1]
    assert merged["body"] == "Mine.\n\na thought to keep\n" and app("GET", "/api/process")[1]["quick"] == []
    assert any(n["title"].endswith("a thought to keep") for n in app("GET", "/api/vault/trash")[1]["notes"])
    assert app("POST", "/api/process/add", json_body={"note": keep["id"], "quick": keep["id"]})[0] == 400
    assert app("POST", "/api/process/aside", json_body={"job": jid, "ids": "all"})[0] == 400 and app("GET", "/api/process?job=20200101-000000-none")[0] == 404
    # related: found on this computer, with the way into the book
    related = app("POST", "/api/vault/related", json_body={"text": "the supply boat and Gannet Rock at noon", "but": [keep["id"]]})[1]
    assert related["passages"][0]["block"] == first and related["passages"][0]["go"] == {"chapter": 2, "word": 3} and related["passages"][0]["job"] == jid
    assert set(related["passages"][0]["shares"]) >= {"gannet", "supply"} and related["notes"] == []


def test_index_notes_and_projects_over_http(app):
    idea = app("POST", "/api/vault/notes", json_body={"title": "Decide once", "body": "A decision made in advance is cheaper.\n"})[1]
    index = app("POST", "/api/vault/notes", json_body={"title": "On habits", "kind": "index", "body": "- [[Decide once]]: the core.\n"})[1]
    assert app("POST", "/api/vault/notes", json_body={"kind": "project"})[0] == 400                      # a project needs a name
    project = app("POST", "/api/vault/notes", json_body={"title": "Talk on habits", "kind": "project"})
    assert project[0] == 201 and project[1]["body"] == "\n\n## Gathered\n\n## Outline\n" and project[1]["project"] == {"status": "active", "notes": []}
    pid = project[1]["id"]
    assert {"key": "status", "value": "active"} in project[1]["props"]
    own = app("POST", "/api/vault/notes", json_body={"title": "Talk - to do", "kind": "project-note", "project": pid, "body": "- [ ] room"})[1]
    assert own["of"] == {"id": pid, "title": "Talk on habits"} and {"key": "project", "value": "[[Talk on habits]]"} in own["props"]
    assert app("POST", "/api/vault/notes", json_body={"title": "x", "kind": "project-note", "project": idea["id"]})[0] == 400
    got = app("POST", f"/api/vault/projects/{pid}/gather", json_body={"note": idea["id"]})[1]
    assert got["added"] and got["body"] == "\n\n## Gathered\n\n- [[Decide once]]\n\n## Outline\n" and got["project"]["notes"][0]["id"] == own["id"]
    assert not app("POST", f"/api/vault/projects/{pid}/gather", json_body={"note": idea["id"]})[1]["added"]
    assert app("POST", f"/api/vault/projects/{idea['id']}/gather", json_body={"note": index["id"]})[0] == 400
    assert app("POST", f"/api/vault/projects/{pid}/gather", json_body={"note": "n00000000"})[0] == 404
    assert app("GET", f"/api/vault/notes/{idea['id']}")[1]["gathered_for"] == [{"id": pid, "title": "Talk on habits"}]
    home = app("GET", "/api/vault")[1]
    assert [n["id"] for n in home["indexes"]] == [index["id"]] and [n["id"] for n in home["recent"]] == [idea["id"]]
    assert [(p["title"], p["status"], p["gathered"], p["project_notes"]) for p in home["projects"]] == [("Talk on habits", "active", 1, 1)]
    # the outline, exported with the notes' text
    now = app("GET", f"/api/vault/notes/{pid}")[1]
    saved = app("PUT", f"/api/vault/notes/{pid}", json_body={"body": now["body"] + "\n- Opening\n  - [[Decide once]]\n", "token": now["token"],
                                                             "props": [p if p["key"] != "status" else {"key": "status", "value": "finished"} for p in now["props"]]})[1]
    assert saved["project"]["status"] == "finished" and app("GET", "/api/vault")[1]["projects"][0]["status"] == "finished"
    request = urllib.request.Request(app.jobs and f"http://127.0.0.1:{app.port}/api/vault/notes/{pid}/export.md")
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.headers["Content-Disposition"] == 'attachment; filename="Talk on habits.md"'
        assert response.read().decode() == "# Talk on habits\n\n## Opening\n\nA decision made in advance is cheaper.\n"
    assert app("GET", "/api/vault/peek?" + q(target="Decide once", full="1"))[1]["body"] == "A decision made in advance is cheaper."


def test_the_graph_a_neighbourhood_what_to_revisit_and_the_timeline(app):
    jid = app.book()
    hid = app("POST", f"/api/jobs/{jid}/notes/highlight", json_body={"chapter": 2, "start": 3, "end": 9})[1]["made"]
    app("PUT", f"/api/jobs/{jid}/notes/target", json_body={"target": {"on": "book"}, "text": "On the whole book"})
    a = app("POST", "/api/vault/notes", json_body={"title": "A", "body": "To [[B]].", "source": {"job": jid, "block": hid}})[1]
    b = app("POST", "/api/vault/notes", json_body={"title": "B", "body": "x"})[1]
    alone = app("POST", "/api/vault/notes", json_body={"title": "Alone", "body": "x"})[1]
    quick = app("POST", "/api/vault/notes", json_body={"body": "a thought", "kind": "quick"})[1]
    graph = app("GET", "/api/vault/graph")[1]
    book = bookfile.book_id(jid)
    assert {n["id"] for n in graph["nodes"]} == {a["id"], b["id"], alone["id"], quick["id"], book}
    assert sorted(map(tuple, graph["edges"])) == sorted([tuple(sorted((a["id"], b["id"]))), tuple(sorted((a["id"], book)))])
    near = app("GET", f"/api/vault/notes/{a['id']}/neighbourhood")[1]
    assert {n["id"]: n["step"] for n in near["nodes"]} == {a["id"]: 0, b["id"]: 1, book: 1} and not near["more"]
    assert app("GET", "/api/vault/notes/n00000000/neighbourhood")[0] == 404
    again = app("GET", "/api/vault/revisit")[1]
    assert [n["id"] for n in again["unlinked"]] == [alone["id"]] and again["old"] == [] and [n["id"] for n in again["today"]] == [alone["id"]]
    assert [(n["id"], n["why"]) for n in app("GET", "/api/vault")[1]["revisit"]] == [(alone["id"], "unlinked")]
    # the timeline: today holds what was written, caught and marked; a note changed on a later day shows there as edited
    import datetime
    import os
    line = app("GET", "/api/timeline")[1]
    today = datetime.date.today().isoformat()
    assert [d["day"] for d in line["days"]] == [today] and not line["more"]
    day = line["days"][0]
    assert {n["id"] for n in day["written"]} == {a["id"], b["id"], alone["id"]} and [n["id"] for n in day["quick"]] == [quick["id"]] and day["edited"] == []
    assert [(x["job"], [h["id"] for h in x["highlights"]], len(x["notes"])) for x in day["books"]] == [(jid, [hid], 1)]
    assert day["books"][0]["highlights"][0]["text"].startswith("The supply boat")
    path = app.folder / "B.md"
    path.write_text(path.read_text(encoding="utf-8").replace("created: " + b["created"], "created: 2026-01-05T12:00:00Z"), encoding="utf-8")
    then = (datetime.datetime(2026, 3, 9, 12) - datetime.datetime(1970, 1, 1)).total_seconds()
    os.utime(app.folder / "Alone.md", (then, then))
    app.jobs.vault().refresh(force=True)
    line = app("GET", "/api/timeline")[1]
    assert [d["day"] for d in line["days"]] == [today, "2026-03-09", "2026-01-05"]
    assert [n["id"] for n in line["days"][1]["edited"]] == [alone["id"]] and [n["id"] for n in line["days"][2]["written"]] == [b["id"]]
    assert b["id"] in {n["id"] for n in line["days"][0]["edited"]} and b["id"] not in {n["id"] for n in line["days"][0]["written"]}
    assert [d["day"] for d in app("GET", "/api/timeline?before=2026-03-09")[1]["days"]] == ["2026-01-05"]
    assert app("GET", "/api/timeline?before=yesterday")[0] == 400
    # a day holds so many of a kind and says how many there are: a copied folder has every note on one day
    assert day["counts"] == {"written": 3, "edited": 0, "quick": 1} and day["books"][0]["counts"] == {"highlights": 1, "notes": 1}
    app.jobs.DAY_SHOWN = 2
    cut = app("GET", "/api/timeline")[1]["days"][0]
    assert len(cut["edited"]) + len(cut["written"]) <= 4 and cut["counts"]["written"] + cut["counts"]["edited"] == 3
    # a file looked at in the Add form and left is dropped after its time, also when the form is not used again
    app.jobs.held["left"] = {"time": web.time.monotonic() - app.jobs.HELD_SECONDS - 1}
    app("GET", "/api/jobs")
    assert "left" not in app.jobs.held


@pytest.fixture
def helped(tmp_path):
    """The app with a stand-in for the model that answers each kind of call of the notes folder."""
    from ebook_processor import assist
    from ebook_processor.llm import ScriptedBackend
    calls: list[dict] = []

    def reply(role, system, prompt, schema):
        calls.append({"role": role, "prompt": prompt})
        need = schema.get("required", [])
        if "connections" in need:
            ref = next(line.split()[0] for line in prompt.split("\n") if "  Decide once  |" in line)
            return {"connections": [{"to": ref, "reason": "The other note is the general rule.", "mine": "A rule removes the daily decision.", "theirs": "nothing it says"}]}
        if "covered" in need:
            return {"covered": True, "answer": "A rule removes the daily decision [N1].", "missing": ""} if "lamp" in prompt.split("SOURCES")[0] \
                else {"covered": False, "answer": "", "missing": "Your notes do not say."}
        if "question" in need:
            return {"question": "Why does a fixed rule make a daily duty easier to keep?" if "ALREADY ASKED" not in prompt else "According to the note, why?",
                    "answer": "It removes the decision that would otherwise be made each day."}
        return {**{c: True for c in assist.CHECKS}, "stands_alone": "According to the note" not in prompt, "problem": "It refers to the note."}

    server = web.make_server(tmp_path / "data", "127.0.0.1", 0, backend_factory=lambda settings: ScriptedBackend(reply),
                             claude={"ok": True, "version": "test"}, models=[])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method: str, path: str, json_body=None):
        body = json.dumps(json_body).encode() if json_body is not None else None
        request = urllib.request.Request(base + path, data=body, method=method, headers={"X-Ebook-Processor": "1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    call.calls, call.data = calls, tmp_path / "data"
    yield call
    server.shutdown()
    server.server_close()


def test_the_model_finds_connections_answers_from_the_notes_and_writes_a_notes_question(helped):
    note = helped("POST", "/api/vault/notes", json_body={"title": "A duty is kept by rule", "body": "The keeper lights the lamp whatever the weather. A rule removes the daily decision.\n"})[1]
    other = helped("POST", "/api/vault/notes", json_body={"title": "Decide once", "body": "A decision made in advance is cheaper. The lamp is one such rule.\n"})[1]
    # connections: what would be sent is told first; then the call, and its reply checked
    told = helped("GET", f"/api/assist/connections?note={note['id']}")[1]
    assert told["sent"] == {"listed": 1, "full": 1, "passages": 0, "total": 1} and told["model"] and helped.calls == []
    found = helped("POST", "/api/assist/connections", json_body={"note": note["id"]})[1]
    assert [(c["id"], c["link"], c["reason"], c["mine"], c["theirs"]) for c in found["connections"]] == [
        (other["id"], "Decide once", "The other note is the general rule.", "A rule removes the daily decision.", "")]   # a quotation that is not there is dropped
    assert helped("POST", "/api/assist/connections", json_body={"note": "n00000000"})[0] == 404
    # an answer, with its sources and the text a kept answer would have
    got = helped("POST", "/api/assist/answer", json_body={"question": "Why does the keeper light the lamp whatever the weather?"})[1]
    assert got["covered"] and got["answer"] == "A rule removes the daily decision [N1]." and [s["id"] for s in got["sources"]] == [note["id"]]
    assert got["keep"].startswith("**Question:** Why does the keeper light the lamp whatever the weather?\n\nA rule removes the daily decision [[A duty is kept by rule]].")
    kept = helped("POST", "/api/vault/notes", json_body={"kind": "quick", "body": got["keep"], "by": "model"})[1]
    assert {"key": "by", "value": "model"} in kept["props"] and kept["inbox"] and kept["links"][0]["to"]["id"] == note["id"]
    gap = helped("POST", "/api/assist/answer", json_body={"question": "What does a rule cost in the weather?"})[1]
    assert not gap["covered"] and gap["missing"] == "Your notes do not say." and gap["closest"] and "keep" not in gap
    assert helped("POST", "/api/assist/answer", json_body={"question": ""})[0] == 400
    # a question from the note: proposed and checked, accepted into Practice, and marked when the note changes
    helped.calls.clear()
    made = helped("POST", "/api/assist/questions", json_body={"note": note["id"]})[1]
    q = made["questions"][0]
    assert [c["role"] for c in helped.calls] == ["draft", "check"] and made["forms"] == ["open"]
    assert q["status"] == "proposed" and q["by"] == "model" and q["checked"]["passed"] and not q["stale"] and q["card"] is None
    assert helped("GET", "/api/practice")[1]["card"] is None and helped("GET", "/api/jobs")[1]["notes_practice"] is None
    accepted = helped("PUT", f"/api/assist/questions/{q['id']}", json_body={"accept": True})[1]["questions"][0]
    assert accepted["status"] == "accepted" and accepted["card"] == {"state": "new", "times": 0}
    held = helped("POST", "/api/assist/questions", json_body={"note": note["id"]})[1]["questions"][1]       # the second proposal fails its check
    assert held["status"] == "held" and held["problem"] == "It refers to the note."
    assert helped("PUT", f"/api/assist/questions/{held['id']}", json_body={"question": "What does a fixed rule remove from a daily duty?"})[1]["questions"][1]["status"] == "proposed"
    assert helped("DELETE", f"/api/assist/questions/{held['id']}")[1]["questions"][0]["id"] == q["id"]
    own = helped("POST", "/api/assist/questions", json_body={"note": note["id"], "question": "What does the keeper do at sunset?", "answer": "Lights the lamp."})[1]["questions"][1]
    assert own["by"] == "you" and own["status"] == "accepted" and own["card"]["state"] == "new"
    assert helped("POST", "/api/assist/questions", json_body={"note": note["id"], "question": "", "answer": "x"})[0] == 400
    card = helped("GET", "/api/practice")[1]
    assert card["counts"]["cards"] == 2 and card["card"]["job"] == "notes" and card["card"]["note"] == note["id"] and card["card"]["book"] == "Your notes"
    assert card["card"]["section"] == "A duty is kept by rule" and card["card"]["passage"][0][0]["t"].startswith("The keeper lights the lamp")
    assert helped("GET", "/api/jobs")[1]["notes_practice"]["cards"] == 2
    after = helped("POST", "/api/practice/answer", json_body={"job": "notes", "id": card["card"]["id"], "remembered": True, "text": ""})[1]
    assert after["card"]["id"] != card["card"]["id"] and json.loads((helped.data / "notes-practice" / "srs.json").read_text())["cards"][card["card"]["id"]]["reviews"]
    now = helped("GET", f"/api/vault/notes/{note['id']}")[1]
    helped("PUT", f"/api/vault/notes/{note['id']}", json_body={"body": now["body"] + "\nAnd more.", "token": now["token"]})
    stale = helped("GET", f"/api/assist/questions?note={note['id']}")[1]["questions"]
    assert [x["stale"] for x in stale] == [True, True]
    again = helped("PUT", f"/api/assist/questions/{q['id']}", json_body={"check": True})[1]["questions"][0]
    assert not again["stale"] and again["checked"]["passed"] and again["status"] == "accepted"
    # the note goes to the trash: its cards rest; the question of a note that is gone can still be removed
    helped("DELETE", f"/api/vault/notes/{note['id']}")
    assert helped("GET", "/api/practice")[1]["card"] is None
    assert helped("PUT", f"/api/assist/questions/{q['id']}", json_body={"accept": True})[0] == 404
    assert helped("DELETE", f"/api/assist/questions/{q['id']}")[1]["questions"] == [] and helped("DELETE", "/api/assist/questions/nq00000000")[0] == 400
