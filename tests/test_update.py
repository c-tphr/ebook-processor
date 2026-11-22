"""``ebook-processor update``: finding and stopping the running app, and fast-forwarding the checkout."""

from __future__ import annotations

import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ebook_processor import cli, update


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    """A checkout on ``main`` with an origin that is one commit ahead."""
    origin, work, clone = tmp_path / "origin.git", tmp_path / "work", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git(work, "checkout", "-q", "-b", "main")
    (work / "file.txt").write_text("one\n")
    git(work, "add", "."), git(work, "commit", "-q", "-m", "first"), git(work, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    (work / "file.txt").write_text("two\n")
    git(work, "commit", "-q", "-am", "second"), git(work, "push", "-q", "origin", "main")
    return clone, work


@pytest.fixture
def app(tmp_path, monkeypatch, make_web_app):
    """A running app with its record, as ``serve`` leaves it."""
    monkeypatch.setenv("EBOOK_PROCESSOR_HOME", str(tmp_path / "home"))
    it = make_web_app(model=None)
    record = update.write_record("127.0.0.1", it.port, it.data)
    it.server.RequestHandlerClass.shutdown_token = record["token"]
    return record, it.server


def test_the_checkout_is_fast_forwarded_to_the_latest_main(checkout):
    clone, work = checkout
    before, after = update.pull(clone)
    assert before != after and after == git(work, "rev-parse", "HEAD") and (clone / "file.txt").read_text() == "two\n"
    assert update.pull(clone) == (after, after)  # nothing new: nothing changes


def test_a_checkout_with_changes_or_on_another_branch_is_left_alone(checkout):
    clone, work = checkout
    (clone / "file.txt").write_text("mine\n")
    with pytest.raises(update.UpdateError, match="not committed"):
        update.pull(clone)
    assert (clone / "file.txt").read_text() == "mine\n"
    git(clone, "checkout", "-q", "--", "file.txt")
    (clone / "untracked.txt").write_text("fine\n")  # a file git does not track is no obstacle
    git(clone, "checkout", "-q", "-b", "other")
    (clone / "own.txt").write_text("work of my own\n")
    git(clone, "add", "own.txt"), git(clone, "commit", "-q", "-m", "mine")
    with pytest.raises(update.UpdateError, match="on the branch 'other', which has commits that are on no branch of the remote"):
        update.pull(clone)                           # leaving it would hide that commit
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "other" and (clone / "own.txt").is_file()


def test_beta_switches_to_the_development_branch_and_update_goes_back_to_main(tmp_path, checkout, monkeypatch):
    clone, work = checkout
    git(work, "checkout", "-q", "-b", "dev/next")
    (work / "file.txt").write_text("three, not yet on main\n")
    git(work, "commit", "-q", "-am", "third"), git(work, "push", "-q", "origin", "dev/next")
    before, after = update.pull(clone, "dev/next")   # from main, one commit behind it, to the development branch
    assert before != after and after == git(work, "rev-parse", "HEAD") and (clone / "file.txt").read_text() == "three, not yet on main\n"
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "dev/next" and update.pull(clone, "dev/next") == (after, after)
    (work / "file.txt").write_text("four\n")
    git(work, "commit", "-q", "-am", "fourth"), git(work, "push", "-q", "origin", "dev/next")
    assert update.pull(clone, "dev/next")[1] == git(work, "rev-parse", "HEAD")           # on it already: brought up to date
    back = update.pull(clone)                                                            # and back to main
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "main" and (clone / "file.txt").read_text() == "two\n" and back[1] == git(work, "rev-parse", "main")
    assert update.pull(clone, "dev/next")[1] == git(work, "rev-parse", "dev/next")       # the branch is there now: checked out, not made
    with pytest.raises(update.UpdateError, match="'no/such' could not be fetched"):
        update.pull(clone, "no/such")
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "dev/next"
    # the command: beta takes the development branch, and names another with --branch; update takes main
    monkeypatch.setenv("EBOOK_PROCESSOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update, "unrecorded", lambda port=update.DEFAULT_PORT: False)
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    asked = []
    monkeypatch.setattr(update, "update", lambda **kw: asked.append(kw) or 0)
    assert cli.main(["beta", "--no-serve"]) == 0 and cli.main(["beta", "--branch", "dev/next"]) == 0 and cli.main(["update", "--force"]) == 0
    assert [(kw["branch"], kw["serve"], kw["force"]) for kw in asked] == [(update.BETA_BRANCH, False, False), ("dev/next", True, False), ("main", True, True)]
    monkeypatch.undo()
    monkeypatch.setenv("EBOOK_PROCESSOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update, "unrecorded", lambda port=update.DEFAULT_PORT: False)
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    said, installed = [], []
    assert update.update(serve=False, say=said.append, installer=installed.append, branch="main") == 0
    text = "\n".join(said)
    assert "Fetching the latest main" in text and "Now on main" in text and "2 commits of the version you had are not in this one" in text and installed == [clone]


def test_a_running_app_is_found_by_its_record_and_stops_when_asked_with_its_token(app):
    record, server = app
    assert [r["port"] for r in update.servers()] == [record["port"]] and update.busy(record) == []
    wrong = urllib.request.Request(f"http://127.0.0.1:{record['port']}/api/shutdown", data=b"", method="POST",
                                   headers={"X-Ebook-Processor": "1", "X-Shutdown-Token": "guess"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(wrong, timeout=10)
    assert refused.value.code == 403 and update.listening("127.0.0.1", record["port"])
    assert update.record_path(record["port"]).is_file()
    # the app stops itself; closing the socket is serve()'s job, done here by the test
    closer = threading.Timer(0.5, server.server_close)
    closer.start()
    assert update.stop(record, timeout=10) is True
    assert not update.listening("127.0.0.1", record["port"]) and not update.record_path(record["port"]).exists()
    assert update.servers() == []


def test_records_of_apps_that_are_gone_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("EBOOK_PROCESSOR_HOME", str(tmp_path / "home"))
    update.write_record("127.0.0.1", 1, tmp_path)  # this process, but nothing listens on port 1
    (update.home() / "servers" / "junk.json").write_text("not json")
    assert update.servers() == [] and list((update.home() / "servers").iterdir()) == []


def test_update_stops_the_app_updates_and_starts_it_again_as_it_was(app, checkout, monkeypatch):
    record, server = app
    clone, _ = checkout
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    said, installed, launched = [], [], []
    threading.Timer(0.5, server.server_close).start()
    status = update.update(say=said.append, installer=installed.append, launch=launched.append)
    assert status == 0 and installed == [clone] and (clone / "file.txt").read_text() == "two\n"
    assert launched == [[update.sys.executable, "-m", "ebook_processor", "serve", "--host", "127.0.0.1", "--port",
                         str(record["port"]), "--data", record["data"], "--no-open"]]
    text = "\n".join(said)
    assert f"Stopping the app on port {record['port']}" in text and "second" in text and "Starting the app" in text


def test_update_without_a_running_app_and_without_starting_one(tmp_path, checkout, monkeypatch):
    monkeypatch.setenv("EBOOK_PROCESSOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update, "unrecorded", lambda port=update.DEFAULT_PORT: False)
    clone, _ = checkout
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    said, installed, launched = [], [], []
    assert update.update(serve=False, say=said.append, installer=installed.append, launch=launched.append) == 0
    assert installed == [clone] and launched == [] and "ebook-processor serve" in said[-1]
    said.clear(), installed.clear()
    assert update.update(serve=True, say=said.append, installer=installed.append, launch=launched.append) == 0
    assert installed == [] and "Already up to date" in "\n".join(said)  # nothing new: nothing to install
    assert launched == [[update.sys.executable, "-m", "ebook_processor", "serve"]]  # defaults, and the browser opens


def test_update_will_not_interrupt_a_book_or_an_app_it_cannot_stop(app, checkout, monkeypatch):
    record, _ = app
    clone, _ = checkout
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    monkeypatch.setattr(update, "busy", lambda r: ["Essays"])
    with pytest.raises(update.UpdateError, match="is processing Essays"):
        update.update(say=lambda s: None, installer=lambda r: None, launch=lambda c: None)
    assert update.listening("127.0.0.1", record["port"]) and (clone / "file.txt").read_text() == "one\n"  # untouched
    monkeypatch.setattr(update, "servers", lambda: [])
    monkeypatch.setattr(update, "unrecorded", lambda port=update.DEFAULT_PORT: True)
    with pytest.raises(update.UpdateError, match="started before this command existed"):
        update.update(say=lambda s: None, installer=lambda r: None, launch=lambda c: None)


def test_the_command_reports_what_stops_it(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(update, "repo_root", lambda: (_ for _ in ()).throw(update.UpdateError("not a git checkout")))
    assert cli.main(["update", "--no-serve"]) == 1 and "not a git checkout" in capsys.readouterr().err
    monkeypatch.undo()
    assert (Path(update.__file__).resolve().parent.parent / ".git").exists() and update.repo_root().name


def test_an_update_that_cannot_be_made_leaves_the_app_running(app, checkout, monkeypatch):
    """The checkout and the remote are looked at before the app is stopped."""
    record, _ = app
    clone, _ = checkout
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    (clone / "file.txt").write_text("mine\n")
    launched: list = []
    with pytest.raises(update.UpdateError, match="not committed"):
        update.update(say=lambda s: None, installer=lambda r: None, launch=launched.append)
    assert update.listening("127.0.0.1", record["port"]) and launched == []
    git(clone, "checkout", "-q", "--", "file.txt")
    git(clone, "remote", "set-url", "origin", str(clone.parent / "no-such-remote"))
    with pytest.raises(update.UpdateError, match="could not be fetched"):
        update.update(say=lambda s: None, installer=lambda r: None, launch=launched.append)
    assert update.listening("127.0.0.1", record["port"]) and launched == []


def test_an_update_that_fails_after_the_app_was_stopped_starts_it_again(app, checkout, monkeypatch):
    record, server = app
    clone, _ = checkout
    monkeypatch.setattr(update, "repo_root", lambda: clone)
    said, launched = [], []
    threading.Timer(0.5, server.server_close).start()

    def broken(repo):
        raise update.UpdateError("the new version was fetched, but installing it failed")
    assert update.update(say=said.append, installer=broken, launch=launched.append) == 1
    assert launched and launched[0][-5:-1] == ["--port", str(record["port"]), "--data", record["data"]]
    assert "did not go through" in "\n".join(said)
