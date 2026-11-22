"""Bytes of unknown encoding, as text."""

from __future__ import annotations

import codecs
import re

BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
META = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.I)


def decode(data: bytes, declared: str | None = None) -> str:
    """A byte-order mark decides; then what the file or the server declares; then UTF-8 when the bytes are valid
    UTF-8; then a guess."""
    for bom, name in BOMS:
        if data.startswith(bom):
            return data.decode(name, errors="replace")
    for name in (declared, "utf-8"):
        if not name:
            continue
        try:
            return data.decode(name)
        except (LookupError, UnicodeError):  # a name Python does not know, or one it refuses ('undefined')
            pass
    try:
        from charset_normalizer import from_bytes
        found = from_bytes(data)
        western = next((m for m in found if m.encoding == "cp1252"), None)  # the commonest by far, when it fits
        best = western or found.best()
        if best is not None:
            return str(best)
    except Exception:  # noqa: BLE001 - a guess that fails must not stop the reading
        pass
    return data.decode("cp1252", errors="replace")


def declared_in_html(data: bytes) -> str | None:
    m = META.search(data[:4096])
    return m.group(1).decode("ascii", "replace") if m else None
