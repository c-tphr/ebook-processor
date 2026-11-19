"""A web page saved as one file: ``.mhtml`` from Chrome and Edge.

The file is a message in parts, as e-mail is: the page first, then everything the page showed, each part under
the address it came from. Python's ``email`` module reads it.
"""

from __future__ import annotations

import email
import email.policy
from typing import Any


class NotSaved(ValueError):
    pass


def read(data: bytes) -> tuple[bytes, str | None, str | None, dict[str, bytes]]:
    """The page's HTML, the encoding it is in, the address it was saved from, and its other parts by address."""
    try:
        message = email.message_from_bytes(data, policy=email.policy.compat32)
    except Exception as exc:  # noqa: BLE001
        raise NotSaved("This file cannot be read as a saved page.") from exc
    page: bytes | None = None
    charset: str | None = None
    address = (message.get("Snapshot-Content-Location") or "").strip() or None
    parts: dict[str, bytes] = {}
    for part in message.walk():
        if part.is_multipart():
            continue
        body: Any = part.get_payload(decode=True)
        if not isinstance(body, bytes):
            continue
        where = (part.get("Content-Location") or "").strip()
        if page is None and part.get_content_type() in ("text/html", "application/xhtml+xml"):
            page, charset = body, part.get_content_charset()
            address = address or where or None
            continue
        if where:
            parts[where] = body
        cid = (part.get("Content-ID") or "").strip("<> ")
        if cid:
            parts["cid:" + cid] = body
    if page is None:
        raise NotSaved("This file holds no page. Save the page again as \"Webpage, Single File\".")
    if address and not address.startswith(("http://", "https://")):
        address = None
    return page, charset, address, parts
