"""The notes folder: plain Markdown files the app reads and writes, which Obsidian can open as a vault.

    <notes folder>/
      Books/The Lighthouse Keeper's Almanac.md   one file per book, written by the app (see bookfile.py)
      Inbox/2026-11-18 1530 a thought.md         quick notes, notes without a title, and files dropped in
      Prices carry information.md                a note: its title is its file name
      .trash/                                    deleted notes, which can be put back

The files are the truth. The app keeps an index of them in memory and reads a file again when its modification
time changes, so an edit made in another program is seen. Nothing is written outside the folder.

A note's file starts with its properties (YAML between two ``---`` lines), then its text:

    ---
    id: n7f3a2c91
    kind: idea
    created: 2026-11-18T15:30:00Z
    tags: [pricing, markets]
    sources:
      - "[[The Lighthouse Keeper's Almanac#^h3f9a]]"
    ---
    Prices carry information that no single person holds.

The properties are read and written here without a YAML library, for a small subset: one value, a list in
brackets, or a list of ``- item`` lines. A property the app does not understand is kept exactly as it was.

``id`` never changes; the title can. Links are ``[[Title]]``, ``[[Title|shown]]``, ``[[Title#Heading]]`` and
``[[Title#^block]]``, found by file name in any letter case, as Obsidian finds them.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

BOOKS, INBOX, TRASH = "Books", "Inbox", ".trash"
KINDS = ("idea", "index", "quick", "project-note", "project", "book")
WRITABLE = ("idea", "index", "quick", "project-note", "project")  # the kinds a person's own note can have
TITLE_MAX = 120
BODY_MAX = 400_000  # characters
FORBIDDEN = '\\/:*?"<>|#^[]'  # not in a file name on Windows, or not in the target of a link
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
PROTECTED = ("id", "created")  # properties only the app writes
UNTITLED_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{4}(?: |$)")  # the name the app gives a note that has no title yet
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class VaultError(ValueError):
    """A request about the notes folder cannot be carried out; the message says why."""


class Missing(VaultError):
    """There is no such note."""


class Conflict(VaultError):
    """The file changed on disk after the note was loaded. ``current`` is the note as it now is."""

    def __init__(self, message: str, current: dict[str, Any]):
        super().__init__(message)
        self.current = current


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def key(title: str) -> str:
    """What two titles share when a file system, or a link, cannot tell them apart."""
    return _nfc(title).casefold()


# --------------------------------------------------------------------------
# Titles and file names


def title_problem(title: str) -> str | None:
    """Why this cannot be a note's title (it is also its file name), or None."""
    if not title:
        return "A note needs a title."
    if len(title) > TITLE_MAX:
        return f"A title can have {TITLE_MAX} characters; this one has {len(title)}."
    bad = sorted({c for c in title if c in FORBIDDEN or ord(c) < 32 or ord(c) == 127})
    if bad:
        shown = " ".join(c if c.isprintable() else "(a control character)" for c in bad)
        return f"A title is also a file name, so it cannot contain {shown}"
    if title[0] == "." or title[-1] in ". ":
        return "A title cannot start with a dot, or end with a dot or a space."
    if title.split(".")[0].strip().casefold() in RESERVED:
        return f"Windows keeps the name {title.split('.')[0].strip()} for itself. Choose another title."
    return None


def clean_title(raw: Any) -> str:
    if not isinstance(raw, str):
        raise VaultError("a title is text")
    title = " ".join(_nfc(raw).split())
    problem = title_problem(title)
    if problem:
        raise VaultError(problem)
    return title


def safe_title(text: str, fallback: str = "Untitled", limit: int = TITLE_MAX) -> str:
    """A title made from any text, for the names the app chooses itself. Never fails."""
    out = "".join(" " if c in FORBIDDEN or ord(c) < 32 or ord(c) == 127 else c for c in _nfc(text))
    out = " ".join(out.split())[:limit].strip(" .")
    if not out or title_problem(out):
        out = (out + " -").strip() if out and out.split(".")[0].strip().casefold() in RESERVED else fallback
    return out


def inbox_name(body: str, when: _dt.datetime | None = None) -> str:
    """The name of a note that has no title yet: the date and time, then its first words."""
    when = when or _dt.datetime.now()
    first = next((line for line in body.split("\n") if re.sub(r"[\W_]+", "", line)), "")
    first = re.sub(r"!?\[\[([^\]|#]*)(#[^\]|]*)?(\|([^\]]*))?\]\]", lambda m: m.group(4) or m.group(1), first)
    first = re.sub(r"^(source:\s*|[#>*+\-\s]+)", "", first, flags=re.I)
    words = re.sub(r"[^\w\s'’-]+", " ", first).split()[:6]
    tail = safe_title(" ".join(words), "", 44) if words else ""
    return (when.strftime("%Y-%m-%d %H%M") + " " + tail).strip()


# --------------------------------------------------------------------------
# Properties: the YAML at the top of a file


class _Opaque:
    def __repr__(self) -> str:
        return "OPAQUE"


OPAQUE = _Opaque()  # the value of a property the app keeps but does not read


@dataclass
class Prop:
    key: str | None  # None: a line that is no property, such as a comment
    value: Any = None  # text, a number, true or false, None, a list of texts, or OPAQUE
    raw: list[str] | None = None  # the lines as they were read; None once the app has set the value


