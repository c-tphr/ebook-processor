"""The command line.

    ebook-processor Book.epub                 process a book (the same as: ebook-processor process Book.epub)
    ebook-processor rebuild Book-processed --remove c0004-p2
    ebook-processor serve                     the local web app

Exit status: 0 done, 1 a problem with the input or the run, 2 a usage error, 3 the run stopped before it was
finished and can be continued with the same command (usage limit reached, or Ctrl-C).
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Any

from . import __version__
from .llm import BackendError, UsageLimitError
from .runner import Cancelled, PipelineError, default_out_dir, rebuild, run_book
from .settings import CONTENT_MODES, DENSITIES, EFFORTS, KINDS, Settings, SettingsError, from_options

EXIT_OK, EXIT_INPUT, EXIT_USAGE, EXIT_STOPPED = 0, 1, 2, 3
COMMANDS = ("process", "rebuild", "serve", "models", "update")


def add_run_options(p: argparse.ArgumentParser) -> None:
    d = Settings()
    g = p.add_argument_group("where checkpoints go")
    g.add_argument("--density", choices=tuple(DENSITIES), default="medium",
                   help="target section length: " + ", ".join(f"{k} {a}-{b} words" for k, (a, b) in DENSITIES.items())
                        + " (default: medium)")
    g.add_argument("--section-words", metavar="MIN-MAX", help="target section length in words, instead of --density")
    g.add_argument("--max-questions", type=int, metavar="N", help=f"most questions per checkpoint, 1 to 4 "
                                                                   f"(default: {d.max_questions})")
    g = p.add_argument_group("what the model reads and how strictly it is checked")
    g.add_argument("--content", choices=CONTENT_MODES,
                   help="tables, code and formulas: 'prose' hides them, 'text' shows their text, 'figures' also "
                        f"lets the model look at the pictures (default: {d.content})")
    g.add_argument("--kind", choices=KINDS, help=f"the kind of book (default: {d.kind}, decided section by section)")
    g.add_argument("--rounds", type=int, metavar="N",
                   help=f"how often a question may be checked; a question failing the last check is left out; "
                        f"0 switches checking off (default: {d.rounds})")
    g = p.add_argument_group("the models (Claude Code, or Codex with a 'codex:' prefix)")
    g.add_argument("--model", help="model for drafting and revising: a Claude Code model (opus), a Codex model "
                                   "(codex:gpt-6.1-sol), or 'codex' for Codex's own default; 'ebook-processor "
                                   "models' lists the choices (default: Claude Code's own default)")
    g.add_argument("--checker-model", help="model for checking, named the same way; 'claude' is Claude Code's own "
                                           "default (default: the same as --model)")
    g.add_argument("--effort", choices=EFFORTS, help="reasoning effort, for both models (default: each tool's own)")
    g.add_argument("--jobs", type=int, metavar="N", help=f"calls that may run at the same time (default: {d.jobs})")
    g.add_argument("--part-words", type=int, metavar="N",
                   help=f"about how many words one drafting call reads (default: {d.part_words})")
    g = p.add_argument_group("output")
    g.add_argument("--epubcheck", action="store_true", default=None,
                   help="also run EPUBCheck on both books (needs Java; downloads EPUBCheck once)")
    g.add_argument("--preview-questions", action="store_true", default=None,
                   help="in the book with visible checkpoints, begin each section with a page that lists its questions")
    g.add_argument("--title-suffix", help="added to the title of the book with visible checkpoints "
                                          f"(default: {d.title_suffix!r}; '' for none)")


def settings_from(args: argparse.Namespace) -> Settings:
    return from_options(
        density=args.density, section_words=args.section_words, max_questions=args.max_questions,
        content=args.content, kind=args.kind, rounds=args.rounds, model=args.model,
        checker_model=args.checker_model, effort=args.effort, jobs=args.jobs, part_words=args.part_words,
        epubcheck=args.epubcheck, title_suffix=args.title_suffix, preview=args.preview_questions)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ebook-processor",
        description="Add retrieval-practice checkpoints to an EPUB and export the questions for spaced repetition.",
        epilog="'ebook-processor Book.epub' is short for 'ebook-processor process Book.epub'.")
    parser.add_argument("--version", action="version", version=f"ebook-processor {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser("process", help="process a book (or continue an unfinished run)",
                       description="Process an EPUB. The original is only read. An unfinished run in the output "
                                   "folder is continued.")
    p.add_argument("epub", type=Path, help="the EPUB to process")
    p.add_argument("-o", "--output", type=Path, help="output folder (default: <name>-processed beside the EPUB)")
    add_run_options(p)
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("rebuild", help="build the outputs again after taking questions out (no model is called)",
                       description="Build every output again from a finished run, with some questions taken out "
                                   "or put back. No model is called.")
    p.add_argument("output", type=Path, help="the output folder of an earlier run")
    p.add_argument("--remove", nargs="+", default=[], metavar="ID", help="question ids to take out (c0004-p2)")
    p.add_argument("--restore", nargs="+", default=[], metavar="ID", help="question ids to put back")
    p.add_argument("--epubcheck", action="store_true", default=None, help="also run EPUBCheck on both books")
    p.add_argument("--preview-questions", choices=("on", "off"),
                   help="in the book with visible checkpoints, begin each section with a page that lists its "
                        "questions, or stop doing so (default: as before)")
    p.add_argument("--cards", choices=("all", "missed", "reached"),
                   help="which questions go into the Anki deck and the text file, by your results in the web "
                        "reader: all, those you missed on the first try, or those you have met (default: as before)")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("serve", help="start the local web app",
                       description="Start the web app on this computer and open it in the browser.")
    p.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765)")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1, this computer only)")
    p.add_argument("--data", type=Path, default=Path.home() / "ebook-processor-runs",
                   help="folder that holds the runs (default: ~/ebook-processor-runs)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("models", help="list the models Claude Code and Codex offer",
                       description="List the models your Claude login can use, as Claude Code reports them, and "
                                   "those of Codex when it is installed and signed in. The first column is what "
                                   "--model and --checker-model take.")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("update", help="update to the latest version and start the app again",
                       description="Stop the app if it is running, fetch the latest 'main' into the git checkout "
                                   "the tool is installed from, install it, and start the app again as it was "
                                   "running. The checkout must be on 'main' with no uncommitted changes.")
    p.add_argument("--no-serve", action="store_true", help="update only; do not start the app afterwards")
    p.add_argument("--force", action="store_true",
                   help="stop the app even while it is processing a book (the run can be continued afterwards)")
    p.set_defaults(func=cmd_update)
    return parser


class Printer:
    """Progress on stderr: one line per event, with a percentage where there is one."""

    def __init__(self) -> None:
        self.lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        done, total = event.get("done"), event.get("total")
        share = f" [{100 * done // total:3d}%]" if total and done is not None else ""
        with self.lock:
            print(f"{event['stage']:>8}{share}  {event['message']}", file=sys.stderr, flush=True)


def print_summary(report: dict[str, Any], out_dir: Path) -> None:
    c = report["counts"]
    print(f"\n{report['book']['title']}: {c['questions']} questions at {c['checkpoints']} checkpoints in "
          f"{c['sections']} sections" + (f"; {c['left_out']} questions left out" if c["left_out"] else "") + ".")
    for key, what in (("prepared", "firmware format"), ("visible", "visible checkpoints"),
                      ("questions", "questions as data"), ("anki", "Anki deck"), ("tsv", "cards as text"),
                      ("report", "report")):
        name = report["files"].get(key)
        print(f"  {what + ':':21}{out_dir / name if name else '(not written; see the report)'}")
    if report["files"].get("prepared") and not report["firmware_format_ok"]:
        print("  The firmware-format book did not pass all its structure checks; the report says which.")


def cmd_process(args: argparse.Namespace) -> int:
    settings = settings_from(args)
    out_dir = (args.output or default_out_dir(args.epub)).resolve()
    cancel = threading.Event()
    try:
        report = run_book(args.epub, out_dir, settings, progress=Printer(), cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print(f"\nStopped. The run is saved in {out_dir}; give the same command again to continue.", file=sys.stderr)
        return EXIT_STOPPED
    except (Cancelled, UsageLimitError) as exc:
        print(f"\nStopped: {exc or 'on request'}\nThe run is saved in {out_dir}; give the same command again to "
              "continue.", file=sys.stderr)
        return EXIT_STOPPED
    print_summary(report, out_dir)
    return EXIT_OK


def cmd_rebuild(args: argparse.Namespace) -> int:
    out_dir = args.output.resolve()
    report = rebuild(out_dir, args.remove, args.restore, epubcheck=args.epubcheck, cards=args.cards,
                     preview=None if args.preview_questions is None else args.preview_questions == "on")
    print_summary(report, out_dir)
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.server import serve
    serve(args.data.expanduser().resolve(), args.host, args.port, not args.no_open)
    return EXIT_OK


def cmd_models(args: argparse.Namespace) -> int:
    from .llm import PROVIDERS, BackendUnavailable, ClaudeCodeBackend, CodexBackend, available_models
    ready, missing = {}, []
    for provider, backend in (("claude", ClaudeCodeBackend()), ("codex", CodexBackend())):
        try:
            backend.check_available()
            ready[provider] = backend
        except BackendUnavailable as exc:
            missing.append(f"{PROVIDERS[provider]}: {exc}")
    models = available_models(ready.get("claude"), ready.get("codex"))
    if "codex" in ready and ready["codex"].models_note:
        missing.append(f"Codex is ready, but its models could not be listed ({ready['codex'].models_note}). Any "
                       "model can still be named: --model codex:NAME")
    if not models:
        print("ebook-processor: no models to list.\n" + "\n".join(missing) if missing else
              "ebook-processor: Claude Code did not report its models (is it signed in and up to date?)", file=sys.stderr)
        return EXIT_INPUT
    width = max(len(m["value"]) for m in models)
    for provider, title in PROVIDERS.items():
        mine = [m for m in models if m["provider"] == provider]
        if not mine:
            continue
        print(f"{title}:")
        for m in mine:
            efforts = f"  [effort: {', '.join(m['efforts'])}]" if m["efforts"] else ""
            print(f"  {m['value']:<{width}}  {m['name']}: {m['description']}{efforts}")
    for line in missing:
        print(f"({line})", file=sys.stderr)
    return EXIT_OK


def cmd_update(args: argparse.Namespace) -> int:
    from . import update
    try:
        return update.update(serve=not args.no_serve, force=args.force)
    except update.UpdateError as exc:
        print(f"ebook-processor: {exc}", file=sys.stderr)
        return EXIT_INPUT


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "process")
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SettingsError as exc:
        parser.error(str(exc))
    except (PipelineError, BackendError) as exc:
        print(f"ebook-processor: {exc}", file=sys.stderr)
        return EXIT_INPUT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
