"""How the tool reaches Claude.

``ClaudeCodeBackend`` runs Claude Code without a terminal (``claude -p``), so a run uses the Claude login of
the machine it runs on and needs no API key. Each call is a fresh session with no memory of any other call:
the checker therefore never sees what the drafter wrote beyond the text it is handed.

Anything with a ``complete`` method of the same shape can stand in (the tests use ``ScriptedBackend``), which is
also where a backend that calls the Anthropic API directly would go.
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
from typing import Any, Callable, Protocol


class BackendError(RuntimeError):
    """A call to Claude failed for good (after retries)."""


class UsageLimitError(BackendError):
    """The Claude plan's usage limit is reached; the run can be resumed later."""


class BackendUnavailable(BackendError):
    """Claude Code is not installed, or not signed in."""


@dataclass
class Completion:
    data: dict[str, Any]
    model: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


class Backend(Protocol):
    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        """Send one self-contained request and return the reply as an object matching ``schema``."""


_LIMIT_RE = re.compile(r"usage limit|hit your limit|limit reached|out of extra usage|quota", re.I)
_TRANSIENT_RE = re.compile(r"overloaded|rate.?limit|timed? ?out|timeout|connection|temporarily|5\d\d|ECONNRESET|socket", re.I)
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
                "(https://code.claude.com/docs/en/setup), run 'claude' once and sign in, then try again.")
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendUnavailable(f"Claude Code could not be started: {exc}") from exc
        return out.stdout.strip() or "unknown version"

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
        last = "no attempt was made"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(self.backoff[min(attempt - 1, len(self.backoff) - 1)])
            try:
                return self._once(cmd, prompt, read_dir)
            except UsageLimitError:
                raise
            except BackendUnavailable:
                raise
            except BackendError as exc:
                last = str(exc)
                if not getattr(exc, "transient", False):
                    raise
        raise BackendError(f"Claude did not answer the {role} request after {self.retries + 1} attempts: {last}")

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


@dataclass
class ScriptedBackend:
    """A stand-in for tests and dry runs: ``reply(role, system, prompt, schema)`` returns the object to answer with."""

    reply: Callable[[str, str, str, dict[str, Any]], dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, *, role: str, system: str, prompt: str, schema: dict[str, Any], model: str | None = None,
                 read_dir: Path | None = None) -> Completion:
        self.calls.append({"role": role, "system": system, "prompt": prompt, "model": model, "read_dir": read_dir})
        return Completion(data=self.reply(role, system, prompt, schema), model=model or "scripted")
