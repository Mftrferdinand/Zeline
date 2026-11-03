"""Tests for SSRF protections: redirect hops and mixed DNS records."""
from unittest import mock

from zeline import tools


class _FakeResponse:
    def __init__(self, url, location, is_redirect=True):
        self.url = url
        self.headers = {"Location": location}
        self.is_redirect = is_redirect


def test_hook_blocks_redirect_to_internal_ip():
    hook = tools._SSRFSafeRedirectHook()
    response = _FakeResponse("https://93.184.216.34/start", "http://169.254.169.254/latest/meta-data/")
    try:
        hook(response)
        blocked = False
    except requests.RequestException:
        blocked = True
    assert blocked, "redirect to link-local metadata IP must be blocked"


import requests  # noqa: E402  (import after helpers defined above is fine in tests)


def test_hook_blocks_redirect_to_localhost():
    hook = tools._SSRFSafeRedirectHook()
    response = _FakeResponse("https://93.184.216.34/start", "http://127.0.0.1:8080/admin")
    try:
        hook(response)
        blocked = False
    except requests.RequestException:
        blocked = True
    assert blocked


def test_hook_blocks_redirect_to_private_range():
    hook = tools._SSRFSafeRedirectHook()
    response = _FakeResponse("https://93.184.216.34/start", "http://192.168.1.10/x")
    try:
        hook(response)
        blocked = False
    except requests.RequestException:
        blocked = True
    assert blocked


def test_hook_allows_public_redirect():
    hook = tools._SSRFSafeRedirectHook()
    # A response that is NOT a redirect passes through untouched.
    assert hook(_FakeResponse("https://93.184.216.34/x", "", is_redirect=False)) is None
    # A redirect to a public target is allowed (returns None = follow).
    response = _FakeResponse("https://93.184.216.34/start", "https://93.184.216.34/landing")
    assert hook(response) is None


def test_hook_enforces_max_hops():
    hook = tools._SSRFSafeRedirectHook(max_hops=2)
    response = _FakeResponse("https://93.184.216.34/start", "https://93.184.216.34/a")
    assert hook(response) is None  # hop 1 allowed
    assert hook(response) is None  # hop 2 allowed
    try:
        hook(response)
        blocked = False
    except requests.RequestException:
        blocked = True
    assert blocked


def test_validate_url_rejects_non_http_schemes():
    assert tools._validate_url_for_ssrf("file:///etc/passwd") is not None
    assert tools._validate_url_for_ssrf("ftp://example.com") is not None


def test_validate_url_rejects_loopback_ip_literal():
    assert tools._validate_url_for_ssrf("http://127.0.0.1/") is not None
    assert tools._validate_url_for_ssrf("http://[::1]/") is not None


def test_validate_url_accepts_public():
    assert tools._validate_url_for_ssrf("https://93.184.216.34/x") is None


def test_is_internal_ip_blocks_when_any_record_is_internal():
    """Mixed public+private DNS must be blocked (any, not all)."""
    info_public = (2, 1, 6, "", ("93.184.216.34", 80))
    info_private = (2, 1, 6, "", ("10.0.0.5", 80))
    with mock.patch("socket.getaddrinfo", return_value=[info_public, info_private]):
        assert tools._is_internal_ip("mixed.example.com") is True


def test_is_internal_ip_allows_all_public_records():
    info_a = (2, 1, 6, "", ("93.184.216.34", 80))
    info_b = (2, 1, 6, "", ("93.184.216.35", 80))
    with mock.patch("socket.getaddrinfo", return_value=[info_a, info_b]):
        assert tools._is_internal_ip("public.example.com") is False
