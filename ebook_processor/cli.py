"""The command line.

    ebook-processor Book.epub                 process a book (the same as: ebook-processor process Book.epub)
    ebook-processor Book.epub --purpose "Why I read it"    ... with what you want from the book
    ebook-processor estimate Book.epub        how many questions a run would write, and the book's chapters
    ebook-processor Essay.md                  a Markdown, text, HTML or Word file is taken in the same way
    ebook-processor https://example.org/essay     ... and so is the article at a web address
    ebook-processor rebuild Book-processed --remove c0004-p2
    ebook-processor serve                     the local web app
    ebook-processor update                    take the latest version and start the app again
    ebook-processor beta                      the same, with the development version

Exit status: 0 done, 1 a problem with the input or the run, 2 a usage error, 3 the run stopped before it was
finished and can be continued with the same command (usage limit reached, or Ctrl-C).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any

from . import __version__, sources
from . import steer as steering
from .pipeline import load_state
from .prep.epub import EpubError, load_book
from .prep.limits import MAX_PROMPTS
from .llm import BackendError, UsageLimitError
from .runner import ready_backend
from .runner import Cancelled, PipelineError, default_out_dir, rebuild, run_book, trial_dir, try_chapter, work_dir
from .settings import CONTENT_MODES, DENSITIES, EFFORTS, KINDS, MAX_QUESTIONS, Settings, SettingsError, from_options

EXIT_OK, EXIT_INPUT, EXIT_USAGE, EXIT_STOPPED = 0, 1, 2, 3
COMMANDS = ("process", "estimate", "rebuild", "serve", "models", "update", "beta")


def add_run_options(p: argparse.ArgumentParser) -> None:
    d = Settings()
    g = p.add_argument_group("where checkpoints go")
    g.add_argument("--density", choices=tuple(DENSITIES), default="medium",
                   help="target section length: " + ", ".join(f"{k} {a}-{b} words" for k, (a, b) in DENSITIES.items())
                        + " (default: medium)")
    g.add_argument("--section-words", metavar="MIN-MAX", help="target section length in words, instead of --density")
    g.add_argument("--max-questions", type=int, metavar="N",
                   help=f"most questions per checkpoint, 1 to {MAX_QUESTIONS}; the model chooses how many by the "
                        f"section's length and density and by what you want from the book (default: {d.max_questions})")
    g.add_argument("--forms", metavar="LIST",
                   help="the kinds of question a run may write, separated by commas: open (you recall the answer and "
                        "grade yourself), choice (multiple choice), truefalse, cloze (fill in the blank), or all. The "
                        "model picks among them for each idea (default: open)")
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


def add_steer_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("what you want from the book (each takes text, or @FILE to read it from a file)")
    g.add_argument("--purpose", metavar="TEXT", help="why you read this book and what you want to keep from it")
    g.add_argument("--about-me", metavar="TEXT", help="what matters to you and what you work on")
    g.add_argument("--question-style", metavar="TEXT", help="how you like questions")
    g.add_argument("--yes", action="store_true",
                   help="use the model's reading of these without asking you to confirm it")
    g.add_argument("--no-reading", action="store_true",
                   help="skip the reading: give your own words to the question writers as they are")
    g.add_argument("--try-chapter", type=int, metavar="N",
                   help="write the questions of one chapter only and print them (N as 'estimate' lists it); a later "
                        "run of the whole book with the same options takes the chapter over")


def settings_from(args: argparse.Namespace) -> Settings:
    return from_options(
        density=args.density, section_words=args.section_words, max_questions=args.max_questions, forms=args.forms,
        content=args.content, kind=args.kind, rounds=args.rounds, model=args.model,
        checker_model=args.checker_model, effort=args.effort, jobs=args.jobs, part_words=args.part_words,
        epubcheck=args.epubcheck, title_suffix=args.title_suffix, preview=args.preview_questions)


def add_source_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--one-piece", action="store_true",
                   help="for what is no EPUB: do not cut it into chapters at its headings")
    p.add_argument("--whole-page", action="store_true",
                   help="for a web page: take the whole page, not only the article found in it")


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
    p.add_argument("epub", metavar="FILE", help="the EPUB to process; or a Markdown, text, HTML or Word file, a saved page "
                                                "(.mhtml), or the web address of an article")
    p.add_argument("-o", "--output", type=Path, help="output folder (default: <name>-processed beside the file, or in "
                                                     "this folder for a web address)")
    add_source_options(p)
    add_run_options(p)
    add_steer_options(p)
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("estimate", help="say how many questions a run would write, and list the book's chapters",
                       description="Print about how many sections and questions a run with these options would "
                                   "give, and the book's chapters with the numbers --try-chapter takes. No model "
                                   "is called.")
    p.add_argument("epub", metavar="FILE", help="the EPUB; or another file or a web address, as for 'process'")
    add_source_options(p)
    add_run_options(p)
    p.set_defaults(func=cmd_estimate)

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
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1, this computer only). Any other address opens "
                        "the app, which has no password, to everyone who can reach that address")
    p.add_argument("--data", type=Path, default=Path.home() / "ebook-processor-runs",
                   help="folder that holds the runs (default: ~/ebook-processor-runs)")
    p.add_argument("--notes", type=Path, default=None, metavar="FOLDER",
                   help="the notes folder to use from now on (default: the one set in Settings, or "
                        "ebook-processor-notes beside the data folder)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("models", help="list the models Claude Code and Codex offer",
                       description="List the models your Claude login can use, as Claude Code reports them, and "
                                   "those of Codex when it is installed and signed in. The first column is what "
                                   "--model and --checker-model take.")
    p.set_defaults(func=cmd_models)

    from .update import BETA_BRANCH
    for name, branch, text in (
            ("update", None, "update to the latest version and start the app again"),
            ("beta", BETA_BRANCH, "switch to the development version, newer and less tested, and start the app again")):
        p = sub.add_parser(name, help=text, description=(
            "Stop the app if it is running, fetch the latest '" + (branch or "main") + "' into the git checkout the tool "
            "is installed from, install it, and start the app again as it was running. The checkout is switched to "
            "that branch when it is on another; it must have no uncommitted changes."
            + (" This is the development branch: what is being worked on before it reaches 'main'. 'ebook-processor "
               "update' goes back to 'main'." if branch else " After 'ebook-processor beta', this goes back to 'main'.")))
        p.add_argument("--no-serve", action="store_true", help="update only; do not start the app afterwards")
        p.add_argument("--force", action="store_true",
                       help="stop the app even while it is processing a book (the run can be continued afterwards)")
        if branch:
            p.add_argument("--branch", default=branch, metavar="NAME", help=f"the branch to take (default: {branch})")
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


def _text(value: str | None) -> str:
    if value and value.startswith("@"):
        try:
            value = Path(value[1:]).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise PipelineError(f"cannot read {value[1:]}: {exc}") from exc
    return steering.clean(value)


def estimate_line(book: Any, settings: Settings, steered: bool) -> str:
    e = steering.estimate([c["words"] for c in steering.chapter_list(book)], settings, steered)
    line = (f"{e['words']:,} words: about {e['sections'][0]} to {e['sections'][1]} sections and {e['questions'][0]} to "
            f"{e['questions'][1]} questions (at most {settings.max_questions} at a checkpoint)")
    line += f". That maximum allows up to {e['ceiling']:,}"
    if e["usual"] < settings.max_questions:
        line += (f"; for sections of this length the model seldom writes more than about {e['usual']} at a checkpoint"
                 + ("" if steered else ", unless a section bears on what you want from the book"))
    if e["ceiling"] > MAX_PROMPTS:
        line += f". The firmware edition holds at most {MAX_PROMPTS:,} questions"
    return line + "."


def print_reading(reading: dict[str, Any]) -> None:
    out = sys.stderr
    print("\nHow your wishes were read", file=out)
    for title, text in (("What the questions will favour", reading["focus"]), ("How they will be written", reading["style"]),
                        ("Cannot be followed", reading["cannot"]), ("Not found in this book", reading["gaps"])):
        if text:
            print(f"\n  {title}:\n" + "\n".join("    " + line for line in text.splitlines()), file=out)
    if reading["chapters"]:
        print("\n  Chapters it bears on most:", file=out)
        for c in reading["chapters"]:
            print(f"    {c['doc']} {c['title']}: {c['why']}", file=out)
    print(file=out, flush=True)


def confirmed_steer(args: argparse.Namespace, epub: Path, out_dir: Path, settings: Settings,
                    cancel: threading.Event) -> tuple[steering.Steer | None, list[Any]]:
    """What the reader wants from the book, as the question writers will be told it, and the calls it took. With
    a terminal the model's reading is shown for the reader to confirm, to correct or to do without."""
    raw = steering.Steer(_text(args.purpose), _text(args.about_me), _text(args.question_style))
    if raw.empty:
        return None, []
    for folder in (out_dir, trial_dir(out_dir)):  # a run or a trial made from these same words: its reading stands
        try:
            old = steering.Steer.from_json((load_state(work_dir(folder)) or {}).get("steer"))
        except PipelineError:
            continue
        if not old.empty and old.raw_key() == raw.raw_key():
            return old, []
    if args.no_reading:
        return raw, []
    book = load_book(epub)
    backend = ready_backend(settings, ("draft",), None, cancel)
    spent: list[Any] = []
    previous, feedback, answers = None, "", None
    while True:
        print("Reading what you want from the book ...", file=sys.stderr, flush=True)
        reading, done = steering.read(backend, book, raw, settings, previous, feedback, answers)
        spent.append(done)
        print_reading(reading)
        if args.yes or not sys.stdin.isatty():
            return steering.confirm(raw, reading), spent
        answers = []
        for question in reading["questions"]:
            answers.append((question, input(f"  The model asks: {question}\n  (Enter to pass) > ")))
        choice = input("Enter: use this reading   f: say what to change   n: go on without it   q: stop > ").strip().lower()
        if choice == "q":
            raise Cancelled("nothing was started")
        if choice == "n":
            return None, spent
        if choice == "f" or any(a.strip() for _, a in answers):
            feedback = input("What should change? > ") if choice == "f" else ""
            previous = reading
            continue
        return steering.confirm(raw, reading), spent