KEY_RE = re.compile(r"^(\w[^:#]*?)[ \t]*:(?:[ \t]+(.*))?$")
INT_RE, FLOAT_RE = re.compile(r"^[-+]?(0|[1-9]\d*)$"), re.compile(r"^[-+]?\d+\.\d+$")


def split(text: str) -> tuple[list[str] | None, str]:
    """A file's text as (the lines of its properties, or None when it has none; the rest)."""
    if not text.startswith("---\n"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def _unquote(text: str) -> tuple[str, str] | None:
    """A quoted text at the start of ``text``, as (what it says, what follows it); None when it is not closed."""
    quote, out, i = text[0], [], 1
    while i < len(text):
        c = text[i]
        if quote == '"' and c == "\\" and i + 1 < len(text):
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(text[i + 1], "\\" + text[i + 1]))
            i += 2
        elif quote == "'" and c == "'" and text[i + 1:i + 2] == "'":
            out.append("'")
            i += 2
        elif c == quote:
            return "".join(out), text[i + 1:]
        else:
            out.append(c)
            i += 1
    return None


def _scalar(text: str, as_text: bool = False) -> Any:
    """One value. ``as_text`` keeps numbers and true/false as the words they were written with."""
    text = text.strip()
    if text[:1] in ('"', "'"):
        got = _unquote(text)
        if got is not None and (not got[1].strip() or got[1].strip().startswith("#")):
            return got[0]
        return text
    note = re.search(r"(^|\s)#", text)
    if note and not text.startswith("[["):
        text = text[:note.start()].rstrip()
    if as_text:
        return text
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE", "false", "False", "FALSE"):
        return text.lower() == "true"
    if INT_RE.match(text):
        return int(text)
    if FLOAT_RE.match(text):
        return float(text)
    return text


def _inline(inner: str) -> list[str]:
    items, buf, quote, depth = [], "", "", 0
    for c in inner:
        if quote:
            buf += c
            if c == quote:
                quote = ""
        elif c in "\"'" and not buf.strip():
            quote, buf = c, buf + c
        elif c == "[":
            depth, buf = depth + 1, buf + c
        elif c == "]":
            depth, buf = depth - 1, buf + c
        elif c == "," and depth <= 0:
            items.append(buf)
            buf = ""
        else:
            buf += c
    items.append(buf)
    return [v for v in (_scalar(i, as_text=True) for i in items if i.strip()) if v != ""]


def _value(rest: str, more: list[str]) -> Any:
    rest = rest.strip()
    if rest.startswith("#"):
        rest = ""
    if more:
        if rest:
            return OPAQUE
        items = []
        for line in more:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if not (s == "-" or s.startswith("- ")):
                return OPAQUE
            item = s[1:].strip()
            if item[:1] in "{|>&*!" or (item[:1] not in "\"'[" and re.search(r":(\s|$)", item)):
                return OPAQUE  # a list of something other than plain values
            items.append(_scalar(item, as_text=True))
        return items
    if rest.startswith("[") and not rest.startswith("[["):
        return _inline(rest[1:-1]) if rest.endswith("]") else OPAQUE
    if rest[:1] in tuple("{|>&*!"):
        return OPAQUE
    return _scalar(rest)


def parse_props(lines: list[str]) -> list[Prop]:
    props: list[Prop] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = KEY_RE.match(line)
        if not m:
            props.append(Prop(None, None, [line]))
            i += 1
            continue
        j = i + 1
        while j < len(lines) and (lines[j][:1] in (" ", "\t") or lines[j] == "-" or lines[j].startswith("- ")):
            j += 1
        props.append(Prop(m.group(1), _value(m.group(2) or "", lines[i + 1:j]), lines[i:j]))
        i = j
    return props


def _plain(text: str) -> bool:
    """This text can be written without quotes and read back as the same text."""
    if not text or text != text.strip() or "\n" in text or "\t" in text:
        return False
    if text[0] in "!&*-?[]{}|>@`\"'#%,:" or ": " in text or " #" in text or text.endswith(":"):
        return False
    return _scalar(text) == text and isinstance(_scalar(text), str)


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def _dump(prop: Prop) -> list[str]:
    if prop.raw is not None:
        return prop.raw
    value = prop.value
    if isinstance(value, list):
        items = [str(v) for v in value]
        if all(_plain(i) and not re.search(r"[,\[\]{}]", i) for i in items) and len(", ".join(items)) <= 80:
            return [f"{prop.key}: [{', '.join(items)}]"]
        return [f"{prop.key}:"] + [f"  - {_quoted(i)}" for i in items]
    if value is None:
        return [f"{prop.key}:"]
    if isinstance(value, bool):
        return [f"{prop.key}: {'true' if value else 'false'}"]
    if isinstance(value, (int, float)):
        return [f"{prop.key}: {value}"]
    text = str(value)
    return [f"{prop.key}: {text if _plain(text) else _quoted(text)}"]


def compose(props: list[Prop], body: str) -> str:
    if not props:
        return body
    return "---\n" + "\n".join(line for p in props for line in _dump(p)) + "\n---\n" + body


def prop(props: list[Prop], name: str) -> Any:
    return next((p.value for p in props if p.key == name), None)


