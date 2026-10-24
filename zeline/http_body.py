"""Bounded, non-caching response previews shared by HTTP-backed tools."""
from __future__ import annotations

import requests
import zlib
from urllib3.exceptions import HTTPError

MAX_BODY_BYTES = 32_768
MAX_WIRE_BYTES = 65_536
CHUNK_BYTES = 4096
MAX_TEXT_CHARS = 8000
TRUNCATED = "\n... [truncated]"


def read_preview(response) -> tuple[str, bool]:
    """Bound wire bytes AND decoded bytes, including compressed response bombs.

    Read at most one lookahead byte beyond each cap to distinguish truncation.
    Content-Length is never used as authority. Unsupported content codings fail
    closed rather than delegating unbounded decompression to urllib3.
    """
    coding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if coding not in {"", "identity", "gzip", "deflate"}:
        raise requests.exceptions.ContentDecodingError("Unsupported bounded content encoding")
    bits = 16 + zlib.MAX_WBITS if coding == "gzip" else zlib.MAX_WBITS
    decoder = zlib.decompressobj(bits) if coding == "gzip" else None
    deflate_header = bytearray()
    payload = bytearray()
    wire_bytes = 0
    truncated = False
    try:
        while len(payload) <= MAX_BODY_BYTES:
            amount = min(CHUNK_BYTES, MAX_WIRE_BYTES + 1 - wire_bytes)
            chunk = response.raw.read(amount, decode_content=False)
            if not chunk:
                if deflate_header:
                    raise requests.exceptions.ContentDecodingError("Incomplete deflate header")
                if decoder is not None and wire_bytes and not decoder.eof:
                    raise requests.exceptions.ContentDecodingError("Incomplete compressed response")
                break
            wire_bytes += len(chunk)
            if wire_bytes > MAX_WIRE_BYTES:
                truncated = True
                break
            if coding == "deflate" and decoder is None:
                deflate_header.extend(chunk)
                if len(deflate_header) < 2:
                    continue
                cmf, flg = deflate_header[:2]
                wrapped = cmf & 15 == 8 and cmf >> 4 <= 7 and (cmf * 256 + flg) % 31 == 0
                bits = zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS
                decoder = zlib.decompressobj(bits)
                chunk = bytes(deflate_header)
                deflate_header.clear()
            if decoder is None:
                payload.extend(chunk[:MAX_BODY_BYTES + 1 - len(payload)])
            else:
                pending = chunk
                while pending:
                    if decoder.eof:
                        if coding != "gzip":
                            raise requests.exceptions.ContentDecodingError("Trailing compressed data")
                        decoder = zlib.decompressobj(bits)
                    payload.extend(decoder.decompress(pending, MAX_BODY_BYTES + 1 - len(payload)))
                    if len(payload) > MAX_BODY_BYTES:
                        break
                    pending = decoder.unused_data
            if len(payload) > MAX_BODY_BYTES:
                truncated = True
                break
    except zlib.error as exc:
        raise requests.exceptions.ContentDecodingError("Invalid compressed response") from exc
    except requests.RequestException:
        raise
    except HTTPError as exc:
        raise requests.RequestException("Response stream read failed") from exc
    charset = response.encoding or "utf-8"
    try:
        text = bytes(payload[:MAX_BODY_BYTES]).decode(charset, errors="replace")
    except LookupError:
        text = bytes(payload[:MAX_BODY_BYTES]).decode("utf-8", errors="replace")
    return text, truncated


def format_preview(text: str, truncated: bool) -> str:
    """Keep the historical character display cap distinct from the byte read cap."""
    return text[:MAX_TEXT_CHARS] + (TRUNCATED if truncated or len(text) > MAX_TEXT_CHARS else "")


def merge_response_cookies(jar, response):
    """Apply response cookies AND expiry tombstones to a redirect-chain jar.

    A response jar omits expired Set-Cookie entries, so update() alone cannot
    delete existing cookies. Re-extract the original headers using Requests'
    policy-aware parser. This does not read/drain the response body.
    """
    jar.update(response.cookies)
    prepared = getattr(response, 'request', None)
    raw = getattr(response, 'raw', None)
    if prepared is not None and raw is not None:
        requests.cookies.extract_cookies_to_jar(jar, prepared, raw)


def discard_redirect_body(response, *args, **kwargs):
    """Prevent requests from draining redirect bodies even with redirects off.

    Session.send prepares Response.next through resolve_redirects, which accesses
    Response.content. Close first and cache an explicitly discarded empty body;
    callers validate and follow Location themselves, never Response.next.
    """
    if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("Location"):
        response.close()
        response._content = b""
        response._content_consumed = True
    return response


def redact_preview(text: str, secrets: list[str], truncated: bool) -> str:
    """Mask a union of original-text ranges, never cut before finding secrets.

    The byte-cap tail can include an incomplete decoded character, so reserve a
    whole maximum-secret-length tail. The mask is bounded by the input preview.
    Unioning ranges also covers overlapping credentials and a new-cut boundary.
    """
    secrets = [secret for secret in secrets if secret]
    if not secrets:
        return text
    mask = bytearray(len(text))
    for secret in secrets:
        start = text.find(secret)
        while start >= 0:
            mask[start:start + len(secret)] = b"\x01" * len(secret)
            start = text.find(secret, start + 1)
    if truncated:
        margin = min(len(text), max(map(len, secrets)))
        mask[len(text) - margin:] = b"\x01" * margin
    parts = []
    start = index = 0
    while index < len(text):
        if not mask[index]:
            index += 1
            continue
        parts.append(text[start:index])
        while index < len(text) and mask[index]:
            index += 1
        parts.append("[REDACTED]")
        start = index
    parts.append(text[start:])
    return "".join(parts)