def print_trial(data: dict[str, Any]) -> None:
    for s in data["sections"]:
        print(f"\n{s['label']}  ({s['words']:,} words)")
        for q in s["questions"]:
            mark = "" if q["kept"] else "  [left out: failed the check]"
            print(f"  Q  {q['question']}{mark}\n  A  {q['answer']}")
        if not s["questions"]:
            print("  (no questions)")
    count = sum(q["kept"] for s in data["sections"] for q in s["questions"])
    print(f"\n{count} question{'s' if count != 1 else ''} in {len(data['sections'])} section"
          f"{'s' if len(data['sections']) != 1 else ''}. Run the same command without --try-chapter for the whole "
          "book; this chapter is taken over as it is.")


def is_address(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _say(made: sources.Made) -> sources.Made:
    for warning in made.warnings:
        print(warning, file=sys.stderr)
    return made


def _how(args: argparse.Namespace) -> dict[str, Any]:
    """How the options say a file or a page is converted: cut into chapters or not, and how much of a page."""
    return {"split_how": "none" if args.one_piece else "auto", "whole": True if args.whole_page else None}


def converted(path: Path, args: argparse.Namespace) -> sources.Made:
    """A file that is no EPUB, as the EPUB that is processed in its place."""
    try:
        return _say(sources.convert(path.name, path.read_bytes(), folder=path.parent, **_how(args)))
    except (sources.SourceError, OSError) as exc:
        raise PipelineError(str(exc)) from exc


def fetched(url: str, args: argparse.Namespace, out_dir: Path | None) -> tuple[sources.Made, Path]:
    """The page at a web address as an EPUB, and the folder of its run. The page is kept in that folder, and a
    later run with the same address reads it from there: the same page gives the same EPUB, so a run that was
    stopped goes on, whatever the site shows by then."""
    how = _how(args)
    try:
        kept = out_dir / "source" / "page.json" if out_dir is not None else None
        page = None
        if kept is not None and kept.is_file():
            page = _kept_page(kept, url)
        if page is None:
            print(f"Fetching {url} ...", file=sys.stderr, flush=True)
            page = sources.fetch_page(url)
            if out_dir is None:  # the folder is named after the piece, which only the page tells
                made = sources.convert(page.name, page.data, url=page.url, fetched=page.fetched, charset=page.charset, **how)
                out_dir = (Path.cwd() / f"{Path(made.name).stem}-processed").resolve()
                again = _kept_page(out_dir / "source" / "page.json", url)
                page = again or page
        (out_dir / "source").mkdir(parents=True, exist_ok=True)
        if not (out_dir / "source" / "page.json").is_file() or _kept_page(out_dir / "source" / "page.json", url) is None:
            (out_dir / "source" / page.name).write_bytes(page.data)
            (out_dir / "source" / "page.json").write_text(json.dumps({"asked": url, "url": page.url, "name": page.name, "charset": page.charset,
                                                                      "fetched": page.fetched}, indent=1), encoding="utf-8")
        return _say(sources.convert(page.name, page.data, url=page.url, fetched=page.fetched, charset=page.charset, **how)), out_dir
    except (sources.SourceError, OSError) as exc:
        raise PipelineError(str(exc)) from exc


def _kept_page(kept: Path, url: str) -> sources.Page | None:
    """The page a run kept, when it is the page of this address."""
    try:
        said = json.loads(kept.read_text(encoding="utf-8"))
        if said.get("asked") != url and said.get("url") != sources.tidy(url):
            return None
        return sources.Page(said["name"], (kept.parent / said["name"]).read_bytes(), said["url"], said.get("charset"), said["fetched"])
    except (OSError, ValueError, KeyError):
        return None


def cmd_process(args: argparse.Namespace) -> int:
    settings = settings_from(args)
    cancel = threading.Event()
    made = None
    if is_address(args.epub):
        made, out_dir = fetched(args.epub, args, args.output.resolve() if args.output else None)
        given = args.epub
    else:
        path = Path(args.epub)
        out_dir = (args.output or default_out_dir(path)).resolve()
        epub = path.resolve()
        if not epub.is_file():
            raise PipelineError(f"{epub} does not exist")
        given = path.name
        if sources.is_source(epub.name):
            made = converted(epub, args)  # the same file gives the same EPUB, so a run that was stopped goes on
    if made is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        epub = out_dir / made.name
        if not epub.is_file() or epub.read_bytes() != made.epub:
            epub.write_bytes(made.epub)
        print(f"{given} is read as {made.title} ({made.words:,} words"
              + (f", {made.found['count']} chapters" if made.found else "") + f"). The EPUB made from it is {epub}", file=sys.stderr)
    try:
        steer, spent = confirmed_steer(args, epub, out_dir, settings, cancel)
        if args.try_chapter is None:
            print(estimate_line(load_book(epub), settings, steer is not None), file=sys.stderr)
            report = run_book(epub, out_dir, settings, progress=Printer(), cancel=cancel, steer=steer, spent=spent)
        else:
            chapters = {c["index"]: c for c in steering.chapter_list(load_book(epub))}
            if args.try_chapter not in chapters:
                raise PipelineError(f"there is no chapter {args.try_chapter}; 'ebook-processor estimate' lists them")
            print_trial(try_chapter(epub, out_dir, settings, args.try_chapter, progress=Printer(), cancel=cancel,
                                    steer=steer, spent=spent))
            return EXIT_OK
    except KeyboardInterrupt:
        cancel.set()
        print(f"\nStopped. The run is saved in {out_dir}; give the same command again to continue.", file=sys.stderr)
        return EXIT_STOPPED
    except (Cancelled, UsageLimitError) as exc:
        print(f"\nStopped: {exc or 'on request'}\nThe run is saved in {out_dir}; give the same command again to "
              "continue.", file=sys.stderr)
        return EXIT_STOPPED
    except EpubError as exc:
        raise PipelineError(str(exc)) from exc
    print_summary(report, out_dir)
    return EXIT_OK


def cmd_estimate(args: argparse.Namespace) -> int:
    settings = settings_from(args)
    if is_address(args.epub):
        try:
            page = sources.fetch_page(args.epub)
            book = _say(sources.convert(page.name, page.data, url=page.url, fetched=page.fetched, charset=page.charset,
                                        **_how(args))).book
        except sources.SourceError as exc:
            raise PipelineError(str(exc)) from exc
        named = args.epub
    else:
        path = Path(args.epub).resolve()
        if not path.is_file():
            raise PipelineError(f"{path} does not exist")
        try:
            book = converted(path, args).book if sources.is_source(path.name) else load_book(path)
        except (EpubError, OSError) as exc:
            raise PipelineError(str(exc)) from exc
        named = path.name
    print(f"{book.title or named}: {estimate_line(book, settings, False)}")
    print("The model decides both within your limits, so this is a range. With a statement of what you want from "
          "the book, the parts that bear on it may get more.\n\nChapters (the number is what --try-chapter takes):")
    for c in steering.chapter_list(book):
        print(f"  {c['index']:>3}  {c['words']:>7,} words  {c['title']}")
    return EXIT_OK


def cmd_rebuild(args: argparse.Namespace) -> int:
    out_dir = args.output.resolve()
    report = rebuild(out_dir, args.remove, args.restore, epubcheck=args.epubcheck, cards=args.cards,
                     preview=None if args.preview_questions is None else args.preview_questions == "on")
    print_summary(report, out_dir)
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.server import serve
    serve(args.data.expanduser().resolve(), args.host, args.port, not args.no_open,
          args.notes.expanduser().resolve() if args.notes else None)
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
        return update.update(serve=not args.no_serve, force=args.force, branch=getattr(args, "branch", None) or update.BRANCH)
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
