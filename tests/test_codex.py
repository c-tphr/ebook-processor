"""Codex beside Claude Code: which tool a model setting names, the call to ``codex exec``, and a run that drafts
with one and checks with the other.

No test calls Codex. ``fake_codex`` writes a stand-in program that answers as the real one does: the version, the
login status, the model catalogue, and for ``exec`` a stream of JSON events plus the file with the last message.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from ebook_processor import cli, prompts
from ebook_processor.llm import (BackendError, Backends, BackendUnavailable, CodexBackend, Completion, ScriptedBackend,
                                 UsageLimitError, available_models, split_model)
from ebook_processor.outputs import _price
from ebook_processor.settings import from_options
from ebook_processor.web.server import make_server
from tests.scripted import Script
from tests.test_pipeline import FIXTURE, run_book

CATALOGUE = {"models": [
    {"slug": "gpt-old", "display_name": "GPT Old", "description": "Legacy.", "visibility": "list", "priority": 9,
     "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
    {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "description": "Workhorse.", "visibility": "list", "priority": 1,
     "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}, {"effort": "ultra"}]},
    {"slug": "internal-review", "display_name": "Hidden", "visibility": "hide", "priority": 2},
    {"display_name": "no slug: skipped"}]}

FAKE = r'''#!{python}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(here, "plan.json")))
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if args == ["login", "status"]:
    print("Logged in using ChatGPT" if plan["logged_in"] else "Not logged in", file=sys.stderr); sys.exit(0 if plan["logged_in"] else 1)
if args[:2] == ["debug", "models"]:
    if plan["catalogue"] is None:
        print("error: unrecognized subcommand 'models'", file=sys.stderr); sys.exit(2)
    print(json.dumps(plan["catalogue"])); sys.exit(0)
if args == ["app-server"]:              # one JSON message per line, as Codex's own apps speak to it
    if plan["app_server"] is None:
        print("error: unrecognized subcommand 'app-server'", file=sys.stderr); sys.exit(2)
    print(json.dumps({"method": "configWarning", "params": {"summary": "a notice, not a reply"}}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("method") == "initialize":
            print(json.dumps({"id": message["id"], "result": {"userAgent": "fake"}}), flush=True)
        elif message.get("method") == "model/list":
            pages = plan["app_server"]
            at = int(message["params"].get("cursor") or 0)
            print(json.dumps({"id": message["id"], "result": {"data": pages[at], "nextCursor": str(at + 1) if at + 1 < len(pages) else None}}), flush=True)
    sys.exit(0)
assert args[0] == "exec" and args[-1] == "-", args
value = lambda flag: args[args.index(flag) + 1]
config = dict(a.split("=", 1) for a in args if "=" in a and args[args.index(a) - 1] == "-c")
calls = os.path.join(here, "calls.json")
seen = json.load(open(calls)) if os.path.exists(calls) else []
seen.append({"args": args, "stdin": sys.stdin.read(), "cwd": os.getcwd(), "cwd_holds": sorted(os.listdir(".")),
             "instructions": open(json.loads(config["model_instructions_file"])).read(),
             "schema": json.load(open(value("--output-schema")))})
json.dump(seen, open(calls, "w"))
step = plan["steps"][min(len(seen), len(plan["steps"])) - 1]
say = lambda event: print(json.dumps(event), flush=True)
say({"type": "thread.started", "thread_id": "t"}); say({"type": "turn.started"})
for notice in step.get("notices", []):
    say({"type": "error", "message": notice})
print("2026-01-01T00:00:00Z ERROR codex_api: a log line, not an event", file=sys.stderr)
if "fail" in step:
    say({"type": "turn.failed", "error": {"message": step["fail"]}}); sys.exit(1)
if step.get("answer") is not None:
    text = step["answer"] if isinstance(step["answer"], str) else json.dumps(step["answer"])
    say({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}})
    if step.get("write_file", True):
        open(value("--output-last-message"), "w").write(text)
say({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 34}})
'''


def fake_codex(tmp_path: Path, steps: list[dict] | None = None, logged_in: bool = True, catalogue: dict | None = CATALOGUE,
               app_server: list | None = None) -> tuple[str, Path]:
    """The stand-in program and the file it lists its ``exec`` calls in. Call n is answered by step n (the last
    step again when there are more calls): ``answer``, ``fail`` (a failed turn), ``notices`` (error events).
    ``app_server`` holds the pages of models its app server gives; ``None`` is a Codex without that command, and
    ``catalogue=None`` one without ``debug models``."""
    folder = tmp_path / "fake-codex"
    folder.mkdir(exist_ok=True)
    (folder / "plan.json").write_text(json.dumps({"logged_in": logged_in, "catalogue": catalogue, "app_server": app_server,
                                                  "steps": steps or [{"answer": {"ranges": []}}]}), encoding="utf-8")
    script = folder / "codex"
    script.write_text(FAKE.replace("{python}", sys.executable), encoding="utf-8")
    script.chmod(0o755)
    return str(script), folder / "calls.json"


def codex(exe: str, **kwargs) -> tuple[CodexBackend, list[float]]:
    waits: list[float] = []
    return CodexBackend(executable=exe, sleep=waits.append, **kwargs), waits


def ask(backend, **kwargs) -> Completion:
    return backend.complete(role="draft", system="SYSTEM TEXT", prompt="THE TASK", schema=prompts.DRAFT_SCHEMA, **kwargs)


# --------------------------------------------------------------------------
# Which tool a model setting names


@pytest.mark.parametrize("setting, tool, model", [
    (None, "claude", None), ("", "claude", None), ("opus", "claude", "opus"), ("default", "claude", "default"),
    ("claude", "claude", None), ("claude:opus", "claude", "opus"),
    ("codex", "codex", None), ("codex:", "codex", None), ("codex:gpt-6.1-sol", "codex", "gpt-6.1-sol"),
    (" Codex:gpt-6-luna ", "codex", "gpt-6-luna"), ("gpt-6.1-sol", "codex", "gpt-6.1-sol"),
    ("anthropic.claude-x-v2:0", "claude", "anthropic.claude-x-v2:0"),  # a colon that is part of a model's name
])
def test_a_model_setting_names_its_tool(setting, tool, model):
    assert split_model(setting) == (tool, model)


def test_each_request_goes_to_the_tool_its_model_names():
    claude, other = ScriptedBackend(lambda *a: {"from": "claude"}), ScriptedBackend(lambda *a: {"from": "codex"})
    both = Backends(claude=claude, codex=other)
    assert ask(both, model="codex:gpt-6.1-sol").data == {"from": "codex"} and other.calls[0]["model"] == "gpt-6.1-sol"
    assert ask(both, model="opus").data == {"from": "claude"} and claude.calls[0]["model"] == "opus"
    assert ask(both, model=None).data == {"from": "claude"} and ask(both, model="codex").data == {"from": "codex"}
    assert other.calls[1]["model"] is None  # "codex" alone: Codex's own default


def test_only_the_tools_a_run_names_are_looked_for(tmp_path):
    exe, _ = fake_codex(tmp_path)
    both = Backends(claude=CodexBackend(executable=str(tmp_path / "no-such-claude")), codex=CodexBackend(executable=exe))
    assert both.check_available(["codex:gpt-6.1-sol", "codex"]) == {"codex": "codex-cli 9.9.9"}
    with pytest.raises(BackendUnavailable):
        both.check_available(["codex", "opus"])


# --------------------------------------------------------------------------
# One call


def test_codex_is_asked_with_our_instructions_in_a_read_only_empty_folder(tmp_path):
    exe, calls = fake_codex(tmp_path, [{"answer": {"ranges": [1]}}])
    backend, _ = codex(exe, effort="high")
    done = ask(backend, model="gpt-6.1-sol")
    assert done.data == {"ranges": [1]} and done.model == "codex:gpt-6.1-sol"
    assert (done.input_tokens, done.output_tokens, done.priced, done.cost_usd) == (1200, 34, False, 0.0)
    call = json.loads(calls.read_text())[0]
    args = call["args"]
    assert call["stdin"] == "THE TASK" and call["instructions"] == "SYSTEM TEXT" and call["schema"] == prompts.DRAFT_SCHEMA
    assert call["cwd_holds"] == [] and "ebook-processor-" in call["cwd"]
    assert args[args.index("--sandbox") + 1] == "read-only" and args[args.index("--model") + 1] == "gpt-6.1-sol"
    for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
        assert flag in args
    config = [args[n + 1] for n, a in enumerate(args) if a == "-c"]
    assert 'model_reasoning_effort="high"' in config and "project_doc_max_bytes=0" in config
    assert "include_environment_context=false" in config  # nothing to open, so nothing is said about folders
    assert not Path(call["cwd"]).exists()  # the temporary folder is gone afterwards

    # figures mode: the run's work folder is the working folder, so that the pictures in it can be opened
    pictures = tmp_path / "work"
    pictures.mkdir()
    (pictures / "fig.png").write_bytes(b"x")
    ask(codex(exe)[0], read_dir=pictures)
    call = json.loads(calls.read_text())[1]
    assert Path(call["cwd"]) == pictures and call["cwd_holds"] == ["fig.png"] and "--model" not in call["args"]
    config = [call["args"][n + 1] for n, a in enumerate(call["args"]) if a == "-c"]
    assert "include_environment_context=false" not in config and not any(c.startswith("model_reasoning_effort") for c in config)
    assert call["args"][call["args"].index("--sandbox") + 1] == "read-only"


def test_the_answer_is_read_from_the_events_when_no_file_was_written(tmp_path):
    exe, _ = fake_codex(tmp_path, [{"answer": 'Here you are:\n```json\n{"ranges": []}\n```', "write_file": False,
                                    "notices": ["Reconnecting... 2/5 (stream disconnected before completion)"]}])
    done = ask(codex(exe)[0])  # a reconnect that then succeeded is not a failure
    assert done.data == {"ranges": []} and done.model == "codex (its default model)"


def test_a_usage_limit_stops_the_run_and_a_missing_login_says_what_to_do(tmp_path):
    exe, calls = fake_codex(tmp_path, [{"fail": "You've hit your usage limit. Upgrade to Pro or try again at 4:15 PM."}])
    backend, waits = codex(exe)
    with pytest.raises(UsageLimitError, match="usage limit"):
        ask(backend)
    assert waits == [] and len(json.loads(calls.read_text())) == 1  # not tried again

    exe, _ = fake_codex(tmp_path, [{"fail": "401 Unauthorized: Not logged in"}])
    with pytest.raises(BackendUnavailable, match="codex login"):
        ask(codex(exe)[0])
    exe, _ = fake_codex(tmp_path, [{"fail": "The model `gpt-nope` does not exist"}])
    with pytest.raises(BackendError, match="Codex reported an error: The model `gpt-nope`") as failure:
        ask(codex(exe)[0], model="gpt-nope")
    assert not isinstance(failure.value, (UsageLimitError, BackendUnavailable))


def test_a_failure_that_looks_temporary_is_tried_again(tmp_path):
    exe, calls = fake_codex(tmp_path, [{"fail": "stream disconnected before completion: connection reset"},
                                       {"answer": "no JSON here"}, {"answer": {"ranges": []}}])
    backend, waits = codex(exe)
    assert ask(backend).data == {"ranges": []}
    assert waits == [15.0, 60.0] and len(json.loads(calls.read_text())) == 3

    exe, calls = fake_codex(tmp_path, [{"fail": "server overloaded (503)"}])
    calls.unlink()
    backend, waits = codex(exe, retries=1)
    with pytest.raises(BackendError, match="Codex did not answer the draft request after 2 attempts"):
        ask(backend)
    assert waits == [15.0]


# --------------------------------------------------------------------------
# What is installed, and the models


def test_codex_must_be_installed_and_signed_in(tmp_path):
    exe, _ = fake_codex(tmp_path)
    assert CodexBackend(executable=exe).check_available() == "codex-cli 9.9.9"
    exe, _ = fake_codex(tmp_path, logged_in=False)
    with pytest.raises(BackendUnavailable, match="not signed in"):
        CodexBackend(executable=exe).check_available()
    with pytest.raises(BackendUnavailable, match="npm install -g @openai/codex"):
        CodexBackend(executable=str(tmp_path / "missing")).check_available()


def test_the_models_are_read_from_codexs_catalogue(tmp_path):
    exe, _ = fake_codex(tmp_path)
    listed = CodexBackend(executable=exe).list_models()
    assert [m["value"] for m in listed] == ["gpt-6.1-sol", "gpt-old"]  # by Codex's own order; hidden ones left out
    assert listed[0] == {"value": "gpt-6.1-sol", "name": "GPT-6.1-Sol", "description": "Workhorse.", "resolved": "gpt-6.1-sol",
                         "efforts": ["low", "high", "ultra"]}
    assert CodexBackend(executable=str(tmp_path / "missing")).list_models() == []

    class Claude:
        def list_models(self):
            return [{"value": "default", "name": "Default", "description": "X 1", "resolved": "claude-x-1", "efforts": ["low"]}]

    both = available_models(Claude(), CodexBackend(executable=exe))
    assert [(m["provider"], m["value"]) for m in both] == [("claude", "default"), ("codex", "codex"),
                                                           ("codex", "codex:gpt-6.1-sol"), ("codex", "codex:gpt-old")]
    assert both[1]["efforts"] == ["low", "high"]  # Codex's default, not named by the catalogue: the levels every model takes
    assert both[1]["name"] == "Codex's default"
    assert available_models(Claude(), None) == [{**Claude().list_models()[0], "provider": "claude"}]


def test_the_models_come_from_codexs_app_server_when_it_has_one(tmp_path):
    pages = [[{"id": "gpt-6.1-sol", "model": "gpt-6.1-sol", "displayName": "GPT-6.1-Sol", "description": "Workhorse.", "isDefault": True,
               "hidden": False, "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "ultra"}]},
              {"id": "secret", "model": "secret", "hidden": True}],
             [{"id": "gpt-6-luna", "model": "gpt-6-luna", "displayName": "GPT-6-Luna", "supportedReasoningEfforts": []}]]
    exe, _ = fake_codex(tmp_path, app_server=pages, catalogue=None)  # this Codex has no "debug models"
    backend = CodexBackend(executable=exe)
    listed = backend.list_models()
    assert listed == [{"value": "gpt-6.1-sol", "name": "GPT-6.1-Sol", "description": "Workhorse.", "resolved": "gpt-6.1-sol",
                       "efforts": ["low", "ultra"], "default": True},
                      {"value": "gpt-6-luna", "name": "GPT-6-Luna", "description": "", "resolved": "gpt-6-luna", "efforts": []}]
    assert backend.models_note == ""
    default = available_models(None, backend)[0]  # Codex's default is named, with that model's own effort levels
    assert (default["value"], default["name"], default["efforts"]) == ("codex", "Codex's default (GPT-6.1-Sol)", ["low", "ultra"])


def test_a_codex_that_lists_nothing_says_why_and_still_offers_its_default(tmp_path, monkeypatch, capsys):
    exe, _ = fake_codex(tmp_path, catalogue=None)  # neither source answers
    backend = CodexBackend(executable=exe)
    assert backend.list_models() == []
    assert "app server: it stopped before replying to initialize" in backend.models_note
    assert "catalogue: error: unrecognized subcommand 'models'" in backend.models_note
    assert [m["value"] for m in available_models(None, backend)] == ["codex"]
    monkeypatch.setenv("PATH", str(Path(exe).parent))
    assert cli.main(["models"]) == 0
    said = capsys.readouterr()
    assert "its models could not be listed" in said.err and "--model codex:NAME" in said.err and "  codex " in said.out


def test_the_command_line_lists_both_tools_models(tmp_path, monkeypatch, capsys):
    exe, _ = fake_codex(tmp_path)
    folder = Path(exe).parent
    monkeypatch.setenv("PATH", str(folder))  # codex is found there; claude is not
    assert cli.main(["models"]) == 0
    out = capsys.readouterr()
    assert "Codex:\n  codex " in out.out and "codex:gpt-6.1-sol  GPT-6.1-Sol: Workhorse.  [effort: low, high, ultra]" in out.out
    assert "Claude Code:" not in out.out and "(Claude Code: Claude Code was not found" in out.err


# --------------------------------------------------------------------------
# A run with both


def test_a_book_is_drafted_by_one_tool_and_checked_by_the_other(tmp_path):
    script = Script()
    claude, other = ScriptedBackend(script.reply), ScriptedBackend(script.reply)
    settings = from_options(density="short", model="codex:gpt-6.1-sol", checker_model="opus")
    report = run_book(FIXTURE, tmp_path / "out", settings, backend=Backends(claude=claude, codex=other))
    assert report["complete"] and report["counts"]["questions"] == 8
    assert {c["role"] for c in other.calls} == {"draft"} and {c["model"] for c in other.calls} == {"gpt-6.1-sol"}
    assert {c["role"] for c in claude.calls} == {"check", "exclusions"} and {c["model"] for c in claude.calls} == {"opus"}


def test_the_report_gives_a_price_only_for_calls_that_have_one():
    assert _price({"calls": 4, "cost_usd": 1.5}) == "list-price equivalent $1.50; "
    assert _price({"calls": 4, "cost_usd": 1.5, "unpriced_calls": 3}) \
        == "list-price equivalent $1.50 for the Claude Code calls, as Codex reports no price; "
    assert _price({"calls": 4, "cost_usd": 0.0, "unpriced_calls": 4}) == ""


def test_a_run_made_with_codex_counts_its_calls_as_unpriced(tmp_path):
    script = Script()

    class Unpriced(ScriptedBackend):
        def complete(self, **kwargs):
            done = super().complete(**kwargs)
            done.priced = False
            return done

    report = run_book(FIXTURE, tmp_path / "out", from_options(density="short", model="codex"),
                      backend=Backends(claude=ScriptedBackend(script.reply), codex=Unpriced(script.reply)))
    assert report["usage"]["unpriced_calls"] == report["usage"]["calls"] > 0
    text = (tmp_path / "out" / "fixture.report.md").read_text(encoding="utf-8")
    assert "Calls to the models" in text and "list-price" not in text


def test_the_web_app_tells_its_page_about_codex(tmp_path):
    models = [{"value": "codex:gpt-6.1-sol", "name": "GPT-6.1-Sol", "description": "", "resolved": "gpt-6.1-sol", "efforts": [], "provider": "codex"}]
    server = make_server(tmp_path / "data", "127.0.0.1", 0, claude={"ok": False, "error": "no claude"}, models=models,
                         codex={"ok": True, "installed": True, "version": "codex-cli 9.9.9"})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/info", timeout=10) as response:
            info = json.loads(response.read())
        assert info["codex"] == {"ok": True, "installed": True, "version": "codex-cli 9.9.9"} and info["models"] == models
    finally:
        server.shutdown()
        server.server_close()
