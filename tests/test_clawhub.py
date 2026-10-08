"""Tests for zeline/clawhub.py.

Covers:
- [CH1] trending_clawhub() limit clamping (1..50, like search_clawhub)
- [CH2] _api_get streaming size guard (10MB cap, A2-L2)
"""

from unittest.mock import patch

import pytest

from zeline import clawhub


class FakeResponse:
    """Minimal requests.Response stand-in for _api_get tests."""

    def __init__(self, json_data, content_length=None, raw_bytes=None,
                 lie_about_length=False):
        import json as _json
        self._json_data = json_data
        # raw_bytes lets tests simulate a lying Content-Length header.
        self._raw = (raw_bytes if raw_bytes is not None
                     else _json.dumps(json_data).encode("utf-8"))
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self._lie = lie_about_length

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data

    def iter_content(self, chunk_size=8192):
        # A2-L2: _api_get streams the body; yield it in chunks.
        for i in range(0, len(self._raw), chunk_size):
            yield self._raw[i:i + chunk_size]


def _items(n):
    return [
        {
            "slug": f"skill-{i}",
            "displayName": f"Skill {i}",
            "summary": f"summary {i}",
            "stats": {"installs": 100 - i, "stars": i},
        }
        for i in range(n)
    ]


# ---------- CH1: trending limit clamping ----------

def test_trending_clawhub_limit_clamped_low():
    """limit=0 and negative values clamp to 1."""
    with patch.object(
        clawhub, "_api_get", return_value={"items": _items(3)}
    ) as m:
        results = clawhub.trending_clawhub(0)
        assert len(results) == 1
        sent = m.call_args[0][1]
        assert sent["limit"] == 1


def test_trending_clawhub_limit_clamped_high():
    """limit=1000 clamps to 50."""
    with patch.object(
        clawhub, "_api_get", return_value={"items": _items(60)}
    ) as m:
        results = clawhub.trending_clawhub(1000)
        sent = m.call_args[0][1]
        assert sent["limit"] == 50
        assert len(results) == 50  # sliced by clamped limit


def test_trending_clawhub_limit_normal_unchanged():
    """A sane limit passes through untouched."""
    with patch.object(
        clawhub, "_api_get", return_value={"items": _items(10)}
    ) as m:
        results = clawhub.trending_clawhub(5)
        assert m.call_args[0][1]["limit"] == 5
        assert len(results) == 5


# ---------- CH2: response size guard ----------

def test_api_get_rejects_oversized_response():
    """Body > 10MB raises while streaming (A2-L2)."""
    raw = b"x" * (clawhub._CLAWHUB_MAX_RESPONSE_BYTES + 1)
    big = FakeResponse(None, raw_bytes=raw)
    with patch("zeline.tools._safe_request", return_value=big):
        with pytest.raises(ValueError, match="too large"):
            clawhub._api_get("/skills", {"limit": 1})


def test_api_get_rejects_lying_content_length():
    """A2-L2: small Content-Length header but huge body still rejected.

    The old code trusted the header; the streaming accumulator does not.
    """
    raw = b"y" * (clawhub._CLAWHUB_MAX_RESPONSE_BYTES + 100)
    lying = FakeResponse(None, content_length=1024, raw_bytes=raw)
    with patch("zeline.tools._safe_request", return_value=lying):
        with pytest.raises(ValueError, match="too large"):
            clawhub._api_get("/skills")


def test_api_get_rejects_invalid_json():
    """Garbage bytes raise a clear ValueError (not a raw JSON error)."""
    bad = FakeResponse(None, raw_bytes=b"not json {{{")
    with patch("zeline.tools._safe_request", return_value=bad):
        with pytest.raises(ValueError, match="invalid JSON"):
            clawhub._api_get("/skills")


def test_api_get_allows_normal_response():
    """Small Content-Length proceeds to json()."""
    ok = FakeResponse({"ok": True}, content_length=1024)
    with patch("zeline.tools._safe_request", return_value=ok):
        assert clawhub._api_get("/skills") == {"ok": True}


def test_api_get_no_content_length_header():
    """Missing Content-Length header: continue (simple approach)."""
    ok = FakeResponse({"ok": True})
    with patch("zeline.tools._safe_request", return_value=ok):
        assert clawhub._api_get("/skills") == {"ok": True}


def test_api_get_malformed_content_length():
    """Non-numeric Content-Length: treated as absent, no crash."""
    ok = FakeResponse({"ok": True}, content_length="not-a-number")
    with patch("zeline.tools._safe_request", return_value=ok):
        assert clawhub._api_get("/skills") == {"ok": True}


def test_api_get_exactly_at_cap():
    """Exactly 10MB body is allowed; 10MB+1 is rejected (A2-L2 streaming)."""
    import json as _json
    cap = clawhub._CLAWHUB_MAX_RESPONSE_BYTES
    # Build a valid JSON body of exactly `cap` bytes: {"data": "<pad>"}.
    prefix, suffix = b'{"data": "', b'"}'
    pad_len = cap - len(prefix) - len(suffix)
    ok = FakeResponse(None, raw_bytes=prefix + b"a" * pad_len + suffix)
    with patch("zeline.tools._safe_request", return_value=ok):
        data = clawhub._api_get("/skills")
        assert data["data"] == "a" * pad_len
    big = FakeResponse(None, raw_bytes=prefix + b"a" * (pad_len + 1) + suffix)
    with patch("zeline.tools._safe_request", return_value=big):
        with pytest.raises(ValueError, match="too large"):
            clawhub._api_get("/skills")
