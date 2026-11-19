"""One web request, with limits.

Used for the pictures of a document and, from step 2, for the page at an address. Standard library only.
"""

from __future__ import annotations

import gzip
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass

# A site answers a browser. This is one request for one page the reader asked for.
AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
PAGE_LIMIT, PAGE_SECONDS = 10 * 1024 * 1024, 20
PICTURE_LIMIT, PICTURE_SECONDS = 5 * 1024 * 1024, 10


class FetchError(Exception):
    """``status`` is the server's answer when there was one; ``reason`` names the other cases."""

    def __init__(self, message: str, status: int | None = None, reason: str = "") -> None:
        super().__init__(message)
        self.status, self.reason = status, reason


@dataclass
class Fetched:
    data: bytes
    kind: str  # the content type, without its parameters
    charset: str | None
    url: str  # where the answer came from, after redirects


def is_private(host: str) -> bool:
    """Whether a host name leads to this computer or the local network."""
    try:
        found = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for entry in found:
        try:
            address = ipaddress.ip_address(entry[4][0].split("%")[0])
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved \
                or address.is_multicast or address.is_unspecified:
            return True
    return False


class _Guard(urllib.request.HTTPRedirectHandler):
    """A redirect must stay on the web, and off the local network when the first address had to be."""

    def __init__(self, public_only: bool) -> None:
        self.public_only = public_only

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        check(newurl, self.public_only)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check(url: str, public_only: bool) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise FetchError("only addresses that begin with http:// or https:// can be fetched", reason="scheme")
    if public_only and is_private(parts.hostname):
        raise FetchError("an address on this computer or the local network is not fetched", reason="private")


def get(url: str, limit: int = PAGE_LIMIT, seconds: float = PAGE_SECONDS, public_only: bool = False,
        accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5") -> Fetched:
    check(url, public_only)
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept,
                                                   "Accept-Language": "en,*;q=0.5", "Accept-Encoding": "gzip, deflate"})
    opener = urllib.request.build_opener(_Guard(public_only))
    try:
        with opener.open(request, timeout=seconds) as reply:
            data = reply.read(limit + 1)
            if len(data) > limit:
                raise FetchError(f"the answer is larger than {limit // (1024 * 1024)} MB", reason="size")
            encoding = (reply.headers.get("Content-Encoding") or "").lower()
            try:
                if encoding == "gzip":
                    data = gzip.decompress(data)
                elif encoding == "deflate":
                    data = zlib.decompress(data) if data[:1] == b"\x78" else zlib.decompress(data, -zlib.MAX_WBITS)
            except (OSError, EOFError, zlib.error) as exc:
                raise FetchError("the answer could not be unpacked", reason="broken") from exc
            if len(data) > limit:
                raise FetchError(f"the answer is larger than {limit // (1024 * 1024)} MB", reason="size")
            return Fetched(data, reply.headers.get_content_type(), reply.headers.get_content_charset(), reply.geturl())
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        raise FetchError(f"the site answered {exc.code}", status=exc.code) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FetchError(f"the site did not answer in {seconds:g} seconds", reason="timeout") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise FetchError(f"the site did not answer in {seconds:g} seconds", reason="timeout") from exc
        raise FetchError(f"the address could not be reached ({reason})", reason="unreachable") from exc
