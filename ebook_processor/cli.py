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
COMMANDS = ("process", "rebuild", "serve")


def add_run_options(p: argparse.ArgumentParser) -> None:
    d = Settings()
    g = p.add_argument_group("where checkpoints go")
    g.add_argument("--density", choices=tuple(DENSITIES), default="medium",
                   help="target section length: " + ", ".join(f"{k} {a}-{b} words" for k, (a, b) in DENSITIES.items())
                        + " (default: medium)")
    g.add_argument("--section-words", metavar="MIN-MAX", help="target section length in words, instead of --density")
    g.add_argument("--max-questions", type=int, metavar="N", help=f"most questions per checkpoint, 1 to 4 "
                                                                   f"(default: {d.max_questions})")
    g = p.add_argument_group("what Claude reads and how strictly it is checked")
    g.add_argument("--content", choices=CONTENT_MODES,
                   help="tables, code and formulas: 'prose' hides them, 'text' shows their text, 'figures' also "
                        f"lets Claude look at the pictures (default: {d.content})")
    g.add_argument("--kind", choices=KINDS, help=f"the kind of book (default: {d.kind}, decided section by section)")
    g.add_argument("--rounds", type=int, metavar="N",
                   help=f"how often a question may be checked; a question failing the last check is left out; "
                        f"0 switches checking off (default: {d.rounds})")
    g = p.add_argument_group("Claude")
    g.add_argument("--model", help="model for drafting and revising (default: Claude Code's own default)")
    g.add_argument("--checker-model", help="model for checking (default: the same as --model)")
    g.add_argument("--effort", choices=EFFORTS, help="reasoning effort (default: Claude Code's own default)")
    g.add_argument("--jobs", type=int, metavar="N", help=f"calls that may run at the same time (default: {d.jobs})")
    g.add_argument("--part-words", type=int, metavar="N",
                   help=f"about how many words one drafting call reads (default: {d.part_words})")
    g = p.add_argument_group("output")
    g.add_argument("--epubcheck", action="store_true", default=None,
                   help="also run EPUBCheck on both books (needs Java; downloads EPUBCheck once)")
    g.add_argument("--title-suffix", help="added to the title of the book with visible checkpoints "
                                          f"(default: {d.title_suffix!r}; '' for none)")


def settings_from(args: argparse.Namespace) -> Settings:
    return from_options(
        density=args.density, section_words=args.section_words, max_questions=args.max_questions,
        content=args.content, kind=args.kind, rounds=args.rounds, model=args.model,
        checker_model=args.checker_model, effort=args.effort, jobs=args.jobs, part_words=args.part_words,
        epubcheck=args.epubcheck, title_suffix=args.title_suffix)


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

    p = sub.add_parser("rebuild", help="build the outputs again after taking questions out (no Claude usage)",
                       description="Build every output again from a finished run, with some questions taken out "
                                   "or put back. Claude is not called.")
    p.add_argument("output", type=Path, help="the output folder of an earlier run")
    p.add_argument("--remove", nargs="+", default=[], metavar="ID", help="question ids to take out (c0004-p2)")
    p.add_argument("--restore", nargs="+", default=[], metavar="ID", help="question ids to put back")
    p.add_argument("--epubcheck", action="store_true", default=None, help="also run EPUBCheck on both books")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("serve", help="start the local web app",
                       description="Start the web app on this computer and open it in the browser.")
    p.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765)")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1, this computer only)")
    p.add_argument("--data", type=Path, default=Path.home() / "ebook-processor-runs",
                   help="folder that holds the runs (default: ~/ebook-processor-runs)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.set_defaults(func=cmd_serve)
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
    report = rebuild(out_dir, args.remove, args.restore, epubcheck=args.epubcheck)
    print_summary(report, out_dir)
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.server import serve
    serve(args.data.expanduser().resolve(), args.host, args.port, not args.no_open)
    return EXIT_OK


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