def set_prop(props: list[Prop], name: str, value: Any) -> None:
    for p in props:
        if p.key == name:
            if p.value != value or type(p.value) is not type(value):
                p.value, p.raw = value, None
            return
    props.append(Prop(name, value))


def clean_tags(raw: Any) -> list[str]:
    """Tags as the app keeps them: no ``#``, no spaces, each once."""
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", raw) if isinstance(raw, str) else []
    out: list[str] = []
    for item in items:
        tag = re.sub(r"[^\w/-]+", "-", _nfc(str(item)).strip().lstrip("#")).strip("-")
        if tag and tag.casefold() not in {t.casefold() for t in out}:
            out.append(tag[:60])
    return out[:50]


# --------------------------------------------------------------------------
# A note's text: links, tags, headings, blocks

LINK_RE = re.compile(r"(!?)\[\[([^\[\]|#\n]*)(?:#([^\[\]|\n]*))?(?:\|([^\[\]\n]*))?\]\]")
TAG_RE = re.compile(r"(?<![\w/#&])#((?:[\w/-]*[^\W\d][\w/-]*))")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
BLOCK_RE = re.compile(r"(?:^|\s)\^([A-Za-z0-9-]+)\s*$")
FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
ESCAPE_RE = re.compile(r"\\[\\`*_\[\]<>#~=^|!]")  # a backslash before a mark of Markdown


def mask_code(text: str) -> str:
    """The text with code blanked out, letter for letter, so that what is found in it is not inside code."""
    out, fence = [], ""
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if fence:
            if m and m.group(1) == fence:
                fence = ""
            out.append(" " * len(line))
        elif m:
            fence = m.group(1)
            out.append(" " * len(line))
        else:
            line = ESCAPE_RE.sub("  ", line)  # an escaped mark is not a mark
            out.append(re.sub(r"(`+)(.+?)\1", lambda c: " " * len(c.group(0)), line))
    return "\n".join(out)


def unescape(text: str) -> str:
    return ESCAPE_RE.sub(lambda m: m.group(0)[1], text)


@dataclass
class Link:
    target: str  # as written, without what follows # or |
    anchor: str  # "Heading", "^block" or ""
    shown: str
    embed: bool
    context: str  # the line the link is in


@dataclass
class Passage:
    """A paragraph of a book's file: a highlight, or something written about the book."""
    block: str  # its block id, or that of the entry it belongs to
    text: str
    heading: str
    quote: bool


@dataclass
class Note:
    path: str  # inside the folder, with forward slashes
    stamp: tuple[int, int]  # modification time and size of the file when it was read
    title: str
    props: list[Prop]
    body: str
    id: str = ""
    provisional: bool = False  # the file names no id of its own yet
    kind: str = "idea"
    tags: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    blocks: dict[str, str] = field(default_factory=dict)
    passages: list[Passage] = field(default_factory=list)
    excerpt: str = ""
    created: str = ""
    modified: str = ""
    fold: str = ""  # title and text, for search

    @property
    def inbox(self) -> bool:
        return self.path.startswith(INBOX + "/")

    @property
    def untitled(self) -> bool:
        return self.inbox and bool(UNTITLED_RE.match(self.title))

    @property
    def token(self) -> str:
        return f"{self.stamp[0]}-{self.stamp[1]}"


def _unquoted(line: str) -> str:
    return re.sub(r"^ {0,3}(> ?)+", "", line)


def _plain_text(text: str) -> str:
    """A line or two of Markdown as words, for a list or a search result."""
    text = LINK_RE.sub(lambda m: m.group(4) or m.group(2) or m.group(3) or "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?<!\\)(\*\*|__|\*|_|`|~~|==)", "", text)
    return " ".join(unescape(text).split())


