"""The properties at the top of a Markdown file: the YAML between two ``---`` lines.

    ---
    id: n7f3a2c91
    kind: idea
    tags: [pricing, markets]
    sources:
      - "[[The Lighthouse Keeper's Almanac#^h3f9a]]"
    ---
    Prices carry information that no single person holds.

They are read and written here without a YAML library, for a small subset: one value, a list in brackets, or a list
of ``- item`` lines. A property this reader does not understand is kept exactly as it was (its value is ``OPAQUE``),
and so is a line that is no property, such as a comment.

The notes folder (``vault.py``) keeps its notes this way. The Markdown converter and a book's file use the same
reader, and need no notes folder for it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


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
        tag = re.sub(r"[^\w/-]+", "-", unicodedata.normalize("NFC", str(item)).strip().lstrip("#")).strip("-")
        if tag and tag.casefold() not in {t.casefold() for t in out}:
            out.append(tag[:60])
    return out[:50]
