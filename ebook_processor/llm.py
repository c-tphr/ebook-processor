"""How the tool reaches a model.

``ClaudeCodeBackend`` runs Claude Code without a terminal (``claude -p``) and ``CodexBackend`` runs OpenAI's Codex
the same way (``codex exec``), so a run uses the Claude or ChatGPT login of the machine it runs on and needs no
API key. Each call is a fresh session with no memory of any other call: the checker therefore never sees what
the drafter wrote beyond the text it is handed.

``Backends`` holds both and sends each request to the tool its model setting names (see ``split_model``), so one
run can draft with one and check with the other.

Anything with a ``complete`` method of the same shape can stand in (the tests use ``ScriptedBackend``), which is
also where a backend that calls an API directly would go.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


class BackendError(RuntimeError):
    """A call to a model failed for good (after retries)."""


class UsageLimitError(BackendError):
    """The plan's usage limit is reached; the run can be resumed later."""


class BackendUnavailable(BackendError):
    """The tool (Claude Code or Codex) is not installed, or not signed in."""


@dataclass
class Completion:
    data: dict[str, Any]
    model: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    priced: bool = True  # False when the tool reports no price for the call (Codex), so ``cost_usd`` says nothing


class Backend(Protocol):
    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        """Send one self-contained request and return the reply as an object matching ``schema``."""


PROVIDERS = {"claude": "Claude Code", "codex": "Codex"}


def split_model(spec: str | None) -> tuple[str, str | None]:
    """Which tool a model setting names, and the model to ask that tool for (``None``: the tool's own default).

    ``codex:gpt-6.1-sol`` and ``claude:opus`` say it outright; ``codex`` and ``claude`` alone mean that tool's
    default model. Without a prefix, a name that starts with ``gpt-`` goes to Codex and anything else to Claude
    Code, which is also where no setting at all goes.
    """
    spec = (spec or "").strip()
    if not spec:
        return "claude", None
    head, _, rest = spec.partition(":")
    if head.lower() in PROVIDERS:
        return head.lower(), rest.strip() or None
    return ("codex" if spec.lower().startswith("gpt-") else "claude"), spec


def _with_retries(tool: str, role: str, retries: int, backoff: tuple[float, ...], sleep: Callable[[float], None],
                  once: Callable[[], "Completion"]) -> "Completion":
    """Call ``once`` again after a failure that looks temporary. Limits and a missing login are not retried."""
    last = "no attempt was made"
    for attempt in range(retries + 1):
        if attempt:
            sleep(backoff[min(attempt - 1, len(backoff) - 1)])
        try:
            return once()
        except (UsageLimitError, BackendUnavailable):
            raise
        except BackendError as exc:
            last = str(exc)
            if not getattr(exc, "transient", False):
                raise
    raise BackendError(f"{tool} did not answer the {role} request after {retries + 1} attempts: {last}")


_LIMIT_RE = re.compile(r"usage limit|hit your limit|limit reached|out of extra usage|quota", re.I)
_TRANSIENT_RE = re.compile(r"structured.?output|overloaded|rate.?limit|timed? ?out|timeout|connection|temporarily|5\d\d|ECONNRESET|socket", re.I)
_LOGIN_RE = re.compile(r"/login|not logged in|invalid api key|authentication|unauthorized|401", re.I)


def extract_json(text: str) -> dict[str, Any] | None:
    """The JSON object in a reply that was meant to be only JSON (fenced or with stray text around it)."""
    text = text.strip()
    for candidate in (text, *re.findall(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.S)[::-1]):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            value = json.loads(text[start : end + 1])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


