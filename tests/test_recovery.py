"""A run that loses its connection, or is stopped while a call hangs.

The stand-in programs here hang or answer as told; the "network" is a function that says whether the service can
be reached. No test calls a model or needs the network.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from ebook_processor import llm, prompts
from ebook_processor.llm import (BackendCancelled, BackendError, Backends, ClaudeCodeBackend, CodexBackend, Connection,
                                 ScriptedBackend, _Halted, _transient, _with_retries, run_process)
from ebook_processor.pipeline import Cancelled
from ebook_processor.runner import default_backend, run_book
from ebook_processor.settings import from_options
from ebook_processor.web.server import Jobs
from tests.programs import gone, make_program
from tests.paths import FIXTURE


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def link_with(answers: list[bool], **kwargs) -> tuple[Connection, Clock, list[bool]]:
    """A connection whose probes give ``answers`` in turn (the last one for ever), on a clock the test moves."""
    clock, seen, left = Clock(), [], list(answers)

    def probe(url: str, timeout: float) -> bool:
        return left.pop(0) if len(left) > 1 else left[0]

    link = Connection("https://service.invalid", "the service", probe=probe, clock=clock, on_change=seen.append, **kwargs)
    return link, clock, seen


# --------------------------------------------------------------------------
# Whether the service can be reached


def test_a_connection_that_was_never_seen_says_nothing():
    link, clock, _ = link_with([False])
    assert not link.reachable() and not link.down() and not link.lost()
    clock.now += 3600
    assert not link.lost()  # the probe may simply not work on this network: calls keep their own time limits


def test_a_connection_is_lost_once_it_has_been_out_of_reach_for_a_while():
    link, clock, _ = link_with([True, False])
    assert link.reachable() and link.seen_up and not link.down()
    clock.now += 11
    assert link.down() and not link.lost()  # out of reach now, but a short gap ends no call
    clock.now += 30
    assert not link.lost()
    clock.now += 20
    assert link.lost()  # 50 seconds: calls in flight are given up


def test_probes_are_shared_and_a_return_resets_the_count():
    asked = []
    link, clock, _ = link_with([True])
    link.probe = lambda url, timeout: asked.append(clock.now) or (clock.now < 1100 or clock.now >= 1200)
    assert link.reachable() and link.reachable(max_age=5) and len(asked) == 1  # the second took the first's answer
    clock.now = 1100
    assert not link.reachable()
    clock.now = 1190
    assert link.lost()
    clock.now = 1201  # past the ten seconds for which the last answer is taken
    assert not link.lost() and not link.down()  # back: the time out of reach starts from nothing again
    clock.now = 1300
    link.probe = lambda url, timeout: False
    assert link.down() and not link.lost()


def test_waiting_ends_when_the_service_is_back_and_says_so_once():
    link, clock, seen = link_with([True, False, False, True])
    link.reachable()
    clock.now += 11
    assert link.down()
    pauses = []

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        clock.now += seconds

    link.wait(pause=pause)
    assert seen == [False, True] and pauses == [10.0, 10.0]


def test_a_stop_ends_the_wait_without_saying_the_connection_is_back():
    link, clock, seen = link_with([True, False])
    link.reachable()
    clock.now += 11
    assert link.down()
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    link.interval = 0.05
    started = time.monotonic()
    with pytest.raises(BackendCancelled):
        link.wait(cancel)
    assert time.monotonic() - started < 2 and seen == [False]


# --------------------------------------------------------------------------
# Trying again


def test_a_call_lost_to_the_network_waits_and_is_made_again_without_using_up_a_try():
    link, clock, seen = link_with([True])
    link.probe = lambda url, timeout: not (1010 <= clock.now < 1075 or 1100 <= clock.now < 1150)  # out of reach twice
    link.reachable()
    tries, pauses = [], []

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        clock.now += seconds

    def once():
        clock.now += 30  # a call takes its time
        tries.append(clock.now)
        if len(tries) < 3:
            raise _transient("API Error: Connection error.")
        return "answer"

    assert _with_retries("Claude", "draft", 0, (15.0,), pause, once, link) == "answer"  # retries=0: no try to spare
    assert tries == [1030, 1110, 1180] and seen == [False, True, False, True] and set(pauses) == {10.0}


def test_a_failure_with_the_network_up_or_never_seen_counts_as_before():
    up, _, seen = link_with([True])
    up.reachable()
    never, _, _ = link_with([False])
    for link in (up, never, None):
        waits: list[float] = []

        def once():
            raise _transient("overloaded")

        with pytest.raises(BackendError, match="did not answer the draft request after 3 attempts: overloaded"):
            _with_retries("Claude", "draft", 2, (15.0, 60.0), waits.append, once, link)
        assert waits == [15.0, 60.0]
    assert seen == []


def test_a_stop_ends_the_pause_between_tries():
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(BackendCancelled):
        _with_retries("Claude", "draft", 3, (60.0,), time.sleep, lambda: (_ for _ in ()).throw(_transient("overloaded")), None, cancel)
    assert time.monotonic() - started < 3


# --------------------------------------------------------------------------
# Giving up a call that is running


def hanging(tmp_path: Path, name: str = "tool") -> tuple[str, Path]:
    """A program that starts a child, notes both process ids, and then hangs. The second time it is run it
    answers as Claude Code does."""
    folder = tmp_path / "bin"
    pids = folder / "pids.json"
    return make_program(folder, name, f'''import json, os, subprocess, sys, time
pids = {str(pids)!r}
if os.path.exists(pids):
    sys.stdin.read()
    print(json.dumps({{"structured_output": {{"ranges": []}}, "usage": {{"input_tokens": 5, "output_tokens": 2}}}}))
    sys.exit(0)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
json.dump([os.getpid(), child.pid], open(pids, "w"))
time.sleep(120)
'''), pids


def wait_for(path: Path) -> list[int]:
    for _ in range(100):
        if path.exists() and path.read_text():
            return json.loads(path.read_text())
        time.sleep(0.05)
    raise AssertionError("the program did not start")


def test_a_running_program_is_given_up_with_everything_it_started(tmp_path):
    exe, pids = hanging(tmp_path)
    reasons = iter([None, None, "cancel"])
    started = time.monotonic()
    with pytest.raises(_Halted) as halted:
        run_process([exe], "input", str(tmp_path), 60, lambda: next(reasons))
    assert halted.value.reason == "cancel" and time.monotonic() - started < 6
    program, child = wait_for(pids)
    assert gone(program) and gone(child)  # the child too: it would keep the pipes open
    with pytest.raises(_Halted) as halted:  # the time limit works the same way
        pids.unlink()
        run_process([exe], "input", str(tmp_path), 0.6)
    assert halted.value.reason == "timeout"


def test_stop_gives_up_a_call_that_hangs(tmp_path):
    exe, pids = hanging(tmp_path, "claude")
    cancel = threading.Event()
    backend = ClaudeCodeBackend(executable=exe, cancel=cancel)
    threading.Timer(0.6, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(BackendCancelled):
        backend.complete(role="draft", system="S", prompt="P", schema=prompts.DRAFT_SCHEMA)
    assert time.monotonic() - started < 6
    assert all(gone(pid) for pid in wait_for(pids))


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_a_call_that_hangs_without_a_connection_is_given_up_and_made_again_when_it_is_back(tmp_path, tool):
    """The case of the report: the network goes while a call runs, and the call never ends by itself."""
    seen: list[bool] = []
    state = {"up": True}
    link = Connection("https://service.invalid", "the service", interval=0.1, down_after=0.4,
                      probe=lambda url, timeout: state["up"], on_change=seen.append)
    link.reachable()
    if tool == "claude":
        exe, pids = hanging(tmp_path, "claude")
        backend = ClaudeCodeBackend(executable=exe, link=link)
    else:
        from tests.test_codex import fake_codex
        exe, _ = fake_codex(tmp_path, [{"answer": {"ranges": []}}])
        folder = Path(exe).parent
        pids = folder / "pids.json"
        # in front of the stand-in Codex: hangs the first time it is run, hands over to it afterwards
        exe = make_program(folder, "codex-first-hangs", f'''import json, os, subprocess, sys, time
if os.path.exists({str(pids)!r}):
    sys.exit(subprocess.call([{exe!r}, *sys.argv[1:]]))
json.dump([os.getpid()], open({str(pids)!r}, "w"))
time.sleep(120)
''')
        backend = CodexBackend(executable=exe, link=link)

    def network() -> None:  # the connection goes once the call is under way, and comes back a second later
        wait_for(pids)
        state["up"] = False
        time.sleep(1.2)
        state["up"] = True

    threading.Thread(target=network, daemon=True).start()
    started = time.monotonic()
    done = backend.complete(role="draft", system="S", prompt="P", schema=prompts.DRAFT_SCHEMA)
    assert done.data == {"ranges": []} and time.monotonic() - started < 15
    assert seen == [False, True]  # said once that it waits, and once that it goes on
    assert gone(wait_for(pids)[0])


# --------------------------------------------------------------------------
# A whole run


def test_stopping_a_run_ends_it_at_once_even_while_a_call_hangs(tmp_path):
    exe, pids = hanging(tmp_path, "claude")
    cancel = threading.Event()
    backend = Backends(claude=ClaudeCodeBackend(executable=exe, cancel=cancel), codex=ScriptedBackend(lambda *a: {}))
    threading.Timer(0.8, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_book(FIXTURE, tmp_path / "out", from_options(density="short"), backend=backend, cancel=cancel)
    assert time.monotonic() - started < 8
    assert (tmp_path / "out" / "work" / "state.json").is_file()  # the run can be continued


def test_a_run_says_when_it_waits_for_the_network_and_when_it_goes_on(tmp_path):
    events: list[dict] = []
    cancel = threading.Event()
    backend = default_backend(from_options(), events.append, cancel)
    assert backend.claude.cancel is cancel and backend.codex.cancel is cancel
    assert backend.claude.link.url == llm.service_urls()["claude"] and backend.codex.link.url == "https://chatgpt.com"
    backend.claude.link.on_change(False)
    backend.claude.link.on_change(True)
    assert [e["stage"] for e in events] == ["network", "network"]
    assert "No connection to Claude Code. Waiting for the network" in events[0]["message"] and "is back" in events[1]["message"]
    # the app shows the message and leaves the step and its progress as they were
    jobs = Jobs(tmp_path / "data")
    job = {"events": [], "stage": "check", "message": "Checking", "done": 3, "total": 8}
    jobs._event(job, events[0])
    assert (job["stage"], job["done"], job["total"]) == ("check", 3, 8) and job["message"].startswith("No connection to Claude Code")


def test_the_address_asked_follows_a_configured_claude_address(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.test")
    assert llm.service_urls()["claude"] == "https://gateway.example.test"
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    assert llm.service_urls()["claude"] == "https://api.anthropic.com"


def test_a_number_inside_a_longer_number_is_no_error_code():
    """'215500 tokens' is not a 5xx answer, and '240100 tokens' is not a 401."""
    from ebook_processor import llm
    for transient in (llm._TRANSIENT_RE, llm._CODEX_TRANSIENT_RE):
        assert not transient.search("Prompt is too long: 215500 tokens > 200000 maximum")
        assert not transient.search("Invalid model name: claude-opus-4-5-20251101")
        assert transient.search("API Error: 529 overloaded_error") and transient.search("HTTP 503")
    for login in (llm._LOGIN_RE, llm._CODEX_LOGIN_RE):
        assert not login.search("Prompt is too long: 240100 tokens")
        assert login.search("API Error: 401 Unauthorized")


@pytest.mark.skipif(sys.platform == "win32", reason="a signal sent to oneself is not Ctrl-C on Windows")
def test_ctrl_c_gives_up_the_calls_that_are_running(tmp_path):
    """Ctrl-C reaches the main thread while it waits for the calls of a step. Leaving that step used to wait for
    every call, up to its limit of half an hour, before the stop event was set."""
    import signal
    exe, pids = hanging(tmp_path, "claude")
    cancel = threading.Event()
    backend = Backends(claude=ClaudeCodeBackend(executable=exe, cancel=cancel), codex=ScriptedBackend(lambda *a: {}))
    threading.Timer(0.8, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_book(FIXTURE, tmp_path / "out", from_options(density="short"), backend=backend, cancel=cancel)
    assert cancel.is_set() and time.monotonic() - started < 8
    assert all(gone(pid) for pid in wait_for(pids))
    assert (tmp_path / "out" / "work" / "state.json").is_file()  # the run can be continued


def test_the_app_ending_stops_its_runs_and_they_save_themselves_as_stopped(tmp_path):
    holder: dict = {}

    def reply(role, system, prompt, schema):
        while not any(e.is_set() for e in list(holder["jobs"].cancels.values())):
            time.sleep(0.02)
        raise BackendCancelled("stopped")
    jobs = holder["jobs"] = Jobs(tmp_path / "data", backend_factory=lambda settings: ScriptedBackend(reply))
    job = jobs.create(FIXTURE.name, FIXTURE.read_bytes(), from_options(density="short"))
    for _ in range(100):
        if jobs.get(job["id"])["status"] == "running":
            break
        time.sleep(0.05)
    started = time.monotonic()
    jobs.stop_all()
    assert time.monotonic() - started < 4 and jobs.stopping.is_set()
    assert jobs.get(job["id"])["status"] == "stopped" and not jobs.cancels


def test_a_call_the_reader_waits_for_has_short_limits_and_the_look_at_the_tools_is_kept_a_minute(tmp_path, monkeypatch):
    """A check or a rewrite went through the backend of a run: half an hour for a call, and three more tries."""
    from ebook_processor import runner
    from ebook_processor.web import server as web
    waiting, run = default_backend(from_options(), waiting=True), default_backend(from_options())
    assert (waiting.claude.timeout, waiting.claude.retries, waiting.codex.timeout, waiting.codex.retries) == (300.0, 1, 300.0, 1)
    assert (run.claude.timeout, run.claude.retries) == (1800.0, 3)
    settings = from_options(model="claude-a", checker_model="gpt-b")
    assert runner.models_for(settings, ["draft", "check", "check"]) == ["claude-a", "gpt-b", "gpt-b"]
    looks: list = []
    monkeypatch.setattr(Backends, "check_available", lambda self, models=None: looks.append(tuple(models)))
    jobs = Jobs(tmp_path / "data")
    first = jobs._backend(settings, ["check"])
    jobs._backend(settings, ["check"])
    jobs._backend(settings, ["draft", "check"])
    assert looks == [("gpt-b",), ("claude-a", "gpt-b")]            # the same models are not looked for twice in a minute
    assert first.claude.cancel is jobs.stopping and first.claude.timeout == 300.0
    jobs.looked.clear()
    assert jobs._backend(settings, ["draft"], waiting=False).claude.timeout == 1800.0 and looks[-1] == ("claude-a",)
    assert web.default_backend is runner.default_backend
