"""``ebook-processor update``: stop the running app, take the latest ``main``, install it, start the app again.

The tool is installed from a git checkout (``pip install -e .``), so updating is a fast-forward of that checkout
followed by ``pip install -e`` for anything the new version needs.

Finding the running app: ``ebook-processor serve`` writes a small record while it runs, one file per port, in
``~/.ebook-processor/servers/`` (``$EBOOK_PROCESSOR_HOME`` overrides the folder). The record holds the process id,
the address, the data folder and a one-time token. The app is stopped by asking it, with that token, to stop
itself; only if it does not answer is the process signalled. An app started by a version from before this command
existed leaves no record and has to be stopped by hand.

What it will not do: touch a checkout that is not on ``main`` or that has uncommitted changes to tracked files, or
stop the app while it is processing a book (unless told to with ``--force``; the run can be continued afterwards).
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

BRANCH = "main"
DEFAULT_PORT = 8765


class UpdateError(Exception):
    """The update cannot go on; the message says why and what to do."""


# --------------------------------------------------------------------------
# The record a running app leaves


def home() -> Path:
    return Path(os.environ.get("EBOOK_PROCESSOR_HOME") or Path.home() / ".ebook-processor")


def record_path(port: int) -> Path:
    return home() / "servers" / f"{port}.json"


def write_record(host: str, port: int, data: Path) -> dict[str, Any]:
    record = {"pid": os.getpid(), "host": host, "port": port, "data": str(data), "token": secrets.token_hex(16),
              "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = record_path(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)  # the token lets its holder stop the app
    except OSError:
        pass
    return record


def remove_record(port: int, pid: int | None = None) -> None:
    """Remove the record of ``port``; with ``pid``, only if it is that process's (a new app may own it by now)."""
    path = record_path(port)
    try:
        if pid is None or json.loads(path.read_text(encoding="utf-8")).get("pid") == pid:
            path.unlink()
    except (OSError, ValueError):
        pass


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((_reach(host), port), timeout=1):
            return True
    except OSError:
        return False