@dataclass
class ClaudeCodeBackend:
    """Claude Code in print mode, one process per call."""

    executable: str = "claude"
    model: str | None = None
    effort: str | None = None
    timeout: float = 1800.0  # seconds for one call
    retries: int = 3  # further attempts after a failure that looks temporary
    backoff: tuple[float, ...] = (15.0, 60.0, 180.0)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def check_available(self) -> str:
        """Claude Code's version, or ``BackendUnavailable`` with what to do."""
        path = shutil.which(self.executable)
        if path is None:
            raise BackendUnavailable(
                f"Claude Code was not found ('{self.executable}' is not on PATH). Install it "
"(https://code.claude.com/docs/en/setup), run 'claude' once and sign in, then try again. To use Codex "
                "instead, choose a Codex model (--model codex).")
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendUnavailable(f"Claude Code could not be started: {exc}") from exc
        return out.stdout.strip() or "unknown version"

    def list_models(self, timeout: float = 45.0) -> list[dict[str, Any]]:
        """The models this login may use, as Claude Code reports them. No model is called.

        Each entry has ``value`` (what ``--model`` takes), ``name``, ``description``, ``resolved`` (the full model
        id) and ``efforts`` (the reasoning-effort levels it accepts, empty when it takes none). The first entry is
        Claude Code's default. Returns an empty list when Claude Code cannot say (too old, not signed in).
        """
        request = json.dumps({"type": "control_request", "request_id": "models", "request": {"subtype": "initialize"}})
        cmd = [self.executable, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
               "--tools", ""]
        try:
            with tempfile.TemporaryDirectory(prefix="ebook-processor-") as tmp:
                proc = subprocess.run(cmd, input=request + "\n", capture_output=True, text=True, encoding="utf-8",
                                      timeout=timeout, cwd=tmp)
        except (OSError, subprocess.TimeoutExpired):
            return []
        for line in proc.stdout.splitlines():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict) or message.get("type") != "control_response":
                continue
            reply = (message.get("response") or {}).get("response") or {}
            out = []
            for m in reply.get("models") or []:
                if isinstance(m, dict) and isinstance(m.get("value"), str) and m["value"]:
                    out.append({"value": m["value"], "name": str(m.get("displayName") or m["value"]),
                                "description": str(m.get("description") or ""),
                                "resolved": str(m.get("resolvedModel") or ""),
                                "efforts": [e for e in m.get("supportedEffortLevels") or [] if isinstance(e, str)]
                                if m.get("supportsEffort") else []})
            return out
        return []

    def command(self, system: str, schema: dict[str, Any], model: str | None, read_dir: Path | None) -> list[str]:
        cmd = [
            self.executable, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema, separators=(",", ":")),
            "--system-prompt", system,
            "--no-session-persistence",  # nothing of the book is kept in Claude Code's session history
            "--strict-mcp-config",  # no MCP servers
            "--disable-slash-commands",
            "--setting-sources", "",  # no user or project settings, hooks or CLAUDE.md rules
            "--permission-prompts", "none",  # nobody is there to answer a prompt: anything unlisted is denied
        ]
        if read_dir is None:
            cmd += ["--tools", ""]
        else:  # figures mode: Claude may open the image files of the work folder, and nothing else
            cmd += ["--tools", "Read", "--allowedTools", "Read", "--add-dir", str(read_dir)]
        if model or self.model:
            cmd += ["--model", model or self.model or ""]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        cmd = self.command(system, schema, model, read_dir)
        return _with_retries("Claude", role, self.retries, self.backoff, self.sleep,
                             lambda: self._once(cmd, prompt, read_dir))

    def _once(self, cmd: list[str], prompt: str, read_dir: Path | None) -> Completion:
        started = time.monotonic()
        # An empty folder as the working directory, so that no project file of the caller reaches the session.
        with tempfile.TemporaryDirectory(prefix="ebook-processor-") as tmp:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                      timeout=self.timeout, cwd=str(read_dir) if read_dir else tmp)
            except FileNotFoundError as exc:
                raise BackendUnavailable(f"Claude Code was not found ('{self.executable}' is not on PATH)") from exc
            except subprocess.TimeoutExpired as exc:
                raise _transient(f"no answer within {int(self.timeout)} seconds") from exc
        seconds = time.monotonic() - started
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            result = None
        if not isinstance(result, dict):
            text = (proc.stderr or proc.stdout or "").strip()[-600:]
            raise self._classify(text or f"Claude Code exited with status {proc.returncode} and no output", None)
        if result.get("is_error") or proc.returncode != 0:
            raise self._classify(str(result.get("result") or result.get("subtype") or proc.stderr.strip()[-600:]),
                                 result.get("api_error_status"))
        data = result.get("structured_output")
        if not isinstance(data, dict):
            data = extract_json(str(result.get("result") or ""))
        if data is None:
            raise _transient("the reply held no JSON object")
        usage = result.get("usage") or {}
        models = result.get("modelUsage") or {}
        return Completion(
            data=data,
            model=max(models, key=lambda m: models[m].get("outputTokens", 0)) if models else None,
            cost_usd=float(result.get("total_cost_usd") or 0.0),
            input_tokens=int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            seconds=seconds,
        )

    @staticmethod
    def _classify(message: str, status: Any) -> BackendError:
        if _LOGIN_RE.search(message) or status == 401:
            return BackendUnavailable(f"Claude Code is not signed in ({message}). Run 'claude' in a terminal, sign "
                                      "in with /login, then try again.")
        if _LIMIT_RE.search(message):
            return UsageLimitError(f"Claude's usage limit is reached ({message}).")
        if _TRANSIENT_RE.search(message) or status in (429, 500, 502, 503, 529):
            return _transient(message)
        return BackendError(f"Claude Code reported an error: {message}")


def _transient(message: str) -> BackendError:
    exc = BackendError(message)
    exc.transient = True  # type: ignore[attr-defined]
    return exc