def read_text(text: str, path: str, stamp: tuple[int, int]) -> Note:
    """One file's text as a note. ``id`` is settled by the folder, which knows the other notes."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    front, body = split(text)
    props = parse_props(front) if front is not None else []
    stem = _nfc(path.rsplit("/", 1)[-1][:-3])
    note = Note(path=path, stamp=stamp, title=stem, props=props, body=body)
    kind = prop(props, "kind")
    note.kind = kind if kind in KINDS else "book" if path.startswith(BOOKS + "/") else "quick" if note.inbox else "idea"
    masked = mask_code(body)
    lines, plain = masked.split("\n"), body.split("\n")
    tags = prop(props, "tags")
    found = clean_tags(tags if isinstance(tags, (list, str)) else []) + [m.group(1) for m in TAG_RE.finditer(masked)]
    note.tags = clean_tags(found)
    for n, line in enumerate(lines):
        for m in LINK_RE.finditer(line):
            note.links.append(Link(_nfc(m.group(2).strip()), (m.group(3) or "").strip(), (m.group(4) or "").strip(),
                                   bool(m.group(1)), _plain_text(_unquoted(plain[n]))[:300]))
        head = HEADING_RE.match(line)
        if head and head.group(2):
            note.headings.append(_plain_text(head.group(2)))
    for held in props:  # links kept in a property count as links of the note
        value = held.value if held.key else None
        for item in value if isinstance(value, list) else [value] if isinstance(value, str) else []:
            for m in LINK_RE.finditer(str(item)):
                if not any(k.target == _nfc(m.group(2).strip()) and k.anchor == (m.group(3) or "").strip() for k in note.links):
                    note.links.append(Link(_nfc(m.group(2).strip()), (m.group(3) or "").strip(), "", False, ""))
    # paragraphs: runs of lines with no empty line between them
    heading, para, pending, before = "", [], [], ""

    def close() -> None:
        nonlocal para, before
        if not para:
            return
        quote = all(line.lstrip().startswith(">") for line in para)
        words = "\n".join(_unquoted(line) for line in para).strip()
        para = []
        if words.startswith("[!"):  # a callout: the app's own notice at the top of a book's file
            return
        for line in words.split("\n")[:-1]:  # an item of a list can carry a block id of its own
            inner = BLOCK_RE.search(line)
            if inner:
                note.blocks[inner.group(1)] = line[:inner.start()].strip()
        m = BLOCK_RE.search(words)
        if m:
            words = unescape(words[:m.start()].rstrip())
            # an id on a line of its own names what stands before it
            note.blocks[m.group(1)] = words or "\n\n".join(p.text for p in pending if not p.block) or before
        else:
            words = unescape(words)
        before = words or before
        if note.kind == "book":
            if words:
                pending.append(Passage(m.group(1) if m else "", words, heading, quote))
            if m:  # the paragraphs before it that have no id of their own belong to the same entry
                for p in pending:
                    p.block = p.block or m.group(1)
                note.blocks[m.group(1)] = "\n\n".join(p.text for p in pending)
                note.passages.extend(pending)
                pending.clear()

    for n, line in enumerate(lines):
        head = HEADING_RE.match(line)
        if head or not plain[n].strip():
            close()
            if head:
                heading = _plain_text(head.group(2))
                note.passages.extend(p for p in pending if p.block)
                pending.clear()
        else:
            para.append(plain[n])
    close()
    words = [_plain_text(_unquoted(line)) for line in plain if line.strip() and not HEADING_RE.match(line)]
    note.excerpt = " ".join(w for w in words if w and not w.startswith("[!"))[:280]
    created = prop(props, "created")
    note.created = created if isinstance(created, str) else ""
    note.modified = _dt.datetime.fromtimestamp(stamp[0] / 1e9, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    note.fold = key(note.title + "\n" + body)
    return note


# --------------------------------------------------------------------------
# The folder


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.lock = threading.RLock()
        self.notes: dict[str, Note] = {}  # by path
        self.by_id: dict[str, Note] = {}
        self.by_name: dict[str, list[Note]] = {}
        self.aliases: dict[str, str] = {}  # the id a note had before its file named one -> the id it has now
        self._back: dict[str, list[tuple[Note, Link]]] | None = None
        self._looked = 0.0

    # ---- files

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    def ensure(self) -> None:
        """Make the folder and the two sub-folders the app uses. Nothing is changed when they are there."""
        try:
            for folder in (self.root, self.root / BOOKS, self.root / INBOX):
                folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise VaultError(f"The notes folder cannot be made at {self.root}: {exc.strerror or exc}") from exc

    def _inside(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        root = self.root.resolve()
        if root != path and root not in path.parents:
            raise VaultError("that is outside the notes folder")
        return path

    def _files(self) -> Iterator[Path]:
        for folder, folders, names in os.walk(self.root):
            folders[:] = sorted(d for d in folders if not d.startswith("."))
            for name in sorted(names):
                if name.lower().endswith(".md") and not name.startswith("."):
                    yield Path(folder) / name

    def _write(self, path: Path, text: str) -> None:
        """Through a temporary file beside it, so that a stop in the middle never leaves half a note."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".{secrets.token_hex(6)}.tmp"
        try:
            tmp.write_bytes(text.encode("utf-8"))
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise VaultError(f"{path.name} could not be written ({exc.strerror or exc}). Is it open in another program?") from exc

    def _move(self, source: Path, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.name.casefold() == target.name.casefold() and source.parent == target.parent:
                middle = source.parent / f".{secrets.token_hex(6)}.tmp"  # a change of letter case only: in two steps
                os.replace(source, middle)
                source = middle
            os.replace(source, target)
        except OSError as exc:
            raise VaultError(f"{source.name} could not be moved ({exc.strerror or exc}). Is it open in another program?") from exc

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int]:
        st = path.stat()
        return st.st_mtime_ns, st.st_size

    # ---- the index

    def refresh(self, force: bool = False) -> None:
        """Read the files that are new or changed, and forget those that are gone. At most once a second."""
        with self.lock:
            if not force and time.monotonic() - self._looked < 1.0:
                return
            seen: dict[str, Note] = {}
            changed = False
            if self.exists:
                for path in self._files():
                    rel = path.relative_to(self.root).as_posix()
                    try:
                        stamp = self._stamp(path)
                        old = self.notes.get(rel)
                        if old is not None and old.stamp == stamp:
                            seen[rel] = old
                            continue
                        seen[rel] = read_text(path.read_bytes().decode("utf-8", errors="replace"), rel, stamp)
                        changed = True
                    except OSError:
                        continue  # gone, or not readable, between the listing and the reading
            if changed or seen.keys() != self.notes.keys():
                self.notes = seen
                self._settle()
            self._looked = time.monotonic()

    def _settle(self) -> None:
        """Give every note its id, and rebuild what is looked up by id and by name."""
        self.by_id, self.by_name, self._back = {}, {}, None
        for path in sorted(self.notes):
            note = self.notes[path]
            own = prop(note.props, "id")
            if isinstance(own, str) and ID_RE.match(own) and own not in self.by_id:
                note.id, note.provisional = own, False
            else:  # none, or one that another file already has (a copy made by hand)
                note.id, note.provisional = "x" + hashlib.sha1(key(path).encode("utf-8")).hexdigest()[:10], True
            self.by_id[note.id] = note
            self.by_name.setdefault(key(note.title), []).append(note)

    def _reread(self, *relative: str) -> None:
        """After the app wrote or moved files itself."""
        for rel in relative:
            path = self.root / rel
            if path.is_file():
                self.notes[rel] = read_text(path.read_bytes().decode("utf-8", errors="replace"), rel, self._stamp(path))
            else:
                self.notes.pop(rel, None)
        self._settle()

    def get(self, note_id: str) -> Note:
        self.refresh()
        with self.lock:
            note = self.by_id.get(note_id) or self.by_id.get(self.aliases.get(note_id, ""))
            if note is None:
                raise Missing("There is no such note. It may have been renamed or removed outside the app.")
            return note

    def resolve(self, target: str) -> Note | None:
        """The note a link points to: by file name in any letter case, with a folder only when one is written."""
        t = _nfc(target).strip().replace("\\", "/")
        if t.lower().endswith(".md"):
            t = t[:-3]
        k = t.casefold().strip("/")
        if not k:
            return None
        if "/" in k:
            found = [n for n in self.notes.values() if key(n.path[:-3]) == k or key(n.path[:-3]).endswith("/" + k)]
        else:
            found = self.by_name.get(k, [])
        return min(found, key=lambda n: (n.path.count("/"), len(n.path), n.path)) if found else None

    def backlinks(self, note: Note) -> list[tuple[Note, Link]]:
        if self._back is None:
            back: dict[str, list[tuple[Note, Link]]] = {}
            for other in self.notes.values():
                for link in other.links:
                    to = self.resolve(link.target) if link.target else None
                    if to is not None and to is not other:
                        back.setdefault(to.path, []).append((other, link))
            self._back = back
        return self._back.get(note.path, [])

    # ---- what the pages show

    def brief(self, note: Note) -> dict[str, Any]:
        return {"id": note.id, "title": note.title, "kind": note.kind, "inbox": note.inbox, "untitled": note.untitled,
                "tags": note.tags, "excerpt": note.excerpt, "created": note.created, "modified": note.modified,
                "links": len(note.links), "backlinks": len({o.path for o, _ in self.backlinks(note)})}

    def peek(self, target: str, anchor: str = "", inside: Note | None = None) -> dict[str, Any]:
        """What a link leads to, for showing it without opening it."""
        note = self.resolve(target) if target else inside
        if note is None:
            return {"found": False}
        out = {"found": True, "id": note.id, "title": note.title, "kind": note.kind, "text": note.excerpt}
        if anchor.startswith("^"):
            text = note.blocks.get(anchor[1:])
            out.update(found=text is not None, text=text or "", block=anchor[1:])
        elif anchor:
            out.update(found=any(key(h) == key(anchor) for h in note.headings), heading=anchor)
        return out

    def detail(self, note: Note) -> dict[str, Any]:
        seen: set[str] = set()
        back = []
        for other, link in sorted(self.backlinks(note), key=lambda pair: pair[0].modified, reverse=True):
            if other.path not in seen:
                seen.add(other.path)
                back.append({"id": other.id, "title": other.title, "kind": other.kind, "untitled": other.untitled,
                             "context": link.context or other.excerpt})
        links = [{"target": k.target, "anchor": k.anchor, "shown": k.shown, "embed": k.embed,
                  "to": self.peek(k.target, k.anchor, note)} for k in note.links]
        props = [{"key": p.key, "raw": "\n".join(p.raw or [])} if p.value is OPAQUE else {"key": p.key, "value": p.value}
                 for p in note.props if p.key]
        return {**self.brief(note), "path": note.path, "body": note.body, "props": props, "token": note.token,
                "headings": note.headings, "backlinks": back, "links": links}

    def listing(self, kind: str | None = None, tag: str | None = None, inbox: bool | None = None) -> list[dict[str, Any]]:
        self.refresh()
        with self.lock:
            rows = [n for n in self.notes.values()
                    if (kind is None or n.kind == kind) and (inbox is None or n.inbox == inbox)
                    and (tag is None or key(tag) in {key(t) for t in n.tags})]
            return [self.brief(n) for n in sorted(rows, key=lambda n: n.modified, reverse=True)]

    def tags(self) -> list[dict[str, Any]]:
        self.refresh()
        with self.lock:
            counts: dict[str, list[Any]] = {}
            for note in self.notes.values():
                for tag in note.tags:
                    counts.setdefault(key(tag), [tag, 0])[1] += 1
            return [{"tag": t, "count": n} for t, n in sorted(counts.values(), key=lambda c: (-c[1], key(c[0])))]

    def complete(self, text: str, limit: int = 12) -> list[dict[str, Any]]:
        """Notes to offer while a link is being typed: those whose title starts with the text, then the rest."""
        self.refresh()
        q = key(text.strip())
        with self.lock:
            rows = []
            for note in self.notes.values():
                t = key(note.title)
                rank = 0 if t.startswith(q) else 1 if any(w.startswith(q) for w in t.split()) else 2 if q in t else None
                if rank is not None:
                    rows.append((note.untitled, rank, note))
            rows.sort(key=lambda r: (r[0], r[1], r[2].path.count("/"), -r[2].stamp[0]))
            return [{"id": n.id, "title": n.title, "kind": n.kind, "untitled": n.untitled, "excerpt": n.excerpt[:120],
                     "link": n.title if len(self.by_name.get(key(n.title), [])) == 1 else n.path[:-3]}
                    for _, _, n in rows[:limit]]

    def anchors(self, target: str) -> dict[str, Any]:
        """The headings and blocks of a note, to complete a link to a part of it."""
        self.refresh()
        with self.lock:
            note = self.resolve(target)
            if note is None:
                return {"headings": [], "blocks": []}
            return {"headings": note.headings, "blocks": [{"id": b, "text": t[:160]} for b, t in note.blocks.items()]}

    # ---- writing

    def _taken(self, title: str, but: Note | None = None) -> Note | None:
        return next((n for n in self.by_name.get(key(title), []) if n is not but), None)

    def _free(self, folder: str, name: str) -> str:
        """A path in ``folder`` for ``name`` that no file has: the name itself, or with a number after it."""
        n, candidate = 1, name
        while True:
            rel = f"{folder}/{candidate}.md" if folder else f"{candidate}.md"
            if not (self.root / rel).exists() and not self._taken(candidate):
                return rel
            n += 1
            candidate = f"{name[:TITLE_MAX - 4]} {n}"

    def _new_id(self, prefix: str = "n") -> str:
        while True:
            made = prefix + secrets.token_hex(4)
            if made not in self.by_id:
                return made

    @staticmethod
    def _body(raw: Any) -> str:
        if not isinstance(raw, str):
            raise VaultError("a note's text is text")
        body = raw.replace("\r\n", "\n").replace("\r", "\n")
        if len(body) > BODY_MAX:
            raise VaultError(f"a note can hold {BODY_MAX:,} characters; this one has {len(body):,}")
        return body

    def _merge(self, note: Note, incoming: Any) -> list[Prop]:
        """The note's properties after the page sent ``incoming``: a list of {key, value}, in the order to keep."""
        old = {p.key: p for p in note.props if p.key}
        if incoming is None:
            return list(note.props)
        if not isinstance(incoming, list) or len(incoming) > 60:
            raise VaultError("properties are a list of names and values")
        out: list[Prop] = []
        for item in incoming:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                raise VaultError("a property has a name")
            name = item["key"].strip()
            if not re.match(r"^\w[^:#\n]*$", name) or len(name) > 60 or any(p.key == name for p in out):
                raise VaultError(f"'{name}' cannot be the name of a property, or is there twice")
            if name in PROTECTED:
                continue
            had = old.get(name)
            if "value" not in item:  # one the app does not read: kept as it was
                if had is not None and had.value is OPAQUE:
                    out.append(had)
                continue
            value = item["value"]
            if isinstance(value, list):
                if len(value) > 200 or not all(isinstance(v, str) and len(v) <= 500 for v in value):
                    raise VaultError(f"'{name}' is a list of short texts")
                value = [v.strip() for v in value if v.strip()]
            elif isinstance(value, str):
                if len(value) > 4000:
                    raise VaultError(f"'{name}' is too long for a property")
            elif value is not None and not isinstance(value, (bool, int, float)):
                raise VaultError(f"'{name}' has a value the app cannot keep")
            if name == "tags":
                value = clean_tags(value if isinstance(value, (list, str)) else [])
            if name == "kind":
                if value not in WRITABLE and value != note.kind:
                    raise VaultError("a note is an idea note, an index note, a quick note or a project note")
            same = had is not None and had.value is not OPAQUE and had.value == value and type(had.value) is type(value)
            out.append(had if same else Prop(name, value))
        kept = [old[k] for k in PROTECTED if k in old]
        if [(p.key, p.value) for p in kept + out] == [(p.key, p.value) for p in note.props if p.key]:
            return list(note.props)  # nothing changed: comments and spacing stay as they were
        return kept + out

    def _stamp_props(self, note: Note, props: list[Prop]) -> list[Prop]:
        """Every note the app saves names its id, its kind and when it was made."""
        head = []
        if note.provisional or prop(props, "id") != note.id:
            props = [p for p in props if p.key != "id"]
            head.append(Prop("id", self._new_id() if note.provisional else note.id))
        if prop(props, "kind") not in KINDS:
            props = [p for p in props if p.key != "kind"]
            head.append(Prop("kind", note.kind))
        if not isinstance(prop(props, "created"), str):
            props = [p for p in props if p.key != "created"]
            head.append(Prop("created", note.created or note.modified or now()))
        return head + props

    def create(self, title: Any = "", body: Any = "", kind: str = "idea", props: dict[str, Any] | None = None,
               when: _dt.datetime | None = None) -> dict[str, Any]:
        """A new note. Without a title, and always for a quick note, it goes to the inbox."""
        if kind not in WRITABLE:
            raise VaultError("a note is an idea note, an index note, a quick note or a project note")
        body = self._body(body)
        title = clean_title(title) if isinstance(title, str) and title.strip() else ""
        self.ensure()
        self.refresh(force=True)
        with self.lock:
            if title and kind != "quick":
                taken = self._taken(title)
                if taken is not None or (self.root / f"{title}.md").exists():
                    raise VaultError(f"There is already a note called {taken.title if taken else title}.")
                rel = f"{title}.md"
            else:
                rel = self._free(INBOX, title or inbox_name(body, when))
            front = [Prop("id", self._new_id()), Prop("kind", kind), Prop("created", now())]
            for name, value in (props or {}).items():
                if value not in (None, "", []):
                    front.append(Prop(name, clean_tags(value) if name == "tags" else value))
            self._write(self._inside(rel), compose(front, body))
            self._reread(rel)
            return self.detail(self.notes[rel])

    def save(self, note_id: str, body: Any = None, props: Any = None, token: str | None = None,
             force: bool = False) -> dict[str, Any]:
        """Write a note's text and properties. Refused, with the note as it now is, when the file changed on disk
        since the page loaded it (``token`` is what the page was given), unless ``force``."""
        with self.lock:
            self.refresh(force=True)
            note = self.get(note_id)
            if not force and token != note.token:
                raise Conflict("This note was changed outside the app since you opened it.", self.detail(note))
            new_body = note.body if body is None else self._body(body)
            merged = self._stamp_props(note, self._merge(note, props))
            text = compose(merged, new_body)
            was = note.id
            if text != compose(note.props, note.body) or note.provisional:
                self._write(self._inside(note.path), text)
                self._reread(note.path)
            note = self.notes[note.path]
            if note.id != was:
                self.aliases[was] = note.id
            return self.detail(note)

    def rename(self, note_id: str, title: Any) -> dict[str, Any]:
        """Give a note a title. Its file is named after it, a note waiting in the inbox leaves the inbox, and
        every link to the note, in any file, is written again with the new name."""
        title = clean_title(title)
        with self.lock:
            self.refresh(force=True)
            note = self.get(note_id)
            if note.kind == "book" and note.path.startswith(BOOKS + "/"):
                raise VaultError("A book's file is named by the app.")
            if note.provisional:  # a file made outside the app: it gets its id before it moves
                saved = self.save(note.id, force=True)
                note = self.by_id[saved["id"]]
            if title == note.title and not note.inbox:
                return self.detail(note)
            taken = self._taken(title, but=note)
            folder = "" if note.inbox or "/" not in note.path else note.path.rsplit("/", 1)[0]
            rel = f"{folder}/{title}.md" if folder else f"{title}.md"
            if taken is not None or (key(rel) != key(note.path) and (self.root / rel).exists()):
                raise VaultError(f"There is already a note called {taken.title if taken else title}.")
            linking = [other for other in self.notes.values()
                       if any(k.target and self.resolve(k.target) is note for k in other.links)]
            texts = {other.path: self._relinked(other, note, title if not folder else rel[:-3]) for other in linking}
            self._move(self._inside(note.path), self._inside(rel))
            old_path = note.path
            failed = []
            for path, text in texts.items():
                target = rel if path == old_path else path
                try:
                    self._write(self._inside(target), text)
                except VaultError:
                    failed.append(path)
            self._reread(old_path, rel, *texts)
            out = self.detail(self.notes[rel])
            out["relinked"], out["not_relinked"] = len(texts) - len(failed), failed
            return out

    def _relinked(self, other: Note, note: Note, name: str) -> str:
        """The text of ``other``'s file with its links to ``note`` pointing at the new name."""
        text = compose(other.props, other.body)
        masked = mask_code(text)

        def swap(m: re.Match[str]) -> str:
            if not m.group(2).strip() or self.resolve(m.group(2)) is not note:
                return text[m.start():m.end()]
            anchor = "#" + m.group(3) if m.group(3) is not None else ""
            shown = "|" + m.group(4) if m.group(4) is not None else ""
            return f"{m.group(1)}[[{name}{anchor}{shown}]]"

        out, last = [], 0
        for m in LINK_RE.finditer(masked):  # found in the text without its code, replaced in the text itself
            out += [text[last:m.start()], swap(m)]
            last = m.end()
        return "".join(out) + text[last:]

    # ---- the trash

    def delete(self, note_id: str) -> dict[str, Any]:
        """Move a note's file to the trash folder, from which it can be put back."""
        with self.lock:
            self.refresh(force=True)
            note = self.get(note_id)
            stem, n = note.path[:-3], 1
            rel = f"{TRASH}/{stem}.md"
            while (self.root / rel).exists():
                n += 1
                rel = f"{TRASH}/{stem} {n}.md"
            self._move(self._inside(note.path), self._inside(rel))
            self._reread(note.path)
            return {"deleted": note.id, "title": note.title, "trash": rel[len(TRASH) + 1:]}

    def trash(self) -> list[dict[str, Any]]:
        folder = self.root / TRASH
        rows = []
        if folder.is_dir():
            for path in sorted(folder.rglob("*.md")):
                try:
                    rel = path.relative_to(folder).as_posix()
                    note = read_text(path.read_bytes().decode("utf-8", errors="replace"), rel, self._stamp(path))
                except OSError:
                    continue
                rows.append({"name": rel, "title": note.title, "kind": note.kind, "excerpt": note.excerpt,
                             "untitled": note.untitled, "modified": note.modified})
        return sorted(rows, key=lambda r: r["modified"], reverse=True)

    def restore(self, name: Any) -> dict[str, Any]:
        """Put a note back where it was. When its title has been taken since, it comes back under another."""
        if not isinstance(name, str) or not name.lower().endswith(".md"):
            raise VaultError("name a note in the trash")
        with self.lock:
            self.refresh(force=True)
            source = self._inside(f"{TRASH}/{name}")
            trash = (self.root / TRASH).resolve()
            if trash not in source.parents or not source.is_file():
                raise Missing("There is no such note in the trash.")
            rel = source.relative_to(trash).as_posix()
            folder, stem = (rel.rsplit("/", 1)[0], rel.rsplit("/", 1)[1][:-3]) if "/" in rel else ("", rel[:-3])
            target, n = (f"{folder}/{stem}.md" if folder else f"{stem}.md"), 1
            while (self.root / target).exists() or self._taken(target.rsplit("/", 1)[-1][:-3]):
                label = "restored" if n == 1 else f"restored {n}"
                target = f"{folder}/{stem} ({label}).md" if folder else f"{stem} ({label}).md"
                n += 1
            self._move(source, self._inside(target))
            self._reread(target)
            return self.detail(self.notes[target])

    # ---- books (see bookfile.py)

    def book(self, book_id: str) -> Note | None:
        self.refresh()
        with self.lock:
            note = self.by_id.get(book_id)
            return note if note is not None and note.kind == "book" else None

    def put_book(self, book_id: str, title: str, write: Any) -> Note:
        """Write a book's file. ``write(name)`` gives its text for the file name it has or is given. The file is
        left alone when the text is what it already holds."""
        self.ensure()
        with self.lock:
            self.refresh(force=True)
            note = self.book(book_id)
            rel = note.path if note is not None else self._free(BOOKS, safe_title(title, "Book"))
            text = write(_nfc(rel.rsplit("/", 1)[-1][:-3]))
            path = self._inside(rel)
            if not path.is_file() or path.read_bytes().decode("utf-8", errors="replace") != text:
                self._write(path, text)
                self._reread(rel)
            return self.notes[rel]

    # ---- search

    def search(self, query: str = "", kind: str | None = None, tag: str | None = None, book: str | None = None,
               limit: int = 60) -> dict[str, Any]:
        """Notes of every kind, and the highlights and notes of every book. Every word asked for must be there.
        ``book`` narrows to one book: its passages, and the notes that cite it."""
        self.refresh()
        words = key(query).split()
        with self.lock:
            cited = self.by_id.get(book) if book else None
            if book and cited is None:
                return {"titles": [], "notes": [], "passages": [], "more": False}
            citing = {o.path for o, _ in self.backlinks(cited)} if cited is not None else None
            tagged = key(tag) if tag else None
            notes: list[tuple[int, Note, str]] = []
            passages: list[dict[str, Any]] = []
            for note in self.notes.values():
                if note.kind == "book":
                    if kind not in (None, "book") or tagged or (cited is not None and note is not cited):
                        continue
                    for p in note.passages:
                        fold = key(p.text)
                        if all(w in fold for w in words) and (words or cited is not None or kind == "book"):
                            passages.append({"book": note.title, "book_id": note.id, "block": p.block, "heading": p.heading,
                                             "quote": p.quote, "text": _snippet(p.text, words, 320)})
                    continue
                if (kind is not None and note.kind != kind) or (tagged and tagged not in {key(t) for t in note.tags}) \
                        or (citing is not None and note.path not in citing):
                    continue
                if not all(w in note.fold or any(w in key(t) for t in note.tags) for w in words):
                    continue
                title = key(note.title)
                rank = 0 if words and not note.untitled and all(w in title for w in words) else 1
                notes.append((rank, note, _snippet(_plain_text(note.body), words, 220) if words else note.excerpt[:220]))
            notes.sort(key=lambda r: (r[0], -r[1].stamp[0]))
            q = key(query.strip())
            titles = [self.brief(n) for r, n, _ in notes if q and q in key(n.title) and not n.untitled][:8]
            return {"titles": titles, "notes": [{**self.brief(n), "snippet": s} for _, n, s in notes[:limit]],
                    "passages": passages[:limit], "more": len(notes) > limit or len(passages) > limit}


def _snippet(text: str, words: list[str], width: int) -> str:
    """The part of a text around a word that was searched for."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    fold = flat.casefold()
    at = next((i for i in (fold.find(w) for w in words) if i >= 0), 0)  # the first word asked for that is there
    start = max(0, at - width // 3)
    if start:
        space = flat.find(" ", start)
        start = space + 1 if 0 <= space < at else start
    cut = flat[start:start + width]
    return ("… " if start else "") + cut.rstrip() + (" …" if start + width < len(flat) else "")
