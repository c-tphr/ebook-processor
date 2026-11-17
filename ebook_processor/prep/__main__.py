"""Low-level commands of the EPUB engine: ``python3 -m ebook_processor.prep <command> ...``.

    python3 -m ebook_processor.prep extract BOOK.epub WORKDIR
    python3 -m ebook_processor.prep build BOOK.epub DRAFT.json -o OUT.prepared.epub
    python3 -m ebook_processor.prep validate BOOK.epub OUT.prepared.epub [--draft DRAFT.json | --work-dir DIR] [--json OUT]

The everyday command is ``ebook-processor`` (see ``ebook_processor/cli.py``); these three are the mechanical
steps it is built on, kept for debugging and for the engine's tests.

Exit status: 0 success (for validate: everything passed), 1 a problem with the input or a failed check (each
problem is printed on stderr), 2 a usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import BuildError, build_file
from .epub import EpubError
from .extract import extract

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_USAGE = 2


def _cmd_extract(args: argparse.Namespace) -> int:
    source = extract(args.epub, args.workdir)
    words = sum(d["words"] for d in source["spine"])
    blocks = sum(d["blocks"] for d in source["spine"])
    print(f"extracted {source['source_file']} (sha256 {source['source_sha256']}): {len(source['spine'])} spine "
          f"documents, {blocks} blocks, {words} words -> {args.workdir}")
    return EXIT_OK


def _cmd_build(args: argparse.Namespace) -> int:
    result = build_file(args.epub, args.draft, args.output)
    sections = result.checkpoints["sections"]
    prompts = sum(len(c["prompts"]) for c in result.checkpoints["checkpoints"])
    print(f"built {args.output}: {len(sections)} sections, {len(result.checkpoints['checkpoints'])} checkpoints, "
          f"{prompts} prompts; changed {', '.join(result.touched)}; added {result.added}")
    return EXIT_OK


def _created(args: argparse.Namespace) -> str:
    from .validate import timestamp
    try:
        return timestamp(args.timestamp)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


class UsageError(Exception):
    """A usage problem found after parsing (exit status 2)."""


def _write_json(path: Path | None, data: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _cmd_validate(args: argparse.Namespace) -> int:
    from . import validate as V
    if args.draft is not None and args.work_dir is not None:
        raise UsageError("give --draft or --work-dir, not both")
    if args.work_dir is not None and not args.work_dir.is_dir():
        raise UsageError(f"--work-dir {args.work_dir} is not a folder")
    draft = V.find_draft(args.draft, args.work_dir)
    if draft is not None and not draft.is_file():
        raise UsageError(f"cannot read the draft {draft}")
    created = _created(args)
    outcome = V.validate_files(args.original, args.prepared, draft, created=created)
    _write_json(args.json, outcome.result)
    r = outcome.result
    print(f"prep validate: {r['prepared']['file']} against {r['original']['file']}"
          + (f" (exclusions from {r['draft']['file']})" if r["draft"] else ""))
    print(V.format_table(r))
    failures = [(c["name"], f) for c in r["checks"] for f in c["failures"]]
    for name, f in failures:
        print(f"  {name}: {f['code']} at {f['location']}: {f['message']}", file=sys.stderr)
    if outcome.passed:
        print(f"PASSED: all {len(r['checks'])} checks passed.")
        return EXIT_OK
    not_passed = [c["name"] for c in r["checks"] if c["status"] != "pass"]
    print(f"FAILED: {len(not_passed)} of {len(r['checks'])} checks did not pass ({', '.join(not_passed)}); "
          f"{len(failures)} failure{'s' if len(failures) != 1 else ''} listed on stderr.")
    return EXIT_INPUT


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m ebook_processor.prep", description="EPUB engine: extract, build, validate.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser("extract", help="write the work folder (numbered text, structure, source facts)")
    p.add_argument("epub", type=Path, help="the original EPUB")
    p.add_argument("workdir", type=Path, help="folder to write (created if missing)")
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("build", help="write the prepared EPUB from the original and a draft")
    p.add_argument("epub", type=Path, help="the original EPUB (never modified)")
    p.add_argument("draft", type=Path, help="draft.json")
    p.add_argument("-o", "--output", type=Path, required=True, help="the prepared EPUB to write")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("validate", help="check a prepared EPUB against its original (every script-verifiable rule)")
    p.add_argument("original", type=Path, help="the original EPUB (only read)")
    p.add_argument("prepared", type=Path, help="the prepared EPUB")
    p.add_argument("--draft", type=Path, help="the draft the book was built from (its exclusions are used)")
    p.add_argument("--work-dir", type=Path, help="the work folder; its draft.json is used when present")
    p.add_argument("--json", type=Path, help="write the result (schema/validation.schema.json) here")
    p.add_argument("--timestamp", help="time to record (ISO 8601, UTC); default $SOURCE_DATE_EPOCH or now")
    p.set_defaults(func=_cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)  # exits with status 2 on a usage error
    try:
        return args.func(args)
    except UsageError as exc:
        parser.error(f"{args.command}: {exc}")  # exits with status 2
    except BuildError as exc:
        count = len(exc.issues)
        print(f"prep {args.command}: the draft cannot be built ({count} problem{'s' if count != 1 else ''}):",
              file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return EXIT_INPUT
    except EpubError as exc:
        print(f"prep {args.command}: {exc}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