_CODEX_LIMIT_RE = re.compile(r"usage limit|usage_limit|out of credits|purchase more credits|quota", re.I)
_CODEX_LOGIN_RE = re.compile(r"not logged in|codex login|log in again|sign in again|unauthorized|\b401\b|token (is |has )?expired", re.I)
_CODEX_TRANSIENT_RE = re.compile(r"stream disconnected|reconnect|overloaded|high demand|rate.?limit|timed? ?out|timeout|connection|"
                                 r"network|temporarily|\b5\d\d\b|ECONNRESET|socket", re.I)


@dataclass
class CodexBackend:
    """OpenAI's Codex in its non-interactive mode (``codex exec``), one process per call.

    Codex is a coding agent, and it cannot be run with no tools at all. A call is kept as close to a plain
    question as its options allow: the tool's instructions take the place of Codex's own, the sandbox is
    read-only, the working folder is an empty temporary one, the user's Codex settings, rules and project
    AGENTS.md files are left out, and the session is not kept. One thing cannot be switched off: a personal
    ``AGENTS.md`` in the Codex home folder is still shown to the model.
    """

    executable: str = "codex"
    effort: str | None = None
    timeout: float = 1800.0  # seconds for one call
    retries: int = 3  # further attempts after a failure that looks temporary
    backoff: tuple[float, ...] = (15.0, 60.0, 180.0)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def check_available(self) -> str:
        """Codex's version, or ``BackendUnavailable`` with what to do."""
        path = shutil.which(self.executable)
        if path is None:
            raise BackendUnavailable(
                f"Codex was not found ('{self.executable}' is not on PATH). Install it "
                "(npm install -g @openai/codex), run 'codex login' and sign in with your ChatGPT account, then "
                "try again.")
        try:
            version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60)
            login = subprocess.run([path, "login", "status"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendUnavailable(f"Codex could not be started: {exc}") from exc
        said = f"{login.stdout}\n{login.stderr}"
        if login.returncode != 0 or "not logged in" in said.lower():
            raise BackendUnavailable("Codex is not signed in. Run 'codex login' in a terminal, sign in with your "
                                     "ChatGPT account, then try again.")
        return version.stdout.strip() or "unknown version"

    def list_models(self, timeout: float = 30.0) -> list[dict[str, Any]]:
        """The models Codex offers, from its own catalogue (``codex debug models``). No model is called.

        Entries have the shape of ``ClaudeCodeBackend.list_models``; ``value`` is the bare model name. The
        catalogue is first asked for as Codex refreshes it, then as shipped with the program. Returns an empty
        list when Codex cannot say.
        """
        for extra in ([], ["--bundled"]):
            try:
                proc = subprocess.run([self.executable, "debug", "models", *extra], capture_output=True, text=True,
                                      encoding="utf-8", timeout=timeout)
                catalogue = json.loads(proc.stdout).get("models")
            except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
                continue
            found = [m for m in catalogue or [] if isinstance(m, dict) and isinstance(m.get("slug"), str)
                     and m["slug"] and m.get("visibility", "list") == "list"]
            if not found:
                continue
            found.sort(key=lambda m: m["priority"] if isinstance(m.get("priority"), (int, float)) else 1e9)
            return [{"value": m["slug"], "name": str(m.get("display_name") or m["slug"]),
                     "description": str(m.get("description") or ""), "resolved": m["slug"],
                     "efforts": [str(level["effort"]) for level in m.get("supported_reasoning_levels") or []
                                 if isinstance(level, dict) and level.get("effort")]} for m in found]
        return []

    def command(self, system_file: Path, schema_file: Path, answer_file: Path, model: str | None,
                read_dir: Path | None) -> list[str]:
        cmd = [
            self.executable, "exec", "--json", "--color", "never",
            "--ephemeral",  # nothing of the book is kept in Codex's session history
            "--skip-git-repo-check",  # the working folder is not a repository
            "--ignore-user-config",  # no user settings, MCP servers or profiles (the login is still used)
            "--ignore-rules",
            "--sandbox", "read-only",  # the agent can change nothing, and nobody is there to approve anything
            "--output-schema", str(schema_file),
            "--output-last-message", str(answer_file),
            "-c", f"model_instructions_file={json.dumps(str(system_file))}",  # in place of Codex's own instructions
            "-c", "project_doc_max_bytes=0",  # no AGENTS.md of the working folder
            "-c", "include_apps_instructions=false",
            "-c", "include_collaboration_mode_instructions=false",
        ]
        if read_dir is None:  # nothing to open: say nothing about folders and permissions either
            cmd += ["-c", "include_environment_context=false", "-c", "include_permissions_instructions=false"]
        if model:
            cmd += ["--model", model]
        if self.effort:
            cmd += ["-c", f"model_reasoning_effort={json.dumps(self.effort)}"]
        return cmd + ["-"]  # the task is read from standard input

    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        return _with_retries("Codex", role, self.retries, self.backoff, self.sleep,
                             lambda: self._once(system, prompt, schema, model, read_dir))

    def _once(self, system: str, prompt: str, schema: dict[str, Any], model: str | None,
              read_dir: Path | None) -> Completion:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ebook-processor-") as tmp:
            folder = Path(tmp)
            (folder / "instructions.md").write_text(system, encoding="utf-8")
            (folder / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
            (folder / "empty").mkdir()  # the working folder, unless pictures are to be looked at (figures mode)
            cmd = self.command(folder / "instructions.md", folder / "schema.json", folder / "answer.txt", model, read_dir)
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                      timeout=self.timeout, cwd=str(read_dir) if read_dir else str(folder / "empty"))
            except FileNotFoundError as exc:
                raise BackendUnavailable(f"Codex was not found ('{self.executable}' is not on PATH)") from exc
            except subprocess.TimeoutExpired as exc:
                raise _transient(f"no answer within {int(self.timeout)} seconds") from exc
            try:
                answer = (folder / "answer.txt").read_text(encoding="utf-8")
            except OSError:
                answer = ""
        seconds = time.monotonic() - started
        usage: dict[str, Any] = {}
        failed = noticed = None
        for line in proc.stdout.splitlines():  # one event per line; see "codex exec --json"
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
            elif event.get("type") == "turn.failed":
                error = event.get("error")
                failed = str(error.get("message") if isinstance(error, dict) else error)
            elif event.get("type") == "error":  # also sent for a reconnect that then succeeds
                noticed = str(event.get("message") or "")
            elif event.get("type") == "item.completed" and item.get("type") == "agent_message" and not answer.strip():
                answer = str(item.get("text") or "")
        if failed or proc.returncode != 0 or not answer.strip():
            message = failed or noticed or (proc.stderr or "").strip()[-600:] \
                or f"Codex exited with status {proc.returncode} and no answer"
            raise self._classify(message)
        data = extract_json(answer)
        if data is None:
            raise _transient("the reply held no JSON object")
        return Completion(data=data, model=f"codex:{model}" if model else "codex (its default model)",
                          input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0),
                          seconds=seconds, priced=False)

    @staticmethod
    def _classify(message: str) -> BackendError:
        if _CODEX_LIMIT_RE.search(message):
            return UsageLimitError(f"Codex's usage limit is reached ({message}).")
        if _CODEX_LOGIN_RE.search(message):
            return BackendUnavailable(f"Codex is not signed in ({message}). Run 'codex login' in a terminal, sign in "
                                      "with your ChatGPT account, then try again.")
        if _CODEX_TRANSIENT_RE.search(message):
            return _transient(message)
        return BackendError(f"Codex reported an error: {message}")


@dataclass
class Backends:
    """Claude Code and Codex side by side: each request goes to the tool its model setting names."""

    claude: Any = field(default_factory=ClaudeCodeBackend)
    codex: Any = field(default_factory=CodexBackend)

    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        provider, name = split_model(model)
        return getattr(self, provider).complete(role=role, system=system, prompt=prompt, schema=schema, model=name,
                                                read_dir=read_dir)

    def check_available(self, models: Iterable[str | None]) -> dict[str, str]:
        """The version of each tool the given model settings need, or ``BackendUnavailable`` for the first that
        is missing or not signed in. A tool no setting names is not looked for."""
        return {provider: getattr(self, provider).check_available()
                for provider in sorted({split_model(m)[0] for m in models})}


def available_models(claude: ClaudeCodeBackend | None, codex: CodexBackend | None) -> list[dict[str, Any]]:
    """The models to choose from, of the tools given. ``value`` is what a model setting takes, ``provider`` the
    tool it belongs to. Codex's come after an entry for Codex's own default."""
    out = [{**m, "provider": "claude"} for m in (claude.list_models() if claude else [])]
    listed = codex.list_models() if codex else []
    if codex:
        shared = [e for e in (listed[0]["efforts"] if listed else []) if all(e in m["efforts"] for m in listed)]
        out.append({"value": "codex", "name": "Codex's default", "description": "the model Codex itself chooses",
                    "resolved": "", "efforts": shared, "provider": "codex"})
    out += [{**m, "value": f"codex:{m['value']}", "provider": "codex"} for m in listed]
    return out


@dataclass
class ScriptedBackend:
    """A stand-in for tests and dry runs: ``reply(role, system, prompt, schema)`` returns the object to answer with."""

    reply: Callable[[str, str, str, dict[str, Any]], dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        self.calls.append({"role": role, "system": system, "prompt": prompt, "model": model, "read_dir": read_dir})
        return Completion(data=self.reply(role, system, prompt, schema), model=model or "scripted")