def _reach(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def servers() -> list[dict[str, Any]]:
    """The apps that are running now, by their records. Records of apps that are gone are removed."""
    out = []
    for path in sorted((home() / "servers").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            ok = isinstance(record.get("pid"), int) and isinstance(record.get("port"), int) and alive(record["pid"]) \
                and listening(str(record.get("host", "")), record["port"])
        except (OSError, ValueError):
            ok, record = False, {}
        if ok:
            out.append(record)
        else:
            path.unlink(missing_ok=True)
    return sorted(out, key=lambda r: r.get("started", ""))


def _call(record: dict[str, Any], method: str, path: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(f"http://{_reach(record['host'])}:{record['port']}{path}", method=method,
                                     data=b"" if method == "POST" else None,
                                     headers={"X-Ebook-Processor": "1", **(headers or {})})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def busy(record: dict[str, Any]) -> list[str]:
    """Titles of the books the app is processing or about to."""
    try:
        jobs = _call(record, "GET", "/api/jobs").get("jobs", [])
    except (OSError, ValueError):
        return []
    return [j.get("title") or j.get("file") or "a book" for j in jobs if j.get("status") in ("queued", "running")]


def stop(record: dict[str, Any], timeout: float = 20.0) -> bool:
    """Stop a running app. True when its port is free again."""
    try:
        _call(record, "POST", "/api/shutdown", {"X-Shutdown-Token": str(record.get("token", ""))})
    except (OSError, ValueError):
        pass
    deadline = time.monotonic() + timeout
    signalled = False
    while time.monotonic() < deadline:
        if not listening(record["host"], record["port"]):
            remove_record(record["port"], record["pid"])
            return True
        if not signalled and time.monotonic() > deadline - timeout / 2 and record["pid"] != os.getpid():
            try:  # it did not stop when asked
                os.kill(record["pid"], signal.SIGTERM)
            except OSError:
                pass
            signalled = True
        time.sleep(0.2)
    return False


def unrecorded(port: int = DEFAULT_PORT) -> bool:
    """Whether an app with no record answers on the usual port: one started before this command existed."""
    if record_path(port).is_file() or not listening("127.0.0.1", port):
        return False
    try:
        info = _call({"host": "127.0.0.1", "port": port}, "GET", "/api/info")
    except (OSError, ValueError):
        return False
    return isinstance(info, dict) and "version" in info and "densities" in info


# --------------------------------------------------------------------------
# The checkout


def repo_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        raise UpdateError(
            f"ebook-processor is not running from a git checkout ({root} has no .git folder), so it cannot update "
            "itself. Install it from a clone: git clone https://github.com/c-tphr/ebook-processor.git, then "
            "python3 -m pip install -e ebook-processor")
    return root


def git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300)
    except FileNotFoundError as exc:
        raise UpdateError("git was not found; install git to update") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"git {' '.join(args)} did not finish within five minutes") from exc
    if proc.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed:\n{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def pull(repo: Path, branch: str = BRANCH) -> tuple[str, str]:
    """Fast-forward the checkout to the latest ``branch``. Returns the commit before and after."""
    current = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise UpdateError(f"the checkout at {repo} is on the branch '{current}', not '{branch}'. Switch to it first "
                          f"(git -C {repo} checkout {branch}), or update by hand.")
    changed = git(repo, "status", "--porcelain", "--untracked-files=no")
    if changed:
        raise UpdateError(f"the checkout at {repo} has changes that are not committed:\n{changed}\n"
                          "Commit them or put them aside (git stash) first; nothing was changed.")
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "pull", "--ff-only", "origin", branch)
    return before, git(repo, "rev-parse", "HEAD")


def install(repo: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-e", str(repo)], capture_output=True,
                          text=True)
    if proc.returncode != 0:
        raise UpdateError("the new version was fetched, but installing it failed:\n"
                          + (proc.stderr or proc.stdout).strip()[-1500:]
                          + f"\nInstall it by hand: {sys.executable} -m pip install -e {repo}")


def serve_command(record: dict[str, Any] | None) -> list[str]:
    """The command that starts the app again, as it was running (or with the defaults when it was not)."""
    cmd = [sys.executable, "-m", "ebook_processor", "serve"]
    if record:  # the browser tab is already open: do not open another
        cmd += ["--host", str(record["host"]), "--port", str(record["port"]), "--data", str(record["data"]), "--no-open"]
    return cmd


def update(serve: bool = True, force: bool = False, say: Callable[[str], None] = print,
           installer: Callable[[Path], None] = install,
           launch: Callable[[list[str]], Any] = lambda cmd: os.execv(cmd[0], cmd)) -> int:
    """Run the update. Returns the exit status (0 done). With ``serve``, it ends by becoming the app."""
    repo = repo_root()
    running = servers()
    if not running and unrecorded() and not force:
        raise UpdateError(
            f"An app started before this command existed is running on port {DEFAULT_PORT}, and it cannot be "
            "stopped from here. Stop it with Ctrl-C in its terminal, then give this command again. (--force "
            "updates without stopping it; it must then be restarted by hand.)")
    for record in running:
        titles = busy(record)
        if titles and not force:
            raise UpdateError(
                f"The app on port {record['port']} is processing {', '.join(titles)}. Wait for it to finish, or "
                "give --force to stop it now; the run can be continued afterwards with its Continue button.")
    for record in running:
        say(f"Stopping the app on port {record['port']} ...")
        if not stop(record):
            raise UpdateError(f"the app on port {record['port']} (process {record['pid']}) did not stop; stop it "
                              "by hand and give this command again. Nothing was updated.")
    say(f"Fetching the latest {BRANCH} ...")
    before, after = pull(repo)
    if before == after:
        say(f"Already up to date ({after[:7]}).")
    else:
        say(f"Updated {before[:7]} to {after[:7]}:")
        for line in git(repo, "log", "--format=  %h %s", f"{before}..{after}").splitlines()[:20]:
            say(line)
        say("Installing ...")
        installer(repo)
    if not serve:
        say("Done. Start the app with: ebook-processor serve")
        return 0
    cmd = serve_command(running[-1] if running else None)
    if len(running) > 1:
        say(f"Several apps were running; starting the one on port {running[-1]['port']} again. Start the others "
            "by hand.")
    say("Starting the app ...")
    sys.stdout.flush()
    launch(cmd)
    return 0
